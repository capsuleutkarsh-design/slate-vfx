import uuid
import time
import logging
from ..infra.database_manager import database_manager


class NotificationManager:
    """
    Manages user notifications.
    Storage: Central Database (SQLite/PostgreSQL) instead of JSON files.

    Who a notification is for is stored as the person's username. Callers used
    to pass whatever they had - the dashboard passed the artist's display name
    ("Priya Sharma") - and lookups matched that string exactly, so Priya's
    assignment sat unread in the table while her Alerts said "Nothing unread".
    add_notification now resolves a display name to the username, and every
    read matches all of a person's names, ignoring case. Rows written the old
    way are repaired the first time the table is opened.
    """

    # Notifications older than this are deleted when a new one is added.
    KEEP_DAYS = 30

    _repaired = False

    def __init__(self, db=None):
        self.db = db or database_manager
        self._ensure_schema()

    def _is_postgres(self):
        backend = getattr(self.db, "backend", self.db)
        return type(backend).__name__ == "PostgresManager"

    def _false(self):
        return False if self._is_postgres() else 0

    def _true(self):
        return True if self._is_postgres() else 1

    def _ensure_schema(self):
        # Epoch seconds need a double. On PostgreSQL REAL is four bytes, which
        # keeps about seven digits - a notification's time was rounded by up
        # to a minute either way, so "just now" could read as the future.
        stamp_type = "DOUBLE PRECISION" if self._is_postgres() else "REAL"
        try:
            # Create notifications table if it doesn't exist
            self.db.execute_update(f"""
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    type TEXT NOT NULL,
                    timestamp {stamp_type} NOT NULL,
                    read BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)
        except Exception as e:
            logging.error(f"Failed to initialize Notifications Schema: {e}")
        if not NotificationManager._repaired:
            NotificationManager._repaired = True
            self._widen_timestamp()
            self.repair_recipients()

    def _widen_timestamp(self):
        """Existing PostgreSQL tables: REAL -> DOUBLE PRECISION (values kept)."""
        if not self._is_postgres():
            return
        try:
            row = self.db.execute_query(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'notifications' AND column_name = 'timestamp'", fetch="one")
            kind = str((row.get("data_type") if isinstance(row, dict) else row[0]) if row else "")
            if kind.lower() == "real":
                self.db.execute_update(
                    "ALTER TABLE notifications ALTER COLUMN timestamp TYPE DOUBLE PRECISION")
        except Exception as exc:
            logging.debug("Notification timestamp not widened: %s", exc)

    # ------------------------------------------------------------ identities
    def resolve_recipient(self, identity):
        """
        The username of the person this names, found by username or display
        name, ignoring case. Unknown names are kept as given (and logged), so
        nothing is dropped - they still match that exact name when read.
        """
        text = str(identity or "").strip()
        if not text:
            return ""
        try:
            row = self.db.execute_query(
                "SELECT username FROM ut_users WHERE LOWER(username) = LOWER(%s)",
                (text,), fetch="one")
            if not row:
                row = self.db.execute_query(
                    "SELECT username FROM ut_users WHERE LOWER(display_name) = LOWER(%s) "
                    "ORDER BY username",
                    (text,), fetch="one")
            if row:
                return str(row["username"] if isinstance(row, dict) else row[0])
        except Exception as exc:
            logging.debug("Notification recipient %r not resolved: %s", text, exc)
        logging.info("Notification for %r: no account by that name; stored as given.", text)
        return text

    def identities_of(self, *names):
        """
        Every name a person may have been addressed by: the names given, plus
        the username and display name of the account they belong to. Lower-case.
        """
        found = {str(n).strip().lower() for n in names if n and str(n).strip()}
        for name in list(found):
            try:
                row = self.db.execute_query(
                    "SELECT username, display_name FROM ut_users "
                    "WHERE LOWER(username) = %s OR LOWER(display_name) = %s",
                    (name, name), fetch="one")
            except Exception:
                row = None
            if row:
                for key in ("username", "display_name"):
                    value = row.get(key) if isinstance(row, dict) else None
                    if value and str(value).strip():
                        found.add(str(value).strip().lower())
        return sorted(found)

    def repair_recipients(self):
        """
        Readdress notifications stored under a display name to the username.

        Safe to run any number of times: it only touches rows whose recipient
        is not a username but is somebody's display name.
        """
        try:
            self.db.execute_update("""
                UPDATE notifications
                   SET user_id = (SELECT MIN(u.username) FROM ut_users u
                                   WHERE LOWER(u.display_name) = LOWER(notifications.user_id))
                 WHERE NOT EXISTS (SELECT 1 FROM ut_users x
                                    WHERE LOWER(x.username) = LOWER(notifications.user_id))
                   AND EXISTS (SELECT 1 FROM ut_users y
                                WHERE LOWER(y.display_name) = LOWER(notifications.user_id))
            """)
        except Exception as exc:
            logging.debug("Notification recipients not repaired: %s", exc)

    # ----------------------------------------------------------------- write
    def add_notification(self, user_id, message, msg_type="info"):
        """Add a new notification for a specific user (username or display name)."""
        try:
            recipient = self.resolve_recipient(user_id)
            if not recipient:
                logging.warning("Notification without a recipient dropped: %s", message)
                return False
            note_id = str(uuid.uuid4())
            ts = time.time()

            ok = self.db.execute_update("""
                INSERT INTO notifications (id, user_id, message, type, timestamp, read)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (note_id, recipient, message, msg_type, ts, self._false()))

            # Cleanup old (> 30 days)
            cutoff = time.time() - (self.KEEP_DAYS * 86400)
            self.db.execute_update("DELETE FROM notifications WHERE timestamp < %s", (cutoff,))
            return ok is not False
        except Exception as e:
            logging.exception(f"Failed to add notification: {e}")
            return False

    def notify(self, recipients, message, msg_type="info"):
        """
        The same message to several people (usernames or display names).
        Each person gets it once, however many of their names were passed.
        Returns how many notifications were written.
        """
        if isinstance(recipients, str):
            recipients = [recipients]
        sent, seen = 0, set()
        for person in recipients or []:
            username = self.resolve_recipient(person)
            if not username or username.lower() in seen:
                continue
            seen.add(username.lower())
            if self.add_notification(username, message, msg_type):
                sent += 1
        return sent

    # ------------------------------------------------------------------ read
    @staticmethod
    def _rows(rows):
        results = []
        for row in rows or []:
            if isinstance(row, dict):
                note = dict(row)
            else:
                note = {
                    "id": row[0], "user_id": row[1], "message": row[2],
                    "type": row[3], "timestamp": row[4], "read": row[5],
                }
            note["read"] = bool(note.get("read"))
            results.append(note)
        return results

    def _who(self, user_id, aliases):
        names = self.identities_of(user_id, *(aliases or ()))
        if not names:
            return None, ()
        return ", ".join(["%s"] * len(names)), tuple(names)

    def get_unread(self, user_id, *aliases):
        """
        All unread notifications for a person, newest first. Matches every
        name they are known by, ignoring case.
        """
        try:
            marks, names = self._who(user_id, aliases)
            if not marks:
                return []
            rows = self.db.execute_query(
                "SELECT id, user_id, message, type, timestamp, read FROM notifications "
                f"WHERE LOWER(user_id) IN ({marks}) AND read = %s ORDER BY timestamp DESC",
                names + (self._false(),),
                fetch="all"
            )
            return self._rows(rows)
        except Exception as e:
            logging.exception(f"Failed to get unread notifications: {e}")
            return []

    def get_recent(self, user_id, *aliases, limit=50):
        """The latest notifications for a person, read or not, newest first."""
        try:
            marks, names = self._who(user_id, aliases)
            if not marks:
                return []
            rows = self.db.execute_query(
                "SELECT id, user_id, message, type, timestamp, read FROM notifications "
                f"WHERE LOWER(user_id) IN ({marks}) ORDER BY timestamp DESC LIMIT %s",
                names + (int(limit),),
                fetch="all"
            )
            return self._rows(rows)
        except Exception as e:
            logging.exception(f"Failed to read notifications: {e}")
            return []

    def unread_count(self, user_id, *aliases):
        try:
            marks, names = self._who(user_id, aliases)
            if not marks:
                return 0
            row = self.db.execute_query(
                "SELECT COUNT(*) AS n FROM notifications "
                f"WHERE LOWER(user_id) IN ({marks}) AND read = %s",
                names + (self._false(),), fetch="one")
            if not row:
                return 0
            return int(row.get("n") if isinstance(row, dict) else row[0])
        except Exception as e:
            logging.debug("Unread count unavailable: %s", e)
            return 0

    def mark_read(self, note_ids):
        """Mark specific notifications as read."""
        if not note_ids: return
        try:
            for note_id in note_ids:
                self.db.execute_update(
                    "UPDATE notifications SET read = %s WHERE id = %s",
                    (self._true(), note_id)
                )
        except Exception as e:
            logging.exception(f"Failed to mark notifications read: {e}")

    def mark_all_read(self, user_id, *aliases):
        """Mark everything addressed to this person as read."""
        try:
            marks, names = self._who(user_id, aliases)
            if not marks:
                return False
            return self.db.execute_update(
                f"UPDATE notifications SET read = %s WHERE LOWER(user_id) IN ({marks})",
                (self._true(),) + names) is not False
        except Exception as e:
            logging.exception(f"Failed to mark notifications read: {e}")
            return False
