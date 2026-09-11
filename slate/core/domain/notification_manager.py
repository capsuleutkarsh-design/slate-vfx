import uuid
import time
import logging
from ..infra.database_manager import database_manager

class NotificationManager:
    """
    Manages user notifications.
    Storage: Central Database (SQLite/PostgreSQL) instead of JSON files.
    """
    def __init__(self):
        self.db = database_manager
        self._ensure_schema()

    def _is_postgres(self):
        backend = getattr(self.db, "backend", self.db)
        return type(backend).__name__ == "PostgresManager"

    def _ensure_schema(self):
        try:
            # Create notifications table if it doesn't exist
            self.db.execute_update("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    read BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
        except Exception as e:
            logging.error(f"Failed to initialize Notifications Schema: {e}")

    def add_notification(self, user_id, message, msg_type="info"):
        """Add a new notification for a specific user."""
        try:
            note_id = str(uuid.uuid4())
            ts = time.time()
            
            # Boolean handling (SQLite lacks native boolean, uses 0/1)
            read_val = False if self._is_postgres() else 0
            
            self.db.execute_update("""
                INSERT INTO notifications (id, user_id, message, type, timestamp, read)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (note_id, user_id, message, msg_type, ts, read_val))
            
            # Cleanup old (> 30 days)
            cutoff = time.time() - (30 * 86400)
            self.db.execute_update("DELETE FROM notifications WHERE timestamp < %s", (cutoff,))
        except Exception as e:
            logging.exception(f"Failed to add notification: {e}")

    def get_unread(self, user_id):
        """Get all unread notifications for a user."""
        try:
            read_val = False if self._is_postgres() else 0
            
            rows = self.db.execute_query(
                "SELECT id, user_id, message, type, timestamp, read FROM notifications WHERE user_id = %s AND read = %s ORDER BY timestamp DESC",
                (user_id, read_val),
                fetch="all"
            )
            
            if not rows: return []
            
            results = []
            for row in rows:
                if isinstance(row, dict):
                    results.append(row)
                else:
                    results.append({
                        "id": row[0],
                        "user_id": row[1],
                        "message": row[2],
                        "type": row[3],
                        "timestamp": row[4],
                        "read": bool(row[5])
                    })
            return results
        except Exception as e:
            logging.exception(f"Failed to get unread notifications: {e}")
            return []

    def mark_read(self, note_ids):
        """Mark specific notifications as read."""
        if not note_ids: return
        try:
            read_val = True if self._is_postgres() else 1
            
            for note_id in note_ids:
                self.db.execute_update(
                    "UPDATE notifications SET read = %s WHERE id = %s",
                    (read_val, note_id)
                )
        except Exception as e:
            logging.exception(f"Failed to mark notifications read: {e}")
