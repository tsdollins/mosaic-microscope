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
from skimage.transform import resize, downscale_local_mean
from basicpy import BaSiC
from ashlar import reg


# Dataset path inside the HDF5 file
DSET = "measurement/simple_tiled_image/live_img_map"

# Glob used to locate the raw scan file inside a downloaded dataset directory.
SCAN_GLOB = "*_simple_tiled_image.h5"

# --- Vignette / flat-field settings ---
BASIC_DARKFIELD = True         # also estimate additive dark offset
FIT_MAX_TILES   = 50           # subsample this many tiles for the BaSiC fit (RAM control)
FIT_DOWNSAMPLE  = 8            # spatial subsample factor for fit tiles. BaSiC works
                               # at working_size=128 px internally, so full-res fit
                               # tiles are wasted RAM; 3584/8=448 px stays well above
                               # that. The smooth flat-/dark-field is upscaled back to
                               # full tile size afterwards for the full-res correction.

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


def read_stage_geometry(f):
    """Return (h0, h1, v0, v1, Nh, Nv, magnification|None) from an open scan H5.

    Prefers the explicit attrs written on the measurement group by
    simple_tiled_image._save_scan_metadata(); falls back to that measurement's
    own settings group (h0/h1/... are ScopeFoundry LQs) and to the turret
    hardware settings for magnification, so older files still work. Returns None
    for magnification if it is not recorded anywhere.
    """
    meas = f.get("measurement/simple_tiled_image")
    settings = f.get("measurement/simple_tiled_image/settings")

    def _get(key):
        if meas is not None and key in meas.attrs:
            return meas.attrs[key]
        if settings is not None and key in settings.attrs:
            return settings.attrs[key]
        raise KeyError(key)

    h0, h1 = float(_get("h0")), float(_get("h1"))
    v0, v1 = float(_get("v0")), float(_get("v1"))
    Nh, Nv = int(_get("Nh")), int(_get("Nv"))

    mag = None
    for grp, key in ((meas, "magnification"),
                     (f.get("hardware/prior_turret/settings"), "magnification")):
        if grp is not None and key in grp.attrs:
            m = float(grp.attrs[key])
            if m > 0:
                mag = m
                break

    return h0, h1, v0, v1, Nh, Nv, mag


