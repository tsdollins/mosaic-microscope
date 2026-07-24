import os
import glob
import warnings

# ashlar's reg.py starts a JVM at import time (via pyjnius) even though we use a
# custom HDF5 reader and never touch Bioformats. The cloud container must provide
# a headless JRE and set JAVA_HOME (pyjnius requires it) -- no in-script setup.

# ashlar's utils.py calls deprecated scikit-image APIs (remove_small_holes'
# `area_threshold`, `binary_dilation`) that emit FutureWarnings from inside the
# library during blending. Silence them here rather than patching site-packages.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"ashlar\.utils")

import h5py
import numpy as np
from scipy.ndimage import rotate
from basicpy import BaSiC
from ashlar import reg


# Dataset path inside the HDF5 file
DSET = "measurement/simple_tiled_image/live_img_map"

# Glob used to locate the raw scan file inside a downloaded dataset directory.
SCAN_GLOB = "*_simple_tiled_image.h5"

# --- Vignette / flat-field settings ---
BASIC_DARKFIELD = True         # also estimate additive dark offset
FIT_MAX_TILES   = 50           # subsample this many tiles for the BaSiC fit (RAM control)

# --- Stitching geometry ---
# Overlap fraction and physical frame size are NOT hardcoded: they are read from
# the scan H5 by read_scan_geometry() (written there by simple_tiled_image, with
# a fallback to the app-level settings ScopeFoundry always stores).

ALIGN_CHANNEL = 1              # which channel to align on (0=R, 1=G, 2=B); G is usually sharpest

# Known camera-vs-stage rotation. ashlar has no rotation model, so we de-rotate
# each tile in the reader before alignment. Use the angle that made features line
# up in MosaicViewer.py (negate if alignment gets worse). 0 disables correction.
ROTATION_CORRECTION_DEG = 0

# --- Output / thumbnail ---
RESULTS_SUBDIR   = "stitch_results"   # outputs written under <directory>/<RESULTS_SUBDIR>
SAVE_THUMBNAIL   = True               # write a downsampled PNG preview of the mosaic
THUMBNAIL_MAX_PX = 1024               # longest edge of the thumbnail in pixels


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


def read_scan_geometry(f):
    """Return (overlap_frac, frame_w_mm, frame_h_mm) from an open scan H5.

    Prefers the explicit attrs written on the measurement group by
    simple_tiled_image; falls back to the app-level settings ScopeFoundry always
    stores (so older files still work). Raises if neither is present -- there is
    no hardcoded default.
    """
    meas = f.get("measurement/simple_tiled_image")
    if meas is not None:
        a = meas.attrs
        if all(k in a for k in ("overlap_frac", "panel_width_mm", "panel_height_mm")):
            return (float(a["overlap_frac"]),
                    float(a["panel_width_mm"]),
                    float(a["panel_height_mm"]))

    app = f.get("app/settings")
    if app is not None:
        a = app.attrs
        if all(k in a for k in ("overlap", "panel_width", "panel_height")):
            # app-level 'overlap' is stored as a percent.
            return (float(a["overlap"]) / 100.0,
                    float(a["panel_width"]),
                    float(a["panel_height"]))

    raise ValueError(
        "Scan geometry (overlap / panel size) not found in H5. Expected attrs "
        "on 'measurement/simple_tiled_image' or 'overlap'/'panel_width'/"
        "'panel_height' under 'app/settings'.")


class H5GridReader(reg.Reader):
    def __init__(self, path, overlap=None, frame_w_mm=None, frame_h_mm=None):
        self.path = path
        self.f = h5py.File(path, "r")
        self.imgs = self.f[DSET]

        # Scan geometry: read from the H5 unless explicitly overridden.
        geo_overlap, geo_w, geo_h = read_scan_geometry(self.f)
        if overlap is None:
            overlap = geo_overlap
        if frame_w_mm is None:
            frame_w_mm = geo_w
        if frame_h_mm is None:
            frame_h_mm = geo_h

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


