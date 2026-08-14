import napari
import tifffile

OUT_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260724_112339_simple_tiled_image_mosaic.tif"


def load_pyramid(path):
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if len(series.levels) > 1:
            return [level.asarray() for level in series.levels]
        pages = [page.asarray() for page in tif.pages]
        return sorted(pages, key=lambda a: a.shape[0] * a.shape[1], reverse=True)


pyramid = load_pyramid(OUT_PATH)

viewer = napari.Viewer()
if len(pyramid) > 1:
    viewer.add_image(pyramid, multiscale=True)
else:
    viewer.add_image(pyramid[0])
napari.run()
