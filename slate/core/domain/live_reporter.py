import logging
import socket
import json
import time
from PySide6.QtCore import QThread
from ..infra.server_hub import ServerHub
from ..system.hardware_info import HardwareInfo

class LiveReporter(QThread):
    def __init__(self, user_name="Unknown"):
        super().__init__()
        self.hub = ServerHub(); self.pc_name = socket.gethostname()
        self.user_name = user_name; self.running = True
        self.report_dir = self.hub.get_livestatus_dir()
        
        # Load heavy static specs ONCE at startup
        try:
            self.static_specs = HardwareInfo.get_static_specs()
        except Exception:
            self.static_specs = {}

        # Cache dynamic specs to avoid constant WMI calls
        self.cached_dynamic_specs = {}
        self.last_dynamic_update = 0
        self.update_interval = 300 # 5 Minutes

    def run(self):
        # Initial immediate report
        self.report_status(force_full=True)
        
        while self.running:
            for _ in range(30): 
                if not self.running: break
                time.sleep(1)
            if self.running:
                self.report_status()

    def update_user(self, user):
        self.user_name = user; self.report_status(force_full=True)

    def report_status(self, force_full=False):
        try:
            now = time.time()
            
            # --- MANUAL MODE ONLY ---
            # Only gather specs if we have NONE (Startup) or if FORCED.
            # No automatic time-based updates.
            if force_full or not self.cached_dynamic_specs:
                self.cached_dynamic_specs = HardwareInfo.get_dynamic_specs()
                self.last_dynamic_update = now
            
            # Combine all data
            import getpass
            data = {
                "pc_name": self.pc_name, 
                "user": self.user_name,       # UT Login (Software)
                "os_user": getpass.getuser(), # Windows Login (System)
                "status": "Online", 
                "last_seen": now,
                **self.static_specs,       # Static (boot time)
                **self.cached_dynamic_specs # Dynamic (Startup or Forced)
            }
            
            # Backward compatibility for 'disk_percent' if UI relies on it specifically
            # Find C drive usage
            c_drive = next((d for d in self.cached_dynamic_specs.get('Drives', []) if d['Root'] == 'C:'), None)
            if c_drive:
                # Parse "45.2%" -> 45.2 float
                try:
                    usage = float(c_drive['Usage'].strip('%'))
                    data['disk_percent'] = usage
                except Exception:
                    data['disk_percent'] = 0
            else:
                data['disk_percent'] = 0

            file_path = self.report_dir / f"{self.pc_name}.json"
            with open(file_path, 'w') as f: json.dump(data, f)
        except Exception as e:
            # Downgrade to debug to avoid spamming the console for offline drives
            logging.debug(f"LiveStatus Report Failed: {e}")
            self.cached_dynamic_specs = {} # Invalidate cache on error so we retry next time

    def stop(self):
        self.running = False; self.wait()