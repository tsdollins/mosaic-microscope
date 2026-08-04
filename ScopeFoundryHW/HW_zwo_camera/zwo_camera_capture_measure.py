from ScopeFoundry.measurement import Measurement
from qtpy import QtCore, QtWidgets
import pyqtgraph as pg
from ScopeFoundry import h5_io
import os
import imageio
import numpy as np
import threading


class _CameraAcquisitionThread(QtCore.QThread):
    """Grabs camera frames off the GUI thread.

    The blocking ``capture_video_frame`` call runs here, so a slow/stalled frame
    can never freeze the UI. Each grab is handed to the GUI thread for display via
    the ``frame_ready`` signal using a drop-to-latest scheme (only the newest
    frame is ever drawn, so display can't fall behind acquisition).
    """

    frame_ready = QtCore.Signal()

    def __init__(self, measure):
        super().__init__()
        self.measure = measure
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        m = self.measure
        cam = m.app.hardware['zwo_camera']
        while not self._stop.is_set():
            try:
                # moderate timeout so a stalled stream releases the camera lock
                # and lets us back off, rather than blocking indefinitely
                frame = cam.capture_video_frame(timeout=2000)
            except Exception:
                # camera not ready / stream stalled -> back off briefly and retry
                if self._stop.wait(0.1):
                    break
                continue

            with m._frame_lock:
                m._latest_frame = frame
                emit = not m._display_pending
                m._display_pending = True
            if emit:
                self.frame_ready.emit()

            # throttle to the configured preview period; the camera lock is FREE
            # during this sleep so GUI control reads don't wait on us
            period_s = max(0.0, m.settings['live_update_period'] / 1000.0)
            if self._stop.wait(period_s):
                break


class _LivePreviewInvoker(QtCore.QObject):
    """Marshals live-preview start/stop onto the GUI thread.

    The preview acquisition QThread must be created/torn down -- and pyqtgraph
    touched -- on the GUI thread. Scans run on a measurement thread, so starting
    the acquisition thread from there does not actually deliver frames to the
    display (the live view fails to resume after a scan). Scans instead call
    ZWOCameraCaptureMeasure.set_live_preview(), which drives these slots via a
    BlockingQueuedConnection so the work runs on the GUI thread.
    """

    def __init__(self, measure):
        super().__init__()
        self.measure = measure

    @QtCore.Slot()
    def start(self):
        self.measure._apply_live_preview(True)

    @QtCore.Slot()
    def stop(self):
        self.measure._apply_live_preview(False)


