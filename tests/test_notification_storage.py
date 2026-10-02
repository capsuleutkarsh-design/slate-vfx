"""
Notification times on PostgreSQL.

The timestamp column was REAL - four bytes there, about seven digits - so an
epoch time was rounded by up to a minute and a fresh notification could read
as being in the future.
"""


def test_notification_times_keep_their_seconds_on_postgres(pg_db):
    """REAL kept about seven digits, so an epoch time was rounded by a minute."""
    from slate.core.domain.notification_manager import NotificationManager
    pg_db.execute_update("DROP TABLE IF EXISTS notifications")
    pg_db.execute_update(
        "CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "message TEXT NOT NULL, type TEXT NOT NULL, timestamp REAL NOT NULL, "
        "read BOOLEAN NOT NULL DEFAULT FALSE)")
    NotificationManager._repaired = False
    try:
        NotificationManager(db=pg_db)               # an existing table is widened
        row = pg_db.execute_query(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'notifications' AND column_name = 'timestamp'", fetch="one")
        assert row["data_type"] == "double precision"
        stamp = 1790000000.25
        pg_db.execute_update(
            "INSERT INTO notifications (id, user_id, message, type, timestamp, read) "
            "VALUES (%s, %s, %s, %s, %s, %s)", ("n1", "priya", "Hi", "info", stamp, False))
        back = pg_db.execute_query(
            "SELECT timestamp FROM notifications WHERE id = %s", ("n1",), fetch="one")
        assert abs(float(back["timestamp"]) - stamp) < 0.01
    finally:
        NotificationManager._repaired = False
