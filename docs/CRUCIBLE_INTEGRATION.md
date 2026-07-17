# Crucible Integration — NewMicroscopeApp

How this app connects to **Crucible** (LBL Molecular Foundry data platform). Verify against
current code before editing. Client library: `nano-crucible`, imported as `crucible`, pinned to
the GitHub **dev** branch (update to tip: `uv.exe run -- uv sync --upgrade-package nano-crucible`,
or `uv.exe sync --upgrade-package nano-crucible`). Auth/config: `%APPDATA%\nano-crucible\config.ini`
or `CRUCIBLE_*` env; `from crucible.config import get_client` → `get_client().whoami()`.

## App-side components (this repo)

### `mf-crucible` HardwareComponent — `ScopeFoundryHW/HW_mf_crucible/mf_crucible_hw.py`
- Class `MFCrucibleHW`, `name = "mf-crucible"` (**hyphen is deliberate** — see below). Registered in
  `microscope_app.py setup()`.
- LQs baked into every scan H5 (the ingestor reads these): `session_name`, `tags`, `orcid`,
  `proposal`. Plus read-only `user`, `email`, `api_url`.
- `connect()` = authenticate: `crucible.config.get_client()` + `whoami()`; auto-fills user/orcid/
  proposal. Helpers used by the uploader: `get_client()`, `owner_orcid`, `project_id`
  (first whitespace token of `proposal`), `tags_list()`, `session_name_value()`.
- **Why a HardwareComponent, not a Measurement:** ScopeFoundry serializes *hardware* settings into
  **every** scan H5 under `hardware/<name>/settings` (`h5_io.h5_save_hardware_lq`); a measurement's
  settings only reach its *own* file. The server ingestor reads `hardware/mf-crucible/settings`, so
  the metadata must live on hardware. (Demo confirmed: a crucible HW component + a `user_login`
  measurement — we mirror both.)

