import h5py
import napari
r"""
H5_PATH = r"C:\Users\lab\Documents\User_Images\Default\d260610_143025_simple_tiled_image.h5"

f = h5py.File(H5_PATH, "r")
imgs = f["measurement/simple_tiled_image/live_img_map"]

viewer = napari.Viewer()
viewer.add_image(imgs, name="tiles")
napari.run()
"""

import napari
import tifffile

OUT_PATH = r"C:\Users\lab\Documents\User_Images\Default\mosaic.ome.tif"

with tifffile.TiffFile(OUT_PATH) as tif:
    series = tif.series[0]
    # build a list of arrays, one per pyramid level
    pyramid = [level.asarray() for level in series.levels]

viewer = napari.Viewer()
viewer.add_image(pyramid, multiscale=True)
napari.run()
