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
import uuid
import calendar
import unicodedata
import numpy as np
import pandas as pd


# ── Configuration ─────────────────────────────────────────────────

CSV_PATH              = r"data\raw\proyectos_georeferenciados.csv"

DESCARGA = {
    "2025": ["01", "04", "08", "12"],
    "2026": ["02", "05", "08"],
}

MAX_NUBOSIDAD           = 50
KM_BUFFER               = 5
CARPETA_SALIDA          = "Imagenes"
PAUSA_ENTRE_DESCARGAS   = 5
MAX_REINTENTOS          = 4
PROYECTOS_LIMITE        = None

# Copernicus tarda varios minutos en generar la composicion pesada (mediana
# de 4 bandas + mascara de nubes sobre un mes completo). Una descarga SINCRONA
# (composicion.download(...) manteniendo una sola conexion HTTP abierta todo
# ese tiempo) es fragil: proxies/balanceadores de carga suelen cortar
# conexiones largas sin avisar, sin importar el timeout que le pongas del lado
# del cliente. La forma robusta es un BATCH JOB asincrono: se le pide el
# trabajo al servidor, se pregunta el estado cada cierto tiempo con llamadas
# cortas, y solo se descarga el archivo cuando el job ya esta listo. Ver
# descargar_mes() / _ejecutar_batch_job().
JOB_POLL_INTERVAL_SEGUNDOS = 30    # cada cuanto se pregunta el estado del job
JOB_TIMEOUT_SEGUNDOS       = 3600  # limite total de espera por job (1 hora)

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


def slugify_tramo(texto) -> str:
    """Convierte el texto de 'georreferenciacion' en un identificador legible
    y seguro para nombres de carpeta (minusculas, sin tildes, espacios -> guiones).
    Trata valores vacios/NaN/"nan" (string, por como pandas.astype(str) los deja
    en pipeline.py) como texto vacio."""
    if texto is None:
        return ""
    texto = str(texto).strip()
    if not texto or texto.lower() == "nan":
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return texto[:80]


def carpeta_sentinel(bpin: str, tramo_slug) -> str:
    """Nombre de carpeta/prefix en Azure y localmente. Sin tramo_slug, mantiene
    el esquema historico 'sentinel2_{bpin}' (proyectos de un solo punto,
    retrocompatible sin necesidad de migracion).

    Chequea isinstance(str) en vez de solo "if tramo_slug" a proposito: al leer
    una fila de un DataFrame de pandas con df.iterrows() (en vez de
    to_dict("records")), un valor None en una columna puede llegar convertido a
    NaN (float) si las demas columnas de esa fila son texto -- y NaN es
    verdadero en Python (bool(float('nan')) es True), a diferencia de None.
    Sin este chequeo, un proyecto de un solo punto terminaria con una carpeta
    'sentinel2_{bpin}_nan' en vez de 'sentinel2_{bpin}'."""
    if isinstance(tramo_slug, str) and tramo_slug.strip():
        return f"sentinel2_{bpin}_{tramo_slug}"
    return f"sentinel2_{bpin}"


