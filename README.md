# SatView · Seguimiento Satelital de Obras Públicas

Aplicación web para visualizar y comparar imágenes satelitales Sentinel-2 de proyectos de infraestructura financiados con recursos del Sistema General de Regalías (SGR). Permite buscar un proyecto por su código BPIN, consultar su ficha técnica y observar la evolución física de la obra a través del tiempo mediante imágenes satelitales.

**Aplicación en funcionamiento:** https://georeferenciacion-de-proyectos-dnp.onrender.com/

---

## Arquitectura general

El sistema tiene tres piezas que viven en lugares distintos y se comunican entre sí:

```
┌──────────────────────┐
│  Excel compartido    │   Metadatos de cada proyecto (BPIN, coordenadas,
│ (público, editable)  │   nombre, sector, avances, etc.). Cualquier persona
└──────────┬───────────┘   con el enlace puede agregar proyectos nuevos.
           │
           │ lectura (link compartido, solo lectura)
           │
     ┌─────┴──────┐
     │            │
     ▼            ▼
┌─────────┐  ┌──────────────────┐
│ app.py  │  │   pipeline.py     │   Se corre manualmente cuando hay
│(Render) │  │  (ejecución local, │   proyectos nuevos. Descarga las
│         │  │   bajo demanda)    │   imágenes faltantes y las sube.
└────┬────┘  └─────────┬─────────┘
     │                 │
     │ lee imágenes    │ descarga de Copernicus, sube a
     │                 │
     ▼                 ▼
┌──────────────────────────────┐
│    Azure Blob Storage         │   Único lugar donde viven las imágenes
│  sentinel2_{BPIN}/AAAA_MM.tiff│   satelitales (GeoTIFF).
└──────────────────────────────┘
```

No hay ninguna base de datos relacional en el sistema. El Excel compartido actúa como base de datos de metadatos, y Azure Blob Storage como almacén de imágenes. `pipeline.py` es el único componente que escribe datos (sube imágenes); todo lo demás solo lee.

---

## Flujo completo: de "agregar un proyecto" a "verlo en la app"

### 1. Alguien agrega un proyecto nuevo

Cualquier persona con el enlace del Excel compartido agrega una fila con los datos del proyecto: BPIN, nombre, sector, coordenadas en formato GMS (`latitud`, `longitud`), y el resto de la ficha técnica. No se necesita ningún permiso especial ni acceso a código.

### 2. Aviso automático por correo

Un script o flujo vinculado al Excel puede detectar el cambio automáticamente y enviar un correo a la lista de destinatarios configurada, indicando cuántas filas nuevas se agregaron y cuáles son. Esto avisa que hay trabajo pendiente, pero no descarga nada por sí solo.

### 3. Alguien corre el pipeline manualmente

Cuando conviene (no hace falta que sea inmediato), se ejecuta:

```bash
python pipeline.py
```

Si se quiere correr sin confirmación manual, por ejemplo en automatizaciones como GitHub Actions, se usa:

```bash
python pipeline.py --auto
```

El script hace lo siguiente, en orden:

1. **Lee el Excel compartido completo** usando el link configurado en `PROJECT_METADATA_XLSX_URL`.
2. **Revisa Azure Blob Storage** para cada proyecto del Excel, y determina qué meses de imágenes ya existen ahí y cuáles faltan. Azure es la fuente de verdad — no se usa ningún archivo local para decidir qué está pendiente, así el resultado es correcto sin importar en qué máquina se corra.
3. **Muestra un resumen** de los proyectos con imágenes faltantes y pide confirmación antes de continuar.
4. **Descarga los meses faltantes desde Copernicus** (Sentinel-2 L2A) usando las funciones de `utils/Download_sat_imgs.py`: autenticación OIDC, filtro de nubosidad, máscara de nubes SCL, composición mensual por mediana, con reintentos automáticos ante límites de tasa (HTTP 429).
5. **Sube cada imagen descargada a Azure Blob Storage** bajo la ruta `sentinel2_{BPIN}/{AAAA}_{MM}.tiff`, y borra la copia local para no acumular espacio en disco.
6. **Registra todo** en `pipeline_log.txt` (log detallado) y `Imagenes/log_descarga.txt` (resumen de éxitos, fallos y meses sin datos disponibles por nubosidad).

### 4. La aplicación muestra el resultado

`app.py`, desplegada en Render, no participa en la descarga. Cuando alguien busca un BPIN:

1. Lee el Excel compartido (con caché de 5 minutos) y busca la fila correspondiente al BPIN.
2. Lista las imágenes disponibles en Azure Blob Storage para ese BPIN.
3. El usuario selecciona una o varias imágenes desde el panel lateral.
4. Cada imagen se descarga temporalmente, se procesa (selección de bandas según el modo de color, normalización por percentiles con manejo robusto de píxeles sin datos) y se renderiza en un mapa interactivo.
5. Según el modo elegido, se muestra en **galería** (varias imágenes en cuadrícula) o en **comparación** (dos imágenes con cortina deslizable).

---

## Estructura del repositorio

```
satview/
├── app.py                        ← Aplicación Streamlit (desplegada en Render)
├── pipeline.py                   ← Descarga y carga de imágenes (ejecución manual)
├── Dockerfile
├── .env                          ← Variables de entorno (no subir a git)
├── .env.example
├── requirements.txt
├── pipeline_state.json           ← Log de auditoría de la última corrida (no es fuente de verdad)
├── pipeline_log.txt              ← Log detallado de la última corrida del pipeline
└── utils/
    ├── Download_sat_imgs.py      ← Lógica de descarga desde Copernicus (reutilizada por pipeline.py)
    ├── mostrar_tiff.py           ← Visualizador local de un GeoTIFF individual, con diagnóstico
    └── verificar_bucket.py       ← Verifica conectividad con Azure Blob Storage
```