def _find_scan_h5(directory):
    """Locate the raw scan HDF5 inside a downloaded dataset directory.

    Searches recursively because the consumer extracts/downloads the dataset into
    a subfolder. Raises FileNotFoundError if none is found; warns and uses the
    first if several match.
    """
    matches = sorted(glob.glob(os.path.join(directory, "**", SCAN_GLOB), recursive=True))
    if not matches:
        raise FileNotFoundError(
            f"No '{SCAN_GLOB}' scan file found under {directory!r}")
    if len(matches) > 1:
        print(f"[warn] {len(matches)} scan files found; using first: {matches[0]}")
    return matches[0]


def _write_thumbnail(ome_tif_path, out_png_path, max_px=THUMBNAIL_MAX_PX):
    """Write a downsampled PNG preview from the smallest OME-TIFF pyramid level."""
    import tifffile
    import imageio

    with tifffile.TiffFile(ome_tif_path) as tif:
        series = tif.series[0]
        levels = getattr(series, "levels", None)
        level = levels[-1] if levels else series
        arr = level.asarray()

    # ashlar writes channels first: (C, H, W). Convert to (H, W, C); collapse a
    # single channel to 2D grayscale.
    if arr.ndim == 3:
        if arr.shape[0] in (3, 4):
            arr = np.moveaxis(arr, 0, -1)
        else:
            arr = arr[0]

    # Further downsample if the smallest pyramid level is still large.
    long_edge = max(arr.shape[0], arr.shape[1])
    if long_edge > max_px:
        step = int(np.ceil(long_edge / max_px))
        arr = arr[::step, ::step]

    # Scale to 8-bit for the preview.
    if arr.dtype != np.uint8:
        a = arr.astype(np.float32)
        amax = float(a.max()) or 1.0
        arr = (a / amax * 255.0).astype(np.uint8)

    imageio.imwrite(out_png_path, arr)
    return out_png_path


def _repackage_as_ifd_pyramid(src_path, dst_path, pixel_size_um=None):
    """Rewrite ashlar's SubIFD-pyramid OME-TIFF as a plain tiled pyramidal TIFF
    whose overviews are reduced-resolution TOP-LEVEL pages.

    Why: the deep-zoom mosaic viewer uses browser geotiff.js, which reads
    pyramids stored as top-level IFDs but CANNOT traverse the SubIFD pyramid that
    ashlar's PyramidWriter emits. This relocates each level from the SubIFD chain
    to a top-level page (overviews flagged NewSubfileType=REDUCEDIMAGE).

    Two transforms, both driven by ashlar's own output:
      1. SubIFD levels -> top-level IFD pages.
      2. ashlar stores every channel as a separate "minisblack" plane (see the
         FIXME in reg.PyramidWriter.run); when there are exactly 3 channels we
         interleave them to RGB so the browser renders true color.

    Lossless: reuses ashlar's stored levels and re-encodes with the same codec
    (adobe_deflate + horizontal predictor). Processes one level at a time to
    bound peak memory (~one full level; the RGB moveaxis briefly doubles it).
    """
    import tifffile

    with tifffile.TiffFile(src_path) as tif:
        series = tif.series[0]
        levels = series.levels
        p0 = levels[0].pages[0]
        tile = (p0.tilelength, p0.tilewidth)

        # Channel layout from the series shape (tifffile squeezes a singleton
        # channel axis, so grayscale is (H,W) and 3-channel is (C,H,W)).
        sshape = series.shape
        if len(sshape) == 2:
            photometric, is_rgb = "minisblack", False
        elif len(sshape) == 3 and sshape[0] == 3:
            photometric, is_rgb = "rgb", True
        else:
            raise ValueError(
                f"Unsupported channel layout {sshape}; expected grayscale (H,W) "
                f"or 3-channel (3,H,W). Extend _repackage_as_ifd_pyramid for this "
                f"case.")

        res_kwargs = {}
        if pixel_size_um:
            res_cm = 10000.0 / float(pixel_size_um)   # px per cm
            res_kwargs = dict(resolution=(res_cm, res_cm),
                              resolutionunit="CENTIMETER")

        with tifffile.TiffWriter(dst_path, bigtiff=True) as out:
            for i, level in enumerate(levels):
                arr = level.asarray()
                if is_rgb:                                  # (C,H,W) -> (H,W,C)
                    arr = np.ascontiguousarray(np.moveaxis(arr, 0, -1))
                kwargs = dict(
                    tile=tile,
                    photometric=photometric,
                    compression="adobe_deflate",
                    predictor=True,
                    subfiletype=0 if i == 0 else 1,        # 1 = REDUCEDIMAGE
                )
                if i == 0:
                    kwargs.update(res_kwargs)              # calibration on base
                out.write(arr, **kwargs)
                del arr
    return dst_path


