
import logging

try:
    import ctypes
except ImportError:
    ctypes = None

class SingleInstance:
    """
    Windows Single Instance Lock using CreateMutex.
    Non-blocking. If locked, returns False immediately.
    """
    def __init__(self, app_name):
        self.app_name = app_name
        self.mutex_name = f"Global\\{app_name}_SingleInstance_Mutex"
        self.mutex_handle = None
        self.is_running = False

    def check(self):
        """Check if an instance is already running. Returns True if we are the FIRST instance."""
        if not ctypes:
            return True # Fallback for non-windows (though this is a windoze app)
            
        ERROR_ALREADY_EXISTS = 183
        
        self.mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, self.mutex_name)
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            self.is_running = True
            try:
                # Show Alert
                ctypes.windll.user32.MessageBoxW(0, f"The application '{self.app_name}' is already running.", "Already Running", 0x40 | 0x1000)
            except OSError as exc:
                logging.warning("SingleInstance alert display failed: %s", exc)
            return False
            
        return True # We are the first!

    def cleanup(self):
        """Release mutex handling optional cleanup."""
        if self.mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            except OSError as exc:
                logging.warning("SingleInstance cleanup failed: %s", exc)
            self.mutex_handle = None

    def __del__(self):
        try:
            self.cleanup()
        except OSError as exc:
            logging.debug("SingleInstance __del__ cleanup warning: %s", exc)
