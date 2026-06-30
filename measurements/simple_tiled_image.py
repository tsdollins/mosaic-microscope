import numpy as np
import os
import imageio
import time
from ScopeFoundryHW.HW_prior_stage.prior_stage_raster import PriorStage2DScan


class SimpleTiledImage(PriorStage2DScan):

    name = 'simple_tiled_image'
    # 2.288 x 1.35

    def setup(self):
        PriorStage2DScan.setup(self)
        # PureFocus850 autofocus options (all opt-in; safe if PF850 absent).
        self.settings.New("use_autofocus", dtype=bool, initial=False,
                          description="Hold focus with the PureFocus850 servo "
                                      "during the scan")
        self.settings.New("autofocus_per_tile", dtype=bool, initial=False,
                          description="At each tile, wait for the PF850 FOCUS "
                                      "flag before capturing")
        self.settings.New("autofocus_timeout", dtype=float, initial=2.0,
                          unit="s", description="Max wait for in-focus per tile")

    def _get_purefocus(self):
        """Return the connected PureFocus850 HW, or None if unavailable."""
        if not self.settings["use_autofocus"]:
            return None
        if "prior_purefocus" not in self.app.hardware:
            return None
        pf = self.app.hardware["prior_purefocus"]
        return pf if pf.settings["connected"] else None

    def pre_scan_setup(self):
        cam = self.app.hardware['zwo_camera']
        # Pause live preview so the scan is the only camera consumer (avoids two
        # threads pulling frames from the same video stream).
        cap = self.app.measurements['zwo_camera_capture']
        self._live_was_on = cap.settings['live_img']
        if self._live_was_on:
            cap._stop_live_acquisition()
        cam.start_video_capture()

        # Continuous-hold autofocus: enable the servo for the whole scan so the
        # PF850 keeps the sample in focus as the stage steps tile to tile.
        pf = self._get_purefocus()
        if pf is not None:
            pf.set_servo(True)

    def post_scan_cleanup(self):
        cam = self.app.hardware['zwo_camera']
        cam.stop_video_capture()
        cap = self.app.measurements['zwo_camera_capture']
        if getattr(self, '_live_was_on', False):
            cap._start_live_acquisition()

        pf = self._get_purefocus()
        if pf is not None:
            pf.set_servo(False)

    def collect_pixel(self, pixel_num, k, j, i):
        # Block until the stage has finished moving so the frame is captured
        # while the stage is stationary (no motion during acquisition).
        while self.stage.is_busy_xy():
            time.sleep(0.01)
        time.sleep(0.5)  # allow mechanical vibration to settle after motion stops

        # Optional per-tile focus confirmation: wait for the PF850 to report
        # in-focus (servo is already running from pre_scan_setup) before grabbing.
        pf = self._get_purefocus()
        if pf is not None and self.settings['autofocus_per_tile']:
            t0 = time.time()
            while not pf.is_in_focus():
                if time.time() - t0 > self.settings['autofocus_timeout']:
                    print(f"warning: PF850 not in focus within timeout at "
                          f"{k},{j},{i}")
                    break
                time.sleep(0.02)

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