### `user_login_mf_crucible` Measurement — `measurements/user_login_mf_crucible.py`
- Class `UserLoginMFCrucible`. Interactive login + **background-threaded** upload of a chosen scan
  `.h5` as a Crucible dataset (Measurement runs in `MeasurementQThread`, so the multipart upload
  doesn't freeze the GUI). Registered in `microscope_app.py`.
- UI: `h5_file` is a ScopeFoundry `FileLQ` (dtype `"file"`) → built-in browse button + drag-drop;
  Log in / whoami, Upload, Cancel buttons.
- `run()`: creates `Dataset` with `project_id` + `owner_orcid` set **at create-time** (sidesteps a
  known server-side parser bug, below), uploads the `.h5` multipart with
  `ingestor='SimpleTiledImageScopeFoundryH5Ingestor'`, and attaches a client-generated montage
  thumbnail (tile rows inverted to match the viewers / `full_stitch_process`).

### Scan H5 geometry metadata — `measurements/simple_tiled_image.py`
- `_save_scan_metadata()` (called from `pre_scan_setup()`, after the H5 group exists) writes these
  **attrs on `measurement/simple_tiled_image`**: `overlap_frac`, `panel_width_mm`,
  `panel_height_mm`, `pixel_size_um`, `magnification`, `pixel_size_um_effective`.
- Stored as attrs (not datasets) so the Crucible H5 ingestor harvests them into scientific_metadata,
  and so stitching reads one group instead of cross-referencing app/hardware. `full_stitch_process.py`
  and the cloud stitcher read overlap/frame size from here (`read_scan_geometry`), falling back to
  `app/settings` for older files — nothing is hardcoded.

## Server-side ingestion (separate repo: `MolecularFoundryCrucible/crucible-ingestion`)
- `SimpleTiledImageScopeFoundryH5Ingestor` already supports `simple_tiled_image` — **no server change
  needed** to ingest our scans, given the `mf-crucible` settings are in the H5.
- ⚠ **hyphen/underscore bug**: the ingestor reads `tags`/`session_name` from
  `hardware['mf-crucible']` (hyphen, works/tested) but `orcid`/`proposal` from `hardware['mf_crucible']`
  (underscore, an xfail/broken path). So we name the HW component `mf-crucible` (hyphen) AND set
  `owner_orcid`/`project_id` on the Dataset at client create-time rather than trusting that parser.
- Ingestor selection: filename `*_simple_tiled_image.h5` auto-matches; we also pass `ingestor=` explicitly.

## Cloud stitching — `crucible/` folder → separate repo `MolecularFoundryCrucible/mosaic-stitch-consumer`
Developed here for AI-agent access; **deploys from its own repo** (GCP project **mf-crucible**,
GKE Autopilot cluster `crucible-cluster`, Cloud Run/GKE).
- `crucible_stitch_process.py` — Crucible-agnostic analysis: `main(directory)` globs
  `*_simple_tiled_image.h5`, ashlar (`EdgeAligner`/`Mosaic`/`PyramidWriter`) + BaSiC flat-field →
  pyramidal **BigTIFF OME-TIFF** in `stitch_results/`; returns dict
  `{mosaic_path, thumbnail_path, source_h5, pixel_size_um, n_tiles, mosaic_shape,
  median_correction_px, max_correction_px}`. Writes a downsampled PNG thumbnail. **Requires a JVM**
  (ashlar → pyjnius starts one at import; JAVA_HOME must be set — Dockerfile installs
  `openjdk-21-jre-headless`).
- `consumer-mosaic-stitcher.py` — RMQ consumer on queue **`mosaic-stitch`** (MUST match the API's
  publish `routing_key`). Per message `{dsid}`: download raw `.h5`, run `main()`, create child
  dataset (`measurement="stitched_mosaic"`), `link_parent_child(raw → mosaic)`, propagate samples,
  attach thumbnail. Threaded + `add_callback_threadsafe` ack; failures → `mosaic-stitch-failed`.
- Deploy files: `Dockerfile` (python:3.11-trixie + uv + Java), `cloudbuild.yaml`
  (`kubectl set image deployment/mosaic-stitch-consumer`), `k8s-conf-main.yaml`, `pyproject.toml`
  (pins **numpy==1.26.4 / ashlar==1.20.0 / basicpy==2.0.0** — numpy must stay <2 for basicpy),
  `uv.lock`, `.python-version` (3.12).
- ⚠ **OOM**: BaSiC fit peaks memory; the pod OOM-kills at 4Gi and crash-loops (message redelivers).
  Fix: raise pod memory (Autopilot ≤ ~6.5 GB per vCPU → 12Gi with cpu 2, or bump cpu) and/or lower
  `FIT_MAX_TILES`. Durable fix (not yet done): run `main()` in a subprocess + timeout so an OOM
  routes the message to the failed queue instead of crash-looping.

## Trigger a stitch
`client.datasets.request_mosaic_stitch(dsid)` → `POST /datasets/{dsid}/mosaic_stitch` → publishes
`{dsid}` to the `mosaic-stitch` queue. Fire-and-forget (result appears as a child dataset in minutes).
```
"/c/Users/Lab/.local/bin/uv.exe" run python -c "import sys; from crucible.config import get_client; print(get_client().datasets.request_mosaic_stitch(sys.argv[1]))" DATASET_ID
```
Verify: `get_client().datasets.list_children('DATASET_ID')`. Logs: `kubectl logs -f deployment/mosaic-stitch-consumer`.

## Mosaic viewer (separate repo: `MolecularFoundryCrucible/crucible_graph_explorer`)
"Mosaic Viewer" dataset view for `stitched_mosaic` datasets. **Method A (client-side):** browser
reads the pyramidal OME-TIFF directly via HTTP Range with geotiff.js and renders with OpenSeadragon
(server stays out of the data path). Files: `views/datasets/mosaic_viewer.py` (auto-discovered
plugin: `MEASUREMENT_TYPES=['stitched_mosaic']`, view + `/file-url` signed-URL + `/local` dev route)
and `flask_templates/dataset_views/mosaic_viewer.html`. `test_data/` holds local `.ome.tif` for the
`/dataset-view/mosaic/local/<file>` dev route (same-origin, Range-capable). Detailed context lives in
that repo. (Full viewer build in progress.)
