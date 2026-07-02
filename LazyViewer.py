import napari
import tifffile

OUT_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\mosaicFull.ome.tif"

with tifffile.TiffFile(OUT_PATH) as tif:
    series = tif.series[0]
    # build a list of arrays, one per pyramid level
    pyramid = [level.asarray() for level in series.levels]

viewer = napari.Viewer()
viewer.add_image(pyramid, multiscale=True)
napari.run()