def compute_mosaic_geometry(f, pixel_size_um, panel_w_mm, panel_h_mm,
                            aligner=None):
    """Build the pixel<->stage geometry the mosaic viewer uses to place a mosaic
    as a detail region on another (lower-mag) mosaic.

    Returns a dict:
      stage_bbox_mm  : full image extent in stage mm {x_min,x_max,y_min,y_max}
      stage_origin_mm: stage (x,y) at image pixel (0,0) = the top-left corner
      mm_per_px      : mosaic scale (pixel_size_um / 1000) -- a fixed physical
                       scale; ashlar's translation-only model never changes it
      y_axis_up      : True -- larger stage y maps to a smaller pixel row (the
                       Y-flip already baked into H5GridMetadata.tile_position)
      magnification  : objective magnification, or None
      solved         : True if corrected with ashlar's solved positions, else
                       False (nominal-only fallback)
      solved_offset_px : [dx, dy] median pixel shift applied (diagnostic; 0 when
                       nominal-only)

    Two-stage construction:

    1. NOMINAL affine from h0/h1/v0/v1 (+ half a panel to go tile-centre ->
       image edge). This is internally self-consistent: it maps the *nominal*
       tile grid exactly onto the nominal mosaic pixel box. Assumes the common
       raster orientation (col 0 at the smaller h, top image row at the larger v).

    2. SOLVED correction (when ``aligner`` is given). ashlar's actual output
       image is NOT the nominal grid: tiles accumulate per-edge shifts along the
       spanning tree (drift), then the whole image is re-anchored so its minimum
       tile position sits at pixel (0,0), and its extent (``mosaic_shape``) is
       padded to hold the shifted tiles. Ignoring this places detail regions
       wrong -- the error grows with mosaic size and can reach double digits on
       large low-mag overviews. We correct it as a translation-only fix
       (matching the viewer's affine, which has no rotation/scale term):
         * origin: shift by the *median* per-tile displacement
           median(aligner.positions - metadata.positions). Median (not tile 0,
           which may itself be the drifted tile) rejects per-tile alignment
           outliers and recovers the frame shift the re-anchoring introduced.
         * extent: size the bbox from aligner.mosaic_shape * mm_per_px so the box
           reflects the real (padded) image, not the nominal grid.

    Approximate by design -- residual per-tile drift the single translation can't
    absorb is what the viewer's manual nudge corrects; validate against a real
    file. Returns None if stage geometry is unavailable.
    """
    try:
        h0, h1, v0, v1, Nh, Nv, mag = read_stage_geometry(f)
    except KeyError:
        return None

    mm_per_px = pixel_size_um / 1000.0

    # --- 1) Nominal affine (self-consistent fallback) ---
    half_w, half_h = panel_w_mm / 2.0, panel_h_mm / 2.0
    x_min, x_max = min(h0, h1) - half_w, max(h0, h1) + half_w
    y_min, y_max = min(v0, v1) - half_h, max(v0, v1) + half_h
    origin_x, origin_y = x_min, y_max          # stage (x,y) at pixel (0,0)

    solved = False
    offset_px = [0.0, 0.0]

    # --- 2) Solved correction from ashlar, if available ---
    if aligner is not None:
        nominal = aligner.metadata.positions          # [y, x] per tile, min=[0,0]
        final = aligner.positions                     # solved, re-anchored min=[0,0]
        # Robust frame shift (translation-only). positions are [y, x].
        d = np.median(final - nominal, axis=0)
        dy, dx = float(d[0]), float(d[1])
        offset_px = [dx, dy]

        # Solved pixel (0,0) = nominal pixel (-dx,-dy); map back through the
        # nominal affine (x grows with px, y-up so y shrinks with px-row).
        origin_x = x_min - dx * mm_per_px
        origin_y = y_max + dy * mm_per_px

        # Extent from the real (padded) output image. mosaic_shape = (H, W).
        H, W = aligner.mosaic_shape
        x_min, x_max = origin_x, origin_x + W * mm_per_px
        y_max, y_min = origin_y, origin_y - H * mm_per_px
        solved = True

    return {
        "stage_bbox_mm": {"x_min": x_min, "x_max": x_max,
                          "y_min": y_min, "y_max": y_max},
        "stage_origin_mm": {"x": origin_x, "y": origin_y},   # pixel (0,0), y-up
        "mm_per_px": mm_per_px,
        "y_axis_up": True,
        "magnification": mag,
        "solved": solved,
        "solved_offset_px": offset_px,
    }


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

        # BaSiC downsamples every tile to working_size (128 px) internally before
        # fitting, so a full-resolution float32 stack of all tiles (GBs) is thrown
        # away. Subsample tiles spatially for the fit, then upscale the resulting
        # smooth flat-/dark-field back to full tile size so read() can divide full
        # tiles pixel-for-pixel.
        s = max(int(FIT_DOWNSAMPLE), 1)
        full_hw = (self.metadata.th, self.metadata.tw)

        basics = []
        for c in range(self.metadata.num_channels):
            print(f"[BaSiC] fitting channel {c} on {len(fit_idx)} tiles "
                  f"(downsample {s}x)...")
            # Pre-allocate a small stack in the native dtype: no list+np.stack
            # doubling and no premature float32 upcast (BaSiC converts internally).
            sample0 = self._read_raw(fit_idx[0], c)[::s, ::s]
            stack = np.empty((len(fit_idx), *sample0.shape), dtype=sample0.dtype)
            stack[0] = sample0
            for k, i in enumerate(fit_idx[1:], 1):
                stack[k] = self._read_raw(i, c)[::s, ::s]

            basic = BaSiC(get_darkfield=BASIC_DARKFIELD)
            basic.fit(stack)

            # BaSiC returns the fields at the fit (downsampled) resolution; resize
            # the smooth fields up to full tile size for the full-res correction.
            if tuple(basic.flatfield.shape) != full_hw:
                basic.flatfield = resize(
                    basic.flatfield, full_hw, order=1, preserve_range=True
                ).astype(np.float32)
                basic.darkfield = resize(
                    basic.darkfield, full_hw, order=1, preserve_range=True
                ).astype(np.float32)

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


