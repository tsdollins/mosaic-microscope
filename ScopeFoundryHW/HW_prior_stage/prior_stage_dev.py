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
