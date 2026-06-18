"""
Download_sat_imgs.py
Sentinel-2 L2A image downloader for SGR infrastructure projects.

Usage:
    python Download_sat_imgs.py
"""

import openeo
import os
import re
import time
import calendar
import numpy as np
import pandas as pd


# ── Configuration ─────────────────────────────────────────────────

CSV_PATH              = r"data\raw\proyectos_georeferenciados.csv"

DESCARGA = {
    "2025": ["01", "04", "08", "12"],
    "2026": ["02", "05"],
}

MAX_NUBOSIDAD         = 50
KM_BUFFER             = 5
CARPETA_SALIDA        = "Imagenes"
PAUSA_ENTRE_DESCARGAS = 5
MAX_REINTENTOS        = 4
PROYECTOS_LIMITE      = None

# ─────────────────────────────────────────────────────────────────


def dms_a_decimal(dms_str: str) -> float:
    patron = r"(\d+)[°º](\d+)['\´]([\d.]+)[\"']?\s*([NSEWOnsewо])"
    match  = re.search(patron, dms_str.strip())
    if not match:
        raise ValueError(f"Unrecognized DMS format: '{dms_str}'")
    decimal = (float(match.group(1))
               + float(match.group(2)) / 60
               + float(match.group(3)) / 3600)
    if match.group(4).upper() in ("S", "W", "O"):
        decimal *= -1
    return round(decimal, 6)


def calcular_bbox(lat: float, lon: float, km: float) -> dict:
    lat_buf = km / 111
    lon_buf = km / (111 * np.cos(np.radians(lat)))
    return {
        "west":  round(lon - lon_buf, 6),
        "south": round(lat - lat_buf, 6),
        "east":  round(lon + lon_buf, 6),
        "north": round(lat + lat_buf, 6),
    }


def descargar_mes(connection, bpin: str, bbox: dict,
                  anio: str, mes: str, log: list) -> str:
    carpeta   = os.path.join(CARPETA_SALIDA, f"sentinel2_{bpin}")
    ruta_tiff = os.path.join(carpeta, f"{anio}_{mes}.tiff")

    if os.path.exists(ruta_tiff):
        log.append(f"SKIPPED | {bpin} | {anio}-{mes} | {ruta_tiff}")
        return "ya_existe"

    os.makedirs(carpeta, exist_ok=True)

    ultimo_dia      = calendar.monthrange(int(anio), int(mes))[1]
    temporal_extent = [f"{anio}-{mes}-01", f"{anio}-{mes}-{ultimo_dia}"]

    intento = 0
    while intento <= MAX_REINTENTOS:
        if intento > 0:
            time.sleep(2 ** intento)

        print(f"  {anio}-{mes}  attempt {intento + 1}/{MAX_REINTENTOS + 1}", end=" ", flush=True)

        try:
            cubo = connection.load_collection(
                "SENTINEL2_L2A",
                spatial_extent=bbox,
                temporal_extent=temporal_extent,
                bands=["B02", "B03", "B04", "B08", "SCL"],
                max_cloud_cover=MAX_NUBOSIDAD,
            )
            cubo        = cubo.process("mask_scl_dilation", data=cubo, scl_band_name="SCL")
            composicion = cubo.reduce_dimension(dimension="t", reducer="median")
            composicion = composicion.apply(lambda x: x * 0.0001)
            composicion.download(ruta_tiff, format="GTiff")

            print("-> [OK]")
            log.append(f"OK | {bpin} | {anio}-{mes} | {ruta_tiff}")
            return "ok"

        except Exception as e:
            msg = str(e)

            if "429" in msg:
                retry_after = 10
                ra_match    = re.search(r"Retry-After.*?(\d+)", msg)
                if ra_match:
                    retry_after = max(int(ra_match.group(1)), 5)
                print(f"-> [RATE LIMITED] waiting {retry_after}s")
                time.sleep(retry_after)
                intento += 1
                continue

            if "NoDataAvailable" in msg or "no data" in msg.lower():
                print("-> [NO DATA]")
                log.append(f"NO_DATA | {bpin} | {anio}-{mes} | {msg[:120]}")
                return "sin_datos"

            print(f"-> [ERROR] {msg[:100]}")
            log.append(f"ERROR | {bpin} | {anio}-{mes} | {msg[:120]}")
            return "error"

    print("-> [MAX RETRIES REACHED]")
    log.append(f"MAX_RETRIES | {bpin} | {anio}-{mes}")
    return "error"


def main():
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype=str)
    df.columns = [c.strip() for c in df.columns]

    if PROYECTOS_LIMITE:
        df = df.head(PROYECTOS_LIMITE)

    print(f"Projects loaded : {len(df)}")
    print(f"Periods         : { {y: m for y, m in DESCARGA.items()} }")
    print(f"Total TIFFs     : {len(df) * sum(len(m) for m in DESCARGA.values())}")
    print(f"Output folder   : {CARPETA_SALIDA}\n")

    connection = openeo.connect("openeo.dataspace.copernicus.eu")
    connection.authenticate_oidc(max_poll_time=120)

    log        = []
    contadores = {"ok": 0, "ya_existe": 0, "sin_datos": 0, "error": 0}

    for idx, row in df.iterrows():
        bpin   = str(row["BPIN"]).strip()
        nombre = str(row.get("NOMBRE DEL PROYECTO", "")).strip()[:60]

        print(f"\n[{idx + 1}/{len(df)}] {bpin}")
        print(f"  {nombre}")

        try:
            lat = dms_a_decimal(str(row["LATITUD_GMS"]).strip())
            lon = dms_a_decimal(str(row["LONGITUD_GMS"]).strip())
        except ValueError as e:
            print(f"  [SKIP] Invalid coordinate: {e}")
            log.append(f"INVALID_COORD | {bpin} | {e}")
            continue

        bbox = calcular_bbox(lat, lon, KM_BUFFER)

        for anio, meses in DESCARGA.items():
            for mes in meses:
                resultado = descargar_mes(connection, bpin, bbox, anio, mes, log)
                contadores[resultado] += 1
                if resultado != "ya_existe":
                    time.sleep(PAUSA_ENTRE_DESCARGAS)

    print("\n--- Summary ---")
    print(f"  [OK]      : {contadores['ok']}")
    print(f"  [SKIPPED] : {contadores['ya_existe']}")
    print(f"  [NO DATA] : {contadores['sin_datos']}")
    print(f"  [ERROR]   : {contadores['error']}")

    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    log_path = os.path.join(CARPETA_SALIDA, "log_descarga.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print(f"\n  Log: {log_path}")


if __name__ == "__main__":
    main()