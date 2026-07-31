# Memory & Performance Report — `crucible_stitch_process.py`

**Workload analyzed:** a 7×7 mosaic (49 tiles) of RGB tiles, 3584×3584 px each.
**Pixel dtype:** `uint8` (confirmed — `measurements/simple_tiled_image.py` writes `live_img_map` as `np.uint8`, 3 colour channels).

---

## 1. Reference sizes for this workload

| Object | Formula | Size |
|---|---|---|
| 1 tile, 1 channel, uint8 | 3584² | **12.85 MB** |
| 1 tile, 1 channel, float32 | 3584²·4 | **51.4 MB** |
| 1 tile, RGB uint8 | 3584²·3 | **38.5 MB** |
| Full raw dataset (49 RGB tiles) | 49·38.5 MB | **1.89 GB** (on disk / in H5) |
| Mosaic side (10% overlap) | 6·(3584·0.9)+3584 | **≈ 22,938 px** |
| Mosaic, 1 channel, uint8 | 22938² | **526 MB** |
| Mosaic, RGB uint8 (3 ch) | 22938²·3 | **1.58 GB** |
| Mosaic, 1 channel, float32 | 22938²·4 | **2.10 GB** |

> Mosaic size scales with overlap. At 10% overlap the mosaic is ~22.9k px/side; the numbers above move by ±10–15% for 5–15% overlap. Everything downstream (assembly array, repackage buffer) scales with these.

---

## 2. Pipeline walkthrough & per-step peak memory

The steps run **sequentially**, so process peak RSS ≈ the largest single step (plus the ~0.9 GB of state that stays resident). Data is read lazily from the H5 per tile — the raw dataset is **never** loaded whole, which is good.

| # | Step | Code | Transient peak | Resident after | Notes |
|---|---|---|---|---|---|
| A | **BaSiC fit** | `_fit_basic()` | **≈ 5.0 GB** ⚠ | +0.29 GB (flat/dark fields) | Per channel: builds a full-res float32 stack of all 49 tiles |
| B | **Edge alignment** | `reg.EdgeAligner.run()` | ≈ 0.9 GB | +0.60 GB (align-channel cache) | Pairwise overlap FFTs; small |
| C | **Mosaic assembly + pyramid write** | `reg.Mosaic` + `PyramidWriter.run()` | ≈ 1.8 GB | — | 0.53 GB assembly buffer/ch + blend float64 temps |
| D | **Thumbnail** | `_write_thumbnail()` | < 0.1 GB | — | Reads smallest pyramid level only |
| E | **Repackage to IFD pyramid** | `_repackage_as_ifd_pyramid()` | **≈ 3.2 GB** ⚠ | — | Loads full level-0 RGB, then a full contiguous copy |

**Two steps dominate: A (~5 GB) and E (~3.2 GB).** Both are largely avoidable.

---

## 3. Detailed findings

### 🔴 A. `_fit_basic()` builds a 2.5 GB float32 stack that BaSiC immediately throws away — **biggest waste**

```python
stack = np.stack([self._read_raw(i, c).astype(np.float32) for i in fit_idx])
basic = BaSiC(get_darkfield=BASIC_DARKFIELD)
basic.fit(stack)
```

- With 49 tiles and `FIT_MAX_TILES = 50`, **all 49 tiles** are used (`49 < 50`).
- The list comprehension holds 49 × 51.4 MB = **2.52 GB**, then `np.stack` allocates a **second** contiguous 2.52 GB copy → **~5.0 GB peak** per channel (list + stack coexist during the copy). This repeats for each of 3 channels (sequential, `del stack` between).
- **The decisive fact:** BaSiC's `fit()` first line is `Im = self._resize_to_working_size(images)`, and `working_size` defaults to **128**. Every tile is bilinearly resized to **128×128** before any computation. So the full-resolution 3584×3584 stack is downsampled to 49×128×128 (**3 MB**) and the 2.5 GB is discarded.
- The `.astype(np.float32)` is also redundant — BaSiC converts to float32 internally during resize.

**Net:** ~5 GB is allocated to produce a 3 MB working array. This is pure overhead, in both memory *and* time (reading + upcasting + resizing 2.5 GB/channel).

