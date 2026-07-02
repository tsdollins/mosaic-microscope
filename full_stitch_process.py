import os
import warnings

JAVA_HOME = r"C:\Program Files\Eclipse Adoptium\jdk-21.0.2.13-hotspot"
os.environ["JAVA_HOME"] = JAVA_HOME
jvm_dir = os.path.join(JAVA_HOME, "bin", "server")
os.environ["PATH"] = jvm_dir + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(jvm_dir)
except (AttributeError, FileNotFoundError):
    pass

# ashlar's utils.py calls deprecated scikit-image APIs (remove_small_holes'
# `area_threshold`, `binary_dilation`) that emit FutureWarnings from inside the
# library during blending. Silence them here rather than patching site-packages.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"ashlar\.utils")

import h5py
import numpy as np
from scipy.ndimage import rotate
from basicpy import BaSiC
from ashlar import reg


# --- Paths ---
IN_PATH  = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260702_114513_simple_tiled_image.h5"
OUT_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\mosaicFull.ome.tif"

# Dataset path inside the HDF5 file
DSET = "measurement/simple_tiled_image/live_img_map"

# --- Vignette / flat-field settings ---
BASIC_DARKFIELD = True         # also estimate additive dark offset
FIT_MAX_TILES   = 50           # subsample this many tiles for the BaSiC fit (RAM control)

# --- Known quantities (stitching geometry) ---
OVERLAP = 0.20                 # overlap fraction between adjacent frames
FRAME_W_MM = 2.695            # physical width  of one frame (H axis) in mm  <-- set this
FRAME_H_MM = 2.695            # physical height of one frame (V axis) in mm  <-- set this

ALIGN_CHANNEL = 1              # which channel to align on (0=R, 1=G, 2=B); G is usually sharpest

# Known camera-vs-stage rotation. ashlar has no rotation model, so we de-rotate
# each tile in the reader before alignment. Use the angle that made features line
# up in MosaicViewer.py (negate if alignment gets worse). 0 disables correction.
ROTATION_CORRECTION_DEG = -1.75


class H5GridMetadata(reg.Metadata):
    def __init__(self, Nv, Nh, th, tw, n_channels, pixel_size,
                 step_h_px, step_v_px, dtype):
        self.Nv, self.Nh = Nv, Nh
        self.th, self.tw = th, tw
        self._n_channels = n_channels
        self._pixel_size = pixel_size
        self._step_h_px = step_h_px
        self._step_v_px = step_v_px
        self._dtype = dtype

    @property
    def _num_images(self):
        return self.Nv * self.Nh

    @property
    def num_channels(self):
        return self._n_channels

    @property
    def pixel_size(self):
        return self._pixel_size

    @property
    def pixel_dtype(self):
        return self._dtype

    def tile_size(self, i):
        return np.array([self.th, self.tw])

    def tile_position(self, i):
        r = i // self.Nh
        c = i % self.Nh
        # position is [y, x] in pixels. Scan rasters v0->v1 (bottom->top, +y up)
        # but image row 0 is the top, so place rows from the bottom up.
        return np.array([(self.Nv - 1 - r) * self._step_v_px,
                         c * self._step_h_px], dtype=float)


