"""
SatView MVP - Satellite tracking of public infrastructure projects.
Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from pathlib import Path
from datetime import datetime
import re
import folium
import leafmap.foliumap as leafmap
from dotenv import load_dotenv
import os
import rioxarray
import rasterio

# En Windows, otras instalaciones (PostgreSQL/PostGIS, QGIS, conda envs
# previos) suelen registrar su propia variable de entorno PROJ_LIB/PROJ_DATA
# a nivel de usuario/sistema, apuntando a un proj.db con un esquema
# incompatible con el que trae empaquetado rasterio. Cuando eso pasa,
# cualquier operacion de reproyeccion (transform_bounds mas abajo) falla con
# "CRSError: The EPSG code is unknown", aunque el codigo este bien -- es un
# conflicto de entorno, no un bug. Forzamos aqui el proj.db que trae rasterio
# consigo mismo para que la app funcione sin importar que mas este instalado
# en la maquina.
_rasterio_proj_data = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
if os.path.isdir(_rasterio_proj_data):
    os.environ["PROJ_LIB"] = _rasterio_proj_data
    os.environ["PROJ_DATA"] = _rasterio_proj_data

from rasterio.warp import transform_bounds
import tempfile
import requests
from PIL import Image
from folium.plugins import SideBySideLayers
from azure.storage.blob import BlobServiceClient

from utils.Download_sat_imgs import carpeta_sentinel, calcular_tramos

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────

st.set_page_config(
    page_title="SatView MVP",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

PROJECT_METADATA_XLSX_URL = os.getenv("PROJECT_METADATA_XLSX_URL")
PROJECT_METADATA_SHEET_NAME = os.getenv(
    "PROJECT_METADATA_SHEET_NAME",
    "proyectos_satview",
)

AZURE_CONN_STR  = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "imagenes-sentinel")

# "azure" (default, produccion) o "local": lee las imagenes de una carpeta en
# disco en vez de Azure Blob Storage. Util para probar sin credenciales/acceso
# de Azure -- ver descargar_local.py para descargar a esa misma carpeta.
IMAGE_STORAGE_MODE = os.getenv("IMAGE_STORAGE_MODE", "azure").strip().lower()
LOCAL_IMAGES_DIR    = os.getenv("LOCAL_IMAGES_DIR", "Imagenes")

MESES_ES = {
    "01": "Enero",   "02": "Febrero",    "03": "Marzo",      "04": "Abril",
    "05": "Mayo",    "06": "Junio",      "07": "Julio",      "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre",  "12": "Diciembre",
}

# ── Styles ────────────────────────────────────────────────────────

st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    section[data-testid="stSidebar"] {
        background-color: #161b26;
        border-right: 1px solid #2a3347;
    }
    .section-title {
        font-size: 10px; font-weight: 700; color: #64748b;
        letter-spacing: .1em; text-transform: uppercase;
        margin-bottom: 8px; margin-top: 4px;
    }
    .info-card {
        background: #1e2535; border: 1px solid #2a3347;
        border-radius: 10px; padding: 14px; margin-bottom: 10px;
    }
    .info-label { font-size: 10px; color: #64748b; font-weight: 600; text-transform: uppercase; }
    .info-value { font-size: 13px; color: #e2e8f0; font-weight: 500; margin-bottom: 8px; }
    .info-mono  { font-family: monospace; font-size: 12px; }
    .badge {
        display: inline-block; padding: 3px 10px; border-radius: 5px;
        font-size: 11px; font-weight: 700;
    }
    .badge-blue  { background: #1e3a5f; color: #3b82f6; }
    .badge-green { background: #052e16; color: #10b981; }
    .prog-wrap  { margin: 4px 0 8px; }
    .prog-label { font-size: 11px; color: #64748b; }
    .prog-bar {
        height: 5px; background: #2a3347;
        border-radius: 99px; overflow: hidden; margin-top: 3px;
    }
    .prog-fill-green { height: 100%; background: #10b981; border-radius: 99px; }
    .prog-fill-blue  { height: 100%; background: #3b82f6; border-radius: 99px; }
    .warn-box {
        background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.3);
        border-radius: 8px; padding: 12px 16px; color: #f59e0b;
        font-size: 13px; text-align: center; margin: 20px 0;
    }
    .gallery-label {
        text-align: center; font-size: 13px; font-weight: 600;
        color: #e2e8f0; margin: 6px 0 2px; padding: 6px;
        background: #161b26; border: 1px solid #2a3347;
        border-radius: 6px;
    }
    .gallery-label small {
        font-weight: 400; color: #64748b; font-family: monospace;
    }
    .basemap-note {
        font-size: 11px; color: #64748b; text-align: center;
        margin: 4px 0 14px; font-style: italic;
    }
</style>
""", unsafe_allow_html=True)


