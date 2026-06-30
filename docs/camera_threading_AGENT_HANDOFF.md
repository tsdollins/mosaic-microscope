# AGENT HANDOFF — Camera freeze/slowness investigation (Path B threading)

> Audience: an AI coding agent (you) resuming this work. Read this top-to-bottom, then
> re-read the cited source files before editing. Verify any claim against current code —
> the user edits files between turns (units/timings especially).

## CURRENT STATUS
- Work is PAUSED by user request. No code has been changed for the camera freeze/slowness fix.
- The user wants to pursue **Path B** (dedicated camera acquisition thread) but asked to stop
  before any implementation. Do NOT start editing on resume unless the user confirms.
- Branch: `master`. Uncommitted changes exist from prior tasks (scan-settings UI, continuous
  measurement port, simple_tiled blocking+drain). Nothing committed for the threading work.

## ENVIRONMENT / INVARIANTS
- Repo: `C:\Users\Lab\Documents\NewMicroscopeApp` (Windows, bash shell, git).
- `uv` is NOT on PATH in bash. Invoke as `/c/Users/Lab/.local/bin/uv.exe`.
  Run python: `"/c/Users/Lab/.local/bin/uv.exe" run python ...`
- Branches: `master` (working camera, no hardware binning) and `HardwareBinning`
  (master + hardware-binning feature commit `1758a7e`; master is a direct ancestor →
  clean fast-forward/merge possible).
- Vendor SDK binaries under `ScopeFoundryHW/HW_zwo_camera/ASI_*` are gitignored. Never stage them.
- HW components live in `ScopeFoundryHW/` (convention; not project root).
- Stage units: app/LQ layer is **mm**; conversion to µm happens at the Prior SDK boundary.

## THE TWO PROBLEMS (root causes, confirmed from source)
1. **Random freeze / "not responding"** — two mechanisms:
   - (A) Blocking camera I/O on the GUI thread. `ZWOCameraCaptureMeasure._on_live_img_timer`
     runs on a GUI-thread `QTimer` and calls `cam.capture_video_frame()` → `ASIGetVideoData`,
     which blocks until a frame/timeout. USB hiccup/dropped frame → GUI frozen.
   - (B) Concurrent ASI SDK access from two threads with NO lock. Measurements run in
     `MeasurementQThread` (ScopeFoundry `measurement.py:32`, launched `:185`, calls `run()` `:254`).
     During a scan, the measurement thread AND the GUI live-preview timer both call the SDK.
     ZWO ASI SDK is not thread-safe per camera → deadlock/hang. The two GUI-thread timers do
     NOT race each other (serialized on GUI thread); GUI-thread-vs-measurement-thread DOES race.
   - `grep` confirmed: zero `threading`/`Lock` in `ScopeFoundryHW/HW_zwo_camera/` (only vendor
     binaries/`setAspectLocked` matched).
2. **GUI sluggish when live_img on** — GUI-thread saturation. Per tick `_on_live_img_timer` does,
   all on the GUI thread: full-sensor grab (6248×4176 ≈ 26 MP; ~26 MB RAW8 / ~52 MB RAW16 /
   ~78 MB RGB24) + optional `np.stack` BGR→RGB copy + `setImage(...)`. `setImage` on ~26 MP is
   the dominant cost (hundreds of ms CPU). Defaults: `px_bin=1` (no downsample, `im[::1,::1]`),
   `img_type` default = first choice `RAW8`, `live_update_period=500` ms. So the GUI thread is
   busy a large fraction of each interval → laggy.

## SCAN LOOP ORDERING (verified — relevant to "is collect_pixel a blocker")
`base_raster_slow_scan.py` lines 62–99, single-threaded sequential per pixel:
`move_position_slow/fast(...)` (line 83/89) → `collect_pixel(...)` (line 98) → next iter moves.
So the next move cannot start until `collect_pixel` returns. `simple_tiled_image.collect_pixel`
now: `while self.stage.is_busy_xy(): sleep(0.01)` → `sleep(0.5)` settle → `cam.capture_fresh_frame()`.
=> Stage is stationary during capture; next move waits for capture. CONFIRMED YES.

