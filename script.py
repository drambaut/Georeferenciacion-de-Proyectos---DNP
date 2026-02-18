import openeo
from pyproj import Transformer
import os
import pandas as pd
import re
import numpy as np

def descargar_imagen_por_año_sentinel2(bpin, lat, lon):
    # 1) Conexión y Autenticación mejorada
    connection = openeo.connect("openeo.dataspace.copernicus.eu")
    print("Iniciando autenticación OIDC (revisa tu navegador)...")
    auth_result = connection.authenticate_oidc(max_poll_time=120)
    print(f"Resultado de autenticación: {auth_result}")

    # 2) Crear Buffer de 1000m alrededor del punto
    # Aproximación: 1 grado latitud ≈ 111km, 1 grado longitud ≈ 111km * cos(lat)
    # Para mayor precisión usamos un offset simple en grados (0.009 aprox = 1km)
# 2) Crear Buffer de 5 km alrededor del punto → 10 km x 10 km total
    km_buffer = 5  # 5 km a cada lado
    lat_buffer = km_buffer / 111
    lon_buffer = km_buffer / (111 * np.cos(np.radians(lat)))

    spatial_extent = {
        "west": lon - lon_buffer,
        "south": lat - lat_buffer,
        "east": lon + lon_buffer,
        "north": lat + lat_buffer
    }


    # 3) Meses solicitados de 2025
    meses_objetivo = ["01", "04", "07", "10", "12"]
    
    if not os.path.exists("EO_data_2025"):
        os.makedirs("EO_data_2025")

    for mes in meses_objetivo:
        print(f"--- Procesando Mes: {mes}/2025 ---")
        
        temporal_extent = [f"2025-{mes}-01", f"2025-{mes}-28"] # Rango mensual simplificado

        # 4) Cargar colección con filtro de nubes estricto para buscar la "menos nublada"
        cube = connection.load_collection(
            "SENTINEL2_L2A",
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            bands=["B02", "B03", "B04", "B08", "SCL"],
            max_cloud_cover=50 # Filtramos imágenes con más del 20% de nubes
        )

        # 5) Aplicar máscara de nubes
        cube = cube.process(
            "mask_scl_dilation",
            data=cube,
            scl_band_name="SCL"
        )

        # 6) Reducción temporal con Mediana
        # Esto genera la imagen compuesta del mes sin nubes
        composite = cube.reduce_dimension(dimension="t", reducer="median")

        # 7) Escalar a reflectancia
        composite = composite.apply(lambda x: x * 0.0001)

        # 8) Guardar y descargar Job
        output_path = f"Imagenes/sentinel2_{bpin}/2025_{mes}.tiff"
        
        print(f"Enviando proceso al servidor para el mes {mes}...")
        try:
            # Ejecución síncrona para simplificar la descarga por mes
            composite.download(output_path, format="GTiff")
            print(f"Descargado: {output_path}")
        except Exception as e:
            print(f"Error procesando el mes {mes}: {e}")

# Ejemplo de uso:
# LEER EL EXCEL
ruta_excel = # ruta del excel con los proyectos

df = pd.read_excel(
    ruta_excel,
    sheet_name="Proyectos seleccionados",
    header=[3,4]
)


for i in range(12): # para descargar los 12 proyectos 
    row = df.iloc[i]

    bpin = row[("CARACTERIZACIÓN DEL PROYECTO", "BPIN")]
    lat_dms = row[("CARACTERIZACIÓN DEL PROYECTO", "LATITUD")]
    lon_dms = row[("CARACTERIZACIÓN DEL PROYECTO", "LONGITUD")]

    def dms_to_decimal(dms_str):
        pattern = r"(\d+)°(\d+)'([\d.]+)\"([NSEW])"
        match = re.match(pattern, dms_str.strip())
        degrees = float(match.group(1))
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        direction = match.group(4)
        decimal = degrees + minutes/60 + seconds/3600
        if direction in ["S", "W"]:
            decimal *= -1
        return decimal

    lat = dms_to_decimal(lat_dms)
    lon = dms_to_decimal(lon_dms)

    print(f'lat: {lat}, long: {lon}')
    print(f'----------------- DESCARGANDO IMAGENES DEL BPIN {bpin} --------------------------')
    descargar_imagen_por_año_sentinel2(bpin, lat,lon)
    print('-----------------------------------------------------------------------------------\n')