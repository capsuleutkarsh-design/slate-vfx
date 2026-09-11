import time
import threading
from ctypes import Structure, windll, c_uint, sizeof, byref

class LASTINPUTINFO(Structure):
    _fields_ = [('cbSize', c_uint), ('dwTime', c_uint)]

class IdleMonitor:
    """
    Monitors Windows System Idle Time.
    """
    def __init__(self, threshold_seconds=300, logger=None):
        self.threshold = threshold_seconds
        self.logger = logger
        self.is_running = False
        self.is_idle = False
        self.idle_start_time = 0

    def get_idle_duration(self):
        """Returns seconds since last input."""
        info = LASTINPUTINFO()
        info.cbSize = sizeof(info)
        windll.user32.GetLastInputInfo(byref(info))
        millis = windll.kernel32.GetTickCount() - info.dwTime
        return millis / 1000.0

    def start(self):
        self.is_running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.is_running = False

    def _loop(self):
        while self.is_running:
            idle_sec = self.get_idle_duration()
            
            if not self.is_idle and idle_sec > self.threshold:
                self.is_idle = True
                self.idle_start_time = time.time()
                if self.logger: self.logger.log_idle_start()
            
            elif self.is_idle and idle_sec < 1.0:
                self.is_idle = False
                duration = time.strftime('%H:%M:%S', time.gmtime(time.time() - self.idle_start_time))
                if self.logger: self.logger.log_idle_end(duration)
            
            time.sleep(1)