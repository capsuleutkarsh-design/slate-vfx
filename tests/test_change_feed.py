"""
The change feed: triggers note every change, and open screens re-read only
when one of their tables actually changed.
"""
import pytest

from slate.gui.components import change_feed


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "feed.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- triggers

def test_every_insert_update_and_delete_is_noted(db):
    start = change_feed.latest_id(db)
    db.execute_update("INSERT INTO it_tickets (submitted_by, category, description, status) "
                      "VALUES ('ravi', 'Workstation', 'Nuke', 'Open')")
    ticket = db.execute_query("SELECT MAX(id) AS id FROM it_tickets", fetch="one")["id"]
    db.execute_update("UPDATE it_tickets SET status='Closed' WHERE id=%s", (ticket,))
    db.execute_update("DELETE FROM it_tickets WHERE id=%s", (ticket,))

    changes, newest = change_feed.read_since(db, start)
    assert changes == {"it_tickets": {str(ticket)}}
    rows = db.execute_query("SELECT COUNT(*) AS n FROM slate_change_feed WHERE id > %s",
                            (start,), fetch="one")
    assert rows["n"] == 3 and newest > start


def test_a_task_change_names_its_shot(db):
    """The dashboard shows shots, so a task change is reported as its shot."""
    start = change_feed.latest_id(db)
    db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status) "
                      "VALUES (42, 'P1', 'comp', 'WIP')")
    changes, _ = change_feed.read_since(db, start)
    assert changes == {"tracking_tasks": {"42"}}


def test_nothing_new_reads_nothing(db):
    changes, newest = change_feed.read_since(db, change_feed.latest_id(db))
    assert changes == {} and newest == change_feed.latest_id(db)


def test_old_rows_are_pruned(db, monkeypatch):
    monkeypatch.setattr(change_feed, "KEEP_ROWS", 2)
    for i in range(5):
        db.execute_update("INSERT INTO it_tickets (submitted_by, category, description, status) "
                          "VALUES ('ravi', 'x', %s, 'Open')", (str(i),))
    newest = change_feed.latest_id(db)
    change_feed.prune(db, newest)
    left = db.execute_query("SELECT COUNT(*) AS n FROM slate_change_feed", fetch="one")["n"]
    assert left <= 3


def test_the_migration_is_safe_to_run_again(db):
    from slate.core.infra.migrations.change_feed import apply_migration
    assert apply_migration(db.backend)
    assert apply_migration(db.backend)
    start = change_feed.latest_id(db)
    db.execute_update("INSERT INTO leave_requests (user_id, type, start_date, end_date, status) "
                      "VALUES ('ravi', 'Casual', '2026-10-01', '2026-10-01', 'Pending')")
    changes, _ = change_feed.read_since(db, start)
    assert list(changes) == ["leave_requests"]
    rows = db.execute_query("SELECT COUNT(*) AS n FROM slate_change_feed WHERE id > %s",
                            (start,), fetch="one")
    assert rows["n"] == 1, "a trigger was created twice"


# ------------------------------------------------------------ screens

class _Screen:
    def __init__(self):
        from PySide6.QtWidgets import QWidget
        self.widget = QWidget()
        self.reads = 0

    def refresh(self):
        self.reads += 1


def _auto(screen, topics=("it_tickets",)):
    from slate.gui.components.auto_refresh import AutoRefresh
    auto = AutoRefresh(screen.widget, screen.refresh, seconds=30, topics=topics)
    auto._last = 0
    return auto


def test_with_the_feed_a_quiet_screen_reads_nothing(app, manual_change_feed):
    manual_change_feed.available = True
    screen = _Screen()
    auto = _auto(screen)
    screen.widget.show()
    app.processEvents()
    auto._tick()
    assert screen.reads == 0


def test_a_change_to_its_table_is_read_at_once(app, manual_change_feed):
    manual_change_feed.available = True
    screen = _Screen()
    _auto(screen)
    screen.widget.show()
    manual_change_feed.push({"leave_requests": {"1"}})
    assert screen.reads == 0                     # not its table
    manual_change_feed.push({"it_tickets": {"7"}})
    assert screen.reads == 1


def test_a_hidden_screen_reads_when_next_shown(app, manual_change_feed):
    manual_change_feed.available = True
    screen = _Screen()
    _auto(screen)
    manual_change_feed.push({"it_tickets": {"7"}})
    assert screen.reads == 0
    screen.widget.show()
    app.processEvents()
    assert screen.reads == 1


def test_without_the_feed_the_timer_still_works(app, manual_change_feed):
    manual_change_feed.available = False
    screen = _Screen()
    auto = _auto(screen)
    screen.widget.show()
    app.processEvents()
    auto._last = 0
    auto._tick()
    assert screen.reads >= 1


# ------------------------------------------------------------ on PostgreSQL

def test_postgres_notes_every_change(pg_db):
    """The studio runs PostgreSQL: the trigger function and triggers must work there."""
    start = change_feed.latest_id(pg_db)
    pg_db.execute_update("INSERT INTO it_tickets (submitted_by, category, description, status) "
                         "VALUES ('ravi', 'Workstation', 'Nuke', 'Open')")
    ticket = pg_db.execute_query("SELECT MAX(id) AS id FROM it_tickets", fetch="one")["id"]
    pg_db.execute_update("UPDATE it_tickets SET status='Closed' WHERE id=%s", (ticket,))
    pg_db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status) "
                         "VALUES (42, 'P1', 'comp', 'WIP')")
    changes, _ = change_feed.read_since(pg_db, start)
    assert changes == {"it_tickets": {str(ticket)}, "tracking_tasks": {"42"}}


def test_postgres_migration_runs_again_without_doubling(pg_db):
    from slate.core.infra.migrations.change_feed import apply_migration
    assert apply_migration(pg_db.backend)
    start = change_feed.latest_id(pg_db)
    pg_db.execute_update("INSERT INTO it_tickets (submitted_by, category, description, status) "
                         "VALUES ('ravi', 'x', 'y', 'Open')")
    rows = pg_db.execute_query("SELECT COUNT(*) AS n FROM slate_change_feed WHERE id > %s",
                               (start,), fetch="one")
    assert rows["n"] == 1
