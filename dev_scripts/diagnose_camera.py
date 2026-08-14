"""
ZWO camera diagnostic script.

Usage:
    uv run python diagnose_camera.py            # check + timed connect test
    uv run python diagnose_camera.py --restart  # also attempt USB device restart
    uv run python diagnose_camera.py --usb      # USB link + throughput benchmark
                                                # (is it USB3? bad/slow cable?)
"""

import sys
import os
import threading
import subprocess
import ctypes as c
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(
    PROJECT_ROOT,
    r"ScopeFoundryHW\HW_zwo_camera\ASI_Windows_SDK_V1.28\ASI SDK\lib\x64\ASICamera2.dll",
)
CONNECT_TIMEOUT = 6.0  # seconds before declaring a hang
CAM_ID = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ps(cmd):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def section(title):
    print(f"\n{'=' * 50}")
    print(f"  {title}")
    print('=' * 50)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_dll():
    section("DLL")
    print(f"Expected path : {DLL_PATH}")
    print(f"File exists   : {os.path.exists(DLL_PATH)}")


def check_usb_device():
    section("USB / PnP device status")
    out = ps(
        "Get-PnpDevice | "
        "Where-Object {$_.FriendlyName -like '*ASI*' -or $_.FriendlyName -like '*ZWO*'} | "
        "Select-Object Status, Class, FriendlyName, InstanceId | Format-List"
    )
    if out:
        print(out)
    else:
        print("No ASI/ZWO device found. Listing all Camera-class devices:")
        print(ps("Get-PnpDevice -Class Camera | Select-Object Status, FriendlyName | Format-Table -AutoSize"))


def check_processes():
    section("Processes that may hold the camera")
    out = ps(
        "Get-Process | "
        "Where-Object {$_.Name -like '*python*' -or $_.Name -like '*ASI*' "
        "-or $_.Name -like '*ZWO*' -or $_.Name -like '*scope*'} | "
        "Select-Object Id, Name, StartTime | Format-Table -AutoSize"
    )
    print(out or "  (none found)")


def check_handle_exe():
    """Use Sysinternals handle.exe if available to see who has the device open."""
    section("Open handles to camera device (requires Sysinternals handle.exe)")
    handle_exe = shutil_which("handle64.exe") or shutil_which("handle.exe")
    if handle_exe:
        out = subprocess.run(
            [handle_exe, "-accepteula", "ASICamera"],
            capture_output=True, text=True,
        ).stdout.strip()
        print(out or "  No open handles found.")
    else:
        print("  handle.exe not found in PATH — skipping.")
        print("  Install Sysinternals: https://learn.microsoft.com/sysinternals/downloads/handle")


def shutil_which(name):
    import shutil
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Timed connect test
# ---------------------------------------------------------------------------