# ── Google Sheets (project metadata) ────────────────────────────────

def _metadata_download_url(url: str) -> str:
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?", 1)[0] + "?download=1"
    return url


@st.cache_data(ttl=300, show_spinner=False)
def cargar_hoja_proyectos() -> pd.DataFrame:
    if not PROJECT_METADATA_XLSX_URL:
        st.error("Missing environment variable: PROJECT_METADATA_XLSX_URL")
        st.stop()

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
    return df


def buscar_tramos(bpin: str) -> list[dict] | None:
    """Devuelve TODAS las filas del Excel que corresponden a este BPIN (una por
    tramo), cada una con su columna 'tramo_slug' ya calculada. None si el BPIN
    no aparece en la hoja."""
    df = cargar_hoja_proyectos()
    if df.empty or "bpin" not in df.columns:
        return None
    match = df[df["bpin"].astype(str).str.strip() == bpin.strip()]
    if match.empty:
        return None
    match = calcular_tramos(match)
    return match.to_dict("records")


# ── Azure Blob Storage (satellite images) ────────────────────────────

@st.cache_resource(show_spinner=False)
def _azure_container_client():
    blob_service = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
    return blob_service.get_container_client(AZURE_CONTAINER)


def parsear_fecha_archivo(filename: str) -> datetime | None:
    parts = Path(filename).stem.split("_")
    if len(parts) != 2:
        return None
    try:
        return datetime(int(parts[0]), int(parts[1]), 1)
    except Exception:
        return None


def _listar_imagenes_azure(bpin: str, tramo_slug: str | None) -> list[dict]:
    container_client = _azure_container_client()
    prefix = f"{carpeta_sentinel(bpin, tramo_slug)}/"
    result = []
    try:
        for blob in container_client.list_blobs(name_starts_with=prefix):
            filename = blob.name.split("/")[-1]
            if not filename.lower().endswith((".tiff", ".tif")):
                continue
            fecha = parsear_fecha_archivo(filename)
            result.append({
                "bucket_path": blob.name,
                "filename":    filename,
                "fecha":       fecha,
                "label":       fecha.strftime("%b %Y") if fecha else Path(filename).stem,
            })
    except Exception as e:
        st.error(f"Error accessing Azure Blob container: {e}")
        return []
    result.sort(key=lambda x: x["fecha"] or datetime.min)
    return result


def _listar_imagenes_local(bpin: str, tramo_slug: str | None) -> list[dict]:
    carpeta = Path(LOCAL_IMAGES_DIR) / carpeta_sentinel(bpin, tramo_slug)
    result = []
    if not carpeta.is_dir():
        return result
    for archivo in sorted(carpeta.iterdir()):
        if not archivo.is_file() or archivo.suffix.lower() not in (".tif", ".tiff"):
            continue
        fecha = parsear_fecha_archivo(archivo.name)
        result.append({
            # "bucket_path" es en realidad una ruta local real en este modo --
            # se mantiene el mismo nombre de campo para que descargar_tiff_temp()
            # y el resto del codigo (galeria/comparacion) no necesiten saber
            # en que modo esta corriendo la app.
            "bucket_path": str(archivo),
            "filename":    archivo.name,
            "fecha":       fecha,
            "label":       fecha.strftime("%b %Y") if fecha else archivo.stem,
        })
    result.sort(key=lambda x: x["fecha"] or datetime.min)
    return result


@st.cache_data(ttl=300, show_spinner=False)
def listar_imagenes(bpin: str, tramo_slug: str | None = None) -> list[dict]:
    if IMAGE_STORAGE_MODE == "local":
        return _listar_imagenes_local(bpin, tramo_slug)
    return _listar_imagenes_azure(bpin, tramo_slug)


