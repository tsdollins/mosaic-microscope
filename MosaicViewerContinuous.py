import h5py
import numpy as np
import napari

H5_PATH = r"C:\Users\lab\Documents\User_Images\Default\260618_104636_continuous_motion_image.h5"

with h5py.File(H5_PATH, "r") as f:
    imgs = f["measurement/continuous_motion_image/live_img_map"]
    coords = f["measurement/continuous_motion_image/coords"][:]  # (N, 2) -> (x, y) in mm
    stack = imgs[:]
    print("stack:", stack.shape, "coords:", coords.shape)

N = stack.shape[0]
th, tw = stack.shape[1], stack.shape[2]
is_rgb = (stack.ndim == 4 and stack.shape[-1] in (3, 4))
nc = stack.shape[-1] if is_rgb else None

# Calibration: pixels per millimeter (stage positions assumed in mm)
PX_PER_MM_X = 4944 / 3.431    # 1441.0
PX_PER_MM_Y = 3284 / 2.3031   # 1426.0

xs = coords[:, 0]
ys = coords[:, 1]

px = ((xs - xs.min()) * PX_PER_MM_X).round().astype(int)
# +y is up, but image row 0 is the top, so map max-y to the top (py=0).
py = ((ys.max() - ys) * PX_PER_MM_Y).round().astype(int)

H = py.max() + th
W = px.max() + tw

if nc is None:
    mosaic = np.zeros((H, W), dtype=np.uint8)
else:
    mosaic = np.zeros((H, W, nc), dtype=np.uint8)

print(f"Mosaic will be {H} x {W}"
      f"{'' if nc is None else ' x ' + str(nc)}"
      f" = {H*W/1e9:.4f} gigapixels")

for i in range(N):
    # Orientation flip is now baked into the saved h5 data.
    if nc is None:
        tile = stack[i]
    else:
        tile = stack[i]
    y = py[i]
    x = px[i]
    mosaic[y:y+th, x:x+tw] = tile

viewer = napari.Viewer()
viewer.add_image(mosaic, name="mosaic", rgb=(nc is not None))
napari.run()