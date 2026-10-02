"""
The VFX Dashboard takes in other people's changes shot by shot.

It used to reload the whole project whenever anything in it changed - every
three seconds at most - which threw away edits somebody had typed but not
saved, and lost their selection. Now only the changed shots are read and
swapped in, and a shot with unsaved edits is never replaced.
"""
import json
from types import SimpleNamespace

import pytest

from slate.gui.tabs.vfx_dashboard_pro.controllers.live_update_mixin import (
    DashboardLiveUpdateMixin, merge_shots,
)
from slate.gui.tabs.vfx_dashboard_pro.models.shot_model import Shot


def _shot(sid, name, status="WIP", **extra):
    shot = Shot(shot_name=name, reel_episode="R1", status=status, **extra)
    shot.id = sid
    return shot


# ------------------------------------------------------------ merging

def test_only_checked_shots_change():
    a, b, c = _shot(1, "SH010"), _shot(2, "SH020"), _shot(3, "SH030")
    new_b = _shot(2, "SH020", "APPROVED")
    result = merge_shots([a, b, c], [new_b], {2})
    assert result.shots == [a, new_b, c]
    assert result.shots[0] is a and result.shots[2] is c
    assert result.replaced == [new_b] and not result.added and not result.removed


def test_unsaved_edits_are_kept():
    b = _shot(2, "SH020", "MINE")
    b._modified = True
    result = merge_shots([b], [_shot(2, "SH020", "THEIRS")], {2})
    assert result.shots[0] is b and b.status == "MINE"
    assert result.kept == [b] and not result.replaced


def test_deleted_and_new_shots():
    a, b = _shot(1, "SH010"), _shot(2, "SH020")
    new = _shot(9, "SH090")
    result = merge_shots([a, b], [new], {1, 9})
    assert result.shots == [b, new]
    assert result.removed == [a] and result.added == [new]


def test_a_shot_this_person_should_not_see_is_dropped_or_never_added():
    """An artist sees their own shots: a reassigned one leaves, a new one elsewhere never arrives."""
    a = _shot(1, "SH010", assigned_artist="ravi")
    moved = _shot(1, "SH010", assigned_artist="neha")
    other = _shot(5, "SH050", assigned_artist="neha")
    mine = lambda s: s.assigned_artist == "ravi"
    result = merge_shots([a], [moved, other], {1, 5}, visible=mine)
    assert result.shots == [] and result.removed == [a] and not result.added


# ------------------------------------------------------------ reading

@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "dash.db"))
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


def _store(db, project, name, status):
    payload = json.dumps({"shot_name": name, "reel_episode": "R1", "status": status})
    db.execute_update(
        "INSERT INTO tracking_shots (project_code, reel, shot_name, status, priority, data_json, version) "
        "VALUES (%s, 'R1', %s, %s, 1, %s, 1)", (project, name, status, payload))
    return db.execute_query(
        "SELECT id FROM tracking_shots WHERE project_code=%s AND shot_name=%s",
        (project, name), fetch="one")["id"]


def _handler(db, project="P1"):
    from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
    return SQLiteHandler(project, db_manager=db, user_role="admin")


def _check_reading(db):
    one = _store(db, "P1", "SH010", "WIP")
    _store(db, "P1", "SH020", "WIP")
    elsewhere = _store(db, "P2", "SH010", "WIP")
    db.execute_update("INSERT INTO tracking_tasks (shot_id, project_code, department, status) "
                      "VALUES (%s, 'P1', 'comp', 'RETAKE')", (one,))

    shots = _handler(db).read_shots_by_id({one, elsewhere, 99999})
    assert [s.shot_name for s in shots] == ["SH010"], "only this project's, only those asked"
    assert shots[0].id == one and shots[0].version == 1
    assert shots[0].dept("comp").status == "RETAKE", "tasks are applied as read_shots applies them"

    whole = {s.id: s for s in _handler(db).read_shots()}
    assert whole[one].dept("comp").status == shots[0].dept("comp").status


def test_reading_only_some_shots(db):
    _check_reading(db)


def test_reading_only_some_shots_on_postgres(pg_db):
    _check_reading(pg_db)


# ------------------------------------------------------------ the dashboard

@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Database:
    """What the other workstations have saved."""

    def __init__(self, shots):
        self.rows = {s.id: s for s in shots}
        self.asked = []
        self.full_reads = 0

    def change(self, sid, **fields):
        old = self.rows.get(sid)
        shot = _shot(sid, fields.pop("shot_name", old.shot_name if old else f"SH{sid}"),
                     fields.pop("status", old.status if old else "WIP"))
        for key, value in fields.items():
            setattr(shot, key, value)
        self.rows[sid] = shot

    def handler(self):
        from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
        handler = SQLiteHandler.__new__(SQLiteHandler)
        copy = lambda s: _shot(s.id, s.shot_name, s.status)

        def by_id(ids):
            self.asked.append(set(ids))
            return [copy(self.rows[i]) for i in ids if i in self.rows]

        def everything():
            self.full_reads += 1
            return [copy(s) for s in self.rows.values()]

        handler.read_shots_by_id = by_id
        handler.read_shots = everything
        return handler