def _write_thumbnail_from_array(arr, out_png_path, max_px=THUMBNAIL_MAX_PX):
    """Write a downsampled 8-bit PNG preview from an assembled mosaic level.

    Accepts a grayscale (H, W) or interleaved RGB (H, W, 3) array -- e.g. the
    smallest pyramid level returned by write_ifd_pyramid, so no file re-read.
    """
    import imageio

    a = arr
    long_edge = max(a.shape[0], a.shape[1])
    if long_edge > max_px:
        step = int(np.ceil(long_edge / max_px))
        a = a[::step, ::step]

    # Scale to 8-bit for the preview.
    if a.dtype != np.uint8:
        af = a.astype(np.float32)
        amax = float(af.max()) or 1.0
        a = (af / amax * 255.0).astype(np.uint8)

    imageio.imwrite(out_png_path, a)
    return out_png_path


def _assemble_mosaic_array(mosaic, verbose=False):
    """Assemble every channel of an ashlar Mosaic into one array ready to write.

    Returns (arr, photometric):
      1 channel (grayscale) -> (H, W),     photometric "minisblack"
      3 channels (RGB)      -> (H, W, 3),  photometric "rgb" (interleaved)

    RGB channels are assembled straight into a single preallocated interleaved
    buffer -- no separate per-channel copies kept, and no channels-first -> RGB
    copy. That is the memory-bounding difference vs. ashlar's writer plus a
    separate repackage pass (which held the whole mosaic twice).
    """
    channels = list(mosaic.channels)
    nch = len(channels)
    H, W = mosaic.shape

    if nch == 1:
        if verbose:
            print("    assembling grayscale channel")
        return mosaic.assemble_channel(channels[0]), "minisblack"

    if nch == 3:
        arr = np.empty((H, W, nch), mosaic.dtype)
        for ci, c in enumerate(channels):
            if verbose:
                print(f"    assembling channel {c} ({ci + 1}/{nch})")
            plane = mosaic.assemble_channel(c)
            arr[..., ci] = plane
            del plane
        return arr, "rgb"

    raise ValueError(
        f"Unsupported channel count {nch}; expected 1 (grayscale) or 3 (RGB). "
        f"Extend _assemble_mosaic_array to handle this case.")


def write_ifd_pyramid(mosaic, path, pixel_size_um=None, tile_size=1024,
                      scale=2, peak_size=1024, verbose=False):
    """Write a browser-readable, pyramidal tiled TIFF directly from an ashlar
    Mosaic -- no SubIFD intermediate and no repackage pass.

    ashlar's PyramidWriter emits a SubIFD pyramid in channel-separated
    "minisblack" planes, which the deep-zoom viewer's geotiff.js cannot read (it
    has no SubIFD support -- confirmed upstream). Previously we wrote that
    intermediate and rewrote it into an IFD pyramid, holding the whole mosaic
    twice and doing a full multi-GB write+read round-trip. Instead we assemble
    the mosaic once and write the final format directly:
      * overviews are reduced-resolution TOP-LEVEL pages (NewSubfileType=1), read
        natively by geotiff.js;
      * 3-channel data is written interleaved as photometric RGB so the browser
        renders true color; 1-channel data is written as minisblack.

    Overview levels are generated in memory by successive block-mean downsampling
    (matching ashlar's pyramid quality). Returns the smallest level array so the
    caller can make a thumbnail without re-reading the file.
    """
    import tifffile

    arr, photometric = _assemble_mosaic_array(mosaic, verbose=verbose)
    is_rgb = (photometric == "rgb")
    dtype = arr.dtype
    factors = (scale, scale, 1) if is_rgb else (scale, scale)

    res_kwargs = {}
    if pixel_size_um:
        res_cm = 10000.0 / float(pixel_size_um)       # pixels per cm
        res_kwargs = dict(resolution=(res_cm, res_cm), resolutionunit="CENTIMETER")

    tile = (tile_size, tile_size)
    level = arr
    smallest = arr
    with tifffile.TiffWriter(path, bigtiff=True) as out:
        i = 0
        while True:
            if verbose:
                print(f"    writing level {i} "
                      f"({level.shape[1]} x {level.shape[0]})")
            out.write(
                np.ascontiguousarray(level),
                tile=tile,
                photometric=photometric,
                compression="adobe_deflate",
                predictor=True,
                subfiletype=0 if i == 0 else 1,           # 1 = REDUCEDIMAGE
                # Calibration on the base page only, matching the previously
                # validated output (the viewer reads pixel size from the base).
                **(res_kwargs if i == 0 else {}),
            )
            smallest = level
            if max(level.shape[0], level.shape[1]) <= peak_size:
                break
            nxt = downscale_local_mean(level, factors)
            if np.issubdtype(dtype, np.integer):
                nxt = np.around(nxt)
            level = nxt.astype(dtype)
            i += 1
    return smallest


