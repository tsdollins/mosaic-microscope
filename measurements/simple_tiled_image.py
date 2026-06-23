import numpy as np
import os
import imageio
import time
from ScopeFoundryHW.HW_prior_stage.prior_stage_raster import PriorStage2DScan


class SimpleTiledImage(PriorStage2DScan):

    name = 'simple_tiled_image'

    def pre_scan_setup(self):
        cam = self.app.hardware['zwo_camera']
        cam.start_video_capture()

    def post_scan_cleanup(self):
        cam = self.app.hardware['zwo_camera']
        cam.stop_video_capture()

    def collect_pixel(self, pixel_num, k, j, i):
        time.sleep(0.75)  # allow stage to settle after move_position_fast

        cam = self.app.hardware['zwo_camera']
        live_img = cam.capture_video_frame()

        self.display_image_map[k, j, i] = live_img.sum()

        if pixel_num == 0:
            self.log.info("pixel 0: creating data arrays")
            live_img_map_shape = self.scan_shape + live_img.shape
            self.live_img_map = np.zeros(live_img_map_shape, dtype=np.uint8)
            if self.settings['save_h5']:
                self.live_img_map_h5 = self.h5_meas_group.create_dataset(
                    'live_img_map', shape=live_img_map_shape, dtype=np.uint8)
                self.img_dir = self.h5_filename + "_images"
                os.makedirs(self.img_dir, exist_ok=True)

        self.live_img_map[k, j, i] = live_img

        if self.settings['save_h5']:
            self.live_img_map_h5[k, j, i, ...] = live_img
            imageio.imsave(
                os.path.join(self.img_dir, f"thumb_{k}_{j}_{i}.jpg"), live_img)

        print("acquired", pixel_num, k, j, i)
