# ── Base: Python 3.11 slim ─────────────────────────────────────────────────
FROM python:3.11-slim

# rasterio y rioxarray necesitan dependencias del sistema
RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# ── Directorio de trabajo ──────────────────────────────────────────────────
WORKDIR /app

# ── Dependencias Python ────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Código fuente ──────────────────────────────────────────────────────────
COPY app.py .

# ── Streamlit: config para producción ─────────────────────────────────────
RUN mkdir -p /app/.streamlit
RUN echo '\
[server]\n\
port = 8501\n\
headless = true\n\
enableCORS = false\n\
enableXsrfProtection = false\n\
\n\
[browser]\n\
gatherUsageStats = false\n\
' > /app/.streamlit/config.toml

# ── Puerto ─────────────────────────────────────────────────────────────────
EXPOSE 8501

# ── Variables de entorno (se pasan en runtime con --env-file .env) ─────────
ENV SUPABASE_URL=""
ENV SUPABASE_ANON_KEY=""

# ── Comando de inicio ──────────────────────────────────────────────────────
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