def process_scan(h5_path, out_dir):
    """Stitch one raw scan HDF5 into a browser-readable, pyramidal (IFD-overview)
    tiled TIFF and return the results dict.

    This is the shared core of the pipeline: both the Crucible cloud entry point
    (``main`` below) and the local test harness (``full_stitch_process.py``) call
    it, so the exact same code path is exercised in both. ``h5_path`` is the raw
    scan file; ``out_dir`` is where the mosaic, thumbnail, and any artifacts are
    written (created if missing).
    """
    stem = os.path.splitext(os.path.basename(h5_path))[0]
    os.makedirs(out_dir, exist_ok=True)
    # Final deliverable is written directly as a browser-readable IFD pyramid
    # (see write_ifd_pyramid for why the browser can't read ashlar's SubIFDs);
    # there is no SubIFD intermediate and no repackage/round-trip anymore.
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

    # Alignment is done; drop the per-tile edge-alignment cache before Mosaic
    # assembly allocates the full-resolution output (Fix 3: don't hold both).
    try:
        aligner.reader._cache.clear()
    except AttributeError:
        pass

    # Mosaic composites ALL channels using the alignment from above, then we
    # assemble + write the IFD pyramid directly (no ashlar PyramidWriter).
    mosaic_channels = range(reader.metadata.num_channels)
    mosaic = reg.Mosaic(
        aligner, aligner.mosaic_shape,
        channels=mosaic_channels, verbose=True,
    )
    smallest = write_ifd_pyramid(
        mosaic, out_path,
        pixel_size_um=reader.metadata.pixel_size,
        tile_size=1024, verbose=True,
    )
    print("Done. Browser-ready mosaic written to:")
    print(f"  {out_path}")

    # Thumbnail straight from the smallest pyramid level already in memory --
    # no file re-read. smallest is (H,W) grayscale or (H,W,3) RGB.
    thumbnail_path = None
    if SAVE_THUMBNAIL:
        thumb_png = os.path.join(out_dir, f"{stem}_mosaic_thumbnail.png")
        try:
            thumbnail_path = _write_thumbnail_from_array(smallest, thumb_png)
            print(f"thumbnail: {thumbnail_path}")
        except Exception as err:
            print(f"[warn] thumbnail generation failed: {err}")

    # Pixel<->stage geometry so the viewer can place this mosaic as a detail
    # region on a lower-mag mosaic of the same sample. Uses the panel (FOV) size
    # the reader already read from the H5. None if stage bounds are unavailable.
    _, frame_w_mm, frame_h_mm = read_scan_geometry(reader.f)
    geometry = compute_mosaic_geometry(
        reader.f, float(reader.metadata.pixel_size), frame_w_mm, frame_h_mm,
        aligner=aligner)
    if geometry is None:
        print("[warn] stage geometry unavailable; mosaic will not auto-place "
              "as a detail region (older scan file without h0/h1/v0/v1).")
    elif geometry["solved"]:
        dx, dy = geometry["solved_offset_px"]
        print(f"geometry: solved origin from ashlar (median frame shift "
              f"dx={dx:.1f}px dy={dy:.1f}px, mosaic_shape={list(aligner.mosaic_shape)})")

    return {
        "mosaic_path": out_path,
        "thumbnail_path": thumbnail_path,
        "source_h5": h5_path,
        "pixel_size_um": float(reader.metadata.pixel_size),
        "n_tiles": int(reader.metadata._num_images),
        "mosaic_shape": [int(x) for x in aligner.mosaic_shape],
        "median_correction_px": median_correction,
        "max_correction_px": max_correction,
        "geometry": geometry,
    }


def main(directory="./"):
    """Stitch the tiled scan found in ``directory`` into a browser-readable,
    pyramidal (IFD-overview) tiled TIFF.

    Mirrors the RGA analysis-script contract: Crucible-agnostic, takes a local
    directory, writes outputs into a subfolder, and returns a results dict for the
    consumer to build the child dataset / attach metadata + thumbnail.
    """
    # Locate the raw scan HDF5 inside the (downloaded) dataset directory.
    h5_path = _find_scan_h5(directory)
    return process_scan(h5_path, os.path.join(directory, RESULTS_SUBDIR))


if __name__ == "__main__":
    main()
