import sys
import numpy as np
from ScopeFoundry import BaseMicroscopeApp
from ScopeFoundry import helper_funcs


class MicroscopeApp(BaseMicroscopeApp):

    name = "MosaicMicroscope"

    def setup(self):
        from ScopeFoundryHW.HW_prior_stage.prior_stage_hw import PriorStageHW
        self.add_hardware(PriorStageHW(self))

        from ScopeFoundryHW.HW_zwo_camera.zwo_camera_hw import ZWOCameraHW
        self.add_hardware(ZWOCameraHW(self))

        from ScopeFoundryHW.HW_prior_stage.prior_stage_control_measure import PriorStageControlMeasure
        self.add_measurement(PriorStageControlMeasure(self))

        from ScopeFoundryHW.HW_zwo_camera.zwo_camera_capture_measure import ZWOCameraCaptureMeasure
        self.add_measurement(ZWOCameraCaptureMeasure(self))

        from measurements.simple_tiled_image import SimpleTiledImage
        self.add_measurement(SimpleTiledImage(self))

        from measurements.continuous_motion_image import ContinuousMotionImage
        self.add_measurement(ContinuousMotionImage(self))

        # Scan-planning parameters: tile field-of-view + overlap -> scan bounds
        self.settings.New("overlap", dtype=float, ro=False, spinbox_step=5,
                          description="Percent overlap between adjacent tiles")
        self.settings.New("panel_height", dtype=float, ro=False, unit="mm",
                          spinbox_decimals=4, initial=2.288)
        self.settings.New("panel_width", dtype=float, ro=False, unit="mm",
                          spinbox_decimals=4, initial=1.35)

        self.settings_load_ini('microscope_defaults.ini')

    def _post_setup_ui_quickaccess(self):
        Q = self.add_quickbar(
            helper_funcs.load_qt_ui_file("microscope_quickbar.ui"))
        self.setup_quickbar()

    def setup_quickbar(self):
        Q = self.quickbar
        stage = self.hardware['prior_stage']
        stage_control = self.measurements['prior_stage_control']
        camera = self.hardware['zwo_camera']
        camera_capture = self.measurements['zwo_camera_capture']

        # --- Prior Stage ---
        stage.settings.connected.connect_to_widget(Q.asi_hw_connect_checkBox)

        stage.settings.x_position.connect_to_widget(Q.x_pos_doubleSpinBox)
        stage.settings.y_position.connect_to_widget(Q.y_pos_doubleSpinBox)

        stage.settings.x_target.connect_to_widget(Q.asi_x_target_doubleSpinBox)
        stage.settings.y_target.connect_to_widget(Q.asi_y_target_doubleSpinBox)

        Q.x_up_pushButton.clicked.connect(stage_control.x_up)
        Q.x_down_pushButton.clicked.connect(stage_control.x_down)
        Q.y_up_pushButton.clicked.connect(stage_control.y_up)
        Q.y_down_pushButton.clicked.connect(stage_control.y_down)

        stage_control.settings.jog_step_xy.connect_to_widget(
            Q.xy_step_doubleSpinBox)

        step_values_mm = np.array([0.0005, 0.001, 0.01, 0.1, 1.0, 2.0])
        step_labels = [f'{v:g} mm' for v in step_values_mm]
        Q.xy_step_comboBox.addItems(step_labels)
        Q.xy_step_comboBox.setCurrentIndex(3)  # default 0.1 mm

        def apply_xy_step():
            stage_control.settings.jog_step_xy.update_value(
                step_values_mm[Q.xy_step_comboBox.currentIndex()])
        Q.xy_step_comboBox.currentIndexChanged.connect(apply_xy_step)

        Q.stop_stage_pushButton.clicked.connect(stage.halt_xy)

        # --- ZWO Camera ---
        camera.settings.connected.connect_to_widget(Q.zwo_hw_checkBox)
        Q.snap_save_pushButton.clicked.connect(camera_capture.snap_and_save)
        # zwo_iso_comboBox, zwo_exp_comboBox, zwo_color_temp_comboBox,
        # open_last_img_pushButton, show_last_img_pushButton — not yet connected
        # z_up/down/halt, stage_locator buttons — not yet connected

        # --- Scan Parameters ---
        scan = self.measurements['simple_tiled_image']
        scan.settings.Nh.connect_to_widget(Q.Nh_doubleSpinBox)
        scan.settings.Nv.connect_to_widget(Q.Nv_doubleSpinBox)
        self.settings.overlap.connect_to_widget(Q.overlap_doubleSpinBox)
        self.settings.panel_height.connect_to_widget(Q.panel_height_doubleSpinBox)
        self.settings.panel_width.connect_to_widget(Q.panel_width_doubleSpinBox)
        Q.calculate_pushButton.clicked.connect(self.new_bounds)


    def new_bounds(self, *args):
        """Compute scan bounds (h0/h1/v0/v1) from tile count, panel size, and overlap.

        Ported from SurveyScope. Bounds are centered on (0, 0).
        """
        overlap = self.settings['overlap'] / 100
        h = self.settings['panel_height']
        w = self.settings['panel_width']

        for name in ('simple_tiled_image', 'continuous_motion_image'):  # scan measurement keys
            s = self.measurements[name].settings

            Nh = s.Nh.val - 1
            Nv = s.Nv.val - 1

            Lh = h + (Nh - 1) * h * (1 - overlap)
            Lv = w + (Nv - 1) * w * (1 - overlap)

            s.h0.update_value(-Lh / 2)
            s.h1.update_value(Lh / 2)
            s.v0.update_value(-Lv / 2)
            s.v1.update_value(Lv / 2)


def main():
    app = MicroscopeApp(sys.argv)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
