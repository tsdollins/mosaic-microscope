import h5py
import numpy as np
import napari

H5_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260624_130607_simple_tiled_image.h5"
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

    step_frac = 1.0
    step_y = int(th * step_frac)
    step_x = int(tw * step_frac)

    H = (Nv - 1) * step_y + th
    W = (Nh - 1) * step_x + tw

    if nc is None:
        mosaic = np.zeros((H, W), dtype=np.uint8)
    else:
        mosaic = np.zeros((H, W, nc), dtype=np.uint8)

    print(f"Mosaic will be {H} x {W}"
          f"{'' if nc is None else ' x ' + str(nc)}"
          f" = {H*W/1e9:.2f} gigapixels")

    for r in range(Nv):
        for c in range(Nh):
            if nc is None:
                tile = imgs[0, r, c, :, :]
                tile = tile[:, ::-1]  
            else:
                tile = imgs[0, r, c, :, :, :]
                tile = tile[:, ::-1]  
            y = r * step_y
            x = c * step_x
            mosaic[y:y+th, x:x+tw] = tile

viewer = napari.Viewer()
if nc is None:
    viewer.add_image(mosaic, name="mosaic")
else:
    viewer.add_image(mosaic, name="mosaic", rgb=True)   # tells napari it's RGB
napari.run()