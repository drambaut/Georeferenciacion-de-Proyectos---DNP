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
IMAGES_BASE_DIR = Path(os.getenv("IMAGES_BASE_DIR", "data/Imagenes"))
EXCEL_PATH = Path(os.getenv("EXCEL_PATH", "data/Base_de_proyectos.xlsx"))

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

@st.cache_data(show_spinner=False)
def cargar_excel(path: Path) -> pd.DataFrame:
    """Carga el Excel con los proyectos."""
    try:
        df = pd.read_excel(
        EXCEL_PATH,
        sheet_name="Proyectos seleccionados",
        header=4 #Solo estamos usando el header del nombre de la variale
    )
        df.columns = (
        df.columns
        .str.strip()
        .str.lower()
        )
        return df
    except Exception as e:
        st.error(f"Error cargando Excel: {e}")
        return pd.DataFrame()


def buscar_proyecto(df: pd.DataFrame, bpin: str) -> dict | None:
    """Busca un proyecto por BPIN."""
    if df.empty:
        return None
    bpin_col = df["bpin"].astype(str).str.strip()
    bpin_input = str(bpin).strip()
    fila = df[bpin_col == bpin_input]
    if fila.empty:
        return None
    return fila.iloc[0].to_dict()


def encontrar_carpeta(bpin: str) -> Path | None:
    """Localiza la carpeta Sentinel2_<BPIN>."""
    carpeta = IMAGES_BASE_DIR / f"Sentinel2_{bpin}"
    if carpeta.exists() and carpeta.is_dir():
        return carpeta
    return None


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


def listar_imagenes(carpeta: Path) -> list[dict]:
    """Lista y ordena los .tiff de una carpeta."""
    tiffs = list(carpeta.glob("*.tiff")) + list(carpeta.glob("*.tif"))
    result = []
    for t in tiffs:
        fecha = parsear_fecha_archivo(t.name)
        result.append({
            "path": t,
            "filename": t.name,
            "fecha": fecha,
            "label": fecha.strftime("%b %Y") if fecha else t.stem,
        })
    result.sort(key=lambda x: x["fecha"] or datetime.min)
    return result


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

df = cargar_excel(EXCEL_PATH)
proyecto = buscar_proyecto(df, bpin_input)

if proyecto is None:
    st.error(f"❌ No se encontró el BPIN **{bpin_input}** en la base de datos.")
    st.stop()

# Subtítulo con nombre
nombre_proy = proyecto.get("nombre del proyecto", "Sin nombre")
st.markdown(f"## **BPIN** `{bpin_input}` - {nombre_proy}")
st.markdown("")

# Buscar carpeta de imágenes
carpeta = encontrar_carpeta(bpin_input)

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
        <div class="info-value">{fval("nombre del proyecto")}</div>
        <div class="info-label">Sector</div>
        <div class="info-value">{fval("sector")}</div>
        <div class="info-label">Alcance</div>
        <div class="info-value">{fval("alcance")}</div>
        <div class="info-label">Fase</div>
        <div class="info-value"><span class="badge badge-blue">{fval("fase del proyecto")}</span></div>
        <div class="info-label">Total Proyecto</div>
        <div class="info-value info-mono">{fval("total proyecto")}</div>
        <div class="info-label">Instancia de Aprobación</div>
        <div class="info-value">{fval("instancia de aprobación inicial")}</div>
        <div class="info-label">Fecha de Aprobación</div>
        <div class="info-value info-mono">{fval("fecha aprobación")}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="info-card">
        <div class="info-label">Entidad ejecutora</div>
        <div class="info-value">{fval("entidad ejecutora")}</div>
        <div class="info-label">NIT</div>
        <div class="info-value info-mono">{fval("nit entidad ejecutora")}</div>
        <div class="info-label">Valor total contratos</div>
        <div class="info-value info-mono">{fval("valor total de los contratos")}</div>
        <div class="info-label">Número de contratos</div>
        <div class="info-value">{fval("numero de contratos asociados al proyecto")}</div>
        <div class="info-label">Fechas programadas</div>
        <div class="info-value info-mono">{fval("fecha inicial de la programación")} → {fval("fecha final de la programación")}</div>
        <div class="info-label">Total pagos al proyecto</div>
        <div class="info-value info-mono">{fval("total pagos al proyecto")}</div>
    </div>
    """, unsafe_allow_html=True)

    # Avances con barra visual
    avance_fis = fval("avance físico", "0")
    avance_fin = fval("avance financiero", "0")
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

    if carpeta is None:
        st.warning(f"No se encontró carpeta `Sentinel2_{bpin_input}`")
        st.stop()

    imagenes = listar_imagenes(carpeta)
    if not imagenes:
        st.warning("No hay archivos .tiff en la carpeta.")
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
            left_tif = generar_tiff_procesado(str(anterior["path"]), modo)
            right_tif = generar_tiff_procesado(str(reciente["path"]), modo)

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