"""Local test harness for the Crucible stitch pipeline.

Runs the EXACT cloud code path (crucible_stitch_process.process_scan) on a local
HDF5 scan file, so you can validate changes on your own machine before they run in
Crucible. Point IN_PATH at a raw scan .h5 and OUT_DIR at where you want
the results; everything else -- BaSiC fit, ashlar alignment, IFD-pyramid write,
thumbnail, geometry -- is whatever process_scan does in production.

The only thing this file adds on top of the shared core is local-machine setup:
pointing the JVM at your Adoptium JDK. That MUST happen before crucible_stitch_process
is imported, because importing it imports ashlar, which starts the JVM immediately.
"""
import os
import sys
import warnings

# Import crucible_stitch_process as a TOP-LEVEL module, exactly as the cloud
# consumer does (`import crucible_stitch_process`). It lives in the local
# crucible/ dir, so put that dir on sys.path. Importing it as
# `crucible.crucible_stitch_process` would instead resolve `crucible` to the
# pip-installed nano-crucible package (a real package, which shadows the local
# namespace dir) and fail -- and it would not match the cloud's import path.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "crucible"))

# --- Local-machine JVM setup (must precede the ashlar import below) ---
JAVA_HOME = r"C:\Program Files\Eclipse Adoptium\jdk-21.0.2.13-hotspot"
os.environ["JAVA_HOME"] = JAVA_HOME
jvm_dir = os.path.join(JAVA_HOME, "bin", "server")
os.environ["PATH"] = jvm_dir + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(jvm_dir)
except (AttributeError, FileNotFoundError):
    pass

# ashlar's utils.py calls deprecated scikit-image APIs that emit FutureWarnings
# from inside the library during blending. Silence them rather than patch site-packages.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"ashlar\.utils")

# Import the shared pipeline core. This import starts the JVM, hence the setup above.
from crucible.crucible_stitch_process import process_scan


# --- Local test paths ---
# Raw scan HDF5 to stitch, and the folder to write results into.
IN_PATH = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\260727_152029_simple_tiled_image.h5"
OUT_DIR = r"C:\Users\Lab\Documents\NewMicroscopeApp\data\stitch_results"


def main():
    results = process_scan(IN_PATH, OUT_DIR)
    print("\n--- process_scan results ---")
    for k, v in results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
