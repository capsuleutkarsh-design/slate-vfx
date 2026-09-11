import logging
import sys
import subprocess
from pathlib import Path
import ctypes

def log(msg):
    # In production, we might want to log to a file since no console
    pass

def error_exit(msg):
    ctypes.windll.user32.MessageBoxW(0, f"Launcher Error:\n{msg}", "UT Launcher", 0x10)
    sys.exit(1)

def main():
    # 1. Identify Environment
    if getattr(sys, 'frozen', False):
        # Running as compiled exe
        application_path = Path(sys.executable).parent
    else:
        # Running from source
        application_path = Path(__file__).parent.parent

    # 2. Target Executable
    # In the installed structure, UTVFX.exe is in the same folder
    target_exe = application_path / "UTVFX.exe"

    # 3. Launch
    if target_exe.exists():
        try:
            # Popen detach logic
            subprocess.Popen([str(target_exe)], cwd=str(application_path))
        except Exception as e:
            error_exit(f"Failed to launch application: {e}")
    else:
        # If not found, maybe we are in a dev environment or weird structure?
        # Try looking one level up or down?
        # For now, just error out if we are frozen.
        if getattr(sys, 'frozen', False):
            error_exit(f"Could not find UTVFX.exe at: {target_exe}")
        else:
            logging.info(f"Usage: This launcher expects UTVFX.exe at {target_exe}")

    sys.exit(0)

if __name__ == "__main__":
    main()