def timed_connect_test():
    section(f"Timed connect test (timeout = {CONNECT_TIMEOUT}s)")

    state = {'step': 'start', 'num_cameras': None, 'success': False, 'error': None}
    done = threading.Event()

    def _worker():
        try:
            state['step'] = 'LoadLibrary'
            zwolib = c.cdll.LoadLibrary(DLL_PATH)

            zwolib.ASIGetNumOfConnectedCameras.restype = c.c_int
            zwolib.ASIOpenCamera.argtypes = [c.c_int]
            zwolib.ASIOpenCamera.restype = c.c_int
            zwolib.ASIInitCamera.argtypes = [c.c_int]
            zwolib.ASIInitCamera.restype = c.c_int
            zwolib.ASICloseCamera.argtypes = [c.c_int]
            zwolib.ASICloseCamera.restype = c.c_int

            state['step'] = 'ASIGetNumOfConnectedCameras'
            n = zwolib.ASIGetNumOfConnectedCameras()
            state['num_cameras'] = n

            state['step'] = f'ASICloseCamera({CAM_ID}) [defensive pre-close]'
            zwolib.ASICloseCamera(CAM_ID)

            state['step'] = f'ASIOpenCamera({CAM_ID})'
            r = zwolib.ASIOpenCamera(CAM_ID)
            if r != 0:
                state['error'] = f'ASIOpenCamera returned error code {r}'
                return

            state['step'] = f'ASIInitCamera({CAM_ID})'
            r = zwolib.ASIInitCamera(CAM_ID)
            if r != 0:
                state['error'] = f'ASIInitCamera returned error code {r}'
                zwolib.ASICloseCamera(CAM_ID)
                return

            state['step'] = f'ASICloseCamera({CAM_ID}) [cleanup]'
            zwolib.ASICloseCamera(CAM_ID)
            state['success'] = True

        except Exception as e:
            state['error'] = str(e)
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    last_step = None
    deadline = time.monotonic() + CONNECT_TIMEOUT
    while time.monotonic() < deadline:
        if done.wait(timeout=0.5):
            break
        if state['step'] != last_step:
            print(f"  [{time.strftime('%H:%M:%S')}] {state['step']} ...")
            last_step = state['step']

    if not done.is_set():
        print(f"\n  *** HANG DETECTED ***")
        print(f"  Stuck at: {state['step']}")
        _diagnose_hang(state['step'])
        return False

    if state.get('num_cameras') is not None:
        print(f"  SDK reports {state['num_cameras']} camera(s) connected")

    if state['error']:
        print(f"\n  FAILED: {state['error']}")
        return False

    print(f"  OK — camera opened and closed successfully in < {CONNECT_TIMEOUT}s")
    return True


def _diagnose_hang(step):
    print("\n  Likely causes:")
    if 'Open' in step:
        print("    - A previous app session was force-quit and left the camera handle open")
        print("    - Another process (ASI Studio, SharpCap, etc.) has the camera open")
        print("    - USB device is in a bad state")
    elif 'Init' in step:
        print("    - Camera opened but USB bandwidth/power issue preventing init")
        print("    - Try a different USB port or powered hub")
    print("\n  Recommended actions:")
    print("    1. Unplug and replug the USB cable  (most reliable)")
    print("    2. Run with --restart to attempt an automated PnP device restart")
    print("    3. Check 'check_processes' output above for competing processes")


# ---------------------------------------------------------------------------
# USB restart
# ---------------------------------------------------------------------------

def restart_usb_device():
    section("Restarting USB device via PnP")
    out = ps("""
$dev = Get-PnpDevice | Where-Object {
    $_.FriendlyName -like '*ASI*' -or $_.FriendlyName -like '*ZWO*'
} | Select-Object -First 1
if ($dev) {
    Write-Output "Disabling: $($dev.FriendlyName)"
    Disable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false
    Start-Sleep -Seconds 2
    Enable-PnpDevice -InstanceId $dev.InstanceId -Confirm:$false
    Write-Output "Re-enabled."
} else {
    Write-Output "No ASI/ZWO PnP device found."
}
""")
    print(out)
    print("Waiting 3s for device to settle...")
    time.sleep(3)


# ---------------------------------------------------------------------------
# USB link + throughput benchmark
# ---------------------------------------------------------------------------

# Bytes per pixel for each ASI image type id.
_BYTES_PER_PX = {0: 1, 1: 3, 2: 2, 3: 1}  # RAW8, RGB24, RAW16, Y8
_IMG_NAME = {0: "RAW8", 1: "RGB24", 2: "RAW16", 3: "Y8"}

# Rough real-world thresholds (MB/s) for the full-frame transfer rate.
USB3_HEALTHY_MBPS = 120.0   # >= this: link behaving like USB3
USB2_LIKELY_MBPS = 50.0     # <= this: link behaving like USB2 / bad cable
N_FRAMES = 30               # frames to time
N_WARMUP = 3                # discarded warmup frames
FRAME_TIMEOUT_MS = 5000     # per-frame wait before counting a timeout


