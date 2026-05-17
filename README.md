# 🛰️ SatView · Seguimiento Satelital de Obras Públicas

Aplicación Streamlit para visualizar y comparar imágenes Sentinel-2 de proyectos de construcción financiados con recursos SGR. Permite buscar un proyecto por BPIN, consultar su ficha técnica y comparar imágenes satelitales en el tiempo para seguir la evolución física de la obra.

---

## Arquitectura

```
Supabase Storage (bucket: sentinel-images)
  └── sentinel2_{BPIN}/
        ├── 2025_01.tiff
        ├── 2025_03.tiff
        └── ...

Supabase DB (tabla: proyectos)
  └── 21 columnas con datos del proyecto (BPIN, avances, entidad, etc.)

Streamlit App
  └── Busca proyecto en DB → lista imágenes en Storage → renderiza comparador
```

---

## Estructura del repositorio

```
satview/
├── app.py                  ← Aplicación principal
├── Dockerfile
├── .env                    ← Variables de entorno (no subir a git)
├── .env.example
├── requirements.txt
└── utils/
    ├── verificar_supabase.py
    └── verificar_bucket.py
```

---

## Variables de entorno

Crea un archivo `.env` en la raíz con:

```env
SUPABASE_URL=https://xxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

## Supabase: configuración requerida

### 1. Tabla `proyectos`

Ejecuta `utils/1_crear_tabla_supabase.sql` en el SQL Editor de Supabase.

Columnas utilizadas por la app:

| Columna | Descripción |
|---|---|
| `bpin` | Código único del proyecto (índice) |
| `nombre_del_proyecto` | |
| `sector` | |
| `alcance` | |
| `fase_del_proyecto` | |
| `total_proyecto` | |
| `instancia_de_aprobacion_inicial` | |
| `fecha_aprobacion` | |
| `entidad_ejecutora` | |
| `nit_entidad_ejecutora` | |
| `valor_total_de_los_contratos` | |
| `numero_de_contratos_asociados` | |
| `fecha_inicial_de_la_programacion` | |
| `fecha_final_de_la_programacion` | |
| `total_pagos_al_proyecto` | |
| `avance_fisico` | Porcentaje (ej: 67 o 67%) |
| `avance_financiero` | Porcentaje (ej: 54 o 54%) |
| `latitud` | Formato DMS (ej: 9°31'26.7"N) |
| `longitud` | Formato DMS (ej: 75°33'56.1"W) |
| `georreferenciacion` | Descripción textual |

### 2. Políticas RLS (Row Level Security)

Ejecuta en el SQL Editor:

```sql
-- Lectura pública de la tabla proyectos
CREATE POLICY "allow_select"
ON proyectos FOR SELECT TO anon USING (true);

-- Lectura pública del bucket de imágenes
CREATE POLICY "allow_anon_read"
ON storage.objects FOR SELECT TO anon
USING (bucket_id = 'sentinel-images');
```

### 3. Bucket de imágenes

- Nombre: `sentinel-images`
- Tipo: Private (acceso vía signed URLs)
- Estructura de paths:

```
sentinel2_{BPIN}/2025_01.tiff
sentinel2_{BPIN}/2025_03.tiff
```

### 4. Importar proyectos desde CSV

```bash
# Preparar el CSV desde el Excel original
python utils/2_importar_proyectos.py --csv tu_archivo.csv --dry-run

# Subir a Supabase (requiere SUPABASE_SERVICE_KEY en .env)
python utils/2_importar_proyectos.py --csv tu_archivo.csv
```

> El CSV de origen usa separador `;`, codificación `latin-1`, y doble header (fila 0 = grupos, fila 1 = nombres).

---

## Nombre de archivos TIFF

El patrón esperado dentro de cada carpeta es:

```
<AÑO>_<MES>.tiff
```

Ejemplos válidos:
- `2025_01.tiff`
- `2025_06.tiff`

---

## Instalación local

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
# Edita .env con tus credenciales de Supabase

# 5. Ejecutar
streamlit run app.py
```

> **Nota Windows:** si hay problemas con `rasterio`, instala con conda:
> ```bash
> conda install -c conda-forge rasterio
> ```

---

## Despliegue con Docker

```bash
# Construir imagen
docker build -t satview .

# Correr contenedor
docker run -p 8501:8501 --env-file .env satview
```

Accede en: `http://localhost:8501`

---

## Funcionalidades

- 🔍 Búsqueda de proyectos por BPIN
- 📊 Ficha completa con barras de avance físico y financiero
- 🗺️ Mapa interactivo centrado en las coordenadas del proyecto
- 📅 Repositorio de imágenes Sentinel-2 agrupado por año
- 🖼️ Comparador lado a lado con slider sincronizado
- 🎨 Modos de visualización: natural, escala de grises, falso color
- ☁️ Imágenes servidas desde Supabase Storage (sin archivos locales)
- 🗄️ Datos de proyectos en Supabase DB (sin Excel local)