@st.cache_data(ttl=300, show_spinner=False)
def descargar_tiff_temp(bucket_path: str) -> str | None:
    if IMAGE_STORAGE_MODE == "local":
        # bucket_path ya es una ruta real en disco (ver _listar_imagenes_local).
        return bucket_path if os.path.exists(bucket_path) else None
    try:
        container_client = _azure_container_client()
        blob_client = container_client.get_blob_client(bucket_path)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tiff")
        with open(tmp.name, "wb") as f:
            f.write(blob_client.download_blob().readall())
        return tmp.name
    except Exception as e:
        st.error(f"Failed to download {bucket_path}: {e}")
        return None


# ── Coordinate and image processing helpers ─────────────────────────

def dms_to_decimal(dms_str) -> float | None:
    if pd.isna(dms_str) or not isinstance(dms_str, str):
        return None
    pattern = r"(\d+)[°º](\d+)['\´]([\d.]+)[\"']?\s*([NSEWOnsewо])"
    match = re.search(pattern, str(dms_str).strip())
    if not match:
        return None
    decimal = (float(match.group(1))
               + float(match.group(2)) / 60
               + float(match.group(3)) / 3600)
    if match.group(4).upper() in ("S", "W", "O"):
        decimal *= -1
    return decimal


def stretch_percentile(band: np.ndarray) -> np.ndarray:
    valid = band[~np.isnan(band)]
    if valid.size == 0:
        return np.zeros_like(band)
    p2, p98 = np.percentile(valid, (2, 98))
    if p98 == p2:
        return np.where(np.isnan(band), 0.0, 0.5)
    stretched = np.clip((band - p2) / (p98 - p2), 0, 1)
    stretched = np.power(stretched, 1 / 1.2)
    stretched = np.where(np.isnan(band), 0.0, stretched)
    return stretched


def generar_tiff_procesado(path_entrada: str, modo: str) -> str:
    data = rioxarray.open_rasterio(path_entrada)

    if modo == "gris":
        banda = data.sel(band=3).values.astype(float)
        canal = stretch_percentile(banda)
        rgb = np.stack([canal, canal, canal])
    elif modo == "falso":
        bandas = data.sel(band=[4, 3, 2]).values.astype(float)
        rgb = np.stack([stretch_percentile(bandas[i]) for i in range(3)])
    else:
        bandas = data.sel(band=[3, 2, 1]).values.astype(float)
        rgb = np.stack([stretch_percentile(bandas[i]) for i in range(3)])

    nan_mask = np.isnan(data.sel(band=3).values.astype(float))
    for i in range(3):
        rgb[i][nan_mask] = 0.0

    rgb_uint8 = (rgb * 255).astype(np.uint8)

    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
    with rasterio.open(
        tmp.name, "w", driver="GTiff",
        height=rgb_uint8.shape[1], width=rgb_uint8.shape[2],
        count=3, dtype=rasterio.uint8,
        crs=data.rio.crs, transform=data.rio.transform(),
    ) as dst:
        dst.write(rgb_uint8)

    return tmp.name


def tiff_has_data(path: str) -> bool:
    try:
        data = rioxarray.open_rasterio(path)
        arr  = data.sel(band=3).values.astype(float)
        return np.any(~np.isnan(arr))
    except Exception:
        return False


def tif_to_png_overlay(tif_path: str) -> tuple[str, list]:
    """
    Converts a processed RGB GeoTIFF into a PNG with alpha (nodata -> transparent)
    plus its bounds in [[south, west], [north, east]] (EPSG:4326), so it can be
    added to a folium map as a static ImageOverlay.

    We avoid leafmap's add_raster()/split_map(), which rely on `localtileserver`
    spinning up its own internal HTTP server on a separate port. That works
    locally but is unreachable in production behind Render's proxy (which only
    exposes the single $PORT Streamlit binds to) -- the tiles silently fail to
    load and the map shows blank. A static image overlay needs no extra server.
    """
    with rasterio.open(tif_path) as src:
        arr = src.read()  # (3, h, w) uint8
        bounds = src.bounds
        crs = src.crs

    if crs is not None and crs.to_epsg() != 4326:
        bounds = transform_bounds(crs, "EPSG:4326", *bounds)

    rgb = np.transpose(arr, (1, 2, 0))  # (h, w, 3)
    alpha = np.where(rgb.sum(axis=2) == 0, 0, 255).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])

    png_path = tif_path.rsplit(".", 1)[0] + ".png"
    Image.fromarray(rgba, "RGBA").save(png_path)

    img_bounds = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]  # [[south, west], [north, east]]
    return png_path, img_bounds


