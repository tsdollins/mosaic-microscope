"""
Low-level pyserial wrapper for the Prior Scientific PureFocus850 laser autofocus.

The PF850 controller (PF100) presents itself as a USB virtual COM port using a
plain ASCII command protocol -- this is a SEPARATE interface from the XY stage,
which uses PriorScientificSDK.dll. See docs/PUREFOCUS850_COMMAND_REFERENCE.md.

Serial: 460800 baud, 8 data bits, no parity, 1 stop bit, no flow control.
Every command and response is terminated by <CR> (ASCII 0x0D, '\\r').
Set commands ("CMD,args") return "0" on success; query commands (no comma)
return a value. Errors come back as "E,<code>".
"""

import threading
import serial

BAUD = 460800
_TERM = b"\r"
_DEFAULT_TIMEOUT = 1.0  # seconds for a single command response

# Error codes from manual section 9.10
_ERROR_CODES = {
    "2": "Not idle",
    "3": "No drive",
    "4": "String parse",
    "5": "Command not found",
    "8": "Value out of range",
    "10": "Argument 1 out of range",
    "11": "Argument 2 out of range",
    "12": "Argument 3 out of range",
    "13": "Argument 4 out of range",
    "14": "Argument 5 out of range",
    "15": "Argument 6 out of range",
}


class PureFocusError(Exception):
    pass