### 🔴 E. `_repackage_as_ifd_pyramid()` holds the full mosaic twice

```python
arr = level.asarray()                                  # level 0: 1.58 GB (RGB)
if is_rgb:
    arr = np.ascontiguousarray(np.moveaxis(arr, 0, -1))  # + 1.58 GB copy
out.write(arr, ...)
```

- For level 0, `asarray()` decompresses the entire base level into RAM: (3, 22938, 22938) uint8 = **1.58 GB**.
- `np.moveaxis` is a view, but `np.ascontiguousarray` forces a full **second 1.58 GB** copy → **~3.2 GB peak**.
- This whole step exists only because ashlar's `PyramidWriter` emits a *SubIFD* pyramid in `minisblack` (channel-separated) planes, which browser `geotiff.js` can't read. We write the intermediate file, then read it all back and rewrite it — a full extra multi-GB **write + read + rewrite** round-trip on disk on top of the memory peak.

### 🟡 B/C. Resident state carried through assembly

- `CachingReader` (inside `EdgeAligner`) caches every align-channel tile: 49 × 12.85 MB = **0.60 GB**, and stays alive as long as the aligner does — i.e. straight through Mosaic assembly (`mosaic.aligner.reader`).
- `reader.basics` holds full-resolution flatfield + darkfield per channel: 6 × 51.4 MB ≈ **0.29 GB**, resident for the whole run (needed — `read()` divides at full res).
- During `Mosaic.assemble_channel`, `out` is a **526 MB** uint8 array per channel, and `pastefunc_blend` builds **float64** temporaries the size of a whole tile per paste (`target*alpha + img*(1-alpha)` → ~2 × 98 MB float64, transient). Combined step-C peak ≈ 1.8 GB. This is mostly inherent to ashlar's paste-based assembler.

### 🟢 Things that are already fine

- Raw H5 is read one tile at a time (h5py lazy slicing) — no whole-dataset load.
- Pyramid sub-levels (`subres_tiles`) stream block-by-block via zarr — cheap.
- Thumbnail reads only the smallest pyramid level.
- Mosaic/output pipeline stays `uint8` end-to-end (no needless global upcast).

---

## 4. Recommended fixes (prioritized)

### Fix 1 — Downsample tiles before the BaSiC fit *(saves ~4.8 GB peak on step A; also faster)*

BaSiC only ever sees 128×128, so feed it small tiles and skip the float32 upcast. Pre-allocate to avoid the list+stack doubling.

```python
BASIC_FIT_DOWNSAMPLE = 8   # 3584 -> 448 px, comfortably above working_size=128

def _fit_basic(self):
    n_tiles = self.Nv * self.Nh
    if n_tiles > FIT_MAX_TILES:
        fit_idx = np.unique(np.linspace(0, n_tiles - 1, FIT_MAX_TILES).astype(int))
    else:
        fit_idx = np.arange(n_tiles)

    s = BASIC_FIT_DOWNSAMPLE
    basics = []
    for c in range(self.metadata.num_channels):
        # strided read -> small stack, pre-allocated (no list + stack doubling)
        sample0 = self._read_raw(fit_idx[0], c)[::s, ::s]
        stack = np.empty((len(fit_idx), *sample0.shape), dtype=sample0.dtype)  # uint8
        stack[0] = sample0
        for k, i in enumerate(fit_idx[1:], 1):
            stack[k] = self._read_raw(i, c)[::s, ::s]
        basic = BaSiC(get_darkfield=BASIC_DARKFIELD)
        basic.fit(stack)                # BaSiC upcasts + resizes to 128 internally
        basics.append(basic)
        del stack
    return basics
```

- Stack drops from **2.52 GB → ~9 MB** per channel (uint8, 448×448×49). Step-A peak **~5 GB → < 0.3 GB**.
- BaSiC upscales the fitted flatfield/darkfield back to full tile resolution on its own, so `read()` is unchanged.
- Fit quality is preserved because the flatfield is a smooth low-rank field; 448 px >> the 128 px BaSiC works at. (Validate flatfield range against a known-good file — see the existing debug print.)
- **Simplest alternative** if you'd rather not downsample: at minimum drop `.astype(np.float32)` and pre-allocate a uint8 stack → 2.52 GB → 0.63 GB and half the time. Downsampling is strictly better.

