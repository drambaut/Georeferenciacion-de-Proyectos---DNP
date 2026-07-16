"""
pipeline.py
Reads the shared Google Sheet, checks Azure Blob Storage to see which
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
import gspread
import requests
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.Download_sat_imgs import (
    dms_a_decimal,
    calcular_bbox,
    descargar_mes,
    CARPETA_SALIDA,
    DESCARGA,
    KM_BUFFER,
    PAUSA_ENTRE_DESCARGAS,
)

load_dotenv()


# ── Configuration ─────────────────────────────────────────────────

GOOGLE_SHEET_ID              = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB             = os.getenv("GOOGLE_SHEET_TAB", "Hoja 1")
GOOGLE_SERVICE_ACCOUNT_JSON  = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
PROJECT_METADATA_XLSX_URL    = os.getenv("PROJECT_METADATA_XLSX_URL")
PROJECT_METADATA_SHEET_NAME  = os.getenv(
    "PROJECT_METADATA_SHEET_NAME",
    os.getenv("GOOGLE_SHEET_TAB", "proyectos_satview"),
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
        if not GOOGLE_SHEET_ID:
            faltantes.append("GOOGLE_SHEET_ID")
        if not GOOGLE_SERVICE_ACCOUNT_JSON:
            faltantes.append("GOOGLE_SERVICE_ACCOUNT_JSON")
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

    if not PROJECT_METADATA_XLSX_URL:
        try:
            json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError as e:
            print(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}")
            sys.exit(1)


# ── Google Sheets reading ───────────────────────────────────────────

def _metadata_download_url(url: str) -> str:
    """
    Normalizes SharePoint/OneDrive viewer links to direct-download links.
    Other URLs are left untouched.
    """
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?", 1)[0] + "?download=1"
    return url


def leer_google_sheet() -> pd.DataFrame:
    if PROJECT_METADATA_XLSX_URL:
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

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    info   = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds  = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_SHEET_TAB)
    data  = sheet.get_all_records()

    df = pd.DataFrame(data)
    df.columns = [c.strip() for c in df.columns]
    df = df.astype(str)
    return df


# ── Azure ground-truth check ────────────────────────────────────────

def meses_objetivo() -> set:
    return {(anio, mes) for anio, meses in DESCARGA.items() for mes in meses}


def meses_ya_en_azure(container_client, bpin: str) -> set:
    prefix = f"sentinel2_{bpin}/"
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
    Returns a list of dicts: {"row": pd.Series, "pendientes": [(anio, mes), ...]}
    Only includes projects that are missing at least one target month in Azure.
    """
    objetivo   = meses_objetivo()
    resultado  = []

    for _, row in df.iterrows():
        bpin = str(row["bpin"]).strip()
        if not bpin:
            continue
        ya_en_azure = meses_ya_en_azure(container_client, bpin)
        pendientes  = sorted(objetivo - ya_en_azure)
        if pendientes:
            resultado.append({"row": row, "pendientes": pendientes})

    return resultado


# ── Azure upload ───────────────────────────────────────────────────

def subir_a_azure(container_client, local_path: str, bpin: str, anio: str, mes: str) -> bool:
    if not os.path.exists(local_path):
        log.error(f"Local file not found, cannot upload: {local_path}")
        return False

    blob_path = f"sentinel2_{bpin}/{anio}_{mes}.tiff"
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


# ── Per-project processing ──────────────────────────────────────────

def procesar_proyecto(connection, container_client, row: pd.Series,
                      pendientes: list, descarga_log: list) -> dict:
    bpin = str(row["bpin"]).strip()
    log.info(f"Processing project: {bpin} ({len(pendientes)} month(s) pending)")

    resultado = {"bpin": bpin, "fecha_proceso": datetime.now(timezone.utc).isoformat(),
                "imagenes_ok": 0, "imagenes_error": 0}

    try:
        lat = dms_a_decimal(str(row["latitud"]).strip())
        lon = dms_a_decimal(str(row["longitud"]).strip())
    except (ValueError, KeyError) as e:
        log.error(f"{bpin}: invalid coordinates, skipping image download: {e}")
        resultado["imagenes_error"] = len(pendientes)
        return resultado

    bbox = calcular_bbox(lat, lon, KM_BUFFER)

    for anio, mes in pendientes:
        estado_descarga = descargar_mes(connection, bpin, bbox, anio, mes, descarga_log)

        if estado_descarga not in ("ok", "ya_existe"):
            resultado["imagenes_error"] += 1
            continue

        ruta_local = os.path.join(CARPETA_SALIDA, f"sentinel2_{bpin}", f"{anio}_{mes}.tiff")
        if subir_a_azure(container_client, ruta_local, bpin, anio, mes):
            resultado["imagenes_ok"] += 1
        else:
            resultado["imagenes_error"] += 1

        time.sleep(PAUSA_ENTRE_DESCARGAS)

    log.info(f"{bpin}: {resultado['imagenes_ok']} uploaded, {resultado['imagenes_error']} failed")
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
    return parser.parse_args()


def main():
    args = parse_args()
    validar_configuracion()

    log.info("Reading project metadata...")
    df = leer_google_sheet()
    log.info(f"{len(df)} total rows in metadata source.")

    duplicados = df[df.duplicated(subset=["bpin"], keep=False)]["bpin"].unique()
    if len(duplicados) > 0:
        print(f"\nWarning: {len(duplicados)} BPIN(s) appear more than once in the sheet:")
        for bpin_dup in duplicados:
            print(f"  - {bpin_dup}")
        print("Only the first occurrence of each will be processed. Consider")
        print("cleaning up duplicate rows in the metadata source.")
        df = df.drop_duplicates(subset=["bpin"], keep="first")

    blob_service     = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
    container_client = blob_service.get_container_client(AZURE_CONTAINER)

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
    connection = openeo.connect("openeo.dataspace.copernicus.eu")
    connection.authenticate_oidc(max_poll_time=120)
    log.info("Copernicus authentication successful.")

    descarga_log = []
    resultados   = []
    for item in pendientes_por_proyecto:
        resultado = procesar_proyecto(
            connection, container_client, item["row"], item["pendientes"], descarga_log,
        )
        resultados.append(resultado)

    # audit log only, not used to decide what runs next time
    STATE_PATH.write_text(
        json.dumps({r["bpin"]: r for r in resultados}, indent=2, ensure_ascii=False),
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
