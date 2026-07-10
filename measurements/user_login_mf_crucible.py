import os
import traceback

import numpy as np
from ScopeFoundry.measurement import Measurement
from qtpy import QtWidgets


class UserLoginMFCrucible(Measurement):
    """Log in to Crucible and upload a scan HDF5 as a Dataset.

    Companion to the ``mf-crucible`` HardwareComponent (which carries the
    metadata baked into every scan H5). This Measurement handles the
    *interactive* and *long-running* side: authenticating (whoami), choosing a
    project, and uploading a chosen ``.h5`` in the background
    ``MeasurementQThread`` so the big multipart upload does not freeze the GUI.

    Metadata precedence: the Dataset is created with ``project_id`` and
    ``owner_orcid`` set explicitly (from the mf-crucible component / whoami), so
    ingestion does not depend on the server-side ORCID/project parser (which has
    a known hyphen/underscore bug). A ``*_simple_tiled_image.h5`` filename
    auto-selects ``SimpleTiledImageScopeFoundryH5Ingestor`` server-side; we also
    pass it explicitly via ``ingestor`` for clarity.
    """

    name = "user_login_mf_crucible"

    # Server-side ingestion class that matches simple_tiled_image scans.
    DEFAULT_INGESTOR = "SimpleTiledImageScopeFoundryH5Ingestor"

    # Dataset path inside a tiled-scan HDF5 (matches full_stitch_process.py).
    TILED_DSET = "measurement/simple_tiled_image/live_img_map"

    def setup(self):
        S = self.settings
        S.New("h5_file", dtype=str, initial="",
              description="Path to the scan .h5 to upload")
        S.New("project_id", dtype=str, initial="",
              description="Target Crucible project id (defaults to mf-crucible proposal)")
        S.New("dataset_name", dtype=str, initial="",
              description="Dataset name (defaults to the h5 filename)")
        S.New("ingestor", dtype=str, initial=self.DEFAULT_INGESTOR,
              description="Server-side ingestion class name")
        S.New("public", dtype=bool, initial=False,
              description="Make the dataset public")
        S.New("wait_for_ingestion", dtype=bool, initial=False,
              description="Block until server-side ingestion finishes")
        S.New("upload_thumbnail", dtype=bool, initial=True,
              description="Generate and upload a thumbnail from the scan")

        # Status (read-only)
        S.New("user", dtype=str, initial="", ro=True)
        S.New("status", dtype=str, initial="idle", ro=True)
        S.New("last_dataset_id", dtype=str, initial="", ro=True)

    @property
    def hw(self):
        return self.app.hardware["mf-crucible"]

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def setup_figure(self):
        self.ui = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        self.ui.setLayout(layout)

        layout.addWidget(self.settings.New_UI(), 0, 0)

        self.login_button = QtWidgets.QPushButton("Log in / whoami")
        self.login_button.clicked.connect(self.log_in)
        layout.addWidget(self.login_button, 1, 0)

        self.browse_button = QtWidgets.QPushButton("Browse for .h5 ...")
        self.browse_button.clicked.connect(self.browse_h5)
        layout.addWidget(self.browse_button, 2, 0)

        self.upload_button = QtWidgets.QPushButton("Upload to Crucible")
        self.upload_button.clicked.connect(self.start)
        layout.addWidget(self.upload_button, 3, 0)

        self.stop_button = QtWidgets.QPushButton("Cancel")
        self.stop_button.clicked.connect(self.interrupt)
        layout.addWidget(self.stop_button, 4, 0)

    # --- interactive helpers (run on the GUI thread) ------------------- #

    def log_in(self):
        """Connect the mf-crucible hardware (authenticates + whoami)."""
        try:
            self.hw.settings["connected"] = True
            self.settings["user"] = self.hw.settings["user"]
            self.settings["status"] = f"logged in as {self.hw.settings['user']}"
        except Exception as err:
            self.settings["status"] = f"login failed: {err}"
            self.log.error(traceback.format_exc())

    def browse_h5(self):
        start_dir = os.path.dirname(self.settings["h5_file"]) or os.getcwd()
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.ui, "Select scan HDF5", start_dir,
            "HDF5 files (*.h5);;All files (*)")
        if fname:
            self.settings["h5_file"] = fname

    # ------------------------------------------------------------------ #
    # Upload (background thread)                                           #
    # ------------------------------------------------------------------ #

    def run(self):
        S = self.settings
        S["status"] = "starting"
        try:
            h5_path = S["h5_file"].strip()
            if not h5_path or not os.path.isfile(h5_path):
                raise FileNotFoundError(f"h5_file not found: {h5_path!r}")

            hw = self.hw
            if not hw.settings["connected"] or hw.get_client() is None:
                raise RuntimeError(
                    "Not logged in to Crucible -- press 'Log in / whoami' "
                    "(or connect the mf-crucible hardware) first.")
            client = hw.get_client()

            project_id = S["project_id"].strip() or hw.project_id
            if not project_id:
                raise ValueError(
                    "No project id set. Fill 'project_id' here or 'proposal' "
                    "on the mf-crucible hardware.")

            dataset_name = S["dataset_name"].strip() or os.path.basename(h5_path)

            from crucible import Dataset

            dataset = Dataset(
                dataset_name=dataset_name,
                project_id=project_id,
                owner_orcid=hw.owner_orcid,
                instrument_name=self.app.name,
                session_name=hw.session_name_value(),
                public=S["public"],
            )
            keywords = hw.tags_list()

            S["status"] = "uploading (this may take a while)..."
            self.set_progress(10.0)

            result = client.datasets.create(
                dataset,
                files_to_upload=[h5_path],
                keywords=keywords,
                ingestor=S["ingestor"].strip() or None,
                wait_for_ingestion_response=S["wait_for_ingestion"],
            )
            dsid = result["dsid"]
            S["last_dataset_id"] = dsid
            self.set_progress(80.0)

            if S["upload_thumbnail"] and not self.interrupt_measurement_called:
                try:
                    thumb = self._generate_thumbnail(h5_path)
                    if thumb is not None:
                        client.datasets.add_thumbnail(
                            dsid, thumb, thumbnail_name=f"{dataset_name}_thumb")
                except Exception as err:
                    self.log.warning(f"thumbnail upload failed: {err}")

            self.set_progress(100.0)
            S["status"] = f"done: {dsid}"
            self.log.info(f"Uploaded {h5_path} -> dataset {dsid}")

        except Exception as err:
            S["status"] = f"failed: {err}"
            self.log.error("Crucible upload failed: " + traceback.format_exc())

    # ------------------------------------------------------------------ #
    # Thumbnail generation (client-side; no server-side tiled thumbnailer) #
    # ------------------------------------------------------------------ #

    def _generate_thumbnail(self, h5_path, tile_px=64):
        """Build a small montage thumbnail from a tiled-image scan H5.

        Reads ``measurement/simple_tiled_image/live_img_map`` (shape
        ``(1, Nv, Nh, th, tw[, 3])``), heavily downsamples each tile, and lays
        them out in an ``Nv x Nh`` grid. Returns an ``(H, W[, 3])`` uint8 array,
        or ``None`` if the expected dataset is not present.
        """
        import h5py

        with h5py.File(h5_path, "r") as f:
            if self.TILED_DSET not in f:
                return None
            imgs = f[self.TILED_DSET]
            shape = imgs.shape
            if len(shape) == 6:
                _, Nv, Nh, th, tw, _nc = shape
                rgb = (_nc == 3)
            elif len(shape) == 5:
                _, Nv, Nh, th, tw = shape
                rgb = False
            else:
                return None

            # Stride each tile down to ~tile_px on its short edge.
            step = max(1, min(th, tw) // tile_px)
            cell_h = len(range(0, th, step))
            cell_w = len(range(0, tw, step))

            if rgb:
                montage = np.zeros((Nv * cell_h, Nh * cell_w, 3), dtype=np.uint8)
            else:
                montage = np.zeros((Nv * cell_h, Nh * cell_w), dtype=np.uint8)

            for r in range(Nv):
                for c in range(Nh):
                    if rgb:
                        tile = np.asarray(imgs[0, r, c, ::step, ::step, :])
                    else:
                        tile = np.asarray(imgs[0, r, c, ::step, ::step])

                    if tile.dtype != np.uint8:
                        tmax = float(tile.max()) or 1.0
                        tile = (tile.astype(np.float32) / tmax * 255).astype(np.uint8)

                    h = min(cell_h, tile.shape[0])
                    w = min(cell_w, tile.shape[1])
                    montage[r * cell_h:r * cell_h + h,
                            c * cell_w:c * cell_w + w] = tile[:h, :w]

        return montage
