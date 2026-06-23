"""
ZWO camera diagnostic script.

Usage:
    uv run python diagnose_camera.py            # check + timed connect test
    uv run python diagnose_camera.py --restart  # also attempt USB device restart
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
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("ZWO Camera Diagnostic")

    check_dll()
    check_usb_device()
    check_processes()
    check_handle_exe()

    ok = timed_connect_test()

    if not ok and '--restart' in sys.argv:
        restart_usb_device()
        print("\nRetrying connect test after restart...")
        timed_connect_test()

    sys.exit(0 if ok else 1)
