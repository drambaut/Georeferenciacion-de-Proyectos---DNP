"""
Verifica conexión a Supabase y que los datos estén bien.
Uso: python verificar_supabase.py
"""
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")  # o SUPABASE_SERVICE_KEY

print(f"URL:  {SUPABASE_URL}")
print(f"KEY:  {SUPABASE_KEY[:20]}..." if SUPABASE_KEY else "KEY: ❌ NO ENCONTRADA")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# 1. Contar filas
res = sb.table("proyectos").select("*", count="exact").execute()
print(f"\nConexión éxitosa — filas en tabla: {res.count}")

# 2. Mostrar los bpins que existen
res2 = sb.table("proyectos").select("bpin").execute()
print("\nBPINs encontrados:")
for row in res2.data:
    print(f"  '{row['bpin']}'")