class PureFocusDev:

    def __init__(self, debug=False):
        self.debug = debug
        self.ser = None
        # The PF850 firmware is not safe for concurrent access; serialise every
        # command exchange (GUI poll timer vs. measurement thread).
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Connection                                                          #
    # ------------------------------------------------------------------ #

    def connect(self, port: int, timeout: float = _DEFAULT_TIMEOUT):
        """Open the controller on COM<port>."""
        self.ser = serial.Serial(
            port=f"COM{int(port)}",
            baudrate=BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False, rtscts=False, dsrdtr=False,
            timeout=timeout,
        )
        # Clear any stale bytes from the line.
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def disconnect(self):
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    # ------------------------------------------------------------------ #
    # Command primitive                                                   #
    # ------------------------------------------------------------------ #

    def _cmd(self, command: str) -> str:
        """Send a command, return the <CR>-terminated response (stripped).

        Raises PureFocusError on an 'E,<code>' response or a read timeout.
        """
        if not self.connected:
            raise PureFocusError("Not connected -- call connect() first")
        with self._lock:
            self.ser.reset_input_buffer()
            self.ser.write(command.encode("ascii") + _TERM)
            self.ser.flush()
            raw = self.ser.read_until(_TERM)
        if not raw.endswith(_TERM):
            raise PureFocusError(
                f"Timeout waiting for response to '{command}' (got {raw!r})")
        resp = raw.rstrip(b"\r\n").decode("latin-1").strip()
        if self.debug:
            print(f"PF850 >> {command!r}  << {resp!r}")
        if resp.startswith("E,"):
            code = resp.split(",", 1)[1].strip()
            raise PureFocusError(
                f"Command '{command}' error E,{code}: "
                f"{_ERROR_CODES.get(code, 'unknown')}")
        return resp

    # ------------------------------------------------------------------ #
    # System / identity                                                   #
    # ------------------------------------------------------------------ #

    def get_date(self) -> str:
        return self._cmd("DATE")

    def get_serial(self) -> str:
        return self._cmd("SERIAL")

    def save(self):
        """Persist all current parameters to flash (can take several seconds)."""
        self._cmd("SAVE")

    def set_auto(self, n: int):
        """Auto-update state: 0=manual, 1=auto, 2=realtime (required for servo)."""
        self._cmd(f"AUTO,{int(n)}")

    def get_auto(self) -> int:
        # Response form is "AUTO,n"
        resp = self._cmd("AUTO")
        return int(resp.split(",")[-1])

    def set_config(self, mode: str, sensor: str):
        """mode: 'S' stepper / 'P' piezo / 'H' measure;  sensor: 'S' slice / 'L' line."""
        self._cmd(f"CONFIG,{mode},{sensor}")

    def get_config(self) -> tuple[str, str]:
        m, s = self._cmd("CONFIG").split(",")
        return m.strip(), s.strip()

    # ------------------------------------------------------------------ #
    # Objective profiles                                                  #
    # ------------------------------------------------------------------ #

    def set_objective(self, n: int):
        """Load the saved parameters for objective n (1..6)."""
        self._cmd(f"OBJ,{int(n)}")

    def get_objective(self) -> int:
        return int(self._cmd("OBJ"))

    # ------------------------------------------------------------------ #
    # Servo / focus loop                                                  #
    # ------------------------------------------------------------------ #

    def set_servo(self, on: bool):
        """Enable/disable the focus servo. NOTE: servo requires AUTO=2."""
        self._cmd(f"SERVO,{1 if on else 0}")

    def get_servo(self) -> bool:
        return self._cmd("SERVO") == "1"

    def get_pos(self) -> float:
        """Position signal (A-B)/(A+B), nominally in [-1, +1]."""
        return float(self._cmd("POS"))

    def get_error(self) -> float:
        """Error signal = POS - TARGET."""
        return float(self._cmd("ERROR"))

    def get_output(self) -> float:
        """Current PID output."""
        return float(self._cmd("OUTPUT"))

    def set_target(self, f: float = None):
        """Set servo set point. With no arg, captures the current error value."""
        if f is None:
            self._cmd("TARGET")
        else:
            self._cmd(f"TARGET,{float(f)}")

    def get_target(self) -> float:
        return float(self._cmd("TARGET,?"))

    # ------------------------------------------------------------------ #
    # Flags / signals                                                     #
    # ------------------------------------------------------------------ #

    def get_abcd(self) -> tuple[int, int, int, int, int, int]:
        """Return (A, B, C, D, focus_state, sample_state)."""
        parts = self._cmd("ABCD").split(",")
        a, b, c, d, i, s = (int(p) for p in parts[:6])
        return a, b, c, d, i, s

    def get_focus_flag(self) -> bool:
        return self._cmd("FOCUS") == "1"

    def get_sample_flag(self) -> bool:
        return self._cmd("SAMPLE") == "1"

    # ------------------------------------------------------------------ #
    # Laser / sensor                                                      #
    # ------------------------------------------------------------------ #

    def get_laser(self) -> int:
        # Response form is "LASER,n"
        resp = self._cmd("LASER")
        return int(resp.split(",")[-1])

    def set_laser(self, n: int):
        """Laser power 0..4095 for the current objective."""
        self._cmd(f"LASER,{int(n)}")

    def get_exposure(self) -> int:
        return int(self._cmd("EXPOSURE"))

    def set_exposure(self, microseconds: int):
        self._cmd(f"EXPOSURE,{int(microseconds)}")

    # ------------------------------------------------------------------ #
    # Focus drive (Z)                                                     #
    # ------------------------------------------------------------------ #

    def get_z(self) -> int:
        """Focus position in user units (default 100 nm). Stepper mode only."""
        return int(self._cmd("PZ"))

    def set_z(self, n: int):
        """Set current focus position to n user units. Stepper mode only."""
        self._cmd(f"PZ,{int(n)}")

    def is_focus_moving(self) -> bool:
        """Focus motion status via '$' : 0 idle, 4 moving."""
        return self._cmd("$") != "0"

    def get_step_size(self) -> int:
        """Default step size (user units, default 100 nm) for U/D moves."""
        return int(self._cmd("C"))

    def set_step_size(self, n: int):
        """Set default step size (user units) for the U/D move commands."""
        self._cmd(f"C,{int(n)}")

    def move_up(self):
        """Move focus up by one step size (the C value)."""
        self._cmd("U")

    def move_down(self):
        """Move focus down by one step size (the C value)."""
        self._cmd("D")

    def halt_focus(self):
        """Stop focus drive motion (velocity 0). Stepper control only."""
        self._cmd("VZ,0")

    def get_upr(self) -> float:
        """Microns per revolution of the focus drive (stepper mode)."""
        return float(self._cmd("UPR"))

    def get_ssz(self) -> int:
        """Microsteps per user unit for focus position (default 50)."""
        return int(self._cmd("SSZ"))

    # ------------------------------------------------------------------ #
    # Offset lens                                                         #
    # ------------------------------------------------------------------ #

    def get_lens_position(self) -> int:
        """Offset lens position in steps (25600 steps/mm)."""
        return int(self._cmd("LENSP"))

    def lens_home(self):
        self._cmd("LENSH")

    def lens_goto_stored(self, n: int):
        """Move offset lens to stored position n (1..5) for the current objective."""
        self._cmd(f"LENSGO,{int(n)}")

    def is_lens_moving(self) -> bool:
        return self._cmd("LENS$") == "1"