def _dashboard(shots, database):
    from PySide6.QtWidgets import QAbstractItemView, QTableView, QWidget
    from slate.gui.tabs.vfx_dashboard_pro.ui.shot_table_model import ShotTableModel

    class Dashboard(DashboardLiveUpdateMixin, QWidget):
        def __init__(self):
            QWidget.__init__(self)
            self._is_closing = False
            self.local_mode = False
            self.current_project = SimpleNamespace(code="P1")
            self.data_handler = database.handler()
            self.all_shots = list(shots)
            self.displayed_shots = list(shots)
            self.table_model = ShotTableModel(self.displayed_shots, user_role="admin")
            self.table = QTableView(self)
            self.table.setModel(self.table_model)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.notices = []
            self._init_live_updates()

        def showEvent(self, event):
            super().showEvent(event)
            self._apply_pending_live_changes_on_show()

        def _filter_shots_for_current_user(self, shots):
            return list(shots)

        def populate_filters(self):
            pass

        def apply_filters(self):
            self.displayed_shots = list(self.all_shots)
            self.update_table()

        def update_table(self):
            self.table_model.update_data(self.displayed_shots,
                                         keep_undo=getattr(self, "_keep_undo", False))

        def _notify(self, message, level="info", duration=0, details="", action=None):
            self.notices.append(message)

        def show_only_shots(self, shots):
            pass

        def log(self, message):
            pass

        def start_thumbnail_loading(self):
            pass

    dash = Dashboard()
    dash.resize(800, 400)
    dash.show()
    _KEEP.append(dash)
    return dash


_KEEP = []


def _three():
    return [_shot(1, "SH010"), _shot(2, "SH020"), _shot(3, "SH030")]


def _row_of(dash, sid):
    model = dash.table_model
    return next(r for r in range(model.rowCount()) if model.get_shot_at(r).id == sid)


def test_a_change_elsewhere_reads_and_swaps_only_that_shot(app, manual_change_feed):
    shots = _three()
    database = _Database(_three())
    dash = _dashboard(shots, database)

    database.change(2, status="APPROVED")
    manual_change_feed.push({"tracking_shots": {"2"}, "it_tickets": {"7"}})
    dash._apply_live_changes()

    assert database.asked == [{2}] and database.full_reads == 0
    assert dash.all_shots[0] is shots[0] and dash.all_shots[2] is shots[2]
    assert dash.all_shots[1].status == "APPROVED"
    assert dash.table_model.get_shot_at(_row_of(dash, 2)).status == "APPROVED"


def test_a_task_change_is_heard_as_its_shot(app, manual_change_feed):
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    manual_change_feed.push({"tracking_tasks": {"3"}})
    dash._apply_live_changes()
    assert database.asked == [{3}]


def test_unsaved_edits_survive_and_are_marked(app, manual_change_feed):
    from PySide6.QtCore import Qt
    shots = _three()
    shots[1].status = "MINE"
    shots[1]._modified = True
    database = _Database(_three())
    dash = _dashboard(shots, database)

    database.change(2, status="THEIRS")
    manual_change_feed.push({"tracking_shots": {"2"}})
    dash._apply_live_changes()

    assert dash.all_shots[1] is shots[1] and shots[1].status == "MINE"
    assert shots[1]._remote_changed
    assert any("SH020" in n for n in dash.notices)
    model = dash.table_model
    name = model.index(_row_of(dash, 2), 1)
    assert model.data(name).startswith("⚠")
    assert "Someone else" in model.data(name, Qt.ItemDataRole.ToolTipRole)

    # Told once, not on every later change.
    dash.notices.clear()
    manual_change_feed.push({"tracking_shots": {"2"}})
    dash._apply_live_changes()
    assert not dash.notices


