"""
SatView MVP - Seguimiento satelital de obras públicas
Ejecutar con: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import re
import leafmap.foliumap as leafmap
from dotenv import load_dotenv
import os
import rioxarray
import rasterio
import tempfile
from supabase import create_client
import tempfile, requests
load_dotenv()

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="SatView MVP · Obras",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Ruta base donde están las carpetas de imágenes

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = "sentinel-images"

# Meses en español para parsing del nombre de archivo
MESES_ES = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
    "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}

# ──────────────────────────────────────────────
# CSS PERSONALIZADO
# ──────────────────────────────────────────────
st.markdown("""
<style>
    /* Fondo oscuro general */
    .stApp { background-color: #0e1117; }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #161b26;
        border-right: 1px solid #2a3347;
    }

    /* Títulos de sección */
    .section-title {
        font-size: 10px;
        font-weight: 700;
        color: #64748b;
        letter-spacing: .1em;
        text-transform: uppercase;
        margin-bottom: 8px;
        margin-top: 4px;
    }

    /* Tarjeta de info */
    .info-card {
        background: #1e2535;
        border: 1px solid #2a3347;
        border-radius: 10px;
        padding: 14px;
        margin-bottom: 10px;
    }
    .info-label { font-size: 10px; color: #64748b; font-weight: 600; text-transform: uppercase; }
    .info-value { font-size: 13px; color: #e2e8f0; font-weight: 500; margin-bottom: 8px; }
    .info-mono  { font-family: monospace; font-size: 12px; }

    /* Badge */
    .badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 5px;
        font-size: 11px;
        font-weight: 700;
    }
    .badge-blue { background: #1e3a5f; color: #3b82f6; }
    .badge-green { background: #052e16; color: #10b981; }

    /* Panel de imagen */
    .img-panel-header {
        background: #161b26;
        border: 1px solid #2a3347;
        border-radius: 8px 8px 0 0;
        padding: 10px 14px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .panel-date { font-size: 13px; font-weight: 600; color: #e2e8f0; }
    .panel-sat-tag { font-size: 10px; color: #64748b; font-family: monospace; }
    .dot-amber { color: #f59e0b; }
    .dot-green  { color: #10b981; }

    /* Progress bar custom */
    .prog-wrap { margin: 4px 0 8px; }
    .prog-label { font-size: 11px; color: #64748b; }
    .prog-bar {
        height: 5px;
        background: #2a3347;
        border-radius: 99px;
        overflow: hidden;
        margin-top: 3px;
    }
    .prog-fill-green { height: 100%; background: #10b981; border-radius: 99px; }
    .prog-fill-blue  { height: 100%; background: #3b82f6; border-radius: 99px; }

    /* Advertencia selección */
    .warn-box {
        background: rgba(245,158,11,.08);
        border: 1px solid rgba(245,158,11,.3);
        border-radius: 8px;
        padding: 12px 16px;
        color: #f59e0b;
        font-size: 13px;
        text-align: center;
        margin: 20px 0;
    }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────
# FUNCIONES AUXILIARES
# ──────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner=False)
def buscar_proyecto_supabase(bpin: str) -> dict | None:
    res = sb.table("proyectos").select("*").eq("bpin", bpin.strip()).execute()
    return res.data[0] if res.data else None

def parsear_fecha_archivo(filename: str) -> datetime | None:
    """
    Extrae fecha de nombres como:
      2025_01.tiff
    """
    name = Path(filename).stem  # sin extensión
    parts = name.split("_")

    # Esperamos exactamente 2 partes: año y mes
    if len(parts) != 2:
        return None

    try:
        año = int(parts[0])
        mes = int(parts[1])
        return datetime(año, mes, 1)
    except Exception:
        return None

def dms_to_decimal(dms_str):
    """Convierte coordenadas DMS a formato decimal."""
    if pd.isna(dms_str) or not isinstance(dms_str, str):
        return None
    pattern = r"(\d+)°(\d+)'([\d.]+)\"([NSEW])"
    match = re.search(pattern, str(dms_str).strip())
    if not match:
        return None
    degrees = float(match.group(1))
    minutes = float(match.group(2))
    seconds = float(match.group(3))
    direction = match.group(4)
    decimal = degrees + minutes/60 + seconds/3600
    if direction in ["S", "W"]:
        decimal *= -1
    return decimal

def stretch_percentile(band):
    p2, p98 = np.percentile(band, (2, 98))
    band = np.clip((band - p2) / (p98 - p2), 0, 1)
    band = np.power(band, 1/1.2)  # ligera corrección gamma
    return band

def generar_tiff_procesado(path_entrada, modo):

    data = rioxarray.open_rasterio(path_entrada)

    if modo == "natural":
        bandas = data.sel(band=[3,2,1]).values.astype(float)

    elif modo == "falso":
        bandas = data.sel(band=[4,3,2]).values.astype(float)

    elif modo == "gris":
        banda = data.sel(band=3).values.astype(float)
        banda = stretch_percentile(banda)
        rgb = np.stack([banda, banda, banda])
        bandas = rgb

    else:
        bandas = data.sel(band=[3,2,1]).values.astype(float)

    if modo != "gris":
        rgb = np.zeros_like(bandas)
        for i in range(3):
            rgb[i] = stretch_percentile(bandas[i])
    else:
        rgb = bandas

    rgb_uint8 = (rgb * 255).astype(np.uint8)

    tmp = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)

    with rasterio.open(
        tmp.name,
        "w",
        driver="GTiff",
        height=rgb_uint8.shape[1],
        width=rgb_uint8.shape[2],
        count=3,
        dtype=rasterio.uint8,
        crs=data.rio.crs,
        transform=data.rio.transform(),
    ) as dst:
        dst.write(rgb_uint8)

    return tmp.name

@st.cache_data(ttl=300, show_spinner=False)
def listar_imagenes_supabase(bpin: str) -> list[dict]:
    prefix = f"sentinel2_{bpin}/"
    try:
        res = sb.storage.from_(BUCKET).list(prefix)
    except Exception as e:
        st.error(f"Error accediendo al bucket: {e}")
        return []
    result = []
    for item in res:
        filename = item["name"]
        if not filename.lower().endswith((".tiff", ".tif")):
            continue
        fecha = parsear_fecha_archivo(filename)
        bucket_path = f"{prefix}{filename}"
        result.append({
            "bucket_path": bucket_path,
            "filename":    filename,
            "fecha":       fecha,
            "label":       fecha.strftime("%b %Y") if fecha else Path(filename).stem,
        })
    result.sort(key=lambda x: x["fecha"] or datetime.min)
    return result

@st.cache_data(ttl=300, show_spinner=False)
def descargar_tiff_temp(bucket_path: str) -> str:
    signed = sb.storage.from_(BUCKET).create_signed_url(bucket_path, 600)
    url = signed["signedURL"]
    r = requests.get(url)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tiff")
    tmp.write(r.content)
    tmp.flush()
    return tmp.name

@st.cache_data(ttl=300, show_spinner=False)
def descargar_tiff_temp(bucket_path: str) -> str:
    """Descarga un TIFF a un archivo temporal y retorna su path."""
    signed = sb.storage.from_(BUCKET).create_signed_url(bucket_path, 300)
    url = signed["signedURL"]
    r = requests.get(url)
    suffix = ".tiff"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.flush()
    return tmp.name

# ──────────────────────────────────────────────
# BARRA DE BÚSQUEDA SUPERIOR
# ──────────────────────────────────────────────

col_logo, col_search, col_btn = st.columns([1, 5, 1])
with col_logo:
    st.markdown("## **SatView MVP**")
with col_search:
    bpin_input = st.text_input(
        "Buscar BPIN",
        placeholder="Ingresa el código BPIN...",
        label_visibility="collapsed",
        key="bpin_search",
    )
with col_btn:
    buscar_btn = st.button("🔍 Buscar", use_container_width=True, type="primary")

st.divider()

# ──────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ──────────────────────────────────────────────

if not bpin_input:
    st.info("👆 Ingresa un BPIN en la barra de búsqueda para comenzar.")
    st.stop()

proyecto = buscar_proyecto_supabase(bpin_input)

if proyecto is None:
    st.error(f"❌ No se encontró el BPIN **{bpin_input}** en la base de datos.")
    st.stop()

# Subtítulo con nombre
nombre_proy = proyecto.get("nombre_del_proyecto", "Sin nombre")
st.markdown(f"## **BPIN** `{bpin_input}` - {nombre_proy}")
st.markdown("")

# Buscar carpeta de imágenes
imagenes = listar_imagenes_supabase(bpin_input)
if not imagenes:
    st.warning(f"No hay imágenes en Supabase para BPIN {bpin_input}.")
    st.stop()
 

# ──────────────────────────────────────────────
# LAYOUT PRINCIPAL: sidebar izq + comparador der
# ──────────────────────────────────────────────
sidebar_col, main_col = st.columns([1, 3], gap="medium")

# ──── SIDEBAR IZQUIERDO ────
with sidebar_col:
    # Info del proyecto
    st.markdown('<div class="section-title">Información del Proyecto</div>', unsafe_allow_html=True)

    def fval(key, default="—"):
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
        <div class="info-label">Instancia de Aprobación</div>
        <div class="info-value">{fval("instancia_de_aprobacion_inicial")}</div>
        <div class="info-label">Fecha de Aprobación</div>
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
        <div class="info-label">Número de contratos</div>
        <div class="info-value">{fval("numero_de_contratos_asociados_al_proyecto")}</div>
        <div class="info-label">Fechas programadas</div>
        <div class="info-value info-mono">{fval("fecha_inicial_de_la_programacion")} → {fval("fecha_final_de_la_programacion")}</div>
        <div class="info-label">Total pagos al proyecto</div>
        <div class="info-value info-mono">{fval("total_pagos_al_proyecto")}</div>
    </div>
    """, unsafe_allow_html=True)

    # Avances con barra visual
    avance_fis = fval("avance_fisico", "0")
    avance_fin = fval("avance_financiero", "0")
    try:
        pct_fis = float(str(avance_fis).replace("%","").replace(",","."))
    except: pct_fis = 0
    try:
        pct_fin = float(str(avance_fin).replace("%","").replace(",","."))
    except: pct_fin = 0

    st.markdown(f"""
    <div class="info-card">
        <div class="prog-wrap">
            <div class="info-label">Avance físico</div>
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

    # ── REPOSITORIO DE IMÁGENES ──
    st.markdown('<div class="section-title">Imágenes disponibles</div>', unsafe_allow_html=True)

    if not imagenes:
        st.warning("No hay imágenes en Supabase para este BPIN.")
        st.stop()

    # Agrupar por año
    años = {}
    for img in imagenes:
        año = img["fecha"].year if img["fecha"] else "Sin fecha"
        años.setdefault(año, []).append(img)

    seleccionadas = []
    for año, imgs in sorted(años.items()):
        with st.expander(f"📅 {año}  —  {len(imgs)} imágenes", expanded=True):
            for img in imgs:
                key_cb = f"cb_{img['filename']}"
                checked = st.checkbox(
                    f"**{img['label']}**  `S-2`",
                    key=key_cb,
                    value=False,
                )
                if checked:
                    seleccionadas.append(img)

    # Renderización
    st.markdown('<div class="section-title">Visualización</div>', unsafe_allow_html=True)
    modo_render = st.radio(
        "Modo",
        ["Natural", "Escala de grises", "Falso color"],
        horizontal=True,
        label_visibility="collapsed",
    )

    # Sync zoom
    sync_zoom = st.toggle("🔗 Sincronizar zoom", value=True)

# ──── ÁREA PRINCIPAL DE COMPARACIÓN ────
with main_col:

    if len(seleccionadas) != 2:
        st.markdown(f"""
        <div class="warn-box">
            ⚠️ Selecciona <strong>exactamente 2 imágenes</strong> en el panel izquierdo para comparar.<br>
            <small>Actualmente: {len(seleccionadas)} seleccionadas</small>
        </div>
        """, unsafe_allow_html=True)
        if len(seleccionadas) > 0:
            for img in seleccionadas:
                st.caption(f"✅ {img['label']}")
        st.stop()

    # Ordenar: izquierda = anterior, derecha = reciente
    par = sorted(seleccionadas, key=lambda x: x["fecha"] or datetime.min)
    anterior, reciente = par[0], par[1]

    # --- NUEVA VISUALIZACIÓN INTERACTIVA ---
    st.markdown(f"### 🛰️ Comparación Sincronizada: {anterior['label']} vs {reciente['label']}")
    
    # 1. Obtener coordenadas para centrar el mapa
    try:
        lat = dms_to_decimal(proyecto.get("latitud"))
        lon = dms_to_decimal(proyecto.get("longitud"))
        if lat is None or lon is None:
            raise ValueError("Coordenadas inválidas")
    except Exception as e:
        # Coordenadas por defecto (Centro de Colombia) si fallara la lectura
        st.warning("No se pudieron leer las coordenadas exactas del proyecto. Mostrando vista por defecto.")
        lat, lon = 4.5709, -74.2973 

    # 2. Crear el mapa interactivo usando leafmap.foliumap
    m = leafmap.Map(center=[lat, lon], zoom=15, locate_control=True)
    
    # 3. Añadir la cortina (split map) con los GeoTIFFs locales
    # localtileserver se encargará automáticamente de renderizar los TIFFs en el mapa
    with st.spinner("Procesando GeoTIFFs para el mapa interactivo..."):
        if modo_render == "Escala de grises":
            modo = "gris"
        elif modo_render == "Falso color":
            modo = "falso"
        else:
            modo = "natural"

        with st.spinner("Procesando GeoTIFFs para render profesional..."):
            left_path = descargar_tiff_temp(anterior["bucket_path"])
            right_path = descargar_tiff_temp(reciente["bucket_path"])
            left_tif = generar_tiff_procesado(left_path, modo)
            right_tif = generar_tiff_procesado(right_path, modo)

        m.split_map(
            left_layer=left_tif,
            right_layer=right_tif,
            left_label=f"Anterior ({anterior['label']})",
            right_label=f"Reciente ({reciente['label']})"
        )
    
    st.divider()

    # Info comparativa
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Imagen anterior", anterior["label"])
    with m2:
        st.metric("Imagen reciente", reciente["label"])
    with m3:
        if anterior["fecha"] and reciente["fecha"]:
            delta = (reciente["fecha"] - anterior["fecha"]).days
            st.metric("Diferencia temporal", f"{delta} días")


    st.divider()
    # 4. Mostrar el mapa en Streamlit
    m.to_streamlit(height=600)