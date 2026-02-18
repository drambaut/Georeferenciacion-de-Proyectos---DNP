import rasterio
import matplotlib.pyplot as plt
import numpy as np

ruta = # ruta de la doc.tiff a visualizar

with rasterio.open(ruta) as src:
    # Sentinel-2 tiene varias bandas. 
    # Si quieres ver la imagen en blanco y negro (una sola banda):
    img = src.read(1) 

# NO USAR np.log10 para Sentinel-2
# Simplemente normalizamos o mostramos directamente
plt.figure(figsize=(10,10))

# Usamos robust=True o calculamos percentiles para que no se vea muy oscura o muy clara
vmin, vmax = np.nanpercentile(img, [2, 98]) 

plt.imshow(img, cmap='gray', vmin=vmin, vmax=vmax)
plt.colorbar(label="Reflectancia")
plt.title("Sentinel-2 - Imagen Óptica (Sin nubes)")
plt.show()