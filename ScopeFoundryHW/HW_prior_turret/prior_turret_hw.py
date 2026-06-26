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

    def setup(self):
        self.settings.New("position", dtype=int, initial=1, vmin=1,
                          description="Objective turret position (1-based)")
        self.settings.New("num_positions", dtype=int, ro=True)
        self.settings.New("nosepiece_name", dtype=str, ro=True)
        self.settings.New("sync_purefocus", dtype=bool, initial=True,
                          description="Send OBJ,n to the PureFocus850 when the "
                                      "turret position changes")

        self.add_operation("Home turret", self.home)

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
