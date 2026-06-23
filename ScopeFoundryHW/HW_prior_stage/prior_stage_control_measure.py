from ScopeFoundry import Measurement
from ScopeFoundry.helper_funcs import load_qt_ui_file, sibling_path


class PriorStageControlMeasure(Measurement):

    name = "prior_stage_control"

    def __init__(self, app, name=None, hw_name="prior_stage"):
        self.hw_name = hw_name
        Measurement.__init__(self, app, name=name)

    def setup(self):
        self.settings.New("jog_step_xy", dtype=float, unit="mm",
                          initial=0.1, spinbox_decimals=4,
                          description="XY jog step size in mm")
        self.stage = self.app.hardware[self.hw_name]

    def setup_figure(self):
        self.ui = load_qt_ui_file(
            sibling_path(__file__, "prior_stage_control.ui"))

        # Connect / disconnect
        self.stage.settings.connected.connect_to_widget(
            self.ui.prior_stage_connect_checkBox)

        # Position readouts
        self.stage.settings.x_position.connect_to_widget(
            self.ui.x_pos_doubleSpinBox)
        self.stage.settings.y_position.connect_to_widget(
            self.ui.y_pos_doubleSpinBox)

        # Target inputs — press Enter to go
        self.ui.x_target_lineEdit.returnPressed.connect(
            self._on_x_target_entered)
        self.ui.x_target_lineEdit.returnPressed.connect(
            lambda: self.ui.x_target_lineEdit.setText(""))
        self.ui.y_target_lineEdit.returnPressed.connect(
            self._on_y_target_entered)
        self.ui.y_target_lineEdit.returnPressed.connect(
            lambda: self.ui.y_target_lineEdit.setText(""))

        # Jog step
        self.settings.jog_step_xy.connect_to_widget(
            self.ui.xy_step_doubleSpinBox)

        # Speed
        self.stage.settings.speed_xy.connect_to_widget(
            self.ui.speed_xy_spinBox)  # QDoubleSpinBox with decimals=0

        # Stop
        self.ui.xy_stop_pushButton.clicked.connect(self.stage.halt_xy)

        # Jog buttons
        self.ui.x_up_pushButton.clicked.connect(self.x_up)
        self.ui.x_down_pushButton.clicked.connect(self.x_down)
        self.ui.y_up_pushButton.clicked.connect(self.y_up)
        self.ui.y_down_pushButton.clicked.connect(self.y_down)

    # ------------------------------------------------------------------ #
    # Target entry                                                         #
    # ------------------------------------------------------------------ #

    def _on_x_target_entered(self):
        try:
            x = float(self.ui.x_target_lineEdit.text())
            self.stage.settings["x_target"] = x
        except ValueError:
            pass

    def _on_y_target_entered(self):
        try:
            y = float(self.ui.y_target_lineEdit.text())
            self.stage.settings["y_target"] = y
        except ValueError:
            pass

    # ------------------------------------------------------------------ #
    # Jog                                                                  #
    # ------------------------------------------------------------------ #

    def x_up(self):
        self.stage.settings["x_target"] = (
            self.stage.settings["x_position"] + self.settings["jog_step_xy"])

    def x_down(self):
        self.stage.settings["x_target"] = (
            self.stage.settings["x_position"] - self.settings["jog_step_xy"])

    def y_up(self):
        self.stage.settings["y_target"] = (
            self.stage.settings["y_position"] + self.settings["jog_step_xy"])

    def y_down(self):
        self.stage.settings["y_target"] = (
            self.stage.settings["y_position"] - self.settings["jog_step_xy"])