def agregar_basemap_satelital(mapa) -> None:
    """Reemplaza el mapa base por defecto (OpenStreetMap, estilo calles) por
    imagenes satelitales reales (Esri World Imagery). Sin esto, las zonas sin
    datos de una imagen Sentinel-2 (nubes enmascaradas, fuera del bbox) se ven
    como transparentes sobre un mapa de calles, lo cual luce muy poco realista
    y hace parecer que hay un "hueco" en la imagen en vez de simplemente no
    tener dato satelital ahi."""
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri, Maxar, Earthstar Geographics",
        name="Satelite",
        overlay=False,
        control=False,
    ).add_to(mapa)


def add_project_marker(mapa, lat: float, lon: float, nombre: str):
    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(nombre, max_width=250),
        tooltip="Ubicacion del proyecto",
        icon=folium.Icon(color="red", icon="map-marker", prefix="fa"),
    ).add_to(mapa)


def crear_mapa_individual(tiff_path: str, lat: float, lon: float, label: str) -> leafmap.Map:
    png_path, img_bounds = tif_to_png_overlay(tiff_path)
    m = leafmap.Map(center=[lat, lon], zoom=14, draw_control=False,
                    measure_control=False, fullscreen_control=True)
    agregar_basemap_satelital(m)
    folium.raster_layers.ImageOverlay(
        image=png_path, bounds=img_bounds, name=label, opacity=1,
    ).add_to(m)
    return m


def crear_mapa_overview(tramos_validos: list) -> leafmap.Map:
    """Mapa con un marcador por cada tramo del proyecto, encuadrado para
    mostrarlos todos juntos. Solo se usa cuando hay mas de un tramo."""
    if len(tramos_validos) == 1:
        t = tramos_validos[0]
        m = leafmap.Map(center=[t["lat"], t["lon"]], zoom=14, draw_control=False,
                        measure_control=False, fullscreen_control=True)
    else:
        m = leafmap.Map(draw_control=False, measure_control=False, fullscreen_control=True)
    agregar_basemap_satelital(m)

    for t in tramos_validos:
        add_project_marker(m, t["lat"], t["lon"], t["nombre_tramo"])

    if len(tramos_validos) > 1:
        m.fit_bounds([[t["lat"], t["lon"]] for t in tramos_validos])

    return m


NOTA_BASEMAP = (
    "El mapa de fondo (fuera del area cubierta por la imagen Sentinel-2, "
    "por ejemplo en zonas de nubosidad) es una foto satelital reciente de "
    "Esri sin fecha exacta -- no corresponde al mes mostrado, es solo "
    "referencia visual del terreno."
)


def mostrar_nota_basemap() -> None:
    st.markdown(f'<div class="basemap-note">{NOTA_BASEMAP}</div>', unsafe_allow_html=True)


def render_map(m, height: int = 600) -> None:
    """
    Renders a leafmap/folium map in Streamlit without going through
    leafmap.to_streamlit(), which writes the map to a temp HTML file and
    reads it back with a text-mode open(). On Windows that open() defaults
    to cp1252, which crashes on any non-ASCII character (accents, tildes)
    in the map content. This renders the HTML directly in memory as UTF-8.
    """
    import streamlit.components.v1 as components
    m.add_layer_control()
    html = m.get_root().render()
    components.html(html, height=height, scrolling=False)


# ── Search bar ────────────────────────────────────────────────────

col_logo, col_search, col_btn = st.columns([1, 5, 1])
with col_logo:
    st.markdown("## **SatView MVP**")
with col_search:
    bpin_input = st.text_input(
        "Buscar BPIN",
        placeholder="Ingresa el codigo BPIN...",
        label_visibility="collapsed",
        key="bpin_search",
    )
with col_btn:
    buscar_btn = st.button("Buscar", use_container_width=True, type="primary")

