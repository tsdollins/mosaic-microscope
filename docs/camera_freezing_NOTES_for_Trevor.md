# Camera Freezing & Slowness — Notes (for Trevor)

A plain-language summary of what we figured out about the camera problems and the plan to
fix them. We paused before changing any code, so the fix described here has not been
implemented yet.

## The symptoms we discussed
1. **Random freezes** — the app stops responding to clicks; clicking the X gives Windows'
   "this app is not responding." Seems to only happen with the camera connected.
2. **Sluggish GUI** when the live image (live_img) is turned on.
3. **Dim live image** vs the ASI Studio software — ✅ **RESOLVED** (see bottom).

## Why the freezing happens
Two separate causes, both confirmed by reading the code:

- **The live preview runs on the same thread as the buttons/menus (the "GUI thread").**
  Every preview update grabs a frame from the camera over USB on that thread. Grabbing a
  frame *waits* for the camera. If the camera or USB hiccups, that wait freezes the entire
  interface until it finishes (or forever, if it stalls).

- **Two parts of the app talk to the camera at the same time with no traffic cop.**
  When you run a scan, the scan runs on its own thread and pulls images from the camera.
  Meanwhile the live preview (on the GUI thread) is *also* pulling images. The ZWO camera
  driver is not built to be called from two threads at once for the same camera — when they
  overlap, the driver can lock up. This explains the "random" timing: it only happens when
  the two overlap.

The exit "not responding" is the same thing — when you click X, the app is stuck mid-wait
on the camera, so it can't shut down cleanly.

## Why the GUI gets slow with live_img on
The camera is 26 megapixels. Every preview frame is large (about 26 MB and up depending on
the image format), and *all* the work — pulling the frame, processing it, and drawing it —
happens on the GUI thread. Drawing a 26-megapixel image is especially expensive (a fraction
of a second each time). So the thread that's supposed to be handling your clicks spends most
of its time on the preview, and the whole app feels laggy.

## The plan we agreed to try: "Path B"
Move the camera work onto its own background thread.

- A dedicated **camera thread** continuously grabs frames in the background.
- It sends each finished frame to the **GUI thread**, which only has to *draw* it.

What this fixes:
- **Freezing from waiting on the camera:** Fixed — the waiting happens in the background, so
  the buttons stay responsive.
- **Freezing from two threads at once:** Fixed — Path B includes adding a "traffic cop" (a
  lock) so only one part of the app uses the camera at a time. (This lock is required either
  way; it's the single most important fix for the lock-ups.)
- **Exit hang:** Fixed *if we shut the background thread down properly* on exit (we will).
- **Sluggishness:** Improved, but **not 100% solved by threading alone.** Because of how Qt
  works, the *drawing* of the image still has to happen on the GUI thread, and drawing a
  26-megapixel image is inherently expensive. To make the preview truly smooth we also need
  to make the frames smaller — that's "hardware binning," which we already built on a
  separate branch (`HardwareBinning`) and can fold in.

### Honest trade-offs
- Threading adds some complexity (starting/stopping the background thread cleanly, and
  coordinating it with scans so they don't fight over the camera).
- A simpler option ("Path A") would keep the current structure but add the traffic-cop lock,
  pause the preview during scans, and put a time limit on each frame grab. It fixes the
  freezes with much less rework, but doesn't make the preview as smooth as Path B.

### Suggested order when we resume
1. Add the camera lock (the traffic cop) — this alone should stop most of the lock-ups.
2. Add the background camera thread + send frames to the GUI to draw.
3. Pause the live preview while a scan is running; make shutdown clean.
4. Optionally bring over hardware binning so the preview is also fast/smooth.

We'd do all this on a separate git branch so it's easy to undo if needed.

## The "dim image" issue — RESOLVED
The dim live image (vs ASI Studio at the same settings) has been **resolved**. It was a
display-brightness/scaling difference, not a sensor problem — the app was showing the raw
camera values without the auto-brightening that ASI Studio applies. No further action needed
unless it comes back.

## Status / where things stand
- **No code changed** for the freezing/slowness fix yet — we stopped here on purpose.
- There are other unsaved changes from earlier in the session (the Scan Parameters panel, the
  continuous-motion measurement, and the "wait for the stage to stop before capturing" tweak
  to the tiled scan). Nothing has been committed recently.
- Other small to-dos noted for later: the imported viewer/stitching scripts need three Python
  packages installed (`ashlar`, `basicpy`, `m2stitch`) before they'll run; and the scan-bounds
  "Calculate" formula was copied exactly from SurveyScope including a likely off-by-one we can
  revisit.

When you're ready to pick this back up, just say so and we'll start with the camera lock.