def _bench_format(cam, asi, width, height, image_type):
    """Grab N_FRAMES at the given full-res image_type; return metrics dict.

    Throughput is measured only from frames that actually arrived; timed-out
    grabs are counted separately (a stalled stream must not look like 'slow USB').
    """
    cam.set_roi_format(width, height, 1, image_type)
    bytes_per_frame = width * height * _BYTES_PER_PX[image_type]

    dropped_before = cam.get_dropped_frames()
    cam.start_video_capture()
    times = []
    timeouts = 0
    try:
        for _ in range(N_WARMUP):
            try:
                cam.capture_video_frame(timeout=FRAME_TIMEOUT_MS)
            except Exception:
                pass

        for _ in range(N_FRAMES):
            t0 = time.perf_counter()
            try:
                cam.capture_video_frame(timeout=FRAME_TIMEOUT_MS)
                times.append(time.perf_counter() - t0)
            except Exception:
                timeouts += 1
    finally:
        cam.stop_video_capture()

    dropped = cam.get_dropped_frames() - dropped_before
    frames_ok = len(times)
    per_frame = (sum(times) / frames_ok) if frames_ok else float("inf")
    mbps = (bytes_per_frame / per_frame) / 1e6 if frames_ok else 0.0
    return {
        "name": _IMG_NAME[image_type],
        "bytes_per_frame": bytes_per_frame,
        "frames_ok": frames_ok,
        "ms_per_frame": (per_frame * 1000.0) if frames_ok else float("inf"),
        "mbps": mbps,
        "dropped": dropped,
        "timeouts": timeouts,
    }


