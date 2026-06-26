from ScopeFoundry import HardwareComponent
from qtpy import QtCore

from .purefocus_dev import PureFocusDev


class PureFocusHW(HardwareComponent):
    """Prior Scientific PureFocus850 laser autofocus.

    Connects over a USB virtual COM port (ASCII protocol, separate from the
    PriorScientificSDK.dll stage controller). See
    docs/PUREFOCUS850_COMMAND_REFERENCE.md.
    """

    name = "prior_purefocus"

    def setup(self):
        self.settings.New("port", dtype=int, initial=4,
                          description="COM port number for the PF850 (e.g. 4 for COM4)")

        # Identity (read once on connect)
        self.settings.New("controller_info", dtype=str, ro=True)

        # Objective profile (1..6) -- mirrors the 6 stored objective configs
        self.settings.New("objective", dtype=int, initial=1, vmin=1, vmax=6,
                          description="Active PF850 objective profile (1-6)")

        # Servo control
        self.settings.New("servo_on", dtype=bool, initial=False,
                          description="Focus servo enabled (requires AUTO=2)")

        # Laser power for current objective
        self.settings.New("laser_power", dtype=int, initial=0, vmin=0, vmax=4095)

        # Live telemetry (read-only, polled)
        self.settings.New("pos", dtype=float, ro=True, spinbox_decimals=4,
                          description="Position signal (A-B)/(A+B)")
        self.settings.New("error", dtype=float, ro=True, spinbox_decimals=4)
        self.settings.New("focus_flag", dtype=bool, ro=True,
                          description="In-focus state")
        self.settings.New("sample_flag", dtype=bool, ro=True,
                          description="Sample detected")
        self.settings.New("z_position", dtype=float, ro=True, unit="um",
                          vmin=-1e6, vmax=1e6, spinbox_decimals=3,
                          description="Focus drive position in microns")
        self.settings.New("z_step", dtype=float, initial=1.0,
                          vmin=0.0001, vmax=10000.0, spinbox_decimals=3, unit="um",
                          description="Step size in microns for the Z up/down buttons")

        # Microns per device 'user unit'; computed from UPR/SSZ at connect.
        # Default assumes UPR=100, SSZ=50 -> 100 nm = 0.1 um.
        self._um_per_unit = 0.1

        self.add_operation("Save params", self.save_params)
        self.add_operation("Home offset lens", self.home_offset_lens)

        self.update_timer = QtCore.QTimer()
        self.update_timer.timeout.connect(self._on_update_timer)
        self.update_timer.start(250)

    def connect(self):
        port = self.settings["port"]
        self.dev = PureFocusDev()
        try:
            self.dev.connect(port)
        except Exception:
            self.dev.disconnect()
            raise

        # AUTO=2 is required for the servo and for real-time ABCD updates.
        self.dev.set_auto(2)

        self.settings["controller_info"] = self.dev.get_date().replace("\r", " ")

        # Determine the real microns-per-user-unit from the drive config so the
        # Z readouts can be shown in microns. Motor has 50000 microsteps/rev, so
        # one user unit = SSZ * UPR / 50000 microns (default 50*100/50000 = 0.1).
        try:
            upr = self.dev.get_upr()
            ssz = self.dev.get_ssz()
            if ssz and upr:
                self._um_per_unit = ssz * upr / 50000.0
        except Exception as err:
            self.log.warning(f"Could not read UPR/SSZ; using default Z scale: {err}")

        # Objective profile
        self.settings.objective.connect_to_hardware(
            read_func=self.dev.get_objective,
            write_func=self.dev.set_objective)
        self.settings.objective.read_from_hardware()

        # Servo
        self.settings.servo_on.connect_to_hardware(
            read_func=self.dev.get_servo,
            write_func=self.dev.set_servo)
        self.settings.servo_on.read_from_hardware()

        # Laser power
        self.settings.laser_power.connect_to_hardware(
            read_func=self.dev.get_laser,
            write_func=self.dev.set_laser)
        self.settings.laser_power.read_from_hardware()

        # Read-only telemetry
        self.settings.pos.connect_to_hardware(read_func=self.dev.get_pos)
        self.settings.error.connect_to_hardware(read_func=self.dev.get_error)
        self.settings.focus_flag.connect_to_hardware(read_func=self.dev.get_focus_flag)
        self.settings.sample_flag.connect_to_hardware(read_func=self.dev.get_sample_flag)
        # Z position/step are exposed in microns; convert at the SDK boundary.
        self.settings.z_position.connect_to_hardware(
            read_func=lambda: self.dev.get_z() * self._um_per_unit)

        self.settings.z_step.connect_to_hardware(
            read_func=lambda: self.dev.get_step_size() * self._um_per_unit,
            write_func=lambda um: self.dev.set_step_size(
                max(1, round(um / self._um_per_unit))))
        self.settings.z_step.read_from_hardware()

    def disconnect(self):
        self.settings.disconnect_all_from_hardware()
        if hasattr(self, "dev") and self.dev.connected:
            self.dev.disconnect()

    # ------------------------------------------------------------------ #
    # Convenience API (used by turret auto-sync and scan hooks)            #
    # ------------------------------------------------------------------ #

    def set_objective(self, n: int):
        """Load PF850 objective profile n (1..6) via the LQ so the UI follows."""
        self.settings["objective"] = int(n)

    def set_servo(self, on: bool):
        self.settings["servo_on"] = bool(on)

    def is_in_focus(self) -> bool:
        return self.dev.get_focus_flag()

    def is_sample_detected(self) -> bool:
        return self.dev.get_sample_flag()

    # --- Manual Z control (quickbar buttons) ---------------------------- #
    # Any manual Z move first disables the focus servo so the user's command
    # does not fight the autofocus loop driving the same motor.

    def _disable_servo_for_manual(self):
        if self.settings["servo_on"]:
            self.settings["servo_on"] = False

    def z_up(self):
        if not self.settings["connected"]:
            return
        self._disable_servo_for_manual()
        self.dev.move_up()
        self.settings.z_position.read_from_hardware()

    def z_down(self):
        if not self.settings["connected"]:
            return
        self._disable_servo_for_manual()
        self.dev.move_down()
        self.settings.z_position.read_from_hardware()

    def z_halt(self):
        if not self.settings["connected"]:
            return
        self._disable_servo_for_manual()
        self.dev.halt_focus()
        self.settings.z_position.read_from_hardware()

    def save_params(self):
        self.dev.save()

    def home_offset_lens(self):
        self.dev.lens_home()

    # ------------------------------------------------------------------ #
    # Timer                                                                #
    # ------------------------------------------------------------------ #

    def _on_update_timer(self):
        if self.settings["connected"]:
            self.settings.pos.read_from_hardware()
            self.settings.error.read_from_hardware()
            self.settings.focus_flag.read_from_hardware()
            self.settings.sample_flag.read_from_hardware()
            self.settings.z_position.read_from_hardware()