def test_the_selection_stays_on_the_same_shot(app, manual_change_feed):
    from PySide6.QtCore import QItemSelectionModel
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    dash.table.selectionModel().select(
        dash.table_model.index(_row_of(dash, 3), 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    database.change(3, status="APPROVED")
    database.change(8, shot_name="SH080")
    manual_change_feed.push({"tracking_shots": {"3", "8"}})
    dash._apply_live_changes()

    rows = dash.table.selectionModel().selectedRows()
    assert [dash.table_model.get_shot_at(i.row()).id for i in rows] == [3]


def test_new_and_deleted_shots(app, manual_change_feed):
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    del database.rows[1]
    database.change(4, shot_name="SH040")
    manual_change_feed.push({"tracking_shots": {"1", "4"}})
    dash._apply_live_changes()
    assert sorted(s.id for s in dash.all_shots) == [2, 3, 4]
    assert dash.table_model.rowCount() == 3


def test_nothing_moves_while_a_cell_is_being_edited(app, manual_change_feed, monkeypatch):
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    database.change(2, status="APPROVED")
    manual_change_feed.push({"tracking_shots": {"2"}})

    monkeypatch.setattr(dash, "_live_busy", lambda: True)
    dash._apply_live_changes()
    assert database.asked == [] and dash.all_shots[1].status == "WIP"

    monkeypatch.setattr(dash, "_live_busy", lambda: False)
    dash._apply_live_changes()
    assert dash.all_shots[1].status == "APPROVED", "done afterwards, not dropped"


def test_a_hidden_dashboard_catches_up_when_shown(app, manual_change_feed):
    from PySide6.QtTest import QTest
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    dash.hide()
    database.change(2, status="APPROVED")
    manual_change_feed.push({"tracking_shots": {"2"}})
    dash._apply_live_changes()
    assert database.asked == []

    dash.show()
    QTest.qWait(50)
    assert dash.all_shots[1].status == "APPROVED"


def test_undo_survives_for_shots_nobody_else_touched(app, manual_change_feed):
    shots = _three()
    database = _Database(_three())
    dash = _dashboard(shots, database)
    dash.table_model._undo_stack = [("a", [(shots[0], {})]), ("b", [(shots[1], {})])]

    database.change(2, status="APPROVED")
    manual_change_feed.push({"tracking_shots": {"2"}})
    dash._apply_live_changes()
    assert dash.table_model._undo_stack == [("a", [(shots[0], {})])]


def test_unknown_changes_read_the_project_once_and_merge(app, manual_change_feed):
    """After the feed was down, or from the fallback check: which shots is unknown."""
    shots = _three()
    shots[0]._modified = True
    database = _Database(_three())
    dash = _dashboard(shots, database)
    database.change(1, status="THEIRS")
    database.change(2, status="APPROVED")
    manual_change_feed.push({"tracking_shots": {None}})
    dash._apply_live_changes()
    assert database.full_reads == 1
    assert dash.all_shots[0] is shots[0] and dash.all_shots[1].status == "APPROVED"


# ------------------------------------------------------------ fallback and recovery

class _CountingDb:
    def __init__(self):
        self.queries = 0

    def get_connection(self):
        db = self

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return self

            def execute(self, *args):
                db.queries += 1

            def fetchone(self):
                return ("2026-09-28 10:00:00",)

        return _Conn()


def _wait(app, seconds):
    # time.sleep lets the worker thread run; QTest.qWait holds it back here.
    import time
    time.sleep(seconds)
    app.processEvents()


def test_the_old_check_sleeps_while_the_feed_works(app):
    from slate.gui.tabs.vfx_dashboard_pro.core.poll_worker import PollWorker

    feed_up = {"value": True}
    counting = _CountingDb()
    worker = PollWorker("P1", counting, interval=20, skip_when=lambda: feed_up["value"])
    heard = []
    worker.updates_available.connect(lambda: heard.append(1))
    worker.start()
    _wait(app, 0.3)
    assert counting.queries == 1, "only the starting read, nothing while the feed works"

    feed_up["value"] = False
    _wait(app, 0.3)
    worker.stop()
    app.processEvents()
    assert counting.queries > 1
    assert heard, "one catch-up read when the feed goes away"


def test_screens_catch_up_after_the_feed_recovers(app):
    from PySide6.QtCore import QObject
    from slate.gui.components.change_feed import ChangeFeed

    feed = ChangeFeed.__new__(ChangeFeed)
    QObject.__init__(feed)
    feed.available = False
    feed._subs = []
    owner = QObject()
    heard = []
    feed.watch(owner, ("it_tickets",), heard.append)

    feed._set_available(True)
    assert heard == [], "the first start is not a recovery"
    feed._set_available(False)
    feed._set_available(True)
    assert heard == [{"it_tickets": {None}}]


def test_a_failed_read_never_clears_the_grid(app, manual_change_feed):
    """A database hiccup must not look like 'those shots were deleted'."""
    database = _Database(_three())
    dash = _dashboard(_three(), database)
    database.rows.clear()                    # what read_shots() returns when it fails
    manual_change_feed.push({"tracking_shots": {None}})
    dash._apply_live_changes()
    assert len(dash.all_shots) == 3
    assert dash._live_pending_full, "tried again later"


def test_a_query_error_is_not_read_as_deleted(db, monkeypatch):
    handler = _handler(db)
    monkeypatch.setattr(handler.db_manager, "execute_query", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        handler.read_shots_by_id({1})
