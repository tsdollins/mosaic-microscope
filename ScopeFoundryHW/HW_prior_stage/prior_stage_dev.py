"""
Low-level ctypes wrapper for the Prior Scientific SDK DLL.
Mirrors the command API described in Prior Scientific SDK v2.0.0.
"""

import time
from ctypes import WinDLL, create_string_buffer

DLL_PATH = r"C:\Users\Lab\Downloads\PriorSDK2.0.0\PriorSDK 2.0.0\x64\PriorScientificSDK.dll"
PRIOR_OK = 0
_RX_BUF_SIZE = 512
_POLL_INTERVAL = 0.05  # seconds between busy polls


class PriorStageError(Exception):
    pass


class PriorStageDev:

    def __init__(self, dll_path=DLL_PATH):
        self._sdk = WinDLL(dll_path)
        ret = self._sdk.PriorScientificSDK_Initialise()
        if ret != PRIOR_OK:
            raise PriorStageError(f"SDK initialise failed: {ret}")
        self._session = None

    def connect(self, port: int):
        """Open a session and connect to the controller on COM<port>."""
        self._session = self._sdk.PriorScientificSDK_OpenNewSession()
        if self._session < 0:
            raise PriorStageError(f"OpenNewSession failed: {self._session}")
        self._cmd(f"controller.connect {port}")

    def disconnect(self):
        if self._session is not None:
            try:
                self._cmd("controller.disconnect")
            finally:
                self._sdk.PriorScientificSDK_CloseSession(self._session)
                self._session = None

    # ------------------------------------------------------------------ #
    # Position                                                             #
    # ------------------------------------------------------------------ #

    def get_position(self) -> tuple[float, float]:
        """Return (x, y) in microns."""
        resp = self._cmd("controller.stage.position.get")
        x, y = resp.split(",")
        return float(x), float(y)

    def set_position_origin(self, x: float = 0, y: float = 0):
        """Redefine the current physical position as (x, y) without moving."""
        self._cmd(f"controller.stage.position.set {int(x)} {int(y)}")

    def goto_position(self, x: float, y: float, wait: bool = True):
        """Move to absolute (x, y) in microns. Blocks until idle when wait=True."""
        self._cmd(f"controller.stage.goto-position {int(x)} {int(y)}")
        if wait:
            self._wait_until_idle()

    def move_relative(self, dx: float, dy: float, wait: bool = True):
        """Move relative to current position by (dx, dy) microns."""
        self._cmd(f"controller.stage.move-relative {int(dx)} {int(dy)}")
        if wait:
            self._wait_until_idle()

    def stop(self, abrupt: bool = False):
        cmd = "controller.stop.abruptly" if abrupt else "controller.stop.smoothly"
        self._cmd(cmd)

    # ------------------------------------------------------------------ #
    # Speed / motion settings                                              #
    # ------------------------------------------------------------------ #

    def get_speed(self) -> int:
        """Return max speed in microns/s."""
        return int(self._cmd("controller.stage.speed.get"))

    def set_speed(self, speed_um_s: int):
        self._cmd(f"controller.stage.speed.set {speed_um_s}")

    # ------------------------------------------------------------------ #
    # Status                                                               #
    # ------------------------------------------------------------------ #

    def is_busy(self) -> bool:
        return self._cmd("controller.stage.busy.get") != "0"

    def get_stage_name(self) -> str:
        return self._cmd("controller.stage.name.get")

    def get_serial_number(self) -> str:
        return self._cmd("controller.serialnumber.get")

    # ------------------------------------------------------------------ #
    # Nosepiece / objective turret (same controller session)              #
    # ------------------------------------------------------------------ #

    def nosepiece_fitted(self) -> bool:
        """True if the controller has auto-detected a fitted nosepiece."""
        return self._cmd("controller.nosepiece.fitted.get") != "0"

    def get_nosepiece_name(self) -> str:
        return self._cmd("controller.nosepiece.name.get")

    def get_nosepiece_num_positions(self) -> int:
        """Number of objective positions the nosepiece has."""
        return int(self._cmd("controller.nosepiece.no-of-positions.get"))

    def get_nosepiece_position(self) -> int:
        """Currently selected objective position (1-based)."""
        return int(self._cmd("controller.nosepiece.position.get"))

    def goto_nosepiece_position(self, position: int, wait: bool = True):
        """Rotate the turret to objective `position` (1-based)."""
        self._cmd(f"controller.nosepiece.goto-position {int(position)}")
        if wait:
            while self.is_nosepiece_busy():
                time.sleep(_POLL_INTERVAL)

    def is_nosepiece_busy(self) -> bool:
        return self._cmd("controller.nosepiece.busy.get") != "0"

    def nosepiece_home(self):
        """Home the nosepiece to position 1 (required for rotational turrets)."""
        self._cmd("controller.nosepiece.home")

    # ------------------------------------------------------------------ #
    # Filter wheel(s) (same controller session)                           #
    #                                                                     #
    # The controller can host up to 6 filter wheels, each addressed by a  #
    # filter id `f` in [1..6]. The HF108FC reports as filter id 1 by      #
    # default.                                                            #
    # ------------------------------------------------------------------ #

    def filter_fitted(self, f: int = 1) -> bool:
        """True if filter wheel `f` is present on the controller."""
        return self._cmd(f"controller.filter.fitted.get {int(f)}") != "0"

    def get_filter_name(self, f: int = 1) -> str:
        """Model name of filter wheel `f`, e.g. 'HF108-8'."""
        return self._cmd(f"controller.filter.name.get {int(f)}")

    def get_filters_per_wheel(self, f: int = 1) -> int:
        """Number of filter slots on wheel `f`.

        The SDK docs spell this command both 'filters-per-wheel' and
        'filter-per-wheel' in different places, so try the documented form
        first and fall back to the alternate spelling.
        """
        try:
            return int(self._cmd(f"controller.filter.filters-per-wheel.get {int(f)}"))
        except PriorStageError:
            return int(self._cmd(f"controller.filter.filter-per-wheel.get {int(f)}"))

    def get_filter_position(self, f: int = 1) -> int:
        """Currently selected filter slot (1-based) on wheel `f`."""
        return int(self._cmd(f"controller.filter.position.get {int(f)}"))

    def goto_filter_position(self, position: int, f: int = 1, wait: bool = True):
        """Rotate wheel `f` to filter slot `position` (1-based)."""
        self._cmd(f"controller.filter.goto-position {int(f)} {int(position)}")
        if wait:
            while self.is_filter_busy(f):
                time.sleep(_POLL_INTERVAL)

    def is_filter_busy(self, f: int = 1) -> bool:
        return self._cmd(f"controller.filter.busy.get {int(f)}") != "0"

    def filter_home(self, f: int = 1):
        """Home wheel `f`; it spins to find its alignment and ends at slot 1."""
        self._cmd(f"controller.filter.home {int(f)}")

    # ------------------------------------------------------------------ #
    # Axis direction                                                      #
    # ------------------------------------------------------------------ #

    def set_host_direction(self, x_dir: int, y_dir: int):
        """Set the physical +ve direction sign for each axis (each +1 or -1).

        NOTE: the controller resets this to the default (1 1) on
        `controller.connect` and on `controller.stage.ss.set`, so it must be
        (re)applied after those.
        """
        self._cmd(f"controller.stage.hostdirection.set {int(x_dir)} {int(y_dir)}")

    def get_host_direction(self) -> tuple[int, int]:
        """Return the current (x, y) host direction signs, e.g. (1, 1) or (-1, 1)."""
        x, y = self._cmd("controller.stage.hostdirection.get").split()
        return int(x), int(y)

    def set_joystick_direction(self, x_dir: int, y_dir: int):
        """Set the physical +ve direction sign for joystick-driven moves (each +1
        or -1). This is INDEPENDENT of set_host_direction (which only affects
        positional/host moves and reported positions). The controller may reset
        it on `controller.connect`, so (re)apply after connecting.
        """
        self._cmd(f"controller.stage.joystickdirection.set {int(x_dir)} {int(y_dir)}")

    def get_joystick_direction(self) -> tuple[int, int]:
        """Return the current (x, y) joystick direction signs, e.g. (1, 1) or (-1, -1)."""
        x, y = self._cmd("controller.stage.joystickdirection.get").split()
        return int(x), int(y)

    def enable_joystick(self, on: bool = True):
        """Enable (or disable) joystick control of the stage."""
        self._cmd("controller.stage.joyxyz.on" if on else "controller.stage.joyxyz.off")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _cmd(self, command: str) -> str:
        if self._session is None:
            raise PriorStageError("Not connected — call connect() first")
        rx = create_string_buffer(_RX_BUF_SIZE)
        ret = self._sdk.PriorScientificSDK_cmd(
            self._session,
            create_string_buffer(command.encode()),
            rx,
        )
        if ret != PRIOR_OK:
            last_err = self._last_error()
            raise PriorStageError(
                f"Command '{command}' failed (api={ret}, controller={last_err})"
            )
        return rx.value.decode('latin-1').strip()

    def _last_error(self) -> str:
        rx = create_string_buffer(_RX_BUF_SIZE)
        self._sdk.PriorScientificSDK_cmd(
            self._session,
            create_string_buffer(b"controller.lasterror.get"),
            rx,
        )
        return rx.value.decode('latin-1').strip()

    def _wait_until_idle(self):
        while self.is_busy():
            time.sleep(_POLL_INTERVAL)
