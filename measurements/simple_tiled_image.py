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
            # Thread-safe: pauses preview on the GUI thread and unchecks live_img.
            cap.set_live_preview(False)
        cam.start_video_capture()

        # Continuous-hold autofocus: enable the servo for the whole scan so the
        # PF850 keeps the sample in focus as the stage steps tile to tile.
        pf = self._get_purefocus()
        if pf is not None:
            pf.set_servo(True)

        # Record explicit, co-located scan geometry so downstream tools
        # (stitching, Crucible) don't cross-reference app/hardware groups or
        # hardcode anything.
        self._save_scan_metadata()

    def _save_scan_metadata(self):
        """Write explicit scan geometry as attrs on the measurement group.

        Everything here is otherwise recoverable from app/hardware settings, but
        co-locating it (and storing the derived per-image pixel size, which lives
        nowhere else) means the stitcher and the Crucible ingestor can read one
        group instead of recomputing. Stored as attrs because the Crucible H5
        ingestor harvests attrs (not dataset values) into scientific_metadata.
        """
        if not self.settings['save_h5'] or not hasattr(self, 'h5_meas_group'):
            return

        app = self.app
        a = self.h5_meas_group.attrs

        # Scan-planning inputs (app-level settings). overlap is a percent.
        a['overlap_frac'] = app.settings['overlap'] / 100.0
        a['panel_width_mm'] = app.settings['panel_width']
        a['panel_height_mm'] = app.settings['panel_height']

        # Stage raster bounds (mm) + grid shape, from this measurement's own
        # settings. These define the scan's stage bounding box, which the
        # stitcher turns into a pixel<->stage affine so the mosaic viewer can
        # place higher-mag mosaics as detail regions on a low-mag map.
        a['h0'] = self.settings['h0']
        a['h1'] = self.settings['h1']
        a['v0'] = self.settings['v0']
        a['v1'] = self.settings['v1']
        a['Nh'] = self.settings['Nh']
        a['Nv'] = self.settings['Nv']

        # Raw sensor pixel size (um) from the camera.
        pixel_size_um = app.hardware['zwo_camera'].settings['pixel_size_um']
        a['pixel_size_um'] = pixel_size_um

        # Objective magnification + derived per-image pixel size, when available.
        if 'prior_turret' in app.hardware:
            mag = app.hardware['prior_turret'].settings['magnification']
            if mag and mag > 0:
                a['magnification'] = mag
                a['pixel_size_um_effective'] = pixel_size_um / mag

    def post_scan_cleanup(self):
        cam = self.app.hardware['zwo_camera']
        cam.stop_video_capture()
        cap = self.app.measurements['zwo_camera_capture']
        if getattr(self, '_live_was_on', False):
            # Thread-safe: resumes preview on the GUI thread and rechecks live_img.
            cap.set_live_preview(True)

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
        # one exposed now that the stage is stationary. Orientation is corrected
        # here so downstream scripts don't need to flip tiles.
        live_img = cam.orient_frame(cam.capture_fresh_frame())

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
