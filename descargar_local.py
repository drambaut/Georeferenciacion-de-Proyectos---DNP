"""
descargar_local.py
Descarga imagenes Sentinel-2 directo a disco local (carpeta Imagenes/), sin
subir nada a Azure ni necesitar sus credenciales. Reutiliza la misma logica
de tramos y nombres de carpeta que pipeline.py y app.py (definida en
utils/Download_sat_imgs.py), asi que las imagenes quedan organizadas
exactamente como las espera app.py en modo IMAGE_STORAGE_MODE=local.

Requiere en el .env: PROJECT_METADATA_XLSX_URL, PROJECT_METADATA_SHEET_NAME,
OPENEO_AUTH_METHOD, OPENEO_AUTH_CLIENT_ID, OPENEO_AUTH_CLIENT_SECRET,
OPENEO_AUTH_PROVIDER_ID. NO requiere ninguna variable de Azure.

Uso:
    python descargar_local.py                        # una pasada, solo BPINS_OBJETIVO_DEFAULT
    python descargar_local.py --bpin 123 456          # BPIN especificos
    python descargar_local.py --todos                 # todos los proyectos de la hoja

    # Modo reintento automatico: si Copernicus esta respondiendo mal (timeouts/
    # errores), vuelve a intentar SOLO lo que sigue faltando cada N minutos,
    # hasta completar todo o hasta agotar --max-horas. Cada pasada ya se salta
    # lo que se descargo con exito en pasadas anteriores (descargar_mes() no
    # vuelve a pedir un archivo que ya existe en disco), asi que es seguro
    # interrumpirlo (Ctrl+C) y volver a correrlo despues sin perder nada.
    python descargar_local.py --reintentar-cada-min 5 --max-horas 6
"""
import sys
import os
import time
import argparse
from pathlib import Path

import openeo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.Download_sat_imgs import (
    calcular_tramos, dms_a_decimal, calcular_bbox, descargar_mes,
    carpeta_sentinel, DESCARGA, KM_BUFFER, PAUSA_ENTRE_DESCARGAS, CARPETA_SALIDA,
)
import pipeline  # reutiliza leer_metadata_proyectos() y la config de .env ya cargada ahi

BPINS_OBJETIVO_DEFAULT = ["2022002200043", "2024002440027", "2025002470027"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--todos", action="store_true",
                   help="Descargar todos los proyectos de la hoja, no solo BPINS_OBJETIVO_DEFAULT.")
    p.add_argument("--bpin", nargs="+",
                   help="Lista de BPIN especificos a descargar (en vez de BPINS_OBJETIVO_DEFAULT).")
    p.add_argument("--reintentar-cada-min", type=float, default=None,
                   help="Si se especifica, tras cada pasada reintenta SOLO lo que sigue "
                        "faltando cada N minutos, hasta completar todo o llegar a --max-horas.")
    p.add_argument("--max-horas", type=float, default=6.0,
                   help="Limite de tiempo total (horas) para el modo --reintentar-cada-min. Default: 6.")
    return p.parse_args()


def _filas_con_tramo(df):
    """Itera filas del df normalizando tramo_slug (None si no es un str real
    -- ver nota sobre pandas/iterrows en carpeta_sentinel())."""
    for _, row in df.iterrows():
        tramo_slug = row.get("tramo_slug")
        if not isinstance(tramo_slug, str):
            tramo_slug = None
        yield row, tramo_slug


def _contar_pendientes(df) -> int:
    """Cuenta cuantas combinaciones (fila, mes) todavia NO tienen su .tiff en
    disco -- chequeo puramente local, no llama a Copernicus."""
    pendientes = 0
    for row, tramo_slug in _filas_con_tramo(df):
        bpin    = str(row["bpin"]).strip()
        carpeta = os.path.join(CARPETA_SALIDA, carpeta_sentinel(bpin, tramo_slug))
        for anio, meses in DESCARGA.items():
            for mes in meses:
                if not os.path.exists(os.path.join(carpeta, f"{anio}_{mes}.tiff")):
                    pendientes += 1
    return pendientes


