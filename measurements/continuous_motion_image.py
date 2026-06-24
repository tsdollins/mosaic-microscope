import numpy as np
import os
import imageio
import time
import traceback
from ScopeFoundryHW.HW_prior_stage.prior_stage_raster import PriorStage2DScan


class ContinuousMotionImage(PriorStage2DScan):

    name = 'continuous_motion_image'

    def pre_scan_setup(self):
        cam = self.app.hardware['zwo_camera']
        cam.start_video_capture()

    def post_scan_cleanup(self):
        cam = self.app.hardware['zwo_camera']
        cam.stop_video_capture()

    def collect_pixel(self, pixel_num, dh):
        cam = self.app.hardware['zwo_camera']

        x = self.stage.settings.x_position.read_from_hardware()
        y = self.stage.settings.y_position.read_from_hardware()

        live_img = cam.capture_video_frame()

        latency = -0.25   # seconds — measure yours precisely from the two-speed data
        speed = self.stage.settings['speed_xy']
        if dh > 0:
            direction = 1   # +1 when moving in increasing direction, -1 otherwise
        else:
            direction = -1

        x = x + direction * speed * latency

        if pixel_num == 0:
            self.log.info("pixel 0: creating data arrays")
            print("pixel 0: creating data arrays")

            img_shape = live_img.shape
            if self.settings['save_h5']:
                self.live_img_map_h5 = self.h5_meas_group.create_dataset(
                    'live_img_map',
                    shape=(0,) + img_shape,
                    maxshape=(None,) + img_shape,
                    dtype=np.uint8,
                    chunks=(1,) + img_shape,
                )
                self.coords_h5 = self.h5_meas_group.create_dataset(
                    'coords',
                    shape=(0, 2),
                    maxshape=(None, 2),
                    dtype=np.float64,
                )

                self.img_dir = self.h5_filename + "_images"
                os.makedirs(self.img_dir, exist_ok=True)

        if self.settings['save_h5']:
            n = self.live_img_map_h5.shape[0]
            self.live_img_map_h5.resize(n + 1, axis=0)
            self.live_img_map_h5[n] = live_img

            self.coords_h5.resize(n + 1, axis=0)
            self.coords_h5[n] = (x, y)
            imageio.imsave(os.path.join(self.img_dir, f"thumb_{pixel_num}_{x:.2f}_{y:.2f}.jpg"), live_img)

            print("acquiring", pixel_num)

    def run(self):
        S = self.settings

        # Compute data arrays
        self.compute_scan_arrays()

        self.initial_scan_setup_plotting = True

        # Fill display image with nan
        # this allows for pyqtgraph histogram to ignore unfilled data
        # pyqtgraph ImageItem also keeps unfilled data pixels transparent
        self.display_image_map = np.nan * np.zeros(self.scan_shape, dtype=float)

        while not self.interrupt_measurement_called:
            try:
                # h5 data file setup
                self.t0 = time.time()

                if self.settings["save_h5"]:
                    H = self.open_new_h5_file()
                    self.h5_filename = self.h5_file.filename

                    # create h5 data arrays
                    H["h_array"] = self.h_array
                    H["v_array"] = self.v_array
                    H["range_extent"] = self.range_extent
                    H["corners"] = self.corners
                    H["imshow_extent"] = self.imshow_extent
                    H["scan_h_positions"] = self.scan_h_positions
                    H["scan_v_positions"] = self.scan_v_positions
                    H["scan_slow_move"] = self.scan_slow_move
                    H["scan_index_array"] = self.scan_index_array

                # start scan
                self.pixel_i = 0
                self.row_i = 0
                self.current_scan_index = self.scan_index_array[0]

                self.pixel_time = np.zeros(self.scan_shape, dtype=float)
                if self.settings["save_h5"]:
                    self.pixel_time_h5 = H.create_dataset(
                        name="pixel_time", shape=self.scan_shape, dtype=float
                    )

                self.pre_scan_setup()

                self.move_position_start(
                    self.scan_h_positions[0], self.scan_v_positions[0]
                )

                for self.row_i in range(self.Nv.val):
                    if self.interrupt_measurement_called:
                        break

                    r = self.row_i
                    first = r*self.Nh.val
                    last = r*self.Nh.val - 1 + self.Nh.val

                    self.current_scan_index = self.scan_index_array[last]
                    kk, jj, ii = self.current_scan_index

                    h, v = self.scan_h_positions[last], self.scan_v_positions[last]

                    if self.pixel_i == 0:
                        dh = self.scan_h_positions[last] - self.scan_h_positions[first]
                        dv = 0
                    else:
                        dh = self.scan_h_positions[last] - self.scan_h_positions[first]
                        dv = self.scan_v_positions[last] - self.scan_v_positions[first]

                    self.move_position_slow(self.scan_h_positions[first], self.scan_v_positions[first], dh, dv)

                    # begin the continuous sweep toward the end of the row
                    self.stage.settings["x_target"] = h

                    period = abs(dh) / self.Nh.val / self.stage.settings['speed_xy']
                    next_t = time.perf_counter()
                    while self.stage.is_busy_xy():
                        self.collect_pixel(self.pixel_i, dh)
                        self.pixel_i += 1

                        next_t += period
                        sleep_for = next_t - time.perf_counter()
                        if sleep_for > 0:
                            time.sleep(sleep_for)
                        else:
                            # we fell behind — collect_pixel took longer than the budget
                            next_t = time.perf_counter()
                    self.pos = (h, v)
                    self.set_progress(100.0 * self.row_i / (self.Nv.val))

            except Exception as err:
                self.last_err = err
                self.log.error("Failed to Scan {}".format(repr(err)))
                traceback.print_exc()
                # raise(err)
            finally:
                self.post_scan_cleanup()
                if hasattr(self, "h5_file"):
                    print("h5_file", self.h5_file)
                    try:
                        self.h5_file.close()
                    except ValueError as err:
                        self.log.warning("failed to close h5_file: {}".format(err))
                if not self.settings["continuous_scan"]:
                    break
        print(self.name, "done")