> Los archivos dentro de `sql/` (creación de tabla, importación a Supabase) corresponden a una arquitectura anterior basada en Supabase y ya no forman parte del flujo activo. Se conservan solo como referencia histórica.

---

## Configuración

### Variables de entorno

Crea un archivo `.env` en la raíz con:

```env
# Excel compartido (metadatos de proyectos)
PROJECT_METADATA_XLSX_URL=https://...
PROJECT_METADATA_SHEET_NAME=proyectos_satview

# Azure Blob Storage (imágenes satelitales)
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_CONTAINER=nombre_del_contenedor

# Copernicus / openEO
OPENEO_AUTH_METHOD=client_credentials
OPENEO_AUTH_CLIENT_ID=sh-...
OPENEO_AUTH_CLIENT_SECRET=...
OPENEO_AUTH_PROVIDER_ID=CDSE
```

### 1. Excel compartido (metadatos de proyectos)

1. Crea un archivo Excel compartido con, como mínimo, estas columnas (en minúsculas, sin tildes, igual a como se muestran aquí):

   `bpin`, `nombre_del_proyecto`, `sector`, `alcance`, `fase_del_proyecto`, `total_proyecto`, `instancia_de_aprobacion_inicial`, `fecha_aprobacion`, `entidad_ejecutora`, `nit_entidad_ejecutora`, `valor_total_de_los_contratos`, `numero_de_contratos_asociados`, `fecha_inicial_de_la_programacion`, `fecha_final_de_la_programacion`, `total_pagos_al_proyecto`, `avance_fisico`, `avance_financiero`, `latitud`, `longitud`, `georreferenciacion`

2. `latitud` y `longitud` deben estar en formato GMS, por ejemplo `6°18'56.0"N` y `76°8'3.0"W`.
3. Comparte el Excel con acceso **"Cualquier persona con el enlace" → Editor**, para que cualquiera pueda agregar proyectos.
4. Copia el link compartido del Excel y ponlo en `PROJECT_METADATA_XLSX_URL`.
5. Pon el nombre de la pestaña en `PROJECT_METADATA_SHEET_NAME`.

### 2. Azure Blob Storage (imágenes satelitales)

1. Crea una cuenta de almacenamiento en Azure y, dentro de ella, un contenedor (por ejemplo `imagenes-sentinel`).
2. Copia la cadena de conexión desde **Cuenta de almacenamiento → Seguridad y redes → Claves de acceso**, y ponla en `AZURE_STORAGE_CONNECTION_STRING`.
3. Pon el nombre del contenedor en `AZURE_CONTAINER`.

### 3. Copernicus / OpenEO (fuente de las imágenes)

1. Crea una cuenta gratuita en [dataspace.copernicus.eu](https://dataspace.copernicus.eu/).
2. Crea un cliente OAuth y guarda su `client_id` y `client_secret`.
3. Configura:
   - `OPENEO_AUTH_METHOD=client_credentials`
   - `OPENEO_AUTH_CLIENT_ID`
   - `OPENEO_AUTH_CLIENT_SECRET`
   - `OPENEO_AUTH_PROVIDER_ID=CDSE`

---

## Instalación y ejecución local

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-org/satview.git
cd satview

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Edita .env con tus credenciales (ver sección Configuración)

# 5. Ejecutar la aplicación
streamlit run app.py

# 6. Correr el pipeline de descarga (cuando haya proyectos nuevos)
python pipeline.py

# 7. Correr el pipeline sin confirmación manual
python pipeline.py --auto
```

> **Nota Windows:** si hay problemas instalando `rasterio`, usa conda:
> ```bash
> conda install -c conda-forge rasterio
> ```

---

## Despliegue

La aplicación está desplegada en **Render** como Web Service, corriendo directamente sobre `requirements.txt` (no requiere el Dockerfile en ese entorno). Las variables de entorno se configuran en el panel de Render, en la sección **Environment**, con los mismos nombres que en `.env`.

Comando de inicio en Render:
```
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0
```

También es posible correr la app con Docker de forma local:

```bash
docker build -t satview .
docker run -p 8501:8501 --env-file .env satview
```

Accede en: `http://localhost:8501`

---

## Nombre de archivos de imágenes

Dentro de cada carpeta `sentinel2_{BPIN}/` en Azure Blob Storage, el patrón esperado es:

```
<AÑO>_<MES>.tiff
```

Ejemplos válidos: `2025_01.tiff`, `2026_05.tiff`. La app y el pipeline dependen de este formato exacto para ordenar las imágenes cronológicamente y detectar qué meses ya están descargados.

---

## Funcionalidades de la aplicación

- Búsqueda de proyectos por BPIN
- Ficha técnica completa con barras de avance físico y financiero
- Marcador opcional de ubicación exacta del proyecto sobre el mapa (útil en obras pequeñas)
- Repositorio de imágenes Sentinel-2 agrupado por año
- **Modo galería**: selecciona cualquier cantidad de imágenes y se muestran en cuadrícula, cada una con su fecha
- **Modo comparación**: selecciona exactamente dos imágenes y se muestran lado a lado con cortina deslizable
- Tres modos de visualización: color natural, escala de grises, falso color
- Manejo robusto de imágenes con nubosidad: si una imagen no tiene datos válidos, se informa al usuario en vez de mostrar un mapa en blanco sin explicación
- Metadatos leídos en vivo desde el Excel compartido; imágenes servidas desde Azure Blob Storage
