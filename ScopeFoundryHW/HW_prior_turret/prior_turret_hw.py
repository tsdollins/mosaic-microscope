from ScopeFoundry import HardwareComponent
from qtpy import QtCore


class PriorTurretHW(HardwareComponent):
    """Motorized objective turret (nosepiece) on the Prior ProScan controller.

    The turret is driven by the SAME controller as the XY stage, so this
    component reuses the existing prior_stage device session rather than opening
    a new connection. Changing the objective optionally auto-syncs the
    PureFocus850 objective profile so its per-objective autofocus parameters
    follow the physical objective.
    """

    name = "prior_turret"

    # Objective magnification in each physical turret slot (1-based).
    DEFAULT_MAGS = {1: 100.0, 2: 40.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 50.0}
    MAX_SLOTS = 6

    def setup(self):
        self.settings.New("position", dtype=int, initial=1, vmin=1,
                          description="Objective turret position (1-based)")
        self.settings.New("num_positions", dtype=int, ro=True)
        self.settings.New("nosepiece_name", dtype=str, ro=True)
        self.settings.New("sync_purefocus", dtype=bool, initial=True,
                          description="Send OBJ,n to the PureFocus850 when the "
                                      "turret position changes")

        # Per-slot objective magnifications (editable; persisted via the ini).
        for i in range(1, self.MAX_SLOTS + 1):
            self.settings.New(f"mag_{i}", dtype=float,
                              initial=self.DEFAULT_MAGS.get(i, 0.0),
                              description=f"Objective magnification in turret slot {i}")
        # Magnification of the objective currently in the light path.
        self.settings.New("magnification", dtype=float, ro=True,
                          description="Magnification of the current objective")

        # Keep `magnification` in sync with the current slot and the slot table.
        self.settings.position.add_listener(self._update_magnification)
        for i in range(1, self.MAX_SLOTS + 1):
            self.settings.get_lq(f"mag_{i}").add_listener(self._update_magnification)
        self._update_magnification()

        self.add_operation("Home turret", self.home)

    def _update_magnification(self):
        pos = self.settings["position"]
        if 1 <= pos <= self.MAX_SLOTS:
            self.settings["magnification"] = self.settings[f"mag_{pos}"]

        self.update_timer = QtCore.QTimer()
        self.update_timer.timeout.connect(self._on_update_timer)
        self.update_timer.start(300)

    @property
    def _dev(self):
        """The shared Prior stage device (same controller session)."""
        return self.app.hardware["prior_stage"].dev

    def connect(self):
        stage = self.app.hardware["prior_stage"]
        if not stage.settings["connected"]:
            raise IOError(
                "Connect the Prior stage first -- the turret shares its "
                "controller session.")

        if not self._dev.nosepiece_fitted():
            raise IOError("No nosepiece detected on the Prior controller.")

        self.settings["num_positions"] = self._dev.get_nosepiece_num_positions()
        self.settings["nosepiece_name"] = self._dev.get_nosepiece_name()
        # Clamp the selectable range to the physical turret.
        self.settings.position.change_min_max(1, self.settings["num_positions"])

        self.settings.position.connect_to_hardware(
            read_func=self._dev.get_nosepiece_position,
            write_func=self._goto_position)
        self.settings.position.read_from_hardware()

    def disconnect(self):
        self.settings.disconnect_all_from_hardware()

    # ------------------------------------------------------------------ #
    # Motion + PF850 sync                                                  #
    # ------------------------------------------------------------------ #

    def _goto_position(self, position: int):
        self._dev.goto_nosepiece_position(int(position), wait=True)
        if self.settings["sync_purefocus"]:
            self._sync_purefocus(int(position))

    def _sync_purefocus(self, position: int):
        """Load the matching objective profile on the PureFocus850, if present."""
        if "prior_purefocus" not in self.app.hardware:
            return
        pf = self.app.hardware["prior_purefocus"]
        if not pf.settings["connected"]:
            return
        try:
            pf.set_objective(position)
        except Exception as err:
            self.log.warning(f"Could not sync PureFocus850 objective: {err}")

    def home(self):
        self._dev.nosepiece_home()
        self.settings.position.read_from_hardware()

    def is_busy(self) -> bool:
        return self._dev.is_nosepiece_busy()

    # ------------------------------------------------------------------ #
    # Timer                                                                #
    # ------------------------------------------------------------------ #

    def _on_update_timer(self):
        if self.settings["connected"]:
            self.settings.position.read_from_hardware()