## RELEVANT CODE (re-read before editing)
- `ScopeFoundryHW/HW_zwo_camera/zwo_camera_hw.py`
  - `start_video_capture` / `stop_video_capture` (sets `self._video_capture_on`).
  - `capture_video_frame(self)` — raises if not capturing; else `self.camera.capture_video_frame()`.
  - `capture_fresh_frame(self, drain_timeout_ms=50)` — drains buffered (stale/blurred) frames via
    `capture_video_frame(timeout=...)` until it raises (buffer empty), then returns next frame.
    Used by simple_tiled to avoid motion-blurred buffered frames. (On measurement thread.)
  - `live_update_timer` (GUI QTimer) → `on_live_update_timer` → `read_from_hardware()` (control
    values; also GUI-thread USB traffic).
  - NOTE on master there is NO `set_roi_format`/`restore_full_roi`/`hw_bin` (those are only on
    the `HardwareBinning` branch).
- `ScopeFoundryHW/HW_zwo_camera/zwo_camera_capture_measure.py`
  - settings: `live_img`(bool), `live_update_period`(int ms,500), `rotate`(bool),
    `px_bin`(int,1, choices 1..32). (No `hw_bin` on master.)
  - `setup_figure` creates `self.live_img_update_timer` (QTimer) → `_on_live_img_timer`,
    interval `live_update_period` (listener updates interval live).
  - `_on_live_img_timer` (GUI thread): grab → rotate → px_bin stride slice → BGR→RGB np.stack →
    `self.live_img_item.setImage(image=im, autoLevels=False)` → setRect. (autoLevels=False is
    also why the preview looks dim vs ASI Studio — separate issue, see below.)
  - `snap_and_save` uses `cam.camera.*` directly (full-frame snap).
- `measurements/simple_tiled_image.py` — `SimpleTiledImage(PriorStage2DScan)`; pre_scan_setup
  `cam.start_video_capture()`; post_scan_cleanup `cam.stop_video_capture()`; collect_pixel as above.
- `measurements/continuous_motion_image.py` — `ContinuousMotionImage(PriorStage2DScan)`; overrides
  `run()` for continuous capture-while-moving; uses `cam.capture_video_frame()` (wants buffered
  stream while moving — leave as-is). Registered in `microscope_app.py`.
- `ScopeFoundryHW/HW_prior_stage/prior_stage_raster.py` — `PriorStage2DScan(BaseRaster2DSlowScan)`;
  sets `self.stage = app.hardware['prior_stage']`; move_position_start/slow/fast in mm.
- `ScopeFoundryHW/HW_prior_stage/prior_stage_hw.py` — has `is_busy_xy()`, `halt_xy()`, mm LQs
  `x_position`(ro)/`x_target`/`y_*`/`speed_xy`(mm/s).

## PATH B PLAN (agreed direction; implement only on user confirm; use a new branch)
Producer/consumer: dedicated camera acquisition thread grabs frames; GUI slot draws them.
Implement in this order:
1. **Camera lock** — add `self._cam_lock = threading.Lock()` in `ZWOCameraHW`; wrap EVERY SDK
   access (`capture_video_frame`, `capture_fresh_frame`, control read/write funcs in `connect()`,
   `start/stop_video_capture`, `set_img_type`) in `with self._cam_lock:`. Keep critical sections
   to a single SDK call. (Fixes freeze Mechanism B. Low risk; valuable on its own.)
2. **Acquisition thread** — `CameraAcquisitionThread(QtCore.QThread)` with
   `frame_ready = Signal(object)`; run loop `while not self._stop: with lock: f=grab(); emit f`;
   clean `stop()` (flag + `wait()`). zwoasi returns a fresh ndarray per grab, so handoff is safe
   (no shared buffer). (Fixes freeze Mechanism A.)