class H5GridReader(reg.Reader):
    def __init__(self, path, overlap, frame_w_mm, frame_h_mm):
        self.path = path
        self.f = h5py.File(path, "r")
        self.imgs = self.f[DSET]

        shape = self.imgs.shape
        # Detect layout:
        #   grayscale: (1, Nv, Nh, th, tw)
        #   RGB:       (1, Nv, Nh, th, tw, 3)
        if len(shape) == 6:
            _, Nv, Nh, th, tw, n_color = shape
            self.is_rgb = (n_color == 3)
            n_channels = n_color
        elif len(shape) == 5:
            _, Nv, Nh, th, tw = shape
            self.is_rgb = False
            n_channels = 1
        else:
            raise ValueError(f"Unexpected dataset shape {shape}")

        self.Nv, self.Nh = Nv, Nh
        self.dtype = self.imgs.dtype

        # Pixel size from physical frame size (mm -> um for ashlar)
        px_from_w = (frame_w_mm * 1000.0) / tw
        px_from_h = (frame_h_mm * 1000.0) / th
        pixel_size = (px_from_w + px_from_h) / 2.0

        # Step = (1 - overlap) of the frame, in pixels
        step_h_px = tw * (1.0 - overlap)
        step_v_px = th * (1.0 - overlap)

        self.metadata = H5GridMetadata(
            Nv, Nh, th, tw, n_channels, pixel_size,
            step_h_px, step_v_px, self.dtype.type,
        )

        # Fit BaSiC flat-/dark-field per channel up front so read() can correct
        # each tile in place (replaces the separate vignette-removal pass).
        self.basics = self._fit_basic()

    def _read_raw(self, series, c):
        """Raw tile in sensor orientation (no flip/rotation/correction)."""
        r = series // self.Nh
        col = series % self.Nh
        if self.is_rgb:
            return self.imgs[0, r, col, :, :, c]
        return self.imgs[0, r, col, :, :]

    def _fit_basic(self):
        n_tiles = self.Nv * self.Nh
        if n_tiles > FIT_MAX_TILES:
            fit_idx = np.unique(
                np.linspace(0, n_tiles - 1, FIT_MAX_TILES).astype(int)
            )
        else:
            fit_idx = np.arange(n_tiles)

        basics = []
        for c in range(self.metadata.num_channels):
            print(f"[BaSiC] fitting channel {c} on {len(fit_idx)} tiles...")
            stack = np.stack(
                [self._read_raw(i, c).astype(np.float32) for i in fit_idx]
            )
            basic = BaSiC(get_darkfield=BASIC_DARKFIELD)
            basic.fit(stack)
            basics.append(basic)
            print(f"[BaSiC] channel {c} flatfield range "
                  f"[{basic.flatfield.min():.3f}, {basic.flatfield.max():.3f}]")
            del stack
        return basics

    def read(self, series, c):
        # 1) Raw tile -> BaSiC flat-field correction: (raw - darkfield) / flatfield
        tile = self._read_raw(series, c).astype(np.float32)
        b = self.basics[c]
        corr = (tile - b.darkfield) / b.flatfield
        if np.issubdtype(self.dtype, np.integer):
            info = np.iinfo(self.dtype)
            corr = np.clip(corr, info.min, info.max)
        tile = corr.astype(self.dtype)

        # 2) 180 flip (acquisition orientation) is now baked into the saved h5 data.

        # 3) Incorporate the known camera-vs-stage rotation: de-rotate each tile
        # about its center, keeping its size so the grid metadata stays valid.
        # ashlar then only has to solve translations on corrected tiles.
        if ROTATION_CORRECTION_DEG:
            tile = rotate(tile, angle=ROTATION_CORRECTION_DEG, axes=(0, 1), reshape=False,
                          order=1, mode="constant", cval=0)

        return np.ascontiguousarray(tile)


def main():
    reader = H5GridReader(IN_PATH, OVERLAP, FRAME_W_MM, FRAME_H_MM)

    # geometry sanity check
    print("layout:", "RGB" if reader.is_rgb else "grayscale")
    print("channels:", reader.metadata.num_channels)
    print("tile size (px):", reader.metadata.tile_size(0))
    print("pixel size (um/px):", reader.metadata.pixel_size)
    print("step_h_px:", reader.metadata._step_h_px,
          " step_v_px:", reader.metadata._step_v_px)
    print("h overlap (px):", reader.metadata.th - reader.metadata._step_v_px)
    print("w overlap (px):", reader.metadata.tw - reader.metadata._step_h_px)

    # Align on a single channel (alignment must use one consistent channel)
    align_ch = ALIGN_CHANNEL if reader.is_rgb else 0
    aligner = reg.EdgeAligner(reader, channel=align_ch, max_shift=100, verbose=True)
    aligner.run()

    # Nominal positions from your metadata (what you fed in)
    nominal = np.array([reader.metadata.tile_position(i)
                        for i in range(reader.metadata._num_images)])

    # Final solved positions
    final = aligner.positions

    # Per-tile correction magnitude
    corrections = np.linalg.norm(final - nominal, axis=1)

    print("\n--- Position corrections (px) ---")
    for i, d in enumerate(corrections):
        r, c = i // reader.Nh, i % reader.Nh
        print(f"tile {i:3d} (r{r},c{c}): correction = {d:7.2f} px")

    print(f"\nmedian correction: {np.median(corrections):.2f} px")
    print(f"max correction:    {np.max(corrections):.2f} px")
    print(f"tiles with ~0 correction (<1px): "
          f"{np.sum(corrections < 1.0)} / {len(corrections)}")

    # Mosaic composites ALL channels using the alignment from above
    mosaic_channels = range(reader.metadata.num_channels)
    mosaic = reg.Mosaic(
        aligner, aligner.mosaic_shape,
        channels=mosaic_channels, verbose=True,
    )
    writer = reg.PyramidWriter([mosaic], OUT_PATH, tile_size=1024, verbose=True)
    writer.run()

    print("Done. Stitched mosaic written to:")
    print(f"  {OUT_PATH}")


if __name__ == "__main__":
    main()
