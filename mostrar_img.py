import os
import rasterio
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt

# Ruta a la carpeta R60m con los .jp2
ruta_r60m = r"D:\andres\Macc\DR\DNP\2026\ProyectoVC\codigo\Sentinel2\2024002700193_27-12-2022\S2A_MSIL2A_20221227T153621_N0510_R068_T18PVR_20240805T231326.SAFE\GRANULE\L2A_T18PVR_A039247_20221227T153718\IMG_DATA\R60m"

# Listar archivos .jp2 en esa carpeta
archivos_jp2 = [f for f in os.listdir(ruta_r60m) if f.endswith(".jp2")]

print(f"Encontrados {len(archivos_jp2)} archivos .jp2")

for archivo in archivos_jp2:
    ruta_archivo = os.path.join(ruta_r60m, archivo)
    print(f"Abriendo: {archivo}")

    with rasterio.open(ruta_archivo) as src:
        img = src.read(1)  # Leer la primera (y única) banda

        plt.figure(figsize=(8, 6))
        plt.title(f"{archivo}")
        plt.imshow(img, cmap='gray')
        plt.colorbar(label='Valor de pixel')
        plt.axis('off')
        plt.show()
