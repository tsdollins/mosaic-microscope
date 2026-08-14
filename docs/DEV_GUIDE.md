# Developer Guide — Mosaic Microscope

A handoff document for a human developer picking up this project. It covers what the
app is, what already works, and — at greater length — what is unfinished and what
needs attention. For an AI-agent-oriented quick reference see
`docs/AGENT_PROJECT_GUIDE.md`; for deep dives on specific problems see the other files
in `docs/`.

---

## 1. Project goal and what has been done

### Goal
A [ScopeFoundry](https://www.scopefoundry.org/)-based Python application that drives a
motorized microscope to build **large-area mosaics** of a sample. The core loop is:
move the XY stage through a grid of positions, capture a camera image at each position,
and stitch the tiles into a single explorable mosaic. Finished mosaics are pushed to
**Crucible** (LBL Molecular Foundry data-management) and viewed in a separate web app.

### Hardware
- **Prior Scientific** microscope base — XY stage, motorized objective **turret**, and
  **filter** wheel, all driven through `PriorScientificSDK.dll` (ctypes `WinDLL`,
  integer-micron command API).
- **Prior PureFocus850** laser autofocus (Z) — a *separate* USB → virtual COM port,
  ASCII protocol over **pyserial @ 460800 8N1**. Not part of the DLL path.
- **ZWO ASI2600MC Pro** color camera — 26 MP (6248×4176), USB3, via the `zwoasi`
  wrapper and the bundled ASI SDK DLL.

### Environment
- Windows 11, git-bash shell. Python `>=3.12,<3.13` managed by **uv**.
  `uv` is not on the bash PATH — invoke it as `/c/Users/Lab/.local/bin/uv.exe`.
- Run the app: `uv.exe run python microscope_app.py` (or the `microscope-app` script).
- No automated test suite. Sanity-check edits with
  `uv.exe run python -m py_compile <files>`; full behavior requires the real hardware.
- There is also a legacy conda env `scopefoundry` and a second venv `.venv-basicpy`
  (for BaSiC illumination correction, which has awkward dependency pins).

### Architecture (what's built and working on `master`)
`microscope_app.py` → `MicroscopeApp(BaseMicroscopeApp)`, `name = "MosaicMicroscope"`.
`setup()` registers hardware then measurements, then loads `microscope_defaults.ini`
**last** (order matters). The quickbar UI is `microscope_quickbar.ui`, wired in
`setup_quickbar()`.

**Hardware components** (all under `ScopeFoundryHW/`, per project convention):
- `HW_prior_stage/` — `prior_stage_hw.py` (mm LQs, converts to µm at the SDK boundary;
  `goto_position`, `is_busy_xy`, `halt_xy`), plus `prior_stage_control_measure.py`
  (jog buttons) and `prior_stage_raster.py` (`PriorStage2DScan(BaseRaster2DSlowScan)`,
  the raster base every scan measurement subclasses).
- `HW_prior_turret/` — objective turret; its `magnification` drives the tile
  field-of-view calculation in `update_panel_fov()`.
- `HW_prior_filter/` — filter wheel (most recently added, commit `9c68d2a`).
- `HW_prior_purefocus/` — PureFocus850 Z/autofocus over pyserial.
- `HW_zwo_camera/` — `zwo_camera_hw.py` + `zwo_camera_capture_measure.py`; bundles the
  vendor ASI SDK (gitignored). Handles orientation via `orient_frame()` (`flip_h`/
  `flip_v`), live preview, snap-and-save.

**Measurements** (`measurements/`):
- `simple_tiled_image.py` — the primary **step-and-shoot** mosaic scan. Stops at each
  tile, settles, drains stale buffered frames, captures. Writes `data/<YYMMDD>_<HHMMSS>_
  simple_tiled_image.h5` (~0.4–2.6 GB) plus a sibling `*.h5_images/` dir of tile JPGs.
- `continuous_motion_image.py` — **capture-while-moving** variant. Currently
  **commented out** in `microscope_app.py` (disabled, commit `9ed6a9a`).
- `timelapse.py` — repeated single-position capture over time.

**Scan planning:** app-level settings `overlap`, `panel_width/height` (tile FOV in mm,
auto-derived from objective mag + camera ROI + pixel size), and `center_h/center_v`.
`new_bounds()` converts these into raster bounds `h0/h1/v0/v1`.

**Stitching / viewing (standalone scripts, run manually — not wired into the GUI):**
`full_stitch_process.py` (ashlar + optional BaSiC, reads the H5 and writes OME-TIFF),
`AshlarTest.py`, `NoVignetteASHLAR.py`, `M2StitchTest.py`, `VignetteRemover.py`,
`export_tiles.py`, and viewers `MosaicViewer.py`, `MosaicViewerContinuous.py`,
`LazyViewer.py`.

**Orientation** (see `docs/TILE_ORIENTATION_NOTES.md`): three separate flip/rotation
problems were found and fixed. Corrections live in the **display and assembly layers**;
the saved H5 stays raw and in natural scan order. Stage X is sign-inverted at the
controller so `+x` = right; a sub-degree per-tile de-rotation (~-1.5°) is applied in the
stitchers/viewers, not baked into stored tiles.

**Crucible integration** (on branch `feature/crucible`, **not merged to master**):
- `ScopeFoundryHW/HW_mf_crucible/mf_crucible_hw.py` — a `mf-crucible` hardware component
  exposing `orcid`, `proposal`, `session_name`, `tags`. Because ScopeFoundry bakes every
  HW component's settings into each scan H5, these fields land exactly where the
  server-side ingestor reads them.
- `crucible/` — the cloud stitch pipeline files kept here for editing but belonging to a
  separate deployed repo (`consumer-mosaic-stitcher.py`, `crucible_stitch_process.py`,
  Dockerfile, cloudbuild, k8s config).
- The Molecular Foundry server **already supports our measurement**: an existing
  `SimpleTiledImageScopeFoundryH5Ingestor` matches any `*_simple_tiled_image.h5`.

**Companion projects (separate repos, documented in memory):**
- `crucible_graph_explorer` — a Flask web app with a built **Mosaic Explorer** deep-zoom
  viewer (OpenSeadragon + geotiff.js) for the stitched OME-TIFFs, including point/area
  annotations, cross-microscope dataset links, and multi-magnification detail regions.

---

## 2. Unfinished goals and issues needing development

Ordered roughly by importance. Verify every claim against current code before acting —
files change between sessions, and several items below were paused mid-flight.

### 2.1 Camera freezing / GUI sluggishness (highest priority; PAUSED)
Full analysis in `docs/camera_threading_AGENT_HANDOFF.md` and
`docs/camera_freezing_NOTES_for_Trevor.md`. **No fix has been implemented.**

Two confirmed root causes:
1. **GUI freezes / "not responding."**
   - (A) Blocking camera I/O on the GUI thread: the live-preview `QTimer`
     (`_on_live_img_timer`) grabs a frame over USB on the GUI thread; a USB hiccup
     freezes the whole interface.
   - (B) Concurrent ASI SDK access with **no lock**: during a scan the measurement
     thread and the GUI preview timer both call the SDK, which is not thread-safe per
     camera → hang/deadlock. There are currently zero `Lock`s in `HW_zwo_camera/`.
2. **GUI sluggish when live preview is on:** each tick does a full 26 MP grab + BGR→RGB
   copy + `setImage` on the GUI thread; `setImage` alone costs hundreds of ms.

Agreed direction is **Path B** (dedicated acquisition thread + camera lock + drop-to-
latest display + pause preview during scans + clean thread shutdown), with **Path A**
(keep the timer but add a lock, scan-time pause, and timeout/guard) as a lower-risk
first step. A full 5-step plan is in the handoff doc. Note: `setImage` must stay on the
GUI thread, so smoothness also needs smaller frames (see hardware binning below).
**Do not start until the user confirms Path A vs B and whether to branch.**

### 2.2 Merge / consolidate the outstanding feature branches
`master` is the working baseline. Several branches carry unmerged work and need
decisions:
- **`feature/crucible`** (9 commits ahead) — the entire Crucible login + upload +
  metadata + cloud-stitch integration, including the `mf-crucible` HW component. This is
  substantial and **not on master**. Deciding how/when to land it is a major open task.
- **`HardwareBinning`** (1 commit ahead, `1758a7e`) — hardware binning for the ZWO live
  preview. Directly relevant to fixing camera slowness (smaller frames → cheap
  `setImage`). master is an ancestor, so it merges cleanly.
- **`fix/continuous-position`** (1 commit ahead, `5e7cc1f`) — "flip mosaic tiles over X
  axis in viewers and stitchers." An orientation fix not yet on master.
- `feature/purefocus850`, `feature/filter-box`, `fix/FixFreezing` appear to be already
  merged/ancestors of master (no unique commits) — safe to delete after confirming.

There are also uncommitted working-tree changes on `master` (`AshlarTest.py`,
`LazyViewer.py`, `M2StitchTest.py`, `MosaicViewer.py`, `microscope_defaults.ini`).
Decide what to keep/commit before other work.

### 2.3 Crucible upload path — finish and wire into the app
Design is settled (see the `crucible-integration` memory / `feature/crucible`) but the
**client-side uploader is not finished**. Remaining work:
- A **manual uploader helper** (run against a chosen scan `.h5` after a scan — *not*
  auto-upload every run). Create the Dataset with `project_id` + `owner_orcid` set at
  create time (this sidesteps a known server-side hyphen/underscore parsing bug for
  orcid/proposal), multipart-upload the big H5, and attach a **client-generated
  thumbnail** (no server-side tiled-image thumbnail generator exists).
- Before coding, three inputs are still needed **from the user**: the exact `project_id`
  to upload to, how to identify the instrument (existing `instrument_name` vs
  `get_or_create`), and how samples are handled per upload.
- Confirm the HW component serializes under the H5 key `mf-crucible` (hyphen) — that is
  the path the passing server tests read `tags`/`session_name` from. Note the known
  hyphen/underscore inconsistency for `orcid`/`proposal` (server tests for those are
  `xfail`); do not rely on server-side parsing of those two.
- Parent→child lineage (raw scan → stitched mosaic) is created in the **cloud** repo via
  `link_parent_child`; this app only needs to upload the raw scan and record its
  `unique_id`. An optional `link_stitched_child(parent_id, child_id)` helper here is
  nice-to-have.

### 2.4 Stitching pipeline is manual and lives partly in another repo
- The stitchers/viewers in the project root are **standalone scripts**, not integrated
  into the GUI. Running them needs `ashlar`, `basicpy`, and `m2stitch`, which are **not
  all installed** in the main env (BaSiC lives in `.venv-basicpy` due to dependency
  pins). Decide whether to `uv add` these and/or wire a "stitch" action into the app.
- The **cloud** stitch consumer (`crucible/`) must be mirrored into the deployed
  `mosaic-stitch-consumer` repo, redeployed to Cloud Run, and re-run to regenerate
  mosaics whenever the stitcher changes. The stitcher emits an **IFD pyramid** OME-TIFF
  (top-level reduced-resolution pages), because geotiff.js in the web viewer **cannot
  read SubIFD pyramids** — keep it that way.
- **RGB color output** through the full cloud path was validated once
  (`stitched_SiliconTestColor`), but re-verify after any stitcher change; the
  channels-first→interleaved-RGB repackage is the fragile part.

### 2.5 Multi-magnification mosaics and cross-microscope links (viewer side; awaiting validation)
In `crucible_graph_explorer` (separate repo) the multi-mag "detail region" feature and
cross-microscope dataset links are **built but awaiting the user's cloud validation**.
The main risk is the pixel↔stage **affine / Y-flip** used to place detail-region boxes;
a manual shift-drag nudge absorbs residual error, but the affine should be validated
against a real file. There is also a written-but-unimplemented plan for **per-microscope
coordinate frames + fiducial markers** (`dev/PLAN_coordinate_frames.md` in that repo) to
make flake locations portable across instruments. Marker **drag-to-move** is not yet
built.

The app-side prerequisite (`simple_tiled_image.py::_save_scan_metadata()` writing stage
geometry attrs `h0/h1/v0/v1/Nh/Nv`) exists on `feature/crucible` but note the memory
flags **doc drift**: confirm it is actually present on whatever branch you build from.

### 2.6 Quickbar / UI gaps
In `setup_quickbar()` several widgets are declared in the `.ui` but **not wired**:
`zwo_iso_comboBox`, `zwo_exp_comboBox`, `zwo_color_temp_comboBox`,
`open_last_img_pushButton`, `show_last_img_pushButton`. Also, continuous-scan `Nh/Nv` are
not wired to the quickbar (only `simple_tiled_image`'s are, though `new_bounds()` sets
both). When `.ui` editing, remember Qt wants `<number>` not `<int>`.

### 2.7 Known smaller issues / quirks
- **`new_bounds()` off-by-one:** ported faithfully from the predecessor SurveyScope,
  including a span quirk (effectively an N-2 span in the original). Flagged previously;
  the user chose to keep it. Revisit if tile spacing looks wrong.
- **Continuous-motion measurement is disabled** (commented out in `microscope_app.py`).
  Re-enable once camera threading is sorted, since it deliberately uses buffered frames.
- **Camera handle can stick open** after a force-quit → next `connect()` hangs; replug
  USB. `diagnose_camera.py` (optionally with Sysinternals `handle.exe`) helps diagnose.
- **`README.md` is a stub.** Worth replacing with a real quickstart once the above
  settles.

---

## Key references
- `docs/AGENT_PROJECT_GUIDE.md` — condensed layout + conventions.
- `docs/camera_threading_AGENT_HANDOFF.md` — the camera-freeze fix plan (Path A/B).
- `docs/TILE_ORIENTATION_NOTES.md` — the three orientation fixes and why.
- `docs/PUREFOCUS850_COMMAND_REFERENCE.md` — PureFocus850 ASCII command set.
- Predecessor project for porting: **SurveyScope**
  (https://github.com/tsdollins/SurveyScope, branch `master`).
- ScopeFoundry source of truth: `.venv/Lib/site-packages/ScopeFoundry/`.
