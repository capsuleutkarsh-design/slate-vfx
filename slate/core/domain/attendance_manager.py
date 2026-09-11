import sqlite3
import logging
import sys
from datetime import datetime
from pathlib import Path

class AttendanceManager:
    """
    Local SQLite Attendance Manager - OFFLINE MODE (FUTURE FEATURE)
    
    PURPOSE:
    --------
    This class provides a local SQLite-based attendance tracking system
    that can serve as a backup/fallback when the network drive is unavailable.
    
    CURRENT STATUS:
    --------------
    **NOT ACTIVELY USED** - The application currently uses `CentralAttendance`
    for all attendance operations, which stores data in centralized JSON files
    on the network drive.
    
    FUTURE INTEGRATION:
    ------------------
    This component is reserved for implementing offline mode functionality:
    - Automatic fallback when network drive is unmapped
    - Local caching of attendance records
    - Sync mechanism to push local records to central storage when network returns
    
    ARCHITECTURE:
    ------------
    - Database: %LOCALAPPDATA%/Slate/slate.db
    - Table: attendance (user_id, clock_in, clock_out, duration, date)
    - No duplicate clock-ins on same day
    
    NOTE: Kept in codebase for future offline mode implementation.
          Do not remove without consulting team lead.
    """
    def __init__(self, db_path=None):
        import os
        if db_path:
            self.db_path = Path(db_path)
            self.db_dir = self.db_path.parent
        else:
            if sys.platform == "win32":
                self.db_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "Slate"
            else:
                self.db_dir = Path.home() / ".slate"
            self.db_path = self.db_dir / "slate.db"
            
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.init_table()

    def _connect(self):
        """Create a fresh connection to the DB."""
        return sqlite3.connect(str(self.db_path))

    def execute_query(self, query, params=()):
        """Safe execution helper."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return True
        except Exception as e:
            logging.exception(f"Attendance DB Write Error: {e}")
            return False

    def fetch_one(self, query, params=()):
        """Safe fetch helper."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchone()
        except Exception as e:
            logging.exception(f"Attendance DB Read Error: {e}")
            return None

    def fetch_all(self, query, params=()):
        """Safe fetch all helper."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
        except Exception as e:
            logging.exception(f"Attendance DB Read All Error: {e}")
            return []

    def init_table(self):
        query = """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT,
            pc_name TEXT,
            clock_in_time TIMESTAMP,
            clock_out_time TIMESTAMP,
            duration TEXT,
            date TEXT,
            action TEXT,
            timestamp TIMESTAMP
        )
        """
        self.execute_query(query)
        # Add columns if migrating from older schema
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(attendance)")
                cols = [c[1] for c in cur.fetchall()]
                if "action" not in cols:
                    cur.execute("ALTER TABLE attendance ADD COLUMN action TEXT")
                if "timestamp" not in cols:
                    cur.execute("ALTER TABLE attendance ADD COLUMN timestamp TIMESTAMP")
                conn.commit()
        except Exception:
            pass

    def clock_in(self, user_id, user_name, pc_name):
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        
        # Check if already clocked in
        check_q = "SELECT id FROM attendance WHERE user_id = ? AND date = ? AND clock_out_time IS NULL"
        if self.fetch_one(check_q, (user_id, date_str)):
            logging.info(f"User {user_id} already clocked in.")
            return

        query = """
        INSERT INTO attendance (user_id, user_name, pc_name, clock_in_time, date, action, timestamp)
        VALUES (?, ?, ?, ?, ?, 'login', ?)
        """
        self.execute_query(query, (user_id, user_name, pc_name, now, date_str, now))
        logging.info(f"[TIME] CLOCK IN: {user_name}")

    def clock_out(self, user_id):
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        
        find_q = "SELECT id, clock_in_time FROM attendance WHERE user_id = ? AND date = ? AND clock_out_time IS NULL"
        record = self.fetch_one(find_q, (user_id, date_str))
        
        if record:
            record_id, clock_in_str = record
            try:
                if isinstance(clock_in_str, str):
                    clock_in = datetime.fromisoformat(clock_in_str)
                else:
                    clock_in = clock_in_str
                duration = str(now - clock_in).split('.')[0]
            except Exception:
                duration = "00:00:00"

            update_q = "UPDATE attendance SET clock_out_time = ?, duration = ?, action = 'logout', timestamp = ? WHERE id = ?"
            self.execute_query(update_q, (now, duration, now, record_id))
            logging.info(f"[STOP] CLOCK OUT: {user_id}")

    # --- Test & Adapter Methods ---

    def log_attendance(self, user_id: str, user_name: str, action: str = "login", timestamp: datetime = None) -> bool:
        """Log attendance action (login, logout, activity)."""
        now = timestamp or datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        now_str = now.isoformat()
        act = action.lower()

        if act in ("login", "in"):
            query = """
            INSERT INTO attendance (user_id, user_name, pc_name, clock_in_time, date, action, timestamp)
            VALUES (?, ?, 'LOCAL', ?, ?, ?, ?)
            """
            return self.execute_query(query, (user_id, user_name, now_str, date_str, action, now_str))
        elif act in ("logout", "out"):
            # Update open clock_in if available for duration
            find_q = "SELECT id, clock_in_time FROM attendance WHERE user_id = ? AND date = ? AND clock_out_time IS NULL ORDER BY id DESC"
            record = self.fetch_one(find_q, (user_id, date_str))
            duration = "00:00:00"
            if record:
                rec_id, cin = record
                try:
                    cin_dt = datetime.fromisoformat(cin) if isinstance(cin, str) else cin
                    duration = str(now - cin_dt).split('.')[0]
                except Exception:
                    pass
                self.execute_query("UPDATE attendance SET clock_out_time = ?, duration = ? WHERE id = ?", (now_str, duration, rec_id))

            query = """
            INSERT INTO attendance (user_id, user_name, pc_name, clock_out_time, duration, date, action, timestamp)
            VALUES (?, ?, 'LOCAL', ?, ?, ?, ?, ?)
            """
            return self.execute_query(query, (user_id, user_name, now_str, duration, date_str, action, now_str))
        else:
            # e.g., activity / heartbeat
            query = """
            INSERT INTO attendance (user_id, user_name, pc_name, date, action, timestamp)
            VALUES (?, ?, 'LOCAL', ?, ?, ?)
            """
            return self.execute_query(query, (user_id, user_name, date_str, action, now_str))

    def get_todays_attendance(self):
        """Get today's attendance log entries."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        rows = self.fetch_all(
            "SELECT user_id, user_name, action, timestamp, clock_in_time, clock_out_time FROM attendance WHERE date = ? ORDER BY id ASC",
            (date_str,)
        )
        return [
            {
                "user_id": r[0],
                "user_name": r[1] or r[0],
                "action": r[2] or "login",
                "timestamp": r[3] or r[4] or date_str,
            }
            for r in rows
        ]

    def get_user_history(self, user_id: str, days: int = 7):
        """Get attendance history for a specific user over the last N days."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.fetch_all(
            "SELECT user_id, user_name, action, timestamp, clock_in_time, clock_out_time, duration, date FROM attendance WHERE user_id = ? AND date >= ? ORDER BY id DESC",
            (user_id, cutoff)
        )
        return [
            {
                "user_id": r[0],
                "user_name": r[1] or r[0],
                "action": r[2] or "login",
                "timestamp": r[3] or r[4] or r[7],
                "duration": r[6] or "00:00:00",
                "date": r[7],
            }
            for r in rows
        ]

    def calculate_work_hours(self, user_id: str, days: int = 1) -> float:
        """Calculate total work hours for a user over the last N days."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self.fetch_all(
            "SELECT action, clock_in_time, clock_out_time, timestamp FROM attendance WHERE user_id = ? AND date >= ? ORDER BY id ASC",
            (user_id, cutoff)
        )
        total_hours = 0.0
        current_in = None
        for r in rows:
            act = (r[0] or "").lower()
            cin = r[1]
            cout = r[2]
            ts = r[3]
            
            if act in ("login", "in"):
                try:
                    current_in = datetime.fromisoformat(cin or ts)
                except Exception:
                    current_in = None
            elif act in ("logout", "out") and current_in:
                try:
                    t_out = datetime.fromisoformat(cout or ts)
                    diff = (t_out - current_in).total_seconds() / 3600.0
                    total_hours += max(0.0, diff)
                except Exception:
                    pass
                current_in = None
        return total_hours

    def calculate_idle_time(self, user_id: str, hours: int = 3) -> float:
        """Calculate idle time in minutes for a user."""
        from datetime import timedelta
        rows = self.fetch_all(
            "SELECT timestamp FROM attendance WHERE user_id = ? AND action = 'activity' ORDER BY timestamp ASC",
            (user_id,)
        )
        if len(rows) < 2:
            return 0.0
        total_idle_min = 0.0
        parsed = []
        for r in rows:
            try:
                dt = datetime.fromisoformat(r[0]) if isinstance(r[0], str) else r[0]
                parsed.append(dt)
            except Exception:
                pass
        for i in range(1, len(parsed)):
            diff_sec = (parsed[i] - parsed[i - 1]).total_seconds()
            if diff_sec > 600:  # > 10 min idle gap
                total_idle_min += diff_sec / 60.0
        return total_idle_min