def _correr_una_pasada(df, connection) -> dict:
    log_descarga = []
    contadores = {"ok": 0, "ya_existe": 0, "sin_datos": 0, "error": 0}

    for row, tramo_slug in _filas_con_tramo(df):
        bpin = str(row["bpin"]).strip()
        etiqueta = f"{bpin} ({tramo_slug})" if tramo_slug else bpin

        try:
            lat = dms_a_decimal(str(row["latitud"]).strip())
            lon = dms_a_decimal(str(row["longitud"]).strip())
        except (ValueError, KeyError) as e:
            print(f"[SKIP] {etiqueta}: coordenadas invalidas: {e}")
            continue

        bbox = calcular_bbox(lat, lon, KM_BUFFER)
        print(f"\n=== {etiqueta} -> {carpeta_sentinel(bpin, tramo_slug)}/ ===")

        for anio, meses in DESCARGA.items():
            for mes in meses:
                resultado = descargar_mes(connection, bpin, bbox, anio, mes, log_descarga, tramo_slug=tramo_slug)
                contadores[resultado] += 1
                if resultado != "ya_existe":
                    time.sleep(PAUSA_ENTRE_DESCARGAS)

    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    with open(os.path.join(CARPETA_SALIDA, "log_descarga_local.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(log_descarga) + "\n")

    return contadores


def main():
    args = parse_args()

    faltantes = [v for v in ("PROJECT_METADATA_XLSX_URL", "OPENEO_AUTH_CLIENT_ID", "OPENEO_AUTH_CLIENT_SECRET")
                 if not os.getenv(v)]
    if faltantes:
        print("Faltan variables de entorno:", ", ".join(faltantes))
        print("Revisa tu archivo .env (este script NO necesita ninguna variable de Azure).")
        sys.exit(1)

    print("Leyendo metadatos de proyectos...")
    df = pipeline.leer_metadata_proyectos()
    print(f"{len(df)} fila(s) totales en la hoja.")

    if args.bpin:
        objetivo = set(args.bpin)
    elif args.todos:
        objetivo = None
    else:
        objetivo = set(BPINS_OBJETIVO_DEFAULT)

    if objetivo is not None:
        df = df[df["bpin"].astype(str).str.strip().isin(objetivo)]
        print(f"Filtrado a {len(df)} fila(s) para BPIN: {sorted(objetivo)}")

    if df.empty:
        print("No hay filas que coincidan con el filtro. Nada que descargar.")
        return

    df = calcular_tramos(df)

    print("\nProyectos/tramos a procesar:")
    for row, tramo_slug in _filas_con_tramo(df):
        carpeta = carpeta_sentinel(str(row["bpin"]).strip(), tramo_slug)
        print(f"  - {row['bpin']} | {row.get('georreferenciacion', '')} -> {carpeta}/")

    total_objetivo = len(df) * sum(len(m) for m in DESCARGA.values())
    print(f"\nTotal de imagenes objetivo: {total_objetivo}")

    print("\nAutenticando con Copernicus...")
    # default_timeout cubre las llamadas ligeras (auth, metadata). La descarga
    # pesada (DataCube.download) se protege aparte con un timeout real por
    # hilo -- ver TIMEOUT_DESCARGA_SEGUNDOS y _download_con_limite() en
    # utils/Download_sat_imgs.py (openeo ignora default_timeout ahi).
    connection = openeo.connect("openeo.dataspace.copernicus.eu", default_timeout=120)
    connection.authenticate_oidc(max_poll_time=120)
    print("Autenticacion exitosa.\n")

    if args.reintentar_cada_min:
        deadline = time.time() + args.max_horas * 3600
        ronda = 1
        while True:
            pendientes_antes = _contar_pendientes(df)
            if pendientes_antes == 0:
                print("\nTodo lo objetivo ya esta descargado. Fin.")
                break

            print(f"\n########## RONDA {ronda} -- {pendientes_antes} imagen(es) pendiente(s) ##########")
            _correr_una_pasada(df, connection)

            pendientes_despues = _contar_pendientes(df)
            if pendientes_despues == 0:
                print("\nTodo lo objetivo quedo descargado. Fin.")
                break

            if time.time() >= deadline:
                print(f"\nSe alcanzo el limite de --max-horas ({args.max_horas}h). "
                      f"Quedan {pendientes_despues} imagen(es) sin descargar. "
                      f"Podes volver a correr el script mas tarde -- lo ya descargado no se repite.")
                break

            espera_min = args.reintentar_cada_min
            print(f"\nQuedan {pendientes_despues} pendiente(s). Esperando {espera_min} min antes de la siguiente ronda "
                  f"(limite total: {args.max_horas}h)...")
            time.sleep(espera_min * 60)
            ronda += 1

        contadores_finales = {"pendientes": _contar_pendientes(df), "objetivo": total_objetivo}
        print("\n--- Resumen final ---")
        print(f"  Objetivo total    : {contadores_finales['objetivo']}")
        print(f"  Descargadas       : {contadores_finales['objetivo'] - contadores_finales['pendientes']}")
        print(f"  Pendientes        : {contadores_finales['pendientes']}")
    else:
        contadores = _correr_una_pasada(df, connection)
        print("\n--- Resumen ---")
        print(f"  [OK]        : {contadores['ok']}")
        print(f"  [YA EXISTE] : {contadores['ya_existe']}")
        print(f"  [SIN DATOS] : {contadores['sin_datos']}")
        print(f"  [ERROR]     : {contadores['error']}")

    print(f"\n  Imagenes guardadas en: {os.path.abspath(CARPETA_SALIDA)}\\")


if __name__ == "__main__":
    main()
