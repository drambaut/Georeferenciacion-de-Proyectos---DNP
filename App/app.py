"""
SatView - Seguimiento satelital de obras públicas
Ejecutar con: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import rasterio
from rasterio.plot import reshape_as_image
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from datetime import datetime
import re
import io
import base64

# ──────────────────────────────────────────────
# CONFIGURACIÓN
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="SatView · Obras",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Ruta base donde están las carpetas de imágenes
IMAGES_BASE_DIR = # Path(ruta de donde estan las carpetas con archivos.tiff)   # ← Ajusta a tu ruta
EXCEL_PATH = # Path(Ruta del excel con los proyectos)  # ← Ajusta a tu ruta

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


@st.cache_data(show_spinner=False)
def renderizar_tiff(path_str: str, modo: str = "gray") -> np.ndarray:
    """
    Carga un .tiff y lo renderiza como array RGB uint8.
    modo: 'gray' (escala de grises) o 'color' (falso color)
    """
    with rasterio.open(path_str) as src:
        bandas = src.count
        if bandas >= 3 and modo == "color":
            # Tomar bandas 3,2,1 como RGB aproximado (ajusta según tu dato)
            r = src.read(3).astype(float)
            g = src.read(2).astype(float)
            b = src.read(1).astype(float)
            def normalizar(arr):
                p2, p98 = np.percentile(arr[arr > 0], (2, 98)) if arr[arr > 0].size else (0, 1)
                return np.clip((arr - p2) / (p98 - p2 + 1e-9), 0, 1)
            rgb = np.dstack([normalizar(r), normalizar(g), normalizar(b)])
            return (rgb * 255).astype(np.uint8)
        else:
            # Escala de grises: primera banda o promedio
            if bandas == 1:
                arr = src.read(1).astype(float)
            else:
                arr = src.read(1).astype(float)  # banda 1
            p2  = np.percentile(arr[arr > 0], 2)  if arr[arr > 0].size else 0
            p98 = np.percentile(arr[arr > 0], 98) if arr[arr > 0].size else 1
            arr = np.clip((arr - p2) / (p98 - p2 + 1e-9), 0, 1)
            gray = (arr * 255).astype(np.uint8)
            return np.dstack([gray, gray, gray])


def array_a_png_b64(arr: np.ndarray) -> str:
    """Convierte array RGB a base64 PNG."""
    fig, ax = plt.subplots(figsize=(5, 5), dpi=100)
    ax.imshow(arr)
    ax.axis("off")
    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, dpi=100)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def mostrar_imagen_con_zoom(arr: np.ndarray, key_zoom: str, label: str, color_dot: str):
    """Muestra imagen con control de zoom individual."""
    dot = "🟡" if color_dot == "amber" else "🟢"
    st.markdown(f"""
    <div class="img-panel-header">
        <span class="panel-date">{dot} {label}</span>
        <span class="panel-sat-tag">Sentinel-2</span>
    </div>
    """, unsafe_allow_html=True)

    zoom = st.slider("Zoom", 50, 400, 100, 10, key=key_zoom, label_visibility="collapsed")
    ancho = int(5 * zoom / 100)
    ancho = max(2, min(ancho, 12))

    fig, ax = plt.subplots(figsize=(ancho, ancho))
    ax.imshow(arr)
    ax.axis("off")
    plt.tight_layout(pad=0)
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)
    return zoom


# ──────────────────────────────────────────────
# BARRA DE BÚSQUEDA SUPERIOR
# ──────────────────────────────────────────────

col_logo, col_search, col_btn = st.columns([1, 5, 1])
with col_logo:
    st.markdown("## 🛰️ **SatView**")
with col_search:
    bpin_input = st.text_input(
        "Buscar BPIN",
        placeholder="Ingresa el código BPIN...",
        label_visibility="collapsed",
        key="bpin_search",
    )
with col_btn:
    buscar_btn = st.button("Buscar", use_container_width=True, type="primary")

st.divider()

# ──────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ──────────────────────────────────────────────

if not bpin_input:
    st.info("Ingresa un BPIN en la barra de búsqueda para comenzar.")
    st.stop()

df = cargar_excel(EXCEL_PATH)
proyecto = buscar_proyecto(df, bpin_input)

if proyecto is None:
    st.error(f"No se encontró el BPIN **{bpin_input}** en el Excel.")
    st.stop()

# Subtítulo con nombre
nombre_proy = proyecto.get("nombre del proyecto", "Sin nombre")
st.markdown(f"**BPIN** `{bpin_input}` · {nombre_proy}")
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
        with st.expander(f"{año}  —  {len(imgs)} imágenes", expanded=True):
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
        ["Escala de grises", "Falso color"],
        horizontal=True,
        label_visibility="collapsed",
    )
    modo_key = "gray" if "grises" in modo_render else "color"

    # Sync zoom
    sync_zoom = st.toggle("Sincronizar zoom", value=True)


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
                st.caption(f"{img['label']}")
        st.stop()

    # Ordenar: izquierda = anterior, derecha = reciente
    par = sorted(seleccionadas, key=lambda x: x["fecha"] or datetime.min)
    anterior, reciente = par[0], par[1]

    # Cargar arrays
    with st.spinner("Renderizando imágenes…"):
        arr_ant = renderizar_tiff(str(anterior["path"]), modo_key)
        arr_rec = renderizar_tiff(str(reciente["path"]), modo_key)

    # Controles de zoom globales
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 1])
    with ctrl1:
        z_left = st.slider("Zoom anterior", 50, 400, 100, 10, key="zoom_left",
                           label_visibility="visible")
    with ctrl2:
        z_right = st.slider("Zoom reciente", 50, 400, 100, 10, key="zoom_right",
                            label_visibility="visible", disabled=sync_zoom)
    with ctrl4:
        if st.button("↺ Reset"):
            st.session_state["zoom_left"] = 100
            st.session_state["zoom_right"] = 100
            st.rerun()

    if sync_zoom:
        z_right = z_left

    # Columnas de imagen
    col_ant, col_rec = st.columns(2, gap="small")

    with col_ant:
        st.markdown(f"""
        <div class="img-panel-header">
            <span class="panel-date">🟡 Anterior · {anterior['label']}</span>
            <span class="panel-sat-tag">Sentinel-2</span>
        </div>
        """, unsafe_allow_html=True)
        ancho_ant = max(2, min(int(5 * z_left / 100), 10))
        fig_ant, ax_ant = plt.subplots(figsize=(ancho_ant, ancho_ant), facecolor='#0e1117')
        ax_ant.imshow(arr_ant)
        ax_ant.axis("off")
        plt.tight_layout(pad=0)
        st.pyplot(fig_ant, use_container_width=True)
        plt.close(fig_ant)

    with col_rec:
        st.markdown(f"""
        <div class="img-panel-header">
            <span class="panel-date">🟢 Reciente · {reciente['label']}</span>
            <span class="panel-sat-tag">Sentinel-2</span>
        </div>
        """, unsafe_allow_html=True)
        ancho_rec = max(2, min(int(5 * z_right / 100), 10))
        fig_rec, ax_rec = plt.subplots(figsize=(ancho_rec, ancho_rec), facecolor='#0e1117')
        ax_rec.imshow(arr_rec)
        ax_rec.axis("off")
        plt.tight_layout(pad=0)
        st.pyplot(fig_rec, use_container_width=True)
        plt.close(fig_rec)

    # Info comparativa
    st.divider()
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Imagen anterior", anterior["label"])
    with m2:
        st.metric("Imagen reciente", reciente["label"])
    with m3:
        if anterior["fecha"] and reciente["fecha"]:
            delta = (reciente["fecha"] - anterior["fecha"]).days
            st.metric("Diferencia temporal", f"{delta} días")
