import logging
import json
import socket
import time
from datetime import datetime
from .global_config import GlobalConfig

class AuditLogger:
    """
    Records sensitive operations (sign-in, user changes, data changes).

    Where: the database's append-only audit trail (slate_secure.audit_trail,
    made by Slate Server; the workstations may add to it and read it, never
    change or delete it). Only when the database cannot take the line - an
    older server, the local copy, an outage - does it go to the daily file on
    the share, as before. The Audit Logs screen reads both.
    """

    _instance = None
    # After the database refused a line, the file is used for this long before
    # the database is tried again, so an older server costs one error, not one
    # per sign-in.
    DB_RETRY_SECONDS = 300
    _db_retry_at = 0.0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuditLogger, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @staticmethod
    def log_directory():
        """
        Where the daily audit files live. The Audit Logs screen reads them from
        here rather than working the path out again.
        """
        return GlobalConfig.server_root() / "Logs" / "Audit"

    def __init__(self):
        if self._initialized: return

        # Nothing touches the share here: this is built during sign-in, and a
        # share that is down must not make signing in slow.
        self.log_dir = self.log_directory()
        self._initialized = True

    def _ensure_dir(self):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logging.exception(f"Failed to create Audit Log directory: {e}")

    def _to_database(self, event_type, user, details, status) -> bool:
        """The line into slate_secure.audit_trail. False when it did not go in."""
        if time.monotonic() < AuditLogger._db_retry_at:
            return False
        try:
            from . import database_manager as dbm
            # Only a connection that is already open: never connect for this.
            if not dbm.is_connected() or str(dbm.database_manager.active_mode).lower() != "postgres":
                return False
            if dbm.database_manager.execute_update(
                    "INSERT INTO slate_secure.audit_trail (kind, username, status, details, pc) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (str(event_type), str(user or ""), str(status), str(details or ""),
                     socket.gethostname())):
                return True
        except Exception as exc:
            logging.debug("Audit line not written to the database: %s", exc)
        AuditLogger._db_retry_at = time.monotonic() + self.DB_RETRY_SECONDS
        return False

    def log_event(self, event_type: str, user: str, details: str, status: str = "SUCCESS"):
        """
        Logs an event to the audit trail (the database; the daily file when it cannot).

        Args:
            event_type: Category (e.g., "AUTH", "USER_MGMT", "SYSTEM")
            user: Username performing the action
            details: Description of the action
            status: SUCCESS / FAILURE / WARNING
        """
        if self._to_database(event_type, user, details, status):
            return
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
            self._ensure_dir()
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
