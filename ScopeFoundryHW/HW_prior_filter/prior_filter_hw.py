from ScopeFoundry import HardwareComponent
from qtpy import QtCore


class PriorFilterHW(HardwareComponent):
    """Prior HF108FC rotating filter wheel on the Prior ProScan controller.

    The filter wheel is driven by the SAME controller as the XY stage, so this
    component reuses the existing prior_stage device session rather than opening
    a new connection (mirroring PriorTurretHW). The controller can host several
    wheels; each is addressed by a filter id in [1..6]. The HF108FC is filter
    id 1 by default.
    """

    name = "prior_filter"

    # Max slots we expose editable name fields for. The HF108 is an 8-position
    # wheel; the selectable range is clamped to the wheel's real slot count on
    # connect.
    MAX_SLOTS = 8

    def setup(self):
        self.settings.New("filter_id", dtype=int, initial=1, vmin=1, vmax=6,
                          description="Which filter wheel axis on the controller (1-6)")
        self.settings.New("position", dtype=int, initial=1, vmin=1,
                          description="Filter wheel slot (1-based)")
        self.settings.New("num_positions", dtype=int, ro=True)
        self.settings.New("wheel_name", dtype=str, ro=True,
                          description="Filter wheel model reported by the controller")

        # Per-slot filter labels (editable; persisted via the ini).
        for i in range(1, self.MAX_SLOTS + 1):
            self.settings.New(f"slot_{i}", dtype=str, initial=f"Filter {i}",
                              description=f"Label for filter slot {i}")
        # Label of the filter currently in the light path.
        self.settings.New("filter_name", dtype=str, ro=True,
                          description="Label of the current filter slot")

        # Keep `filter_name` in sync with the current slot and the slot table.
        self.settings.position.add_listener(self._update_filter_name)
        for i in range(1, self.MAX_SLOTS + 1):
            self.settings.get_lq(f"slot_{i}").add_listener(self._update_filter_name)
        self._update_filter_name()

        self.add_operation("Home filter", self.home)

        self.update_timer = QtCore.QTimer()
        self.update_timer.timeout.connect(self._on_update_timer)
        self.update_timer.start(300)

    def _update_filter_name(self):
        pos = self.settings["position"]
        if 1 <= pos <= self.MAX_SLOTS:
            self.settings["filter_name"] = self.settings[f"slot_{pos}"]

    @property
    def _dev(self):
        """The shared Prior stage device (same controller session)."""
        return self.app.hardware["prior_stage"].dev

    @property
    def _fid(self) -> int:
        return int(self.settings["filter_id"])

    def connect(self):
        stage = self.app.hardware["prior_stage"]
        if not stage.settings["connected"]:
            raise IOError(
                "Connect the Prior stage first -- the filter wheel shares its "
                "controller session.")

        if not self._dev.filter_fitted(self._fid):
            raise IOError(
                f"No filter wheel detected at filter id {self._fid} on the "
                "Prior controller.")

        self.settings["wheel_name"] = self._dev.get_filter_name(self._fid)
        self.settings["num_positions"] = self._dev.get_filters_per_wheel(self._fid)
        # Clamp the selectable range to the physical wheel.
        self.settings.position.change_min_max(1, self.settings["num_positions"])

        self.settings.position.connect_to_hardware(
            read_func=lambda: self._dev.get_filter_position(self._fid),
            write_func=self._goto_position)
        self.settings.position.read_from_hardware()

    def disconnect(self):
        self.settings.disconnect_all_from_hardware()

    # ------------------------------------------------------------------ #
    # Motion                                                              #
    # ------------------------------------------------------------------ #

    def _goto_position(self, position: int):
        self._dev.goto_filter_position(int(position), self._fid, wait=True)

    def home(self):
        self._dev.filter_home(self._fid)
        self.settings.position.read_from_hardware()

    def is_busy(self) -> bool:
        return self._dev.is_filter_busy(self._fid)

    # ------------------------------------------------------------------ #
    # Timer                                                               #
    # ------------------------------------------------------------------ #

    def _on_update_timer(self):
        if self.settings["connected"]:
            self.settings.position.read_from_hardware()
