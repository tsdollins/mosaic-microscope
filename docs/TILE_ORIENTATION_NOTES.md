# Tile / Image Orientation — What we found and fixed

The camera is mounted rotated/mirrored relative to the stage, and several
display and data-layout conventions were disagreeing. There turned out to be
**three separate problems**, each with a different root cause and fix.

## 1. Live view rotated 90° from stage motion (+x moved the view "up")
- **Cause:** the live preview's pyqtgraph `ImageItem` used the default
  *col-major* axis order, which transposes a numpy `[row, col]` frame — so the
  camera's axes showed up swapped relative to the stage.
- **Fix:** display the frame row-major and set the plot to `invertY(True)` so a
  numpy image is drawn the normal way (row 0 at top). `+x` now reads horizontal.

## 2. Scans looked vertically flipped, but a single snap looked correct
- **Cause:** pyqtgraph also draws with the Y axis pointing *up* (row 0 at the
  bottom). That display flip silently cancelled the vertical flip we applied to
  the live view, so the preview looked right — but the **saved scan** had nothing
  to cancel it, so it came out flipped. In short, the live view and the saved
  data needed *opposite* vertical flips, which one shared setting can't provide.
- **Fix:** move the vertical correction into the display (`invertY`, see #1) and
  leave the saved pixels raw. A single orientation transform, `cam.orient_frame()`
  (settings `flip_h` / `flip_v`, both **default off**), is now applied identically
  to the live view, snaps, and scan writes — so all three always match. Raw frames
  are already correct, so no flip is baked into the data.

## 3. Mosaic showed the bottom row at the top (tiles themselves were fine)
- **Cause:** the scan rasters `v0 → v1` (bottom → top, since +y is up), so data
  **row 0 is the bottom** of the sample. Image/napari viewers put row 0 at the
  **top**, flipping the row order (individual tiles were never affected).
- **Fix:** reverse the row placement in the **assembly scripts**, not the data —
  place row `r` from the bottom up (`(Nv-1-r)`). The h5 stays in natural scan
  order, so this also fixes every *existing* scan (no re-scan needed).
  - `MosaicViewer.py`, `AshlarTest.py`, `NoVignetteASHLAR.py`,
    `full_stitch_process.py`: `tile_position` / placement use `(Nv-1-r)`.
  - `MosaicViewerContinuous.py` (coords-based): maps max-y → top
    (`py = (ys.max()-ys)*scale`).
  - `LazyViewer.py`: unchanged — it just displays a finished OME-TIF.

## Related, but separate, corrections
- **Stage X sign:** inverted at the Prior controller (`hostdirection`) via an
  `invert_x` setting so `+x` = right. Re-applied after every connect, because
  `controller.connect` resets it to default.
- **Sub-degree camera tilt:** a per-tile de-rotation (~ -1.5° to -1.75°,
  `ROTATION_CORRECTION_DEG`) is applied about each tile's center in the
  viewers/stitchers. Left there (not baked in) because rotating every stored tile
  would interpolate/soften the data.

## Net result
Live view, single snaps, and stitched mosaics now all agree. Orientation
corrections live in the **display and assembly layers**; the saved h5 stays raw
and in natural scan order. The camera flip settings exist for a future physical
re-mount but default to off.
</content>
