import time
import os
from supabase import create_client
from azure.storage.blob import BlobServiceClient
import tempfile, requests
from dotenv import load_dotenv
load_dotenv()

# --- Supabase (origen) ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

AZURE_CONTAINER = os.getenv("AZURE_CONTAINER")
BUCKET_ORIGEN = "sentinel-images"

# --- Azure (destino) ---
AZURE_CONN_STR = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
blob_service = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
container_client = blob_service.get_container_client(AZURE_CONTAINER)
try:
    container_client.create_container()
    print(f"Contenedor '{AZURE_CONTAINER}' creado.")
except Exception as e:
    print(f"Contenedor ya existe o no se pudo crear: {e}")

def migrar_carpeta(prefix=""):
    items = sb.storage.from_(BUCKET_ORIGEN).list(prefix)
    for item in items:
        name = item["name"]
        full_path = f"{prefix}{name}" if prefix else name

        if not name.lower().endswith((".tif", ".tiff")):
            migrar_carpeta(f"{full_path}/")
            continue

        # --- Saltar si ya existe en Azure (resume) ---
        blob_client = container_client.get_blob_client(full_path)
        if blob_client.exists():
            print(f"Ya existe, saltando: {full_path}")
            continue

        # --- Reintentos en la descarga ---
        max_intentos = 3
        for intento in range(1, max_intentos + 1):
            try:
                print(f"Migrando: {full_path} (intento {intento})")
                signed = sb.storage.from_(BUCKET_ORIGEN).create_signed_url(full_path, 600)
                url = signed["signedURL"]
                r = requests.get(url, timeout=60)
                r.raise_for_status()

                container_client.upload_blob(
                    name=full_path,
                    data=r.content,
                    overwrite=True
                )
                break  # éxito, salir del loop de reintentos
            except Exception as e:
                print(f"  Error: {e}")
                if intento == max_intentos:
                    print(f"  ❌ Falló definitivamente: {full_path}")
                else:
                    time.sleep(3)  # esperar antes de reintentar

migrar_carpeta()