def usb_throughput_test():
    section("USB link + throughput benchmark")
    try:
        import zwoasi as asi
    except Exception as e:
        print(f"  Could not import zwoasi: {e}")
        return False

    try:
        asi.init(DLL_PATH)
    except Exception as e:
        # init() raises if already initialised in this process — that's fine.
        if "already" not in str(e).lower():
            print(f"  asi.init failed: {e}")
            return False

    if asi.get_num_cameras() < 1:
        print("  No camera detected by the SDK.")
        return False

    cam = None
    try:
        cam = asi.Camera(CAM_ID)
        props = cam.get_camera_property()
        w, h = props["MaxWidth"], props["MaxHeight"]

        # --- Link type (the headline check) ---
        is_usb3_cam = props.get("IsUSB3Camera")
        is_usb3_host = props.get("IsUSB3Host")
        print(f"  Camera          : {props.get('Name')}  ({w}x{h})")
        print(f"  IsUSB3Camera    : {is_usb3_cam}")
        print(f"  IsUSB3 link     : {is_usb3_host}   <-- port + cable actually negotiated USB3?")
        if is_usb3_host is False:
            print("  >>> Link negotiated as USB2. A 26 MP camera over USB2 is very slow")
            print("      -> suspect the cable (use the short ZWO USB3 cable) or a USB2 port.")

        # short exposure so transfer time dominates, not exposure time
        try:
            cam.set_control_value(asi.ASI_EXPOSURE, 1000)   # 1 ms
        except Exception:
            pass
        try:
            bw = cam.get_control_value(asi.ASI_BANDWIDTHOVERLOAD)[0]
            print(f"  BandWidth ctrl  : {bw} %")
        except Exception:
            pass

        # --- Throughput at full resolution ---
        print(f"\n  Benchmarking {N_FRAMES} full-res frames per format "
              f"(warmup {N_WARMUP}, exposure 1 ms)...")
        results = []
        for image_type in (0, 2):  # RAW8 (app default), RAW16
            try:
                r = _bench_format(cam, asi, w, h, image_type)
                results.append(r)
                ms = r["ms_per_frame"]
                ms_str = f"{ms:6.0f}" if ms != float("inf") else "   n/a"
                print(f"    {r['name']:5s}: {r['bytes_per_frame']/1e6:5.1f} MB/frame  "
                      f"{ms_str} ms/frame  {r['mbps']:5.0f} MB/s  "
                      f"ok={r['frames_ok']}/{N_FRAMES}  "
                      f"dropped={r['dropped']}  timeouts={r['timeouts']}")
            except Exception as e:
                print(f"    {_IMG_NAME[image_type]}: benchmark failed: {e}")

        # --- Verdict ---
        section("Verdict")
        if not results:
            print("  No formats benchmarked — inconclusive.")
            return False

        total_ok = sum(r["frames_ok"] for r in results)
        if total_ok == 0:
            print("  CAMERA STALL: zero frames delivered (every grab timed out).")
            print("  This is NOT a slow-cable signature -- a slow link still delivers")
            print("  frames, just slowly. The video stream produced nothing at all.")
            print("  This is very likely the same intermittent stall that freezes the app")
            print("  (capture_video_frame blocking on a frame that never arrives).")
            print("  Try:")
            print("    - Unplug/replug the camera USB to clear a stuck stream, then re-run.")
            print("    - Ensure no other process/app has the camera open.")
            print("    - If it recurs, it points to camera-state / threading, not the cable.")
            return False

        ok_results = [r for r in results if r["frames_ok"] > 0]
        raw8 = next((r for r in ok_results if r["name"] == "RAW8"), None)
        raw16 = next((r for r in ok_results if r["name"] == "RAW16"), None)
        # Peak MB/s across formats that delivered frames estimates link capacity.
        best_mbps = max((r["mbps"] for r in ok_results), default=0.0)
        any_loss = any(r["dropped"] or r["timeouts"] for r in results)

        # If RAW16 (2x the bytes) is NOT ~2x slower than RAW8, the per-frame time is
        # set by full-frame sensor readout, not USB bandwidth -> RAW8 MB/s is not a
        # link-speed measurement.
        readout_limited = False
        fps = 0.0
        if raw8 and raw16 and raw8["ms_per_frame"] > 0:
            if raw16["ms_per_frame"] / raw8["ms_per_frame"] < 1.5:
                readout_limited = True
                fps = 1000.0 / raw8["ms_per_frame"]

        if is_usb3_host is False or best_mbps <= USB2_LIKELY_MBPS:
            print(f"  LIKELY USB TRANSFER PROBLEM (peak {best_mbps:.0f} MB/s).")
            print("  Consistent with a USB2 link, a bad/long cable, or a hub.")
            print("  Actions: use the short ZWO USB3 cable; plug into a rear-panel")
            print("           USB3 (blue/'SS') port; remove hubs; re-run this test.")
            return False

        if any_loss:
            print(f"  PARTIAL LOSS (peak {best_mbps:.0f} MB/s, but some frames were "
                  f"dropped/timed out).")
            print("  Two possibilities: a marginal/bad cable (try the short ZWO USB3")
            print("  cable / a different port), OR an intermittent stream stall (the")
            print("  same thing that freezes the app). Re-run a few times: steady small")
            print("  losses => cable; occasional bursts of timeouts => stall/threading.")
            return False

        print(f"  Link looks HEALTHY (peak {best_mbps:.0f} MB/s, no dropped/timeout frames).")
        if readout_limited:
            print(f"  Note: both formats hit ~{raw8['ms_per_frame']:.0f} ms/frame "
                  f"(~{fps:.1f} fps) -> frame rate is limited by full-frame SENSOR")
            print("  READOUT, not USB. RAW8's lower MB/s is just fewer bytes in the same")
            print("  time, not a slow link.")
        print("  => USB transfer is NOT the cause. The freezes are the GUI-thread /")
        print("     SDK-threading issue (see docs handoff). For a faster live preview,")
        print("     reduce resolution (hardware binning / ROI) since readout caps the fps.")
        return True

    except Exception as e:
        print(f"  Benchmark error: {e}")
        return False
    finally:
        if cam is not None:
            try:
                cam.stop_video_capture()
            except Exception:
                pass
            try:
                cam.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("ZWO Camera Diagnostic")

    check_dll()
    check_usb_device()
    check_processes()
    check_handle_exe()

    if '--usb' in sys.argv:
        ok = usb_throughput_test()
        sys.exit(0 if ok else 1)

    ok = timed_connect_test()

    if not ok and '--restart' in sys.argv:
        restart_usb_device()
        print("\nRetrying connect test after restart...")
        timed_connect_test()

    sys.exit(0 if ok else 1)
