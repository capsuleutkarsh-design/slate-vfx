import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

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
        except Exception as e:
            logging.exception(f"Check Out Failed: {e}")
            return False

    def get_attendance(self, limit: int = 100) -> List[Dict[str, Any]]:
        q = "SELECT * FROM ut_attendance ORDER BY check_in_time DESC LIMIT %s"
        rows = self.db.execute_query(q, (limit,)) or []
        return [dict(r) for r in rows]
