# 🛰️ SatView · Seguimiento Satelital de Obras

Aplicación Streamlit para visualizar y comparar imágenes Sentinel-2 de proyectos de construcción.

## Estructura esperada de archivos

```
tu_proyecto/
├── app.py
├── requirements.txt
├── proyectos.xlsx          ← Tu Excel con los proyectos
└── imagenes/
    ├── Sentinel2_2021011000123/
    │   ├── 2021011000123_2025_01.tiff
    │   ├── 2021011000123_2025_03.tiff
    │   ├── 2021011000123_2025_06.tiff
    │   └── 2021011000123_2025_09.tiff
    ├── Sentinel2_2021011000456/
    │   └── ...
    └── ...
```

## Columnas requeridas en el Excel

El Excel `proyectos.xlsx` debe tener al menos estas columnas (los nombres exactos importan):

| Columna | Descripción |
|---|---|
| BPIN | Código único del proyecto |
| Nombre del proyecto | |
| Sector | |
| Alcance | |
| Fase | |
| Total Proyecto | |
| Instancia de Aprobación | |
| Fecha de Aprobación | |
| Entidad ejecutora | |
| NIT entidad ejecutora | |
| Valor total contratos | |
| Número de contratos | |
| Fecha inicial prog. | |
| Fecha final prog. | |
| Total pagos | |
| Avance físico | Porcentaje (ej: 67 o 67%) |
| Avance financiero | Porcentaje (ej: 54 o 54%) |

## Nombre de archivos .tiff

El patrón esperado es:
```
<BPIN>_<AÑO>_<MES>.tiff
```

Ejemplos válidos:
- `2021011000123_2025_01.tiff`
- `2021011000123_2025_march.tiff`
- `2021011000123_2025_jun.tiff`

## Instalación y ejecución

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. (Solo si hay problemas con rasterio en Windows)
conda install -c conda-forge rasterio

# 3. Ejecutar
streamlit run app.py
```

## Ajustes en app.py

Al inicio del archivo `app.py`, ajusta estas dos líneas con tus rutas:

```python
IMAGES_BASE_DIR = Path("./imagenes")   # Carpeta raíz con las carpetas Sentinel2_*
EXCEL_PATH = Path("./proyectos.xlsx")  # Tu archivo Excel
```

## Funcionalidades

- 🔍 Búsqueda por BPIN
- 📊 Ficha completa del proyecto con barras de avance
- 📅 Repositorio de imágenes agrupado por año
- 🖼️ Comparación lado a lado de 2 imágenes (requiere selección exacta de 2)
- 🔍 Zoom independiente por imagen
- 🔗 Opción de sincronizar zoom entre ambas imágenes
- 🎨 Visualización en escala de grises o falso color
