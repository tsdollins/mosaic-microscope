from ScopeFoundry import HardwareComponent


class MFCrucibleHW(HardwareComponent):
    """Molecular Foundry **Crucible** data-management metadata + authentication.

    This component carries the per-session Crucible metadata (session name,
    tags, ORCID, proposal/project) that must be embedded in *every* scan HDF5.
    ScopeFoundry serialises hardware settings into every measurement's H5 under
    ``hardware/<name>/settings`` (see ``ScopeFoundry.h5_io.h5_save_hardware_lq``),
    which is exactly where the Crucible server-side ``ScopeFoundryH5Ingestor``
    reads its tags/session/orcid/proposal from -- so this metadata lives on a
    HardwareComponent, NOT a Measurement (a measurement's settings only reach
    its own file, never the tiled-scan file the ingestor processes).

    ``connect()`` authenticates to Crucible using the API key configured for the
    ``nano-crucible`` package (``%APPDATA%\\nano-crucible\\config.ini`` or the
    ``CRUCIBLE_*`` environment variables) and records the current user via
    ``whoami()``.

    NOTE on the name: the component ``name`` uses a **hyphen** (``mf-crucible``)
    so the H5 group becomes ``hardware/mf-crucible/settings`` -- the path the
    ingestor's (passing, tested) tag/session parser expects. The server's
    orcid/proposal parser looks under ``mf_crucible`` (underscore) and is
    currently a known-broken xfail; we therefore set ``owner_orcid`` and
    ``project_id`` explicitly at dataset-create time (see the companion
    ``user_login_mf_crucible`` measurement) rather than relying on that parser.
    """

    name = "mf-crucible"

    # Server-side sentinel defaults: ``ScopeFoundryH5Ingestor`` ignores tags /
    # session values that still equal these strings, so leaving them unchanged
    # keeps junk keywords out of the uploaded dataset.
    DEFAULT_TAGS = "list,tags,separated,by,commas (optional)"
    DEFAULT_SESSION = "(optional)"

    def setup(self):
        S = self.settings

        # --- Metadata baked into every scan H5 (read by the ingestor) ---
        S.New("session_name", dtype=str, initial=self.DEFAULT_SESSION,
              description="Crucible session name grouping related datasets")
        S.New("tags", dtype=str, initial=self.DEFAULT_TAGS,
              description="Comma-separated keywords added to uploaded datasets")
        S.New("orcid", dtype=str, initial="",
              description="Owner ORCID (auto-filled from whoami on connect)")
        S.New("proposal", dtype=str, initial="",
              description="Proposal / project id (first token is used as project_id)")

        # --- Identity / connection status (read-only, filled on connect) ---
        S.New("user", dtype=str, initial="", ro=True,
              description="Authenticated Crucible user")
        S.New("email", dtype=str, initial="", ro=True)
        S.New("api_url", dtype=str, initial="", ro=True)

        # Live client + cached whoami, populated on connect().
        self.client = None
        self._whoami = {}

    # ------------------------------------------------------------------ #
    # Connection == authentication                                        #
    # ------------------------------------------------------------------ #

    def connect(self):
        # Imported lazily so app startup does not depend on crucible/config
        # (and so the "ASI SDK not found"-style import chatter stays out of the
        # way until the user actually connects).
        from crucible.config import get_client, get_api_url, get_current_project

        # get_client() raises ValueError if no API key is configured.
        self.client = get_client()
        self._whoami = self.client.whoami() or {}

        orcid = self._whoami.get("user_unique_id", "") or ""
        info = self._whoami.get("user_info", {}) or {}
        first = info.get("first_name", "") or ""
        last = info.get("last_name", "") or ""
        display = f"{first} {last}".strip() or info.get("username", "") or orcid

        self.settings["user"] = display
        self.settings["email"] = info.get("email", "") or ""
        self.settings["api_url"] = get_api_url()

        # Auto-fill ORCID only if the user has not typed one in manually.
        if not self.settings["orcid"]:
            self.settings["orcid"] = orcid

        # Seed proposal/project from the config default if still empty.
        if not self.settings["proposal"]:
            proj = get_current_project()
            if proj:
                self.settings["proposal"] = proj

    def disconnect(self):
        self.client = None
        self._whoami = {}

    # ------------------------------------------------------------------ #
    # Convenience API (used by the user_login_mf_crucible measurement)     #
    # ------------------------------------------------------------------ #

    def get_client(self):
        """Return the live ``CrucibleClient`` (None until connected)."""
        return self.client

    def whoami(self) -> dict:
        """Return a copy of the cached whoami() response."""
        return dict(self._whoami)

    @property
    def owner_orcid(self):
        """ORCID to stamp on datasets (LQ override, else whoami, else None)."""
        return (self.settings["orcid"].strip()
                or self._whoami.get("user_unique_id", "")
                or None)

    @property
    def project_id(self):
        """Project id parsed from ``proposal`` (first whitespace-delimited token)."""
        proposal = self.settings["proposal"].strip()
        return proposal.split(" ")[0] if proposal else None

    def tags_list(self):
        """User tags as a list, or [] when left at the sentinel default."""
        tags = self.settings["tags"].strip()
        if not tags or tags == self.DEFAULT_TAGS:
            return []
        return [t.strip() for t in tags.split(",") if t.strip()]

    def session_name_value(self):
        """Session name, or None when left at the sentinel default."""
        s = self.settings["session_name"].strip()
        return None if (not s or s == self.DEFAULT_SESSION) else s
