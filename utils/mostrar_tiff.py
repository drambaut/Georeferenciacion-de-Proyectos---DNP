"""
mostrar_tiff.py
Sentinel-2 GeoTIFF viewer with band diagnostics.

Usage:
    python mostrar_tiff.py
    python mostrar_tiff.py --guardar
    python mostrar_tiff.py --verbose
    python mostrar_tiff.py --guardar --verbose
"""

import sys
import numpy as np
import rioxarray
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

FILEPATH = r"ruta\del\archivo.tiff" # Reemplazar por el archivo que se desee ver

BAND_NAMES = {1: "B02", 2: "B03", 3: "B04", 4: "B08"}


def log(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg)


def normalize(band: np.ndarray, p_low: int = 2, p_high: int = 98) -> np.ndarray:
    valid = band[~np.isnan(band)]
    if valid.size == 0:
        return np.zeros_like(band)
    p2, p98 = np.percentile(valid, p_low), np.percentile(valid, p_high)
    if p98 == p2:
        return np.where(np.isnan(band), np.nan, 0.5)
    return np.clip((band - p2) / (p98 - p2), 0, 1)


def diagnose(data, verbose: bool) -> bool:
    n_bands = data.shape[0]
    labels  = [BAND_NAMES.get(i + 1, f"Band {i + 1}") for i in range(n_bands)]

    log(f"  Shape  : {data.shape[1]} x {data.shape[2]} px, {n_bands} bands", verbose)

    all_empty = False
    for i in range(n_bands):
        arr     = data[i].values.astype(float)
        valid   = arr[~np.isnan(arr)]
        pct     = 100 * valid.size / arr.size
        status  = "[OK]" if pct > 20 else ("[WARN]" if pct > 0 else "[EMPTY]")

        if pct == 0:
            all_empty = True

        rng = f"  range [{valid.min():.4f} - {valid.max():.4f}]" if valid.size > 0 else ""
        log(f"  {status} {labels[i]:6s}: {pct:5.1f}% valid pixels{rng}", verbose)

    if all_empty:
        log("  No valid pixels found. Image is likely fully cloud-masked.", verbose)

    return all_empty


def render(filepath: str, guardar: bool, verbose: bool) -> None:
    log(f"Reading: {filepath}", verbose)
    data = rioxarray.open_rasterio(filepath)

    empty = diagnose(data, verbose)
    if empty:
        print("No renderable data. All pixels are NaN.")
        return

    R   = data[2].values.astype(float)
    G   = data[1].values.astype(float)
    B   = data[0].values.astype(float)
    nan_mask = np.isnan(R)

    rgb = np.stack([normalize(R), normalize(G), normalize(B)], axis=-1)
    rgb[nan_mask] = 1.0

    pct_valid = 100 * (1 - nan_mask.mean())

    has_nir = data.shape[0] >= 4
    if has_nir:
        NIR = data[3].values.astype(float)
        fc  = np.stack([normalize(NIR), normalize(R), normalize(G)], axis=-1)
        fc[nan_mask] = 1.0

    ncols = 2 if has_nir else 1
    fig, axes = plt.subplots(1, ncols, figsize=(8 * ncols, 8))
    if ncols == 1:
        axes = [axes]

    axes[0].imshow(rgb)
    axes[0].set_title(f"Natural Color (RGB)\n{pct_valid:.1f}% valid pixels", fontsize=12)
    axes[0].axis("off")

    if has_nir:
        axes[1].imshow(fc)
        axes[1].set_title("False Color (NIR-Red-Green)", fontsize=12)
        axes[1].axis("off")

    filename = filepath.replace("\\", "/").split("/")[-1]
    fig.suptitle(f"Sentinel-2  |  {filename}", fontsize=13)
    plt.tight_layout()

    if guardar:
        out = filepath.rsplit(".", 1)[0] + "_preview.png"
        plt.savefig(out, dpi=150, bbox_inches="tight")
        log(f"Saved: {out}", verbose)

    plt.show()


if __name__ == "__main__":
    args    = sys.argv[1:]
    guardar = "--guardar" in args
    verbose = "--verbose" in args
    render(FILEPATH, guardar, verbose)