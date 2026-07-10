import numpy as np
import os
import imageio
import time
import traceback
from ScopeFoundry.measurement import Measurement
from qtpy import QtWidgets
import pyqtgraph as pg


class Timelapse(Measurement):
    """Grab a frame from the camera buffer once per ``interval`` until ``length``
    seconds have elapsed.

    A plain (non-raster) measurement: the stage is not moved. Like the tiled
    scans it pauses the live preview so the timelapse is the only camera
    consumer, then pulls frames from the running video stream. Each frame is
    orientation-corrected (see ``ZWOCameraHW.orient_frame``) so the saved data
    matches the live view and downstream scripts.
    """

    name = 'timelapse'

    def setup(self):
        S = self.settings
        S.New('interval', dtype=float, initial=10.0, vmin=0.0, unit='s',
              description='Time between successive captures')
        S.New('length', dtype=float, initial=60.0, vmin=0.0, unit='s',
              description='Total duration of the timelapse')
        S.New('save_h5', dtype=bool, initial=True,
              description='Save frames to an HDF5 file (plus a jpg per frame)')

    def setup_figure(self):
        self.ui = QtWidgets.QWidget()
        self.ui_layout = QtWidgets.QGridLayout()
        self.ui.setLayout(self.ui_layout)

        self.ui_settings = self.settings.New_UI()
        self.ui_layout.addWidget(self.ui_settings, 0, 0)

        self.start_button = QtWidgets.QPushButton("Start")
        self.ui_layout.addWidget(self.start_button, 1, 0)
        self.start_button.clicked.connect(self.start)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.ui_layout.addWidget(self.stop_button, 2, 0)
        self.stop_button.clicked.connect(self.interrupt)

        self.graph_layout = pg.GraphicsLayoutWidget()
        self.ui_layout.addWidget(self.graph_layout, 0, 1, 3, 1)
        self.ui_layout.setColumnStretch(1, 1)

        self.plot = self.graph_layout.addPlot()
        self.plot.setAspectLocked(lock=True, ratio=1)
        # invertY so row 0 is at the top, matching the live preview / saved data.
        self.plot.invertY(True)
        self.img_item = pg.ImageItem(axisOrder='row-major')
        self.plot.addItem(self.img_item)

        self.display_frame = None

    def pre_scan_setup(self):
        cam = self.app.hardware['zwo_camera']
        # Pause live preview so the timelapse is the only camera consumer (avoids
        # two threads pulling frames from the same video stream).
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

    def run(self):
        S = self.settings
        cam = self.app.hardware['zwo_camera']

        interval = S['interval']
        length = S['length']
        # Number of captures: one at t=0, then one per interval up to length.
        if interval > 0:
            n_frames = int(length / interval) + 1
        else:
            n_frames = 1

        self.pre_scan_setup()
        try:
            if S['save_h5']:
                self.open_new_h5_file()
                self.h5_filename = self.h5_file.filename
                self.h5_meas_group['interval'] = interval
                self.h5_meas_group['length'] = length
                self.img_dir = self.h5_filename + "_images"
                os.makedirs(self.img_dir, exist_ok=True)

            self.frame_times = []
            frame_i = 0
            t0 = time.perf_counter()
            next_t = t0

            while not self.interrupt_measurement_called and frame_i < n_frames:
                # Pull the most recent frame from the camera buffer. Orientation
                # is corrected here so saved data matches the live view.
                frame = cam.orient_frame(cam.capture_fresh_frame())
                t_capture = time.perf_counter() - t0
                self.frame_times.append(t_capture)

                # Hand the newest frame to the display (drawn in update_display).
                self.display_frame = frame

                if frame_i == 0 and S['save_h5']:
                    self.log.info("frame 0: creating data arrays")
                    img_shape = frame.shape
                    self.frame_stack_h5 = self.h5_meas_group.create_dataset(
                        'frame_stack',
                        shape=(0,) + img_shape,
                        maxshape=(None,) + img_shape,
                        dtype=np.uint8,
                        chunks=(1,) + img_shape,
                    )
                    self.frame_time_h5 = self.h5_meas_group.create_dataset(
                        'frame_time',
                        shape=(0,),
                        maxshape=(None,),
                        dtype=np.float64,
                    )

                if S['save_h5']:
                    n = self.frame_stack_h5.shape[0]
                    self.frame_stack_h5.resize(n + 1, axis=0)
                    self.frame_stack_h5[n] = frame
                    self.frame_time_h5.resize(n + 1, axis=0)
                    self.frame_time_h5[n] = t_capture
                    imageio.imsave(
                        os.path.join(self.img_dir, f"frame_{frame_i:05d}.jpg"),
                        frame)

                print("timelapse captured frame", frame_i, f"t={t_capture:.2f}s")
                frame_i += 1
                self.set_progress(100.0 * frame_i / n_frames)

                if frame_i >= n_frames:
                    break

                # Sleep until the next scheduled capture, but wake often enough
                # to notice an interrupt request promptly.
                next_t += interval
                while not self.interrupt_measurement_called:
                    remaining = next_t - time.perf_counter()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.1))

        except Exception as err:
            self.last_err = err
            self.log.error("Timelapse failed: {}".format(repr(err)))
            traceback.print_exc()
        finally:
            self.post_scan_cleanup()
            if S['save_h5']:
                self.close_h5_file()
        print(self.name, "done")

    def update_display(self):
        if self.display_frame is None:
            return
        im = self.display_frame
        # Some color cameras deliver BGR; swap to RGB for display.
        if im.ndim == 3 and im.shape[-1] == 3:
            im = np.ascontiguousarray(im[:, :, ::-1])
        self.img_item.setImage(image=im, autoLevels=False)
