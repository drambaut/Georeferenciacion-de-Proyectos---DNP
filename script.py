import os
import re
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from pyproj import CRS
from pystac_client import Client
from datetime import datetime
from tqdm import tqdm
from dotenv import load_dotenv

# CONFIGURACIÓN
load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID_COPERNICUS")
CLIENT_SECRET = os.getenv("CLIENT_KEY_COPERNICUS")

START_YEAR = 2022
END_YEAR = 2025   # 4 años → 8 imágenes (S1 y S2)

BUFFER_METERS = 1000

OUTPUT_S1 = "Sentinel1"
OUTPUT_S2 = "Sentinel2"

os.makedirs(OUTPUT_S1, exist_ok=True)
os.makedirs(OUTPUT_S2, exist_ok=True)

# AUTENTICACIÓN OAUTH2
def get_access_token():
    url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }

    response = requests.post(url, data=data)
    response.raise_for_status()
    return response.json()["access_token"]

token = get_access_token()
headers = {"Authorization": f"Bearer {token}"}

# CONVERTIR DMS A DECIMAL
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

# CONECTAR A STAC
catalog = Client.open(
    "https://stac.dataspace.copernicus.eu/v1/",
    headers=headers
)

# FUNCIÓN DE DESCARGA
def descargar_producto(url, filepath):

    token = get_access_token()  # renovar siempre antes de descargar
    headers = {"Authorization": f"Bearer {token}"}

    with requests.get(url, headers=headers, stream=True, allow_redirects=True) as r:
        if r.status_code == 401:
            print("Token expirado, renovando...")
            token = get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(url, headers=headers, stream=True, allow_redirects=True)

        r.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def descargar_por_año(bpin, lat, lon):

    point = Point(lon, lat)
    gdf = gpd.GeoDataFrame(geometry=[point], crs="EPSG:4326")

    # Convertir a UTM para buffer real en metros
    utm_crs = CRS.from_user_input(gdf.estimate_utm_crs())
    gdf_utm = gdf.to_crs(utm_crs)
    gdf_utm["geometry"] = gdf_utm.buffer(BUFFER_METERS)

    area = gdf_utm.to_crs("EPSG:4326").geometry.iloc[0]

    for year in range(START_YEAR, END_YEAR + 1):

        date_range = f"{year}-01-01/{year}-12-31"

        for collection, folder in [
            ("sentinel-2-l2a", OUTPUT_S2),
            ("sentinel-1-grd", OUTPUT_S1)
        ]:

            search = catalog.search(
                collections=[collection],
                intersects=area,
                datetime=date_range
            )

            items = list(search.items())

            if not items:
                print(f"No hay imágenes {collection} para {year}")
                continue

            # Elegimos la primera del año
            item = items[0]

            fecha = item.datetime.strftime("%d-%m-%Y")

            if "Product" in item.assets:
                asset = item.assets["Product"]
                print("Asset accedido")
            else:
                print("No se encontró asset descargable")
                return


            url = asset.href
            print(url)
            filename = f"{bpin}_{fecha}.zip"
            filepath = os.path.join(folder, filename)

            print(f"Descargando {filename}")

            descargar_producto(url, filepath)


# LEER EL EXCEL
ruta_excel = r'd:\andres\Macc\DR\DNP\2026\ProyectoVC\base de datos\Base_de_proyectos__15_12_2025_(version_1).xlsx'

df = pd.read_excel(
    ruta_excel,
    sheet_name="Proyectos seleccionados",
    header=[3,4]
)

# PROBAR SOLO PROYECTO 0
row = df.iloc[0]

bpin = row[("CARACTERIZACIÓN DEL PROYECTO", "BPIN")]
lat_dms = row[("CARACTERIZACIÓN DEL PROYECTO", "LATITUD")]
lon_dms = row[("CARACTERIZACIÓN DEL PROYECTO", "LONGITUD")]

lat = dms_to_decimal(lat_dms)
lon = dms_to_decimal(lon_dms)

descargar_por_año(bpin, lat, lon)

print("Proceso finalizado.")
