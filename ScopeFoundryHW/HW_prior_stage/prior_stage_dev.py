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
