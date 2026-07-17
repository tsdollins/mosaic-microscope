import shutil
import numpy as np
import h5py
from basicpy import BaSiC
import tifffile
import matplotlib.pyplot as plt

# --- Paths ---
IN_PATH  = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260709_161955_simple_tiled_image.h5"
OUT_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260709_161955_simple_tiled_image_flat.h5"

# Dataset path inside the HDF5 file
DSET = "measurement/simple_tiled_image/live_img_map"

# --- Settings ---
BASIC_DARKFIELD = True       # also estimate additive dark offset
FIT_MAX_TILES   = 50         # subsample this many tiles for the BaSiC fit (RAM control)


def detect_layout(shape):
    """Return (Nv, Nh, th, tw, n_channels, is_rgb) for 5D or 6D datasets."""
    if len(shape) == 6:
        _, Nv, Nh, th, tw, n_color = shape
        return Nv, Nh, th, tw, n_color, (n_color == 3)
    elif len(shape) == 5:
        _, Nv, Nh, th, tw = shape
        return Nv, Nh, th, tw, 1, False
    else:
        raise ValueError(f"Unexpected dataset shape {shape}")


def main():
    # 1) Copy the whole file so all groups/attrs/other datasets are preserved.
    print(f"Copying {IN_PATH}\n     -> {OUT_PATH}")
    shutil.cop2 = shutil.copy2  # noqa (just to be explicit)
    shutil.copy2(IN_PATH, OUT_PATH)

    # 2) Open the source (read) and the copy (read/write).
    with h5py.File(IN_PATH, "r") as fin, h5py.File(OUT_PATH, "r+") as fout:
        src = fin[DSET]
        dst = fout[DSET]
        shape = src.shape
        dtype = src.dtype

        Nv, Nh, th, tw, n_channels, is_rgb = detect_layout(shape)
        n_tiles = Nv * Nh
        print(f"layout: {'RGB' if is_rgb else 'grayscale'}, "
              f"grid {Nv}x{Nh}, tile {th}x{tw}, "
              f"channels {n_channels}, dtype {dtype}")

        def read_raw(series, c):
            r, col = series // Nh, series % Nh
            if is_rgb:
                return src[0, r, col, :, :, c]
            else:
                return src[0, r, col, :, :]

        def write_corrected(series, c, tile):
            r, col = series // Nh, series % Nh
            if is_rgb:
                dst[0, r, col, :, :, c] = tile
            else:
                dst[0, r, col, :, :] = tile

        # 3) Fit BaSiC per channel on a subsample of tiles.
        if n_tiles > FIT_MAX_TILES:
            fit_idx = np.unique(
                np.linspace(0, n_tiles - 1, FIT_MAX_TILES).astype(int)
            )
        else:
            fit_idx = np.arange(n_tiles)

        basics = []
        for c in range(n_channels):
            print(f"[BaSiC] fitting channel {c} on {len(fit_idx)} tiles...")
            stack = np.stack(
                [read_raw(i, c).astype(np.float32) for i in fit_idx]
            )
            basic = BaSiC(get_darkfield=BASIC_DARKFIELD)
            basic.fit(stack)
            basics.append(basic)
            print(f"[BaSiC] channel {c} flatfield range "
                  f"[{basic.flatfield.min():.3f}, {basic.flatfield.max():.3f}]")
            del stack

        # 4) Apply correction to every tile and write into the copy.
        is_int = np.issubdtype(dtype, np.integer)
        if is_int:
            info = np.iinfo(dtype)

        for series in range(n_tiles):
            for c in range(n_channels):
                tile = read_raw(series, c).astype(np.float32)
                b = basics[c]
                # corrected = (raw - darkfield) / flatfield
                corr = (tile - b.darkfield) / b.flatfield
                if is_int:
                    corr = np.clip(corr, info.min, info.max)
                write_corrected(series, c, corr.astype(dtype))
            if (series + 1) % 10 == 0 or series == n_tiles - 1:
                print(f"  corrected tile {series + 1}/{n_tiles}")
    
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))

    im0 = axes[0].imshow(b.flatfield, cmap='gray')
    axes[0].set_title('Flatfield')
    fig.colorbar(im0, ax=axes[0])

    im1 = axes[1].imshow(b.darkfield, cmap='gray')
    axes[1].set_title('Darkfield')
    fig.colorbar(im1, ax=axes[1])
    for ax in axes.flat:
        # Pass an empty list to clear the numerical text labels
        ax.set_xticks([])
        ax.set_yticks([])
    plt.tight_layout()
    plt.show()
    fig.savefig(r'C:\Users\Lab\Documents\NewMicroscopeApp\data\flatdarkfield_figure.png', dpi=600, bbox_inches='tight')

    tifffile.imwrite(r'C:\Users\Lab\Documents\NewMicroscopeApp\data\flatfield.tif', b.flatfield.astype(np.float32))
    tifffile.imwrite(r'C:\Users\Lab\Documents\NewMicroscopeApp\data\darkfield.tif', b.darkfield.astype(np.float32))

    print("Done. Corrected file written to:")
    print(f"  {OUT_PATH}")


if __name__ == "__main__":
    main()