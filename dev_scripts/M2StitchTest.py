import os
import h5py
import numpy as np
from scipy.ndimage import rotate
import m2stitch
import tifffile

H5_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260702_114513_simple_tiled_image.h5"
OUT_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\mosaic_m2stitch2.ome.tif"

# --- Known quantities ---
OVERLAP = 0.20                 # 20% overlap between adjacent frames
FRAME_W_MM = 0.6738            # physical width  of one frame (H axis) in mm
FRAME_H_MM = 0.6738            # physical height of one frame (V axis) in mm

ALIGN_CHANNEL = 1             # which channel to align on (0=R, 1=G, 2=B); G is usually sharpest

# Known camera-vs-stage rotation. m2stitch has no rotation model, so we de-rotate
# each tile before alignment.
ROTATION_CORRECTION_DEG = -3.0


def load_tiles(path):
    """Load all tiles from the H5 file, applying only rotation correction.

    Returns:
        tiles: dict channel -> (N, th, tw) uint array, N tiles in grid order
        rows:  list of grid row index per tile
        cols:  list of grid col index per tile
        Nv, Nh, is_rgb, n_channels, th, tw
    """
    f = h5py.File(path, "r")
    imgs = f["measurement/simple_tiled_image/live_img_map"]

    shape = imgs.shape
    # Detect layout:
    #   grayscale: (1, Nv, Nh, th, tw)
    #   RGB:       (1, Nv, Nh, th, tw, 3)
    if len(shape) == 6:
        _, Nv, Nh, th, tw, n_color = shape
        is_rgb = (n_color == 3)
        n_channels = n_color
    elif len(shape) == 5:
        _, Nv, Nh, th, tw = shape
        is_rgb = False
        n_channels = 1
    else:
        raise ValueError(f"Unexpected dataset shape {shape}")

    def process(tile):
        # Raw frames are already upright (see docs/TILE_ORIENTATION_NOTES.md §2),
        # so no flip is baked in here -- matches AshlarTest's reader.
        if ROTATION_CORRECTION_DEG:
            tile = rotate(tile, angle=ROTATION_CORRECTION_DEG, axes=(0, 1),
                          reshape=False, order=1, mode="constant", cval=0)
        return np.ascontiguousarray(tile)

    # Build a per-channel stack of tiles plus grid indices, in row-major order.
    channels = {c: [] for c in range(n_channels)}
    rows, cols = [], []
    for r in range(Nv):
        for col in range(Nh):
            # Scan rasters v0->v1 (bottom->top, +y up), so scan row 0 is the
            # physical bottom. m2stitch's grid row index increases downward in the
            # mosaic, so place scan row r at grid row (Nv-1-r) to keep the bottom
            # of the sample at the bottom (docs/TILE_ORIENTATION_NOTES.md §3).
            rows.append(Nv - 1 - r)
            cols.append(col)
            for c in range(n_channels):
                if is_rgb:
                    tile = imgs[0, r, col, :, :, c]
                else:
                    tile = imgs[0, r, col, :, :]
                channels[c].append(process(tile))

    tiles = {c: np.array(channels[c]) for c in channels}
    f.close()
    return tiles, rows, cols, Nv, Nh, is_rgb, n_channels, th, tw


tiles, rows, cols, Nv, Nh, is_rgb, n_channels, th, tw = load_tiles(H5_PATH)

# Pixel size from physical frame size (mm -> um)
px_from_w = (FRAME_W_MM * 1000.0) / tw
px_from_h = (FRAME_H_MM * 1000.0) / th
pixel_size = (px_from_w + px_from_h) / 2.0

print("layout:", "RGB" if is_rgb else "grayscale")
print("channels:", n_channels)
print("grid (Nv x Nh):", Nv, "x", Nh)
print("tile size (px):", (th, tw))
print("pixel size (um/px):", pixel_size)
print("expected overlap (px):", OVERLAP * th)

# --- Run m2stitch on the alignment channel ---
align_ch = ALIGN_CHANNEL if is_rgb else 0
align_stack = tiles[align_ch]

# m2stitch wants overlap as a percentage (0-100). Passing overlap_diff / range
# lets it search around the nominal value.
result_df, _ = m2stitch.stitch_images(
    align_stack,
    rows,
    cols,
    row_col_transpose=False,
    overlap_diff_threshold=10,
    pou=3,
    ncc_threshold=0.1,
)

print("\n--- m2stitch results ---")
print(result_df.head())

# m2stitch reports positions in the "y_pos" / "x_pos" columns (pixels).
# Normalize so the minimum position is 0.
result_df["y_pos2"] = result_df["y_pos"] - result_df["y_pos"].min()
result_df["x_pos2"] = result_df["x_pos"] - result_df["x_pos"].min()

# result_df is indexed by the input tile index; reindex so positions align
# 1:1 with the tile stacks (tiles[c][i]) even if rows get reordered/dropped.
y_pos = result_df["y_pos2"].reindex(range(len(rows))).to_numpy()
x_pos = result_df["x_pos2"].reindex(range(len(rows))).to_numpy()

# Build lookup from (row, col) -> solved position, since m2stitch may drop tiles
pos_lookup = {(int(r), int(c)): (int(y), int(x))
              for r, c, y, x in zip(result_df["row"], result_df["col"],
                                    result_df["y_pos"], result_df["x_pos"])}

missing = [(rows[i], cols[i]) for i in range(len(rows))
           if (rows[i], cols[i]) not in pos_lookup]
print("missing tiles (failed registration):", missing)

# Measure regularity instead of "correction vs nominal":
# for each column, look at the row-to-row y step; it should be near-constant.
ys = result_df.sort_values(["col", "row"])
for col in sorted(result_df["col"].unique()):
    sub = ys[ys["col"] == col].sort_values("row")
    dy = np.diff(sub["y_pos"].to_numpy())
    if len(dy):
        print(f"col {col}: median row-step={np.median(dy):.1f}px "
              f"(std={np.std(dy):.1f})")

# --- Composite the mosaic using the solved positions, for ALL channels ---
canvas_h = int(np.ceil(np.nanmax(y_pos))) + th
canvas_w = int(np.ceil(np.nanmax(x_pos))) + tw

dtype = align_stack.dtype
mosaic = np.zeros((n_channels, canvas_h, canvas_w), dtype=dtype)

for c in range(n_channels):
    stack = tiles[c]
    for i in range(len(rows)):
        # Skip tiles m2stitch couldn't place (position is NaN).
        if np.isnan(y_pos[i]) or np.isnan(x_pos[i]):
            continue
        y = int(round(y_pos[i]))
        x = int(round(x_pos[i]))
        # Simple last-writer-wins placement (overlaps overwritten).
        mosaic[c, y:y + th, x:x + tw] = stack[i]

# Write OME-TIFF. Squeeze channel axis for grayscale.
out = mosaic if n_channels > 1 else mosaic[0]
tifffile.imwrite(
    OUT_PATH,
    out,
    photometric="minisblack",
    metadata={"axes": "CYX" if n_channels > 1 else "YX",
              "PhysicalSizeX": pixel_size,
              "PhysicalSizeY": pixel_size},
    ome=True,
)
print(f"\nWrote mosaic: {OUT_PATH}  shape={out.shape}")