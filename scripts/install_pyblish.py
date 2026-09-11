import subprocess
import sys
import importlib

def install_and_check():
    log_file = "pyblish_install_log.txt"
    with open(log_file, "w") as f:
        f.write(f"Python Executable: {sys.executable}\n")
        
        # Install
        try:
            f.write("Installing pyblish-base pyblish-lite...\n")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyblish-base", "pyblish-lite"], stdout=f, stderr=f)
            f.write("Installation command finished.\n")
        except subprocess.CalledProcessError as e:
            f.write(f"Installation FAILED: {e}\n")
            return

        # Check
        try:
            import pyblish.api
            f.write(f"SUCCESS: Pyblish imported from {pyblish.api.__file__}\n")
        except ImportError as e:
            f.write(f"FAILURE: Could not import pyblish after install: {e}\n")
        except Exception as e:
            f.write(f"FAILURE: Unexpected error during import: {e}\n")

if __name__ == "__main__":
    install_and_check()
