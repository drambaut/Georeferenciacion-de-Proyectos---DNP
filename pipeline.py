"""
pipeline.py
Reads the shared Excel metadata file, checks Azure Blob Storage to see which
monthly images already exist for each project, downloads only the missing
ones from Copernicus using utils/Download_sat_imgs.py, and uploads them.

Azure Blob Storage is the source of truth for what has already been
processed -- not a local state file. This means the script gives correct
results even on a fresh machine or after pipeline_state.json is deleted.

Runs once and exits.

Usage:
    python pipeline.py
"""

import sys
import os
import json
import time
import logging
import argparse
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import openeo
import requests
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.Download_sat_imgs import (
    dms_a_decimal,
    calcular_bbox,
    descargar_mes,
    carpeta_sentinel,
    calcular_tramos,
    CARPETA_SALIDA,
    DESCARGA,
    KM_BUFFER,
    PAUSA_ENTRE_DESCARGAS,
)

load_dotenv()


# ── Configuration ─────────────────────────────────────────────────

PROJECT_METADATA_XLSX_URL    = os.getenv("PROJECT_METADATA_XLSX_URL")
PROJECT_METADATA_SHEET_NAME  = os.getenv(
    "PROJECT_METADATA_SHEET_NAME",
    "proyectos_satview",
)

AZURE_CONN_STR  = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "imagenes-sentinel")

STATE_PATH = Path("pipeline_state.json")   # audit log only, not source of truth
LOG_PATH   = Path("pipeline_log.txt")

BORRAR_LOCAL_TRAS_SUBIR = True

# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("pipeline")

