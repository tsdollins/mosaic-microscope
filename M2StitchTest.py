import h5py
import numpy as np
import m2stitch


def load_tiles(h5path):
    """Load the ScopeFoundry tiled image into a (N, H, W) stack + grid indices."""
    with h5py.File(h5path, "r") as f:
        m = f["measurement/simple_tiled_image"]
        img_map = m["live_img_map"][0]          # (Nv, Nh, H, W), frame 0
        Nv, Nh, H, W = img_map.shape

        images, rows, cols = [], [], []
        for v in range(Nv):
            for h in range(Nh):
                images.append(img_map[v, h])
                rows.append(v)
                cols.append(h)

    images = np.array(images)                   # (Nv*Nh, H, W)
    rows = np.array(rows)
    cols = np.array(cols)
    return images, rows, cols, (Nv, Nh, H, W)


def stitch(images, rows, cols, overlap=0.15):
    th, tw = images.shape[1], images.shape[2]   # 3284, 4944
    step_y = th * (1 - overlap)
    step_x = tw * (1 - overlap)

    # (N, 2) initial guess in pixels, (y, x) per docstring's row/col -> dim order
    guess = np.array(
        [[r * step_y, c * step_x] for r, c in zip(rows, cols)],
        dtype=float,
    )

    result_df, prop = m2stitch.stitch_images(
        images,
        rows,
        cols,
        position_initial_guess=guess,
        row_col_transpose=False,        # use rows/cols as I defined them
        overlap_diff_threshold=10,      # allow +/-10% of image size from guess
        ncc_threshold=0.3,              # lower than default 0.5; loosen if needed
    )

    # Correct column names are x_pos / y_pos
    result_df["y_pos"] -= result_df["y_pos"].min()
    result_df["x_pos"] -= result_df["x_pos"].min()
    return result_df, prop


def feather_weights(h, w):
    wy = np.minimum(np.arange(h), np.arange(h)[::-1]) + 1.0
    wx = np.minimum(np.arange(w), np.arange(w)[::-1]) + 1.0
    return np.outer(wy, wx).astype(np.float32)


def composite(images, result_df, out_dtype=np.uint8):
    ys = result_df["y_pos"].to_numpy()
    xs = result_df["x_pos"].to_numpy()
    n, th, tw = images.shape

    H = int(round(ys.max())) + th
    W = int(round(xs.max())) + tw

    acc = np.zeros((H, W), np.float32)
    wsum = np.zeros((H, W), np.float32)
    base_w = feather_weights(th, tw)

    for i in range(n):
        y, x = int(round(ys[i])), int(round(xs[i]))
        tile = images[i].astype(np.float32)
        acc[y:y+th, x:x+tw] += tile * base_w
        wsum[y:y+th, x:x+tw] += base_w

    wsum[wsum == 0] = 1.0
    return (acc / wsum).astype(out_dtype)


if __name__ == "__main__":
    h5path = r"C:\Users\lab\Documents\User_Images\Default\260622_104313_simple_tiled_image_flat.h5"

    images, rows, cols, (Nv, Nh, H, W) = load_tiles(h5path)
    #result_df, prop = stitch(images, rows, cols, overlap=0.15)
    #print(result_df[["row", "col", "y_pos", "x_pos"]].to_string())
    #mosaic = composite(images, result_df)
    #print("Mosaic shape:", mosaic.shape)

    #import napari
    #napari.view_image(mosaic)
    #napari.run()
    with h5py.File(h5path, "r") as f:
        s = f["measurement/simple_tiled_image/settings"]
        print("dh, dv (mm):", s.attrs["dh"], s.attrs["dv"])
        print("Nh, Nv:", s.attrs["Nh"], s.attrs["Nv"])
    import matplotlib.pyplot as plt
    print("dtype:", images.dtype, "shape:", images.shape)
    for i in [0, 1, 7, 8]:
        t = images[i]
        print(f"tile {i}: min={t.min()} max={t.max()} mean={t.mean():.1f} std={t.std():.1f}")
    plt.imshow(images[0], cmap="gray"); plt.title("tile 0"); plt.colorbar(); plt.show()
    import numpy as np
    from skimage.registration import phase_cross_correlation

    # Your measured FOV and the file's step
    fov_x_mm, fov_y_mm = 3.4310, 2.3031
    dh, dv = 3.2594, 2.1879
    W, H = 4944, 3284

    px_x = fov_x_mm / W          # mm/px
    px_y = fov_y_mm / H
    overlap_x_px = int(round((fov_x_mm - dh) / px_x))   # ~247 px
    overlap_y_px = int(round((fov_y_mm - dv) / px_y))   # ~164 px
    print("overlap px (x, y):", overlap_x_px, overlap_y_px)

    t0 = images[0].astype(np.float32)   # row 0, col 0
    t1 = images[1].astype(np.float32)   # row 0, col 1 (horizontal neighbor)

    ov = overlap_x_px
    strip0 = t0[:, -ov:]
    strip1 = t1[:, :ov]
    shift, error, _ = phase_cross_correlation(strip0, strip1)
    print("horizontal pair shift:", shift, "error:", error)