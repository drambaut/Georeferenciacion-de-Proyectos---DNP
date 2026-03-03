import rioxarray
import matplotlib.pyplot as plt
import numpy as np

filepath = r"ruta/del/tiff" # reemplazar


# Leer GeoTIFF
data = rioxarray.open_rasterio(filepath)
# Seleccionar bandas RGB reales
rgb = data.sel(band=[3,2,1]).transpose("y","x","band")

rgb_np = rgb.values.astype(float)

rgb_norm = np.zeros_like(rgb_np)

for i in range(3):
    band = rgb_np[:,:,i]
    p2, p98 = np.percentile(band, (2,98))
    rgb_norm[:,:,i] = np.clip((band - p2) / (p98 - p2), 0, 1)

plt.figure(figsize=(10,10))
plt.title("Sentinel-2 RGB Natural Color")
plt.imshow(rgb_norm)
plt.axis('off')
plt.show()