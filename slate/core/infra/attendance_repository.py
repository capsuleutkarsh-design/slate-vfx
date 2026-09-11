import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

# A database that is down must not look like a studio with no data. The manager
# raises DatabaseUnavailableError precisely so a read cannot quietly come back
# empty; catching it here and returning a fallback puts the fault straight back.
# So it is re-raised, and anything else is logged before the fallback is used.
try:
    from .postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AttendanceRepository:
    """Attendance persistence methods extracted and expanded."""

    def __init__(self, db):
        self.db = db

    def log_check_in(self, username: str, notes: str = "") -> bool:
        try:
            timestamp = datetime.now().isoformat()
            q = """
                INSERT INTO ut_attendance (username, check_in_time, status, notes)
                VALUES (%s, %s, %s, %s)
            """
            self.db.execute_query(q, (username, timestamp, "checked_in", notes), fetch="none")
            return True
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logging.exception(f"Check In Failed: {e}")
            return False

    def log_check_out(self, username: str, notes: str = "") -> bool:
        try:
            timestamp = datetime.now().isoformat()
            q = """
                UPDATE ut_attendance
                SET check_out_time=%s, status=%s, notes=COALESCE(notes, '') || %s
                WHERE username=%s AND check_out_time IS NULL
            """
            return (self.db.execute_query(q, (timestamp, "checked_out", notes, username), fetch="rowcount") or 0) > 0
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            logging.exception(f"Check Out Failed: {e}")
            return False

    def get_attendance(self, limit: int = 100) -> List[Dict[str, Any]]:
        q = "SELECT * FROM ut_attendance ORDER BY check_in_time DESC LIMIT %s"
        rows = self.db.execute_query(q, (limit,)) or []
        return [dict(r) for r in rows]