st.divider()

# ── Main logic ────────────────────────────────────────────────────

if not bpin_input:
    st.info("Ingresa un BPIN en la barra de busqueda para comenzar.")
    st.stop()

tramos = buscar_tramos(bpin_input)

if tramos is None:
    st.error(f"No se encontro el BPIN **{bpin_input}** en la hoja de proyectos.")
    st.stop()

proyecto = tramos[0]  # nombre/sector/alcance/etc. son idénticos en todas las filas-tramo
nombre_proy = proyecto.get("nombre_del_proyecto", "Sin nombre")
st.markdown(f"## **BPIN** `{bpin_input}` - {nombre_proy}")
st.markdown("")

# ── Resolve coordinates per tramo (no fallback to Bogota: an unparseable
# tramo is excluded and flagged, never silently mislocated) ──────────

tramos_validos = []
nombres_invalidos = []
for t in tramos:
    lat = dms_to_decimal(t.get("latitud"))
    lon = dms_to_decimal(t.get("longitud"))
    nombre_tramo = str(t.get("georreferenciacion") or "").strip() or nombre_proy
    if lat is not None and lon is not None:
        tramos_validos.append({**t, "lat": lat, "lon": lon, "nombre_tramo": nombre_tramo})
    else:
        nombres_invalidos.append(nombre_tramo)

if nombres_invalidos:
    st.warning(
        f"No se pudieron ubicar {len(nombres_invalidos)} tramo(s) por coordenadas "
        f"invalidas o faltantes: {', '.join(nombres_invalidos)}"
    )

if not tramos_validos:
    st.error("Ninguno de los tramos de este proyecto tiene coordenadas validas.")
    st.stop()

# ── Layout: sidebar + main area ──────────────────────────────────
# El mapa/selector de tramos se escribe primero en main_col (arriba de la
# galeria) para que quede al mismo nivel que "Informacion del Proyecto" en
# sidebar_col -- ambas columnas parten desde el mismo punto vertical. Como
# sidebar_col necesita saber que tramo esta activo (para filtrar imagenes),
# el bloque de main_col se llena ANTES que sidebar_col, aunque sidebar_col
# se vea a la izquierda -- el orden del codigo no determina el orden visual
# en columnas de Streamlit, solo en que columna cae cada elemento.

sidebar_col, main_col = st.columns([1, 3], gap="medium")

with main_col:
    if len(tramos_validos) > 1:
        st.markdown(f"### Tramos del proyecto ({len(tramos_validos)})")
        render_map(crear_mapa_overview(tramos_validos), height=350)
        mostrar_nota_basemap()
        idx_tramo = st.selectbox(
            "Tramo",
            options=list(range(len(tramos_validos))),
            format_func=lambda i: tramos_validos[i]["nombre_tramo"],
        )
        tramo_activo = tramos_validos[idx_tramo]
        st.divider()
    else:
        tramo_activo = tramos_validos[0]

proj_lat = tramo_activo["lat"]
proj_lon = tramo_activo["lon"]

imagenes = listar_imagenes(bpin_input, tramo_activo.get("tramo_slug"))
if not imagenes:
    st.warning(
        f"No hay imagenes en Azure Blob Storage para BPIN {bpin_input} "
        f"(tramo: {tramo_activo['nombre_tramo']})."
    )
    st.stop()