class ZWOCameraCaptureMeasure(Measurement):

    name = 'zwo_camera_capture'

    def setup(self):
        self.settings.New('live_img', dtype=bool)
        self.settings.New('live_update_period', dtype=int, initial=500, unit='ms',
                          vmin=100, vmax=10000,
                          description='Interval between live preview frame captures')
        self.settings.New('px_bin', dtype=int, initial=1, choices=(1,2,4,8,16,32),
                          description='Software display downsample (does not reduce USB transfer)')

        self.add_operation('clear_and_plot', self.clear_and_plot)
        self.add_operation('snap_and_save', self.snap_and_save)
        self.settings.live_img.add_listener(self.on_toggle_live_img)
        # Guards against re-entering on_toggle_live_img when _apply_live_preview
        # programmatically syncs the live_img checkbox.
        self._suppress_toggle = False

        # Live-preview acquisition thread + drop-to-latest frame handoff
        self._acq_thread = None
        self._latest_frame = None
        self._display_pending = False
        self._frame_lock = threading.Lock()

        # GUI-thread invoker: lets scans (running on a measurement thread) pause
        # and resume the live preview via set_live_preview() without creating the
        # acquisition QThread on the wrong thread. Created here in setup(), which
        # runs on the GUI thread, so it carries GUI-thread affinity.
        self._gui_invoker = _LivePreviewInvoker(self)

        # Stop the acquisition thread if the camera is disconnected out from under us
        self.app.hardware['zwo_camera'].settings.connected.add_listener(
            self._on_camera_connection_changed)



    def setup_figure(self):

        self.ui = QtWidgets.QWidget()
        self.ui_layout = QtWidgets.QGridLayout()
        self.ui.setLayout(self.ui_layout)

        self.ui_settings = self.settings.New_UI()
        self.ui_layout.addWidget(self.ui_settings, 0,0)
        self.ui_cam_settings= self.app.hardware['zwo_camera'].settings.New_UI()
        self.ui_layout.addWidget(self.ui_cam_settings,1,0)

        snap_button = QtWidgets.QPushButton("Snap")
        self.ui_layout.addWidget(snap_button)
        snap_button.clicked.connect(self.snap_and_save)



        self.graph_layout = pg.GraphicsLayoutWidget()
        self.graph_layout.clear()
        self.ui_layout.addWidget(self.graph_layout, 0,1,2,1)

        # Live preview is driven by the acquisition thread (see on_toggle_live_img),
        # NOT a GUI-thread timer -- the GUI never blocks on a frame grab.
        self.ui_layout.setColumnStretch(1, 1)
        self.clear_and_plot()

    def on_toggle_live_img(self):
        # Checkbox-driven path (already on the GUI thread). Skip when we are
        # programmatically syncing the checkbox from _apply_live_preview.
        if self._suppress_toggle:
            return
        self._apply_live_preview(self.settings['live_img'])

    def set_live_preview(self, enabled):
        """Thread-safe entry point to turn the live preview on/off.

        Keeps the live_img checkbox in sync and guarantees the acquisition
        thread + pyqtgraph work happen on the GUI thread. Call this from scans
        (measurement thread) instead of _start/_stop_live_acquisition: it
        marshals to the GUI thread and blocks until the change is applied, so the
        preview reliably resumes after a scan and the checkbox reflects reality.
        """
        if QtCore.QThread.currentThread() is self._gui_invoker.thread():
            self._apply_live_preview(enabled)
        else:
            QtCore.QMetaObject.invokeMethod(
                self._gui_invoker,
                "start" if enabled else "stop",
                QtCore.Qt.ConnectionType.BlockingQueuedConnection)

    def _apply_live_preview(self, enabled):
        """GUI-thread worker: start/stop acquisition and sync the checkbox.

        Must run on the GUI thread -- use set_live_preview() from other threads.
        """
        if enabled:
            self._start_live_acquisition()
        else:
            self._stop_live_acquisition()
        # Keep the checkbox consistent with the actual state, without recursing
        # back into on_toggle_live_img (which would re-run start/stop).
        if bool(self.settings['live_img']) != bool(enabled):
            self._suppress_toggle = True
            try:
                self.settings['live_img'] = enabled
            finally:
                self._suppress_toggle = False

    def _start_live_acquisition(self):
        """Begin video capture and spin up the off-GUI-thread frame grabber."""
        cam = self.app.hardware['zwo_camera']
        if not hasattr(cam, 'camera'):
            return
        if self._acq_thread is not None and self._acq_thread.isRunning():
            return
        cam.start_video_capture()
        self._acq_thread = _CameraAcquisitionThread(self)
        self._acq_thread.frame_ready.connect(self.on_frame_ready)
        self._acq_thread.start()

    def _stop_live_acquisition(self):
        """Stop the frame grabber (join it), then stop video capture."""
        t = self._acq_thread
        self._acq_thread = None
        if t is not None:
            t.stop()
            t.wait(3000)  # join, up to 3 s
            try:
                t.frame_ready.disconnect(self.on_frame_ready)
            except Exception:
                pass
        cam = self.app.hardware['zwo_camera']
        if hasattr(cam, 'camera'):
            try:
                cam.stop_video_capture()
            except Exception:
                pass

        # Reset the drop-to-latest handshake so a resumed preview isn't wedged by
        # a stale _display_pending=True (which would make the new acquisition
        # thread never emit frame_ready -> display never refreshes).
        with self._frame_lock:
            self._latest_frame = None
            self._display_pending = False

    def _on_camera_connection_changed(self):
        """If the camera disconnects, tear down the grabber thread first so it
        never calls into a closing/closed camera."""
        connected = self.app.hardware['zwo_camera'].settings['connected']
        if not connected and self._acq_thread is not None:
            t = self._acq_thread
            self._acq_thread = None
            t.stop()
            t.wait(3000)

    def on_frame_ready(self):
        """GUI-thread slot: draw the most recent frame (drop-to-latest)."""
        if not hasattr(self, 'live_img_item'):
            return
        with self._frame_lock:
            im = self._latest_frame
            self._display_pending = False
        if im is None:
            return

        # Same normalization that is baked into the saved data (orientation flips
        # + BGR->RGB), so the live view matches what gets stored/stitched.
        im = self.app.hardware['zwo_camera'].orient_frame(im)

        stride = self.settings['px_bin']
        if stride > 1:
            im = im[::stride, ::stride]

        self.live_img_item.setImage(image=im, autoLevels=False)
        scale = 1
        center_x = 50
        center_y = 50
        im_aspect = im.shape[1] / im.shape[0]
        self.img_rect = pg.QtCore.QRectF(0 - center_x * scale / 100,
                            0 - center_y * scale * im_aspect / 100,
                            scale,
                            scale * im_aspect)
        self.live_img_item.setRect(self.img_rect)


    def clear_and_plot(self):
        #scale = self.settings['scale'] # m/V

        self.graph_layout.clear()

        self.xy_plot = self.graph_layout.addPlot(0,0)
        self.xy_plot.setAspectLocked(lock=True, ratio=1)
        # invertY so row 0 is drawn at the TOP like a normal image viewer.
        # Without this, pyqtgraph draws with the y-axis pointing up (row 0 at
        # the bottom), i.e. a vertical flip vs saved files -- which is why a
        # flip_v that looked right live came out doubly-flipped in saved scans.
        self.xy_plot.invertY(True)
        #self.xy_plot.setLabels(left=('mcl y', 'm'), bottom=('mcl x', 'm'))
        # row-major so a numpy [row, col] frame is displayed faithfully (default
        # col-major transposes it, which made stage +x appear to move the view
        # up -- a 90 deg mismatch with stage motion).
        self.live_img_item = pg.ImageItem(axisOrder='row-major')
        self.xy_plot.addItem(self.live_img_item)

        self.xy_plot.addItem(pg.InfiniteLine(angle=0))
        self.xy_plot.addItem(pg.InfiniteLine(angle=90))




    def snap_and_save(self):
        print("snap_and_save")
        cam = self.app.hardware['zwo_camera']

        # Pause live preview so this snap is the only camera consumer.
        was_live = self.settings['live_img']
        if was_live:
            self._stop_live_acquisition()

        try:
            cam.start_video_capture()

            print("creating h5")
            self.h5_file = h5_io.h5_base_file(self.app, measurement=self)
            self.h5_filename = self.h5_file.filename
            print(self.h5_filename)
            self.h5_m = h5_io.h5_create_measurement_group(measurement=self, h5group=self.h5_file)

            print("capture frame")
            new_img = cam.orient_frame(cam.capture_video_frame())

            print("save jpg")
            imageio.imsave(self.h5_filename +".jpg", new_img, quality=100)
            print("save tif")
            imageio.imsave(self.h5_filename +".tif", new_img)
            print("save h5")
            self.h5_m['img'] = new_img

        finally:
            try:
                self.h5_file.close()
            except Exception:
                pass
            # Resume live preview if it was running, else leave the camera idle.
            if was_live:
                self._start_live_acquisition()
            else:
                try:
                    cam.stop_video_capture()
                except Exception:
                    pass
