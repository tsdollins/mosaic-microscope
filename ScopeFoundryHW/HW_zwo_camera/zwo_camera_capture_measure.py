from ScopeFoundry.measurement import Measurement
from qtpy import QtCore, QtWidgets
import pyqtgraph as pg
from ScopeFoundry import h5_io
import os
import imageio
import numpy as np

class ZWOCameraCaptureMeasure(Measurement):

    name = 'zwo_camera_capture'

    def setup(self):
        self.settings.New('live_img', dtype=bool)
        self.settings.New('live_update_period', dtype=int, initial=500, unit='ms',
                          vmin=100, vmax=10000,
                          description='Interval between live preview frame captures')
        self.settings.New('rotate', dtype=bool)
        self.settings.New('px_bin', dtype=int, initial=1, choices=(1,2,4,8,16,32),
                          description='Software display downsample (does not reduce USB transfer)')

        self.add_operation('clear_and_plot', self.clear_and_plot)
        self.add_operation('snap_and_save', self.snap_and_save)
        self.settings.live_img.add_listener(self.on_toggle_live_img)



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

        self.live_img_update_timer = QtCore.QTimer()
        self.live_img_update_timer.timeout.connect(self._on_live_img_timer)
        self.live_img_update_timer.start(self.settings['live_update_period'])
        self.settings.live_update_period.add_listener(
            lambda: self.live_img_update_timer.setInterval(self.settings['live_update_period']))

        self.ui_layout.setColumnStretch(1, 1)
        self.clear_and_plot()

    def on_toggle_live_img(self):
        cam = self.app.hardware['zwo_camera']
        if not hasattr(cam, 'camera'):
            return
        if self.settings['live_img']:
            cam.start_video_capture()
        else:
            cam.stop_video_capture()
    """
    #just overrided the run functions here to start video capture and the interrupt function
    #to stop capture. this should support non-thread blocking video capture through the run functions multi-threading

    def run(self):
        cam = self.app.hardware['zwo_camera']
        cam.camera.start_video_capture()

    def interrupt(self):
        cam = self.app.hardware['zwo_camera']
        cam.camera.stop_video_capture()
    """

    def _on_live_img_timer(self):
        if self.settings['live_img']:
            cam = self.app.hardware['zwo_camera']
            im  = cam.capture_video_frame()
            if self.settings['rotate']:
                im = im.swapaxes(0,1)

            if self.settings['px_bin'] >= 1:
                stride = self.settings['px_bin']
                im = im[::stride,::stride]

            # TODO this is a fix for certain cameras returning BGR instead of RGB
            if True:
                if im.shape[-1] == 3:
                    im_r  = im[:,:,2]
                    im_g  = im[:,:,1]
                    im_b  = im[:,:,0]
                    im = np.stack([im_r,im_g, im_b], axis=2)


            self.live_img_item.setImage(image=im,
                                        autoLevels=False)
            scale = 1
            center_x = 50
            center_y = 50
            im_aspect = im.shape[1]/im.shape[0]
            self.img_rect = pg.QtCore.QRectF(0 - center_x * scale / 100,
                                0 - center_y * scale * im_aspect / 100,
                                scale,
                                scale * im_aspect)
            self.live_img_item.setRect(self.img_rect)

            #print(cam.camera.get_dropped_frames())
                # # get rectangle
                # im_aspect = im.shape[1]/im.shape[0]
                # stageS = self.app.hardware["mcl_xyz_stage"].settings
                # x,y,z =  stageS["x_position"]*1e-6, stageS["y_position"]*1e-6, stageS["z_position"]*1e-6
                #
                # scale = self.settings['img_scale']
                # S = self.settings
                # rect= pg.QtCore.QRectF(x - S['img_center_x'] * scale / 100,
                #                         y - S['img_center_y'] * scale * im_aspect / 100,
                #                         scale,
                #                         scale * im_aspect)
                # self.live_img_item.setRect(rect)


    def clear_and_plot(self):
        #scale = self.settings['scale'] # m/V

        self.graph_layout.clear()

        self.xy_plot = self.graph_layout.addPlot(0,0)
        self.xy_plot.setAspectLocked(lock=True, ratio=1)
        #self.xy_plot.setLabels(left=('mcl y', 'm'), bottom=('mcl x', 'm'))
        self.live_img_item = pg.ImageItem()
        self.xy_plot.addItem(self.live_img_item)

        self.xy_plot.addItem(pg.InfiniteLine(angle=0))
        self.xy_plot.addItem(pg.InfiniteLine(angle=90))




    def snap_and_save(self):
        print("snap_and_save")
        cam = self.app.hardware['zwo_camera']
        cam.camera.start_video_capture()

        try:
            print("creating h5")
            self.h5_file = h5_io.h5_base_file(self.app, measurement=self)
            self.h5_filename = self.h5_file.filename
            print(self.h5_filename)
            self.h5_m = h5_io.h5_create_measurement_group(measurement=self, h5group=self.h5_file)

            print("capture frame")
            new_img = cam.camera.capture_video_frame()

            print("save jpg")
            imageio.imsave(self.h5_filename +".jpg", new_img, quality=100)
            print("save tif")
            imageio.imsave(self.h5_filename +".tif", new_img)
            print("save h5")
            self.h5_m['img'] = new_img


        finally:
            self.h5_file.close()
