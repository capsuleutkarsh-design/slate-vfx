import sys
import subprocess
from PySide6.QtCore import QThread, Signal

class ProcessRunner(QThread):
    """
    Runs a shell command in a background thread and streams output.
    """
    log_output = Signal(str)      # Streamed stdout/stderr
    finished_code = Signal(int)   # Exit code
    error_occurred = Signal(str)  # Startup errors

    def __init__(self, command, cwd=None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self._is_running = True

    def run(self):
        try:
            # Prepare startup info to hide window on Windows
            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, # Merge stderr into stdout
                text=True,
                shell=True, # Allow batch files/shell commands
                startupinfo=startupinfo,
                encoding='cp1252' # Windows console default
            )

            # Stream output
            for line in self.process.stdout:
                if not self._is_running: break
                self.log_output.emit(line.rstrip())

            self.process.wait()
            self.finished_code.emit(self.process.returncode)

        except Exception as e:
            self.error_occurred.emit(str(e))
        
    def stop(self):
        """Force-kill the full process tree to avoid orphan child processes."""
        self._is_running = False
        if self.process:
            try:
                if self.process.poll() is None:
                    if sys.platform == 'win32':
                        # Kill shell + all children (pyinstaller, iscc, etc.).
                        subprocess.run(
                            ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False
                        )
                    else:
                        self.process.kill()
            except Exception:
                pass