# Silence verbose HTTP request/response logging from the Azure SDK and its
# dependencies. They log at INFO level by default, which floods the console
# with request headers on every single blob list/upload call.
for noisy_logger in ("azure", "azure.core.pipeline.policies.http_logging_policy",
                     "urllib3", "msrest"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# ── Configuration validation ────────────────────────────────────────

def validar_configuracion() -> None:
    faltantes = []
    if not PROJECT_METADATA_XLSX_URL:
        faltantes.append("PROJECT_METADATA_XLSX_URL")
    if not AZURE_CONN_STR:
        faltantes.append("AZURE_STORAGE_CONNECTION_STRING")
    if not AZURE_CONTAINER:
        faltantes.append("AZURE_CONTAINER")

    if faltantes:
        print("Missing required environment variables:")
        for var in faltantes:
            print(f"  - {var}")
        print("\nCheck your .env file and that it sits next to pipeline.py.")
        sys.exit(1)


# ── Metadata reading (Excel) ────────────────────────────────────────

def _metadata_download_url(url: str) -> str:
    """
    Normalizes SharePoint/OneDrive viewer links to direct-download links.
    Other URLs are left untouched.
    """
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?", 1)[0] + "?download=1"
    return url


def leer_metadata_proyectos() -> pd.DataFrame:
    response = requests.get(_metadata_download_url(PROJECT_METADATA_XLSX_URL), timeout=120)
    response.raise_for_status()
    excel_bytes = BytesIO(response.content)
    try:
        df = pd.read_excel(
            excel_bytes,
            sheet_name=PROJECT_METADATA_SHEET_NAME,
            dtype=str,
        )
    except ValueError:
        excel_bytes.seek(0)
        df = pd.read_excel(excel_bytes, sheet_name=0, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.astype(str)
    return df


# ── Azure ground-truth check ────────────────────────────────────────

def meses_objetivo() -> set:
    return {(anio, mes) for anio, meses in DESCARGA.items() for mes in meses}


def meses_ya_en_azure(container_client, bpin: str, tramo_slug: str | None = None) -> set:
    prefix = f"{carpeta_sentinel(bpin, tramo_slug)}/"
    existentes = set()
    for blob in container_client.list_blobs(name_starts_with=prefix):
        filename = blob.name.split("/")[-1]
        stem = filename.rsplit(".", 1)[0]
        parts = stem.split("_")
        if len(parts) == 2:
            existentes.add((parts[0], parts[1]))
    return existentes


def calcular_pendientes(df: pd.DataFrame, container_client) -> list:
    """
    Returns a list of dicts: {"row": pd.Series, "tramo_slug": str|None, "pendientes": [(anio, mes), ...]}
    Only includes projects (or project-tramos) missing at least one target month in Azure.
    Expects df to already have a "tramo_slug" column (see calcular_tramos()).
    """
    objetivo   = meses_objetivo()
    resultado  = []

    for _, row in df.iterrows():
        bpin = str(row["bpin"]).strip()
        if not bpin:
            continue
        # df.iterrows() puede convertir un tramo_slug None a NaN (float) si el
        # resto de columnas de la fila son texto (ver nota en carpeta_sentinel()
        # en utils/Download_sat_imgs.py) -- normalizar explicitamente a None.
        tramo_slug  = row.get("tramo_slug")
        if not isinstance(tramo_slug, str):
            tramo_slug = None
        ya_en_azure = meses_ya_en_azure(container_client, bpin, tramo_slug)
        pendientes  = sorted(objetivo - ya_en_azure)
        if pendientes:
            resultado.append({"row": row, "tramo_slug": tramo_slug, "pendientes": pendientes})

    return resultado


# ── Azure upload ───────────────────────────────────────────────────

def subir_a_azure(container_client, local_path: str, bpin: str, anio: str, mes: str,
                  tramo_slug: str | None = None) -> bool:
    if not os.path.exists(local_path):
        log.error(f"Local file not found, cannot upload: {local_path}")
        return False

    blob_path = f"{carpeta_sentinel(bpin, tramo_slug)}/{anio}_{mes}.tiff"
    try:
        with open(local_path, "rb") as f:
            container_client.upload_blob(name=blob_path, data=f, overwrite=True)
        return True
    except Exception as e:
        log.error(f"Azure upload failed for {blob_path}: {e}")
        return False
    finally:
        if BORRAR_LOCAL_TRAS_SUBIR and os.path.exists(local_path):
            os.remove(local_path)


# ── Tramo manifest: safe automatic rename when georreferenciacion text changes ──
#
# For BPINs with multiple tramo rows, we keep a small manifest blob
# (sentinel2_{bpin}/manifest.json) mapping the stable "tramo_id" column to the
# tramo's current folder slug. If the text in "georreferenciacion" changes for
# an existing tramo_id, we know for certain (via the stable id) that it's a
# rename, not a new/deleted tramo, and can safely move the existing images in
# Azure to the new folder name instead of leaving them orphaned or
# re-downloading from Copernicus.

def _normalizar_tramo_id(valor) -> str:
    # valor es None cuando la columna 'tramo_id' no existe en la hoja (es
    # opcional). str(None) da el texto "None", que NO es vacio para el chequeo
    # de abajo -- sin este caso aparte, todas las filas sin tramo_id real
    # terminarian compartiendo el mismo id falso "None" y "colisionando" entre
    # si en el manifiesto, disparando renombrados en cascada sin sentido.
    if valor is None:
        return ""
    s = str(valor).strip()
    if not s or s.lower() == "nan":
        return ""
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def leer_manifiesto(container_client, bpin: str) -> dict:
    blob_path = f"sentinel2_{bpin}/manifest.json"
    try:
        contenido = container_client.get_blob_client(blob_path).download_blob().readall()
        return json.loads(contenido)
    except Exception:
        return {}


def escribir_manifiesto(container_client, bpin: str, manifiesto: dict) -> None:
    blob_path = f"sentinel2_{bpin}/manifest.json"
    data = json.dumps(manifiesto, indent=2, ensure_ascii=False).encode("utf-8")
    container_client.upload_blob(name=blob_path, data=data, overwrite=True)


def renombrar_carpeta_azure(container_client, prefix_viejo: str, prefix_nuevo: str) -> int:
    """Mueve todos los blobs bajo prefix_viejo/ a prefix_nuevo/ (download+upload+delete
    a traves de la misma conexion ya autenticada; los archivos son chicos, no hace
    falta server-side copy con SAS). Retorna cuantos blobs se movieron."""
    prefix_viejo = prefix_viejo.rstrip("/") + "/"
    prefix_nuevo = prefix_nuevo.rstrip("/") + "/"
    blobs = list(container_client.list_blobs(name_starts_with=prefix_viejo))
    for blob in blobs:
        nombre_nuevo = prefix_nuevo + blob.name[len(prefix_viejo):]
        contenido = container_client.get_blob_client(blob.name).download_blob().readall()
        container_client.upload_blob(name=nombre_nuevo, data=contenido, overwrite=True)
        container_client.delete_blob(blob.name)
    return len(blobs)


def sincronizar_tramos_bpin(container_client, bpin: str, filas_tramo: list) -> None:
    """filas_tramo: lista de dicts (filas del Excel) de un mismo BPIN con
    'tramo_id' y 'tramo_slug'. Compara contra el manifiesto guardado en Azure;
    si un tramo_id sigue existiendo pero su slug cambio (texto editado), renombra
    la carpeta en Azure automaticamente. Si un tramo_id del manifiesto ya no
    aparece en el Excel, no se toca su carpeta -- solo se registra un aviso."""
    if not filas_tramo:
        return

    manifiesto_viejo = leer_manifiesto(container_client, bpin)
    manifiesto_nuevo = {}

    for fila in filas_tramo:
        tramo_id    = _normalizar_tramo_id(fila.get("tramo_id"))
        slug_actual = fila.get("tramo_slug")
        if not tramo_id or not slug_actual:
            continue
        manifiesto_nuevo[tramo_id] = slug_actual
        slug_anterior = manifiesto_viejo.get(tramo_id)
        if slug_anterior and slug_anterior != slug_actual:
            prefix_viejo = carpeta_sentinel(bpin, slug_anterior)
            prefix_nuevo = carpeta_sentinel(bpin, slug_actual)
            log.info(f"{bpin}: tramo_id {tramo_id} cambio de slug ('{slug_anterior}' -> '{slug_actual}'), "
                     f"renombrando carpeta en Azure...")
            movidos = renombrar_carpeta_azure(container_client, prefix_viejo, prefix_nuevo)
            log.info(f"{bpin}: {movidos} archivo(s) movidos de '{prefix_viejo}/' a '{prefix_nuevo}/'")

    huerfanos = set(manifiesto_viejo) - set(manifiesto_nuevo)
    for tramo_id_huerfano in huerfanos:
        slug_huerfano = manifiesto_viejo[tramo_id_huerfano]
        log.warning(f"{bpin}: el tramo_id {tramo_id_huerfano} (carpeta "
                    f"'{carpeta_sentinel(bpin, slug_huerfano)}') ya no aparece en el Excel. "
                    f"No se borro nada automaticamente -- revisar manualmente si ya no aplica.")

    if manifiesto_nuevo != manifiesto_viejo:
        escribir_manifiesto(container_client, bpin, manifiesto_nuevo)


# ── Per-project processing ──────────────────────────────────────────

def procesar_proyecto(connection, container_client, row: pd.Series,
                      pendientes: list, descarga_log: list,
                      tramo_slug: str | None = None) -> dict:
    bpin = str(row["bpin"]).strip()
    etiqueta = f"{bpin} ({tramo_slug})" if tramo_slug else bpin
    log.info(f"Processing project: {etiqueta} ({len(pendientes)} month(s) pending)")

    resultado = {"bpin": bpin, "tramo_slug": tramo_slug,
                "fecha_proceso": datetime.now(timezone.utc).isoformat(),
                "imagenes_ok": 0, "imagenes_error": 0}

    try:
        lat = dms_a_decimal(str(row["latitud"]).strip())
        lon = dms_a_decimal(str(row["longitud"]).strip())
    except (ValueError, KeyError) as e:
        log.error(f"{etiqueta}: invalid coordinates, skipping image download: {e}")
        resultado["imagenes_error"] = len(pendientes)
        return resultado

    bbox = calcular_bbox(lat, lon, KM_BUFFER)

    for anio, mes in pendientes:
        estado_descarga = descargar_mes(connection, bpin, bbox, anio, mes, descarga_log,
                                        tramo_slug=tramo_slug)

        if estado_descarga not in ("ok", "ya_existe"):
            resultado["imagenes_error"] += 1
            continue

        # Misma carpeta_sentinel() que usa descargar_mes() para la ruta local --
        # si se reconstruyera este string a mano por separado, un cambio futuro
        # en el esquema de nombres podria desincronizar los dos y subir_a_azure
        # fallaria con "Local file not found" para todos los tramos con slug.
        ruta_local = os.path.join(CARPETA_SALIDA, carpeta_sentinel(bpin, tramo_slug), f"{anio}_{mes}.tiff")
        if subir_a_azure(container_client, ruta_local, bpin, anio, mes, tramo_slug=tramo_slug):
            resultado["imagenes_ok"] += 1
        else:
            resultado["imagenes_error"] += 1

        time.sleep(PAUSA_ENTRE_DESCARGAS)

    log.info(f"{etiqueta}: {resultado['imagenes_ok']} uploaded, {resultado['imagenes_error']} failed")
    return resultado


# ── Main (single run) ────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Download missing Sentinel-2 images and upload them to Azure Blob Storage."
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run without asking for manual confirmation before processing.",
    )
    parser.add_argument(
        "--bpin",
        nargs="+",
        default=None,
        help="Limit the run to these specific BPIN(s) instead of every project in the sheet.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    validar_configuracion()

    log.info("Reading project metadata...")
    df = leer_metadata_proyectos()
    log.info(f"{len(df)} total rows in metadata source.")

    if args.bpin:
        objetivo = set(args.bpin)
        df = df[df["bpin"].astype(str).str.strip().isin(objetivo)]
        log.info(f"Filtered to {len(df)} row(s) for BPIN: {sorted(objetivo)}")
        if df.empty:
            print("No rows match the given --bpin. Nothing to do.")
            return

    df = calcular_tramos(df)
    multi_tramo_bpins = sorted(df.loc[df["tramo_slug"].notna(), "bpin"].astype(str).str.strip().unique())
    if multi_tramo_bpins:
        print(f"\n{len(multi_tramo_bpins)} BPIN(s) have multiple tramo rows, processed independently:")
        for bpin_multi in multi_tramo_bpins:
            print(f"  - {bpin_multi}")

    blob_service     = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
    container_client = blob_service.get_container_client(AZURE_CONTAINER)

    if multi_tramo_bpins:
        print("\nSyncing tramo manifests (safe auto-rename if georreferenciacion text changed)...")
        for bpin_multi in multi_tramo_bpins:
            filas_bpin = df[df["bpin"].astype(str).str.strip() == bpin_multi].to_dict("records")
            sincronizar_tramos_bpin(container_client, bpin_multi, filas_bpin)

    print("\nChecking Azure Blob Storage for existing images per project...")
    pendientes_por_proyecto = calcular_pendientes(df, container_client)

    if not pendientes_por_proyecto:
        print("\nAll projects in the sheet already have their images in Azure. Nothing to do.")
        return

    print(f"\n{len(pendientes_por_proyecto)} project(s) with missing images:")
    for item in pendientes_por_proyecto:
        row  = item["row"]
        bpin = row["bpin"]
        nombre = str(row.get("nombre_del_proyecto", ""))[:55]
        meses_str = ", ".join(f"{a}-{m}" for a, m in item["pendientes"])
        print(f"  - {bpin}: {nombre}")
        print(f"      pending: {meses_str}")

    if not args.auto:
        respuesta = input("\nProceed with download and upload for these? [y/N]: ").strip().lower()
        if respuesta != "y":
            print("Cancelled. No changes made.")
            return
    else:
        log.info("Automatic mode active (--auto): skipping manual confirmation.")

    log.info("Authenticating with Copernicus...")
    # default_timeout evita que una peticion colgada tarde 30-40+ minutos en
    # fallar en vez de unos pocos minutos, dejando que el reintento con backoff
    # de descargar_mes() se active mas rapido.
    connection = openeo.connect("openeo.dataspace.copernicus.eu", default_timeout=120)
    connection.authenticate_oidc(max_poll_time=120)
    log.info("Copernicus authentication successful.")

    descarga_log = []
    resultados   = []
    for item in pendientes_por_proyecto:
        resultado = procesar_proyecto(
            connection, container_client, item["row"], item["pendientes"], descarga_log,
            tramo_slug=item.get("tramo_slug"),
        )
        resultados.append(resultado)

    # audit log only, not used to decide what runs next time. Keyed by
    # bpin::tramo_slug (not just bpin) so tramos of the same project don't
    # overwrite each other's entry.
    STATE_PATH.write_text(
        json.dumps(
            {f"{r['bpin']}::{r.get('tramo_slug') or 'default'}": r for r in resultados},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    with open(os.path.join(CARPETA_SALIDA, "log_descarga.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(descarga_log))

    print("\n--- Summary ---")
    total_ok    = sum(r["imagenes_ok"] for r in resultados)
    total_error = sum(r["imagenes_error"] for r in resultados)
    print(f"  Projects processed : {len(resultados)}")
    print(f"  Images uploaded     : {total_ok}")
    print(f"  Images failed       : {total_error}")
    print(f"  Log file            : {LOG_PATH}")


if __name__ == "__main__":
    main()
