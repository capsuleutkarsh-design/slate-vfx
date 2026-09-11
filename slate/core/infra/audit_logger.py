import logging
import json
from datetime import datetime
from .global_config import GlobalConfig

class AuditLogger:
    """
    Handles logging of sensitive operations (Login, User Creation, Data Modification)
    to a centralized, immutable-style audit log.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuditLogger, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized: return
        
        self.log_dir = GlobalConfig.server_root() / "Logs" / "Audit"
        self._ensure_dir()
        self._initialized = True
        
    def _ensure_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.exception(f"Failed to create Audit Log directory: {e}")

    def log_event(self, event_type: str, user: str, details: str, status: str = "SUCCESS"):
        """
        Logs an event to the daily audit file.
        
        Args:
            event_type: Category (e.g., "AUTH", "USER_MGMT", "SYSTEM")
            user: Username performing the action
            details: Description of the action
            status: SUCCESS / FAILURE / WARNING
        """
        timestamp = datetime.now()
        date_str = timestamp.strftime("%Y-%m-%d")
        log_file = self.log_dir / f"audit_{date_str}.log"
        
        entry = {
            "timestamp": timestamp.isoformat(),
            "type": event_type,
            "user": user,
            "status": status,
            "details": details
        }
        
        try:
            # We append line-by-line JSON (JSONL) for robustness and ease of parsing
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logging.exception(f"AUDIT LOG FAILURE: {e}")

    # --- Convenience Methods ---
    
    def log_auth(self, user, success=True, reason=""):
        status = "SUCCESS" if success else "FAILURE"
        details = "Login successful" if success else f"Login failed: {reason}"
        self.log_event("AUTH", user, details, status)

    def log_user_change(self, admin_user, target_user, action):
        self.log_event("USER_MGMT", admin_user, f"{action} user: {target_user}")

    def log_system(self, details):
        self.log_event("SYSTEM", "SYSTEM", details)