3. **Display slot** on capture measure `on_new_frame(frame)` (GUI thread): move rotate/BGR/px_bin/
   setImage/setRect here. Add **drop-to-latest** (skip if a draw is still pending / only keep
   newest) so draws can't queue up. Retire `_on_live_img_timer` grab; start/stop the thread on
   `live_img` toggle. (Reduces slowness — but setImage stays on GUI thread, so ALSO need smaller
   frames: px_bin and/or fold in HardwareBinning for a cheap setImage.)
4. **Pause preview during scans** — in `simple_tiled`/`continuous` pre_scan_setup stop preview
   thread; post_scan_cleanup restart. Avoids two threads pulling the same video stream.
5. **Clean shutdown** — stop+join the thread on `live_img` off, in `disconnect()`, and on app
   exit. CRITICAL: a non-stopped thread will itself cause the exit "not responding". 

### What Path B does per problem (honest)
- Freeze A (GUI blocked on I/O): FIXED (grab off GUI thread).
- Freeze B (concurrent SDK): FIXED by the lock (required part of Path B).
- Slowness: REDUCED not eliminated — USB transfer + grab + np copy leave GUI thread, but
  `setImage` MUST stay on GUI thread (Qt rule). Needs drop-to-latest + smaller frames (binning/
  px_bin) to be truly smooth.
- Exit "not responding": IMPROVED if shutdown done right; WORSENED if thread not joined.

### Qt rules to honor
- Never touch a QWidget / pyqtgraph from the acquisition thread — only `emit` signals.
- Cross-thread signal→slot is auto-queued (runs slot on GUI thread). Good.
- LQ→widget updates use Qt signals already → safe from worker threads.

### Change surface
`zwo_camera_hw.py` (lock + thread class + start/stop + disconnect stop),
`zwo_camera_capture_measure.py` (replace timer grab with thread + on_new_frame + drop-to-latest),
`simple_tiled_image.py` & `continuous_motion_image.py` (pause/resume preview),
app/measurement shutdown (join thread on exit).

## ALTERNATIVE (Path A) — if user wants smaller change first
Keep QTimer model but add: (1) camera lock, (2) pause preview during scans, (3) short timeout +
try/except + re-entrancy guard on the live grab. Addresses both freeze mechanisms with much less
restructuring. Path B = Path A + the thread restructure.

## RELATED OPEN ITEMS (not the threading task, but in-flight)
- **Dim live preview** (separate from slowness): RESOLVED by the user (2026-06-24). It was the
  display-scaling difference vs ASI Studio (`setImage(..., autoLevels=False)` → no histogram
  stretch; `img_type` defaulting to RAW8 un-debayered Bayer). Resolution mechanism not captured
  in chat — verify in current `zwo_camera_capture_measure.py` / camera settings before assuming.
  No further action needed unless it regresses.
- **Standalone scripts imported as-is** (done, uncommitted): MosaicViewer.py,
  MosaicViewerContinuous.py, LazyViewer.py, AshlarTest.py, ASHLARContinuous, NoVignetteASHLAR.py,
  VignetteRemover.py, M2StitchTest.py. They need deps NOT installed: `ashlar`, `basicpy`,
  `m2stitch` (napari/numpy/scipy/h5py/tifffile already present). Offered `uv add`; user hasn't said.
- **new_bounds off-by-one**: ported faithfully from SurveyScope incl. quirk
  `Nh = s.Nh.val - 1; Lh = h + (Nh-1)*h*(1-overlap)` (effectively N-2 span). Flagged; user kept it.
- **Continuous Nh/Nv not wired to quickbar** (only simple_tiled's are); new_bounds sets both.
- **Nothing committed recently** — many uncommitted changes on master. Ask user before committing
  and about whether to branch for Path B.

## RESUME CHECKLIST
1. `git status` / `git branch --show-current`; confirm uncommitted state with user.
2. Confirm user wants Path B (vs Path A) and OK to branch.
3. Re-read the 6 source files above (user may have changed units/timings).
4. Implement in the 5-step order; verify each with
   `"/c/Users/Lab/.local/bin/uv.exe" run python -m py_compile <files>`.
5. Cannot fully test threading without hardware — note that; ask user to test on the scope.