def main(directory="./"):
    """Stitch the tiled scan found in ``directory`` into a browser-readable,
    pyramidal (IFD-overview) tiled TIFF.

    Mirrors the RGA analysis-script contract: Crucible-agnostic, takes a local
    directory, writes outputs into a subfolder, and returns a results dict for the
    consumer to build the child dataset / attach metadata + thumbnail.
    """
    # Locate the raw scan HDF5 inside the (downloaded) dataset directory.
    h5_path = _find_scan_h5(directory)
    stem = os.path.splitext(os.path.basename(h5_path))[0]

    out_dir = os.path.join(directory, RESULTS_SUBDIR)
    os.makedirs(out_dir, exist_ok=True)
    # ashlar emits a SubIFD pyramid (intermediate); we repackage it into a
    # browser-readable IFD pyramid as the final deliverable (see
    # _repackage_as_ifd_pyramid for why the browser can't read SubIFDs).
    subifd_path = os.path.join(out_dir, f"{stem}_mosaic_subifd.ome.tif")
    out_path = os.path.join(out_dir, f"{stem}_mosaic.tif")

    reader = H5GridReader(h5_path)

    # geometry sanity check
    print("source h5:", h5_path)
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

    median_correction = float(np.median(corrections))
    max_correction = float(np.max(corrections))
    print(f"\nmedian correction: {median_correction:.2f} px")
    print(f"max correction:    {max_correction:.2f} px")
    print(f"tiles with ~0 correction (<1px): "
          f"{np.sum(corrections < 1.0)} / {len(corrections)}")

    # Mosaic composites ALL channels using the alignment from above
    mosaic_channels = range(reader.metadata.num_channels)
    mosaic = reg.Mosaic(
        aligner, aligner.mosaic_shape,
        channels=mosaic_channels, verbose=True,
    )
    writer = reg.PyramidWriter([mosaic], subifd_path, tile_size=1024, verbose=True)
    writer.run()
    print("ashlar SubIFD mosaic written; repackaging as IFD pyramid...")

    # Thumbnail from the intermediate: its series.levels[-1] is the smallest
    # pyramid level and _write_thumbnail already handles ashlar's channel order.
    thumbnail_path = None
    if SAVE_THUMBNAIL:
        thumb_png = os.path.join(out_dir, f"{stem}_mosaic_thumbnail.png")
        try:
            thumbnail_path = _write_thumbnail(subifd_path, thumb_png)
            print(f"thumbnail: {thumbnail_path}")
        except Exception as err:
            print(f"[warn] thumbnail generation failed: {err}")

    # Relocate levels SubIFD -> top-level IFD (and channels-first -> RGB) so the
    # browser mosaic viewer can read the pyramid.
    _repackage_as_ifd_pyramid(subifd_path, out_path,
                              pixel_size_um=reader.metadata.pixel_size)
    print("Done. Browser-ready mosaic written to:")
    print(f"  {out_path}")

    # Remove the intermediate (kept on failure above for debugging).
    try:
        os.remove(subifd_path)
    except OSError as err:
        print(f"[warn] could not remove intermediate {subifd_path}: {err}")

    return {
        "mosaic_path": out_path,
        "thumbnail_path": thumbnail_path,
        "source_h5": h5_path,
        "pixel_size_um": float(reader.metadata.pixel_size),
        "n_tiles": int(reader.metadata._num_images),
        "mosaic_shape": [int(x) for x in aligner.mosaic_shape],
        "median_correction_px": median_correction,
        "max_correction_px": max_correction,
    }


if __name__ == "__main__":
    main()