with sidebar_col:
    st.markdown('<div class="section-title">Informacion del Proyecto</div>', unsafe_allow_html=True)

    def fval(key, default="-"):
        v = proyecto.get(key, default)
        return v if pd.notna(v) and str(v).strip() else default

    st.markdown(f"""
    <div class="info-card">
        <div class="info-label">Nombre</div>
        <div class="info-value">{fval("nombre_del_proyecto")}</div>
        <div class="info-label">Sector</div>
        <div class="info-value">{fval("sector")}</div>
        <div class="info-label">Alcance</div>
        <div class="info-value">{fval("alcance")}</div>
        <div class="info-label">Fase</div>
        <div class="info-value"><span class="badge badge-blue">{fval("fase_del_proyecto")}</span></div>
        <div class="info-label">Total Proyecto</div>
        <div class="info-value info-mono">{fval("total_proyecto")}</div>
        <div class="info-label">Instancia de Aprobacion</div>
        <div class="info-value">{fval("instancia_de_aprobacion_inicial")}</div>
        <div class="info-label">Fecha de Aprobacion</div>
        <div class="info-value info-mono">{fval("fecha_aprobacion")}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="info-card">
        <div class="info-label">Entidad ejecutora</div>
        <div class="info-value">{fval("entidad_ejecutora")}</div>
        <div class="info-label">NIT</div>
        <div class="info-value info-mono">{fval("nit_entidad_ejecutora")}</div>
        <div class="info-label">Valor total contratos</div>
        <div class="info-value info-mono">{fval("valor_total_de_los_contratos")}</div>
        <div class="info-label">Numero de contratos</div>
        <div class="info-value">{fval("numero_de_contratos_asociados")}</div>
        <div class="info-label">Fechas programadas</div>
        <div class="info-value info-mono">{fval("fecha_inicial_de_la_programacion")} - {fval("fecha_final_de_la_programacion")}</div>
        <div class="info-label">Total pagos al proyecto</div>
        <div class="info-value info-mono">{fval("total_pagos_al_proyecto")}</div>
    </div>
    """, unsafe_allow_html=True)

    avance_fis = fval("avance_fisico", "0")
    avance_fin = fval("avance_financiero", "0")
    try:
        pct_fis = float(str(avance_fis).replace("%", "").replace(",", "."))
    except ValueError:
        pct_fis = 0.0
    try:
        pct_fin = float(str(avance_fin).replace("%", "").replace(",", "."))
    except ValueError:
        pct_fin = 0.0

    st.markdown(f"""
    <div class="info-card">
        <div class="prog-wrap">
            <div class="info-label">Avance fisico</div>
            <div class="prog-bar"><div class="prog-fill-green" style="width:{pct_fis}%"></div></div>
            <div style="font-size:12px;color:#10b981;margin-top:2px">{pct_fis:.1f}%</div>
        </div>
        <div class="prog-wrap">
            <div class="info-label">Avance financiero</div>
            <div class="prog-bar"><div class="prog-fill-blue" style="width:{pct_fin}%"></div></div>
            <div style="font-size:12px;color:#3b82f6;margin-top:2px">{pct_fin:.1f}%</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Image selection
    st.markdown('<div class="section-title">Imagenes disponibles</div>', unsafe_allow_html=True)

    anios = {}
    for img in imagenes:
        anio = img["fecha"].year if img["fecha"] else "Sin fecha"
        anios.setdefault(anio, []).append(img)

    seleccionadas = []
    for anio, imgs in sorted(anios.items()):
        with st.expander(f"{anio}  --  {len(imgs)} imagenes", expanded=True):
            for img in imgs:
                checked = st.checkbox(
                    f"**{img['label']}**  `S-2`",
                    key=f"cb_{tramo_activo.get('tramo_slug') or 'default'}_{img['filename']}",
                    value=False,
                )
                if checked:
                    seleccionadas.append(img)

    # Render mode
    st.markdown('<div class="section-title">Visualizacion</div>', unsafe_allow_html=True)
    modo_render = st.radio(
        "Modo",
        ["Natural", "Escala de grises", "Falso color"],
        horizontal=True,
        label_visibility="collapsed",
    )

    # Comparison and marker toggles
    modo_comparar    = st.toggle("Modo comparacion (2 imagenes)", value=False)
    mostrar_marcador = st.toggle("Mostrar ubicacion del proyecto", value=True)


# ── Main area ─────────────────────────────────────────────────────

modo_map = {"Natural": "natural", "Escala de grises": "gris", "Falso color": "falso"}
modo = modo_map.get(modo_render, "natural")

with main_col:

    if not seleccionadas:
        st.markdown("""
        <div class="warn-box">
            Selecciona al menos una imagen en el panel izquierdo para visualizar.
        </div>
        """, unsafe_allow_html=True)
        st.stop()

    # ── Comparison mode ───────────────────────────────────────────

    if modo_comparar:
        if len(seleccionadas) != 2:
            st.markdown(f"""
            <div class="warn-box">
                El modo comparacion requiere <strong>exactamente 2 imagenes</strong>.<br>
                <small>Actualmente: {len(seleccionadas)} seleccionadas</small>
            </div>
            """, unsafe_allow_html=True)
            st.stop()

        par = sorted(seleccionadas, key=lambda x: x["fecha"] or datetime.min)
        anterior, reciente = par[0], par[1]

        st.markdown(f"### Comparacion: {anterior['label']} vs {reciente['label']}")

        with st.spinner("Procesando imagenes satelitales..."):
            left_path  = descargar_tiff_temp(anterior["bucket_path"])
            right_path = descargar_tiff_temp(reciente["bucket_path"])

            if left_path is None or right_path is None:
                st.error("No se pudieron descargar una o ambas imagenes.")
                st.stop()

            left_empty  = not tiff_has_data(left_path)
            right_empty = not tiff_has_data(right_path)

            if left_empty and right_empty:
                st.error("Ambas imagenes estan vacias (sin datos). Selecciona otros meses.")
                st.stop()
            if left_empty:
                st.warning(f"{anterior['label']} no tiene datos (nubosidad total).")
            if right_empty:
                st.warning(f"{reciente['label']} no tiene datos (nubosidad total).")

            left_tif  = generar_tiff_procesado(left_path, modo)
            right_tif = generar_tiff_procesado(right_path, modo)

        m = leafmap.Map(center=[proj_lat, proj_lon], zoom=14,
                        draw_control=False, measure_control=False)
        agregar_basemap_satelital(m)

        if not left_empty:
            left_png, left_bounds = tif_to_png_overlay(left_tif)
            left_layer = folium.raster_layers.ImageOverlay(
                image=left_png, bounds=left_bounds,
                name=f"Anterior ({anterior['label']})", opacity=1,
            )
            left_layer.add_to(m)
        if not right_empty:
            right_png, right_bounds = tif_to_png_overlay(right_tif)
            right_layer = folium.raster_layers.ImageOverlay(
                image=right_png, bounds=right_bounds,
                name=f"Reciente ({reciente['label']})", opacity=1,
            )
            right_layer.add_to(m)
        if not left_empty and not right_empty:
            SideBySideLayers(layer_left=left_layer, layer_right=right_layer).add_to(m)

        if mostrar_marcador:
            add_project_marker(m, proj_lat, proj_lon, tramo_activo["nombre_tramo"])

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Anterior", anterior["label"])
        with c2:
            st.metric("Reciente", reciente["label"])
        with c3:
            if anterior["fecha"] and reciente["fecha"]:
                delta = (reciente["fecha"] - anterior["fecha"]).days
                st.metric("Diferencia", f"{delta} dias")

        render_map(m, height=600)

    # ── Gallery mode ──────────────────────────────────────────────

    else:
        ordenadas = sorted(seleccionadas, key=lambda x: x["fecha"] or datetime.min)

        st.markdown(f"### Galeria  --  {len(ordenadas)} imagen(es) seleccionadas")

        with st.spinner("Descargando y procesando imagenes..."):
            processed = []
            for img in ordenadas:
                raw_path = descargar_tiff_temp(img["bucket_path"])
                if raw_path is None:
                    processed.append({"img": img, "tif": None, "empty": True})
                    continue
                empty = not tiff_has_data(raw_path)
                tif   = None if empty else generar_tiff_procesado(raw_path, modo)
                processed.append({"img": img, "tif": tif, "empty": empty})

        for row_start in range(0, len(processed), 2):
            row_items = processed[row_start:row_start + 2]
            cols = st.columns(2)

            for col, item in zip(cols, row_items):
                with col:
                    img       = item["img"]
                    label     = img["label"]
                    fecha_str = img["fecha"].strftime("%d/%m/%Y") if img["fecha"] else "-"

                    st.markdown(
                        f'<div class="gallery-label">{label} <small>| Sentinel-2 | {fecha_str}</small></div>',
                        unsafe_allow_html=True,
                    )

                    if item["empty"]:
                        st.warning(f"Sin datos para {label} (nubosidad total).")
                    else:
                        gm = crear_mapa_individual(item["tif"], proj_lat, proj_lon, label)
                        if mostrar_marcador:
                            add_project_marker(gm, proj_lat, proj_lon, tramo_activo["nombre_tramo"])
                        render_map(gm, height=420)