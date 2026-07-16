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
import json
import folium
import leafmap.foliumap as leafmap
from dotenv import load_dotenv
import os
import rioxarray
import rasterio
import tempfile
import gspread
import requests
from google.oauth2.service_account import Credentials
from azure.storage.blob import BlobServiceClient

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────

st.set_page_config(
    page_title="SatView MVP",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

GOOGLE_SHEET_ID  = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "proyectos_satview")
PROJECT_METADATA_XLSX_URL = os.getenv("PROJECT_METADATA_XLSX_URL")
PROJECT_METADATA_SHEET_NAME = os.getenv(
    "PROJECT_METADATA_SHEET_NAME",
    os.getenv("GOOGLE_SHEET_TAB", "proyectos_satview"),
)

AZURE_CONN_STR  = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "imagenes-sentinel")

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
</style>
""", unsafe_allow_html=True)


# ── Google Sheets (project metadata) ────────────────────────────────

@st.cache_resource(show_spinner=False)
def _google_sheets_client():
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        st.error("Missing environment variable: GOOGLE_SERVICE_ACCOUNT_JSON")
        st.stop()
    info   = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds  = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


def _metadata_download_url(url: str) -> str:
    if "sharepoint.com" in url and "/:x:/" in url:
        return url.split("?", 1)[0] + "?download=1"
    return url


@st.cache_data(ttl=300, show_spinner=False)
def cargar_hoja_proyectos() -> pd.DataFrame:
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
        return df

    client = _google_sheets_client()
    sheet  = client.open_by_key(GOOGLE_SHEET_ID).worksheet(GOOGLE_SHEET_TAB)
    data   = sheet.get_all_records()
    df     = pd.DataFrame(data)
    df.columns = [c.strip() for c in df.columns]
    return df


def buscar_proyecto(bpin: str) -> dict | None:
    df = cargar_hoja_proyectos()
    if df.empty or "bpin" not in df.columns:
        return None
    match = df[df["bpin"].astype(str).str.strip() == bpin.strip()]
    if match.empty:
        return None
    return match.iloc[0].to_dict()


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


@st.cache_data(ttl=300, show_spinner=False)
def listar_imagenes(bpin: str) -> list[dict]:
    container_client = _azure_container_client()
    prefix = f"sentinel2_{bpin}/"
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


@st.cache_data(ttl=300, show_spinner=False)
def descargar_tiff_temp(bucket_path: str) -> str | None:
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


def add_project_marker(mapa, lat: float, lon: float, nombre: str):
    folium.Marker(
        location=[lat, lon],
        popup=folium.Popup(nombre, max_width=250),
        tooltip="Ubicacion del proyecto",
        icon=folium.Icon(color="red", icon="map-marker", prefix="fa"),
    ).add_to(mapa)


def crear_mapa_individual(tiff_path: str, lat: float, lon: float, label: str) -> leafmap.Map:
    m = leafmap.Map(center=[lat, lon], zoom=14, draw_control=False,
                    measure_control=False, fullscreen_control=True)
    m.add_raster(tiff_path, layer_name=label)
    return m


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

proyecto = buscar_proyecto(bpin_input)

if proyecto is None:
    st.error(f"No se encontro el BPIN **{bpin_input}** en la hoja de proyectos.")
    st.stop()

nombre_proy = proyecto.get("nombre_del_proyecto", "Sin nombre")
st.markdown(f"## **BPIN** `{bpin_input}` - {nombre_proy}")
st.markdown("")

imagenes = listar_imagenes(bpin_input)
if not imagenes:
    st.warning(f"No hay imagenes en Azure Blob Storage para BPIN {bpin_input}.")
    st.stop()

# ── Resolve project coordinates once ─────────────────────────────

try:
    proj_lat = dms_to_decimal(proyecto.get("latitud"))
    proj_lon = dms_to_decimal(proyecto.get("longitud"))
    if proj_lat is None or proj_lon is None:
        raise ValueError("Invalid coordinates")
except Exception:
    proj_lat, proj_lon = 4.5709, -74.2973

# ── Layout: sidebar + main area ──────────────────────────────────

sidebar_col, main_col = st.columns([1, 3], gap="medium")

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
                    key=f"cb_{img['filename']}",
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
    mostrar_marcador = st.toggle("Mostrar ubicacion del proyecto", value=False)


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
        m.split_map(
            left_layer=left_tif,
            right_layer=right_tif,
            left_label=f"Anterior ({anterior['label']})",
            right_label=f"Reciente ({reciente['label']})",
        )
        if mostrar_marcador:
            add_project_marker(m, proj_lat, proj_lon, nombre_proy)

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
                            add_project_marker(gm, proj_lat, proj_lon, nombre_proy)
                        render_map(gm, height=420)
