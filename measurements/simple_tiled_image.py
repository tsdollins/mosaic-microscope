import numpy as np
import os
import imageio
import time
from ScopeFoundryHW.HW_prior_stage.prior_stage_raster import PriorStage2DScan


class SimpleTiledImage(PriorStage2DScan):

    name = 'simple_tiled_image'
    # 2.288 x 1.35
    def pre_scan_setup(self):
        cam = self.app.hardware['zwo_camera']
        # Pause live preview so the scan is the only camera consumer (avoids two
        # threads pulling frames from the same video stream).
        cap = self.app.measurements['zwo_camera_capture']
        self._live_was_on = cap.settings['live_img']
        if self._live_was_on:
            cap._stop_live_acquisition()
        cam.start_video_capture()

    def post_scan_cleanup(self):
        cam = self.app.hardware['zwo_camera']
        cam.stop_video_capture()
        cap = self.app.measurements['zwo_camera_capture']
        if getattr(self, '_live_was_on', False):
            cap._start_live_acquisition()

    def collect_pixel(self, pixel_num, k, j, i):
        # Block until the stage has finished moving so the frame is captured
        # while the stage is stationary (no motion during acquisition).
        while self.stage.is_busy_xy():
            time.sleep(0.01)
        time.sleep(0.5)  # allow mechanical vibration to settle after motion stops

        cam = self.app.hardware['zwo_camera']
        # Discard frames exposed while the stage was moving, then grab a fresh
        # one exposed now that the stage is stationary.
        live_img = cam.capture_fresh_frame()

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