### Fix 2 — Eliminate (or stream) the repackage step *(saves ~3.2 GB peak on step E + a full multi-GB disk round-trip)*

**Best: write the browser-ready RGB IFD pyramid directly**, skipping the SubIFD intermediate and the read-back entirely. You already control the writer call; replace ashlar's `PyramidWriter` with a small writer that emits top-level IFD pages with `photometric="rgb"`, assembling each channel with `Mosaic.assemble_channel` and interleaving. This removes step E's 3.2 GB peak, the intermediate `.ome.tif` write, and the full re-read. It also removes the thumbnail's dependency on the intermediate (generate it from the smallest level you write).

**Lower-effort: stream the existing repackage** so it never holds the level twice. Read the source level through a zarr store and write tile-by-tile with a generator (mirroring ashlar's own `subres_tiles`), interleaving RGB per tile-row instead of `ascontiguousarray` on the whole plane:

```python
import zarr
with tifffile.TiffFile(src_path) as tif:
    for i, level in enumerate(tif.series[0].levels):
        z = zarr.open(level.aszarr(), mode="r")   # (C,H,W), lazy
        C, H, W = z.shape
        th, tw = tile
        def rgb_tiles():
            for y in range(0, H, th):
                for x in range(0, W, tw):
                    block = z[:, y:y+th, x:x+tw]          # (3, <=th, <=tw)
                    yield np.ascontiguousarray(np.moveaxis(block, 0, -1))
        out.write(rgb_tiles(), shape=(H, W, C), dtype=z.dtype, tile=tile,
                  photometric="rgb", compression="adobe_deflate", predictor=True,
                  subfiletype=0 if i == 0 else 1, **(res_kwargs if i == 0 else {}))
```

Peak drops from ~3.2 GB to a few tiles (~a few MB). Note: writing tiled RGB pages this way still needs the level's tile grid to line up; validate the output opens in the viewer.

### Fix 3 — Free the align-channel cache before assembly *(saves ~0.6 GB resident during steps C/E)*

`EdgeAligner`'s `CachingReader` keeps all 49 align-channel tiles (0.60 GB) alive through mosaic assembly. Alignment is finished once `aligner.run()` returns; the cache isn't needed for assembly (assembly re-reads tiles). Clear it before building the mosaic:

```python
aligner.run()
# ... position diagnostics ...
aligner.reader._cache.clear()   # drop 0.60 GB of cached align tiles
```

Assembly re-reads those tiles from the H5 (cheap relative to the ~1.8 GB step). This lowers the concurrent floor during the heaviest steps.

### Fix 4 — Minor: skip redundant work

- `.astype(np.float32)` in the fit stack (covered by Fix 1).
- The full nominal-vs-solved per-tile correction print loop (lines 514–518) is fine at 49 tiles but O(N); keep as-is.
- `read()` re-runs the full-res BaSiC division for every channel of every tile during assembly (compute, not memory). If assembly time matters, the flat/dark division could be done once per tile for all 3 channels together, but this is a speed, not memory, concern.

---

## 5. Before / after estimate

| Step | Current peak | After fixes | Change |
|---|---|---|---|
| A — BaSiC fit | ~5.0 GB | ~0.3 GB | **Fix 1** |
| B — Alignment | ~0.9 GB | ~0.9 GB | — |
| C — Assembly + write | ~1.8 GB | ~1.2 GB | Fix 3 (cache freed) |
| E — Repackage | ~3.2 GB | ~0.9 GB (stream) / removed (direct write) | **Fix 2** |
| **Process peak RSS** | **~5.0 GB** | **~1.2–1.5 GB** | **~3–4× lower** |

Plus, Fix 2's direct-write variant removes an entire multi-GB intermediate-file **write + full read-back**, the largest single I/O cost in the current flow, and Fix 1 removes reading/upcasting/resizing ~7.5 GB of tile data across the three BaSiC fits.

**Recommended order:** Fix 1 (small, isolated, biggest memory win) → Fix 3 (one line) → Fix 2 streaming variant → consider Fix 2 direct-write later as an architecture cleanup. Validate flatfield range and the viewer output against a real scan file after Fix 1 and Fix 2.
