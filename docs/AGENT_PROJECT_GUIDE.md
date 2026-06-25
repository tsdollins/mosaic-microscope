# AGENT PROJECT GUIDE — NewMicroscopeApp

Condensed project-level reference for any AI agent. Verify against current code before editing
(files change between sessions). Not tied to any one conversation.

## SCOPE
ScopeFoundry-based **mosaic scanning microscope** app. Hardware: **Prior Scientific** XY stage +
**ZWO ASI2600MC Pro** color camera (26 MP, 6248×4176). Purpose: tiled mosaic imaging —
step-and-shoot (`simple_tiled_image`) and capture-while-moving (`continuous_motion_image`) —
then stitch (ashlar / m2stitch) and view (napari). Ported from the ASI-stage predecessor
**SurveyScope** (https://github.com/tsdollins/SurveyScope, default branch `master`) — use it as
the reference for porting measurements/UI.

## ENVIRONMENT
- Windows 11, git-bash shell, git repo at `C:\Users\Lab\Documents\NewMicroscopeApp`.
- Python via **uv**, BUT uv is NOT on bash PATH → call `"/c/Users/Lab/.local/bin/uv.exe"`.
  Run: `uv.exe run python ...`; add deps: `uv.exe add <pkg>`. Python `>=3.12,<3.13`.
- Run app: `uv.exe run python microscope_app.py` (or console script `microscope-app`).
- No test suite. Verify edits with `uv.exe run python -m py_compile <files>` and module import.
  Full runtime needs real hardware (stage on serial COM port, camera on USB3).
- pyproject deps: scopefoundry>=1.3.0, numpy, pyqt6, pyqtgraph, h5py, zwoasi, pyserial, imageio,
  ipython, qtconsole, napari. Stitching/viewer scripts also need (NOT installed):
  `ashlar`, `basicpy`, `m2stitch`.

## LAYOUT
- `microscope_app.py` — entry. `MicroscopeApp(BaseMicroscopeApp)`, name "MosaicMicroscope".
  `setup()` registers HW + measurements then `settings_load_ini('microscope_defaults.ini')`
  (MUST be last). `_post_setup_ui_quickaccess()`→`setup_quickbar()`. `new_bounds()` computes
  scan bounds from tile FOV+overlap.
- `microscope_quickbar.ui` — quickbar (Qt .ui). Groups: ASI Stage Position, Scan Parameters,
  Stage Locator, ZWO Camera. ⚠ in .ui use `<number>` not `<int>`.
- `microscope_defaults.ini` — default settings (Prior port/speed, scan params). Loaded last.
- `ScopeFoundryHW/HW_prior_stage/` — `prior_stage_hw.py`, `prior_stage_control_measure.py`,
  `prior_stage_control.ui`, `prior_stage_raster.py` (`PriorStage2DScan(BaseRaster2DSlowScan)`).
- `ScopeFoundryHW/HW_zwo_camera/` — `zwo_camera_hw.py`, `zwo_camera_capture_measure.py`,
  vendor SDK dirs `ASI_*` (gitignored).
- `measurements/` — `simple_tiled_image.py`, `continuous_motion_image.py`, others.
- Root standalone scripts imported as-is from SurveyScope: `MosaicViewer.py`,
  `MosaicViewerContinuous.py`, `LazyViewer.py`, `AshlarTest.py`, `ASHLARContinuous`,
  `NoVignetteASHLAR.py`, `VignetteRemover.py`, `M2StitchTest.py`.
- `data/` (gitignored, h5+jpg+tif output), `log/`, `docs/`. `diagnose_camera.py` (USB/handle
  diagnostics; optional Sysinternals handle.exe).

## CONVENTIONS / MEMORIES (override defaults)
- **Hardware components live in `ScopeFoundryHW/`**, never project root.
- **Stage units: app/LQ layer = mm; convert to µm at the Prior SDK boundary** (SDK = integer µm).
  Prior `goto_position` needs BOTH x and y every call → during scans pass TARGET values, not
  polled positions (else the other axis snaps back).
- `settings_load_ini` only AFTER all `add_hardware`/`add_measurement`.
- Don't commit unless asked; if asked, end commit msg with the Co-Authored-By trailer. Never
  stage vendor SDK binaries (gitignored).

## HARDWARE NOTES
- **Prior stage** (`PriorStageHW`): Windows DLL via ctypes WinDLL, integer µm. LQs (mm):
  `x_position`/`y_position` (ro), `x_target`/`y_target`, `speed_xy` (mm/s). Methods:
  `is_busy_xy()`, `halt_xy()`, `goto_position(x_um, y_um, wait=False)`. Serial COM port in ini.
- **ZWO camera** (`ZWOCameraHW`): `zwoasi` wrapper; DLL at
  `ScopeFoundryHW/HW_zwo_camera/ASI_Windows_SDK_V1.28/ASI SDK/lib/x64/ASICamera2.dll`.
  img_types: RAW8/RGB24/RAW16/Y8 (default = first = RAW8). Video mode:
  `start_video_capture` / `capture_video_frame` / `stop_video_capture`; plus
  `capture_fresh_frame()` (drains stale buffered frames). ⚠ **SDK not thread-safe per camera**
  (serialize all access). "ASI SDK library not found" warning at import is harmless (module-level
  `init()` with no path); `connect()` re-inits with the explicit DLL path. Force-quit can leave
  the camera handle open → connect hang; replug USB (see `diagnose_camera.py`).

## SCOPEFOUNDRY ESSENTIALS (installed at `.venv/Lib/site-packages/ScopeFoundry/`)
- `BaseMicroscopeApp`: `setup`, `add_hardware`, `add_measurement`, `add_quickbar`,
  `settings_load_ini`, `_post_setup_ui_quickaccess`.
- `HardwareComponent`: `setup` (define LQs), `connect`/`disconnect`; `settings.New(...)`;
  `lq.connect_to_hardware(read_func, write_func)`; `lq.connect_to_widget(qwidget)`.
- `Measurement`: `setup`, `run` — **runs in a background `MeasurementQThread`** (`measurement.py`).
- `BaseRaster2DSlowScan` (`scanning/base_raster_slow_scan.py`): provides settings
  `Nh,Nv,h0,h1,v0,v1,save_h5,continuous_scan`; hooks `pre_scan_setup`, `post_scan_cleanup`,
  `move_position_start/slow/fast`, `collect_pixel(pixel_num,k,j,i)`. **Scan loop is single-thread
  sequential**: move → collect → next move (so collect_pixel blocks the next move).
- Concurrency reality: GUI-thread QTimers (live preview, HW control reads) vs the
  measurement-thread scan both touch the camera → needs a lock (known issue; see threading notes).

## GIT
- `master` = working baseline (Prior stage + camera, no hardware binning).
- `HardwareBinning` = master + hardware-binning feature commit; master IS its ancestor
  (clean fast-forward/merge). Do disruptive/experimental work on a feature branch.

## INTERNET / EXTERNAL RESOURCES
- SurveyScope repo (port reference): raw fetch `https://raw.githubusercontent.com/tsdollins/SurveyScope/master/<path>`.
- ScopeFoundry source for ground-truth behavior: `.venv/Lib/site-packages/ScopeFoundry/`.
- Vendor ASI SDK bundled under `HW_zwo_camera/ASI_*`; Prior DLL via its HW module.
- Optional: Sysinternals `handle.exe` for camera-handle diagnostics.