def calcular_tramos(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega la columna 'tramo_slug' (str o None) a una copia de df.
    Regla: un BPIN con una sola fila no tiene tramo (None, retrocompatible).
    Un BPIN con varias filas obtiene un slug por fila, derivado del texto de
    'georreferenciacion', con desambiguacion de colisiones (-2, -3...) y
    fallback 'tramo-{n}' si el texto viene vacio."""
    df = df.copy()
    df["tramo_slug"] = None
    bpins = df["bpin"].astype(str).str.strip()
    conteos = bpins.value_counts()

    for bpin, n in conteos.items():
        if n <= 1:
            continue
        indices = df.index[bpins == bpin]
        usados = {}
        for posicion, idx in enumerate(indices, start=1):
            texto = df.at[idx, "georreferenciacion"] if "georreferenciacion" in df.columns else None
            base = slugify_tramo(texto) or f"tramo-{posicion}"
            slug = base
            if slug in usados:
                usados[slug] += 1
                slug = f"{base}-{usados[slug]}"
            else:
                usados[slug] = 1
            df.at[idx, "tramo_slug"] = slug

    return df


def _ejecutar_batch_job(composicion, ruta_final: str, titulo: str) -> None:
    """Envia la composicion como batch job, espera a que termine sondeando
    el estado cada JOB_POLL_INTERVAL_SEGUNDOS (llamadas cortas, no una sola
    conexion larga) y descarga el resultado solo cuando ya esta listo."""
    # executor-memory/executor-memoryOverhead mas altos: algunos errores de
    # "reproject failed" / "read GridBounds failed" reportados en el foro de
    # CDSE resultaron ser en realidad falta de memoria del ejecutor, mal
    # etiquetados como fallas de lectura/reproyeccion. No hace daño subirlo.
    job = composicion.create_job(
        title=titulo, out_format="GTiff",
        job_options={"executor-memory": "3g", "executor-memoryOverhead": "2g"},
    )
    job.start()

    def _ultimos_logs(n=5) -> str:
        try:
            return " | ".join(l.get("message", "") for l in job.logs()[-n:])
        except Exception as e:
            return f"(no se pudieron leer logs: {e})"

    inicio = time.time()
    estado_anterior = None
    ultimo_log_impreso = 0
    while True:
        estado = job.status()
        if estado != estado_anterior:
            print(f"[job {estado}]", end=" ", flush=True)
            estado_anterior = estado
            if estado == "queued" and time.time() - inicio > 300:
                # atascado en "queued" mas de 5 min: probablemente saturacion
                # del backend de CDSE o creditos agotados, no un problema
                # de tus coordenadas/bbox.
                print("\n    (lleva >5min en 'queued' -- puede ser saturacion "
                      "del backend de CDSE o creditos agotados, revisa tu "
                      "saldo en dataspace.copernicus.eu)", flush=True)

        if estado == "finished":
            break
        if estado in ("error", "canceled"):
            raise RuntimeError(f"BATCH_JOB_FALLIDO: estado={estado} {_ultimos_logs(3)}".strip())

        # mientras esta "running", imprime logs cada ~5 min para ver que
        # esta haciendo de verdad en vez de solo ver el reloj correr
        if estado == "running" and time.time() - ultimo_log_impreso > 300:
            print(f"\n    [logs job] {_ultimos_logs(5)}", flush=True)
            ultimo_log_impreso = time.time()

        if time.time() - inicio > JOB_TIMEOUT_SEGUNDOS:
            raise TimeoutError(f"TIMEOUT_JOB: sin terminar en {JOB_TIMEOUT_SEGUNDOS}s. "
                                f"Ultimos logs: {_ultimos_logs(5)}")

        time.sleep(JOB_POLL_INTERVAL_SEGUNDOS)

    resultados = job.get_results()
    assets = list(resultados.get_assets())
    if not assets:
        raise RuntimeError("BATCH_JOB_SIN_ASSETS: el job termino pero no genero archivos")

    tmp_path = f"{ruta_final}.part-{uuid.uuid4().hex}"
    assets[0].download(tmp_path)
    os.replace(tmp_path, ruta_final)


def descargar_mes(connection, bpin: str, bbox: dict,
                  anio: str, mes: str, log: list, tramo_slug: str | None = None) -> str:
    carpeta   = os.path.join(CARPETA_SALIDA, carpeta_sentinel(bpin, tramo_slug))
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
            titulo      = f"{carpeta_sentinel(bpin, tramo_slug)}_{anio}_{mes}"
            _ejecutar_batch_job(composicion, ruta_tiff, titulo)

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

            if "TIMEOUT_JOB" in msg or "BATCH_JOB_FALLIDO" in msg or "BATCH_JOB_SIN_ASSETS" in msg:
                print(f"-> [JOB FALLIDO] {msg[:150]}")
                log.append(f"JOB_FALLIDO | {bpin} | {anio}-{mes} | {msg[:150]}")
                intento += 1
                continue

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

    connection = openeo.connect("openeo.dataspace.copernicus.eu", default_timeout=120)
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