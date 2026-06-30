import h5py
import numpy as np
import napari
from scipy.ndimage import rotate

H5_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260626_150943_simple_tiled_image_flat.h5"

# --- Rotation correction ---------------------------------------------------
# The camera is mounted rotated relative to the stage. Each captured tile is
# therefore rotated by this angle relative to the stage axes, which makes
# features fail to line up across tiles. We correct by rotating EACH tile about
# its own center by this angle, keeping the tile the same size and grid position
# (only the content is de-rotated). If features line up worse, negate the angle.
ROTATION_CORRECTION_DEG = 1.0
SHOW_RAW = True            # show the uncorrected mosaic
SHOW_CORRECTED = False      # show the per-tile rotation-corrected mosaic

# --- Placement mode --------------------------------------------------------
# "abut":    tiles placed edge-to-edge (step = tile size), hard overwrite.
# "overlap": tiles placed at their nominal grid positions assuming OVERLAP
#            fraction and feather-blended across the overlaps. Previews what an
#            un-ashlar-aligned (nominal-position) stitch should look like.
MODE = "abut"
OVERLAP = 0.15      # fraction of a tile that overlaps its neighbor (overlap mode)
FEATHER = False      # blend overlaps with a tapered weight (overlap mode)
# ---------------------------------------------------------------------------


def rotate_tile(tile, angle_deg):
    """Rotate a tile about its own center, keeping its shape (and any color
    axis) unchanged so its position in the mosaic grid does not move."""
    return rotate(tile, angle=angle_deg, axes=(0, 1), reshape=False,
                  order=1, mode="constant", cval=0)


def feather_window(th, tw, fy, fx):
    """Separable tapered weight window: ~1.0 in the interior, ramping linearly to
    near 0 over fy/fx pixels at each edge. Used to feather-blend overlaps."""
    def taper(n, f):
        w = np.ones(n, dtype=np.float32)
        f = int(min(f, n // 2))
        if f > 0:
            ramp = (np.arange(f, dtype=np.float32) + 0.5) / f
            w[:f] = ramp
            w[-f:] = ramp[::-1]
        return w
    return np.outer(taper(th, fy), taper(tw, fx))


def build_mosaic(imgs, Nv, Nh, th, tw, nc, apply_rotation):
    """Assemble one mosaic according to MODE; optionally de-rotate each tile."""
    if MODE == "overlap":
        step_y = int(round(th * (1.0 - OVERLAP)))
        step_x = int(round(tw * (1.0 - OVERLAP)))
    elif MODE == "abut":
        step_y, step_x = th, tw
    else:
        raise ValueError(f"Unknown MODE {MODE!r} (use 'abut' or 'overlap')")

    H = (Nv - 1) * step_y + th
    W = (Nh - 1) * step_x + tw
    chan = () if nc is None else (nc,)

    feather = (MODE == "overlap" and FEATHER)
    if feather:
        acc = np.zeros((H, W) + chan, dtype=np.float32)
        wsum = np.zeros((H, W), dtype=np.float32)
        win = feather_window(th, tw, th - step_y, tw - step_x)
        win_b = win if nc is None else win[:, :, None]
    else:
        out = np.zeros((H, W) + chan, dtype=np.uint8)

    for r in range(Nv):
        for c in range(Nh):
            if nc is None:
                tile = imgs[0, r, c, :, :]
            else:
                tile = imgs[0, r, c, :, :, :]
            tile = tile[::-1, ::-1]
            if apply_rotation and ROTATION_CORRECTION_DEG:
                tile = rotate_tile(tile, ROTATION_CORRECTION_DEG)
            y, x = r * step_y, c * step_x
            if feather:
                acc[y:y+th, x:x+tw] += tile.astype(np.float32) * win_b
                wsum[y:y+th, x:x+tw] += win
            else:
                out[y:y+th, x:x+tw] = tile

    if feather:
        w = wsum if nc is None else wsum[:, :, None]
        np.divide(acc, w, out=acc, where=(w > 0))
        out = np.clip(acc, 0, 255).astype(np.uint8)
    return out


with h5py.File(H5_PATH, "r") as f:
    imgs = f["measurement/simple_tiled_image/live_img_map"]
    shape = imgs.shape
    print("shape:", shape)

    if len(shape) == 5:
        _, Nv, Nh, th, tw = shape
        nc = None
    elif len(shape) == 6:
        _, Nv, Nh, th, tw, nc = shape   # trailing color channel
    else:
        raise ValueError(f"Unexpected shape {shape} - tell me what this is")

    if MODE == "overlap":
        print(f"mode: overlap (overlap {OVERLAP:.0%}, feather={FEATHER})")
    else:
        print("mode: abut")

    layers = []
    if SHOW_RAW:
        print("building raw mosaic ...")
        layers.append(("mosaic",
                       build_mosaic(imgs, Nv, Nh, th, tw, nc, apply_rotation=False)))
    if SHOW_CORRECTED:
        print("building rotation-corrected mosaic ...")
        layers.append((f"mosaic (per-tile rot {ROTATION_CORRECTION_DEG:g} deg)",
                       build_mosaic(imgs, Nv, Nh, th, tw, nc, apply_rotation=True)))


is_rgb = nc is not None
viewer = napari.Viewer()
for name, img in layers:
    viewer.add_image(img, name=name, rgb=is_rgb)
napari.run()
