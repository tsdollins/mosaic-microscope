from ScopeFoundry import HardwareComponent
from qtpy import QtCore
from .prior_stage_dev import PriorStageDev


class PriorStageHW(HardwareComponent):

    name = "prior_stage"

    def setup(self):
        self.settings.New("port", dtype=int, initial=3,
                          description="COM port number (e.g. 3 for COM3)")
        self.settings.New("speed_xy", dtype=float, initial=10.0, unit="mm/s",
                          vmin=0.001, vmax=100.0, spinbox_decimals=1,
                          description="Max stage speed in mm/s")
        self.settings.New("invert_x", dtype=bool, initial=True,
                          description="Invert stage X sign so +X moves right "
                                      "(applies controller hostdirection after connect)")
        self.settings.New("x_position", dtype=float, ro=True, unit="mm",
                          spinbox_decimals=4)
        self.settings.New("y_position", dtype=float, ro=True, unit="mm",
                          spinbox_decimals=4)
        self.settings.New("x_target", dtype=float, unit="mm",
                          spinbox_decimals=4)
        self.settings.New("y_target", dtype=float, unit="mm",
                          spinbox_decimals=4)

        self.settings.New('new_x_target', dtype=float, unit="mm",
                          spinbox_decimals=4)      
        self.settings.New('new_y_target', dtype=float, unit="mm",
                          spinbox_decimals=4)
    
        self.settings.New('target_j', dtype=float, initial=1, ro=False, spinbox_step=1)
        self.settings.New('target_k', dtype=float, initial=1, ro=False, spinbox_step=1)
        self.settings.New("stage_name", dtype=str, ro=True)

        self.add_operation("Halt XY", self.halt_xy)

        self.settings.invert_x.add_listener(self._on_invert_x)

        self.update_timer = QtCore.QTimer()
        self.update_timer.timeout.connect(self._on_update_timer)
        self.update_timer.start(200)

    def connect(self):
        port = self.settings["port"]
        self.dev = PriorStageDev()
        try:
            self.dev.connect(port)
        except Exception:
            self.dev.disconnect()
            raise

        self.settings["stage_name"] = self.dev.get_stage_name()

        # connect() reset hostdirection to default (1 1); apply invert_x now, so
        # the position reads below already carry the intended X sign.
        self._apply_host_direction()

        self.settings.x_position.connect_to_hardware(
            read_func=lambda: self.dev.get_position()[0] / 1000.0)
        self.settings.y_position.connect_to_hardware(
            read_func=lambda: self.dev.get_position()[1] / 1000.0)

        self.settings.x_position.read_from_hardware()
        self.settings.y_position.read_from_hardware()

        self.settings["x_target"] = self.settings["x_position"]
        self.settings["y_target"] = self.settings["y_position"]

        self.settings.x_target.connect_to_hardware(
            write_func=self._move_x_target)
        self.settings.y_target.connect_to_hardware(
            write_func=self._move_y_target)

        self.settings.speed_xy.connect_to_hardware(
            read_func=lambda: self.dev.get_speed() / 1000.0,
            write_func=lambda s: self.dev.set_speed(int(s * 1000)))
        self.settings.speed_xy.write_to_hardware()

    def disconnect(self):
        self.settings.disconnect_all_from_hardware()
        if hasattr(self, "dev") and self.dev._session is not None:
            self.dev.disconnect()

    # ------------------------------------------------------------------ #
    # Motion                                                               #
    # ------------------------------------------------------------------ #

    def _move_x_target(self, x_mm):
        self.dev.goto_position(x_mm * 1000, self.settings["y_target"] * 1000, wait=False)

    def _move_y_target(self, y_mm):
        self.dev.goto_position(self.settings["x_target"] * 1000, y_mm * 1000, wait=False)

    def _apply_host_direction(self):
        """Push the invert_x setting to the controller (Y left unchanged).
        Must run after connect / stage.ss.set, which reset it to default."""
        x_dir = -1 if self.settings["invert_x"] else 1
        self.dev.set_host_direction(x_dir, 1)

    def _on_invert_x(self):
        """Re-apply direction live. Flipping X changes the sign of reported
        positions, so refresh readings and re-seat targets (no physical move)."""
        if not self.settings["connected"]:
            return
        self._apply_host_direction()
        self.settings.x_position.read_from_hardware()
        self.settings.y_position.read_from_hardware()
        self.settings["x_target"] = self.settings["x_position"]
        self.settings["y_target"] = self.settings["y_position"]

    def halt_xy(self):
        self.dev.stop()

    def is_busy_xy(self):
        return self.dev.is_busy()

    def get_position(self) -> tuple[float, float]:
        return self.settings["x_position"], self.settings["y_position"]

    # ------------------------------------------------------------------ #
    # Timer                                                                #
    # ------------------------------------------------------------------ #

    def _on_update_timer(self):
        if self.settings["connected"]:
            self.settings.x_position.read_from_hardware()
            self.settings.y_position.read_from_hardware()

    # --- Locate functions ---

    def locate_xy(self):
        self.settings['x_target'] = self.settings['new_x_target']
        self.settings['y_target'] = self.settings['new_y_target']

    def locate_tile(self):
        m = self.app.measurements['simple_tiled_image']
        h0 = m.settings['h0']
        v0 = m.settings['v0']
        Nh = m.settings['Nh']
        Nv = m.settings['Nv']
        dh = m.settings['dh']
        dv = m.settings['dv']
        j = abs(self.settings['target_j'])
        k = abs(self.settings['target_k'])
        if j > Nh-1 or k > Nv-1:
            return
        else:
            h_j = h0 + dh*j
            v_k = v0 + dv*k
            self.settings['x_target'] = h_j
            self.settings['y_target'] = v_k
