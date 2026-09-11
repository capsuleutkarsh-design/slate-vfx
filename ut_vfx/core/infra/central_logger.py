import json
import socket
import logging
from datetime import datetime
from .server_hub import ServerHub

class CentralLogger:
    def __init__(self):
        self.hub = ServerHub()
        self.log_dir = self.hub.get_log_path()
        self.pc_name = socket.gethostname()
        self.current_user = "Unknown"

    def set_user(self, user_id):
        self.current_user = user_id

    def log_event(self, event_type, details=""):
        if self.current_user == "Unknown":
            logging.debug("CentralLogger: skipping event %s because current user is unknown", event_type)
            return
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}_{self.current_user}.json"
        file_path = self.log_dir / filename
        
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "pc": self.pc_name,
            "event": event_type,
            "details": details
        }
        
        data = []
        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                logging.debug("CentralLogger: failed reading %s (%s)", file_path, exc)

        data.append(entry)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        except (OSError, TypeError, ValueError) as exc:
            logging.warning("CentralLogger: failed writing %s (%s)", file_path, exc)

    def log_login(self): self.log_event("LOGIN", "Active")
    def log_logout(self): self.log_event("LOGOUT", "Session End")
    def log_idle_start(self): self.log_event("IDLE_START", "Inactive > 5m")
    def log_idle_end(self, d): self.log_event("IDLE_END", f"Back after {d}")
