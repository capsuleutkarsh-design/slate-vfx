"""
The server window builds, and the new screens draw what they are given.

These are the tests that would have caught a dashboard which shows an IP, a port
and a connection count and nothing about which database any of it refers to.
They build the real widgets and put real facts through them.
"""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------- the dashboard

def test_the_dashboard_says_which_database_it_is_serving(app, qtbot):
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    qtbot.addWidget(view)

    view.set_cluster_facts(
        {"data_dir": r"D:\Studio\LocalDatabase",
         "running_data_dir": r"D:\Studio\LocalDatabase",
         "size": "126.0 MB", "tables": 48, "database": "ut_vfx",
         "matches_config": True, "error": ""},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "ut_vfx", "expects": "ut_vfx", "agrees": True})

    assert "LocalDatabase" in view.card_data_dir.lbl_value.text()
    assert view.card_tables.lbl_value.text() == "48"
    assert view.lbl_warning.isHidden(), "nothing is wrong, so say nothing"


def test_the_dashboard_shouts_when_the_database_is_empty(app, qtbot):
    """
    The exact state that cost a morning: everything reachable, everything
    agreeing, and no tables in it.
    """
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    qtbot.addWidget(view)

    view.set_cluster_facts(
        {"data_dir": r"C:\Users\x\AppData\Local\Slate_Central\LocalDatabase",
         "running_data_dir": r"C:\Users\x\AppData\Local\Slate_Central\LocalDatabase",
         "size": "51.0 MB", "tables": 0, "database": "ut_vfx",
         "matches_config": True, "error": ""},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "ut_vfx", "expects": "ut_vfx", "agrees": True})

    assert not view.lbl_warning.isHidden()
    warning = view.lbl_warning.text().lower()
    assert "no tables" in warning
    # It no longer calls an empty database a mistake outright - a brand new
    # studio is empty until its first workstation logs in - but it must still
    # point at the one thing worth checking.
    assert "data directory" in warning


def test_the_dashboard_shouts_when_it_fell_back_to_another_directory(app, qtbot):
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    qtbot.addWidget(view)

    view.set_cluster_facts(
        {"data_dir": r"X:\Extra\Slate_Central\Database",
         "running_data_dir": r"C:\Users\x\AppData\Local\Slate_Central\LocalDatabase",
         "size": "51.0 MB", "tables": 12, "database": "ut_vfx",
         "matches_config": False, "error": ""},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "ut_vfx", "expects": "ut_vfx", "agrees": True})

    assert "fell back" in view.lbl_warning.text().lower()


def test_the_dashboard_shouts_when_the_pool_disagrees(app, qtbot):
    from slate_server.gui.views.dashboard_view import DashboardView

    view = DashboardView()
    qtbot.addWidget(view)

    view.set_cluster_facts(
        {"data_dir": "d", "running_data_dir": "d", "size": "1.0 MB",
         "tables": 40, "database": "ut_vfx", "matches_config": True, "error": ""},
        {"installed": True, "running": True, "listen_port": 6432,
         "publishes": "slate", "expects": "ut_vfx", "agrees": False})

    assert view.card_pool.lbl_value.text() == "wrong database"
    assert "refused" in view.lbl_warning.text().lower()


# -------------------------------------------------------------- the operations

def test_the_operations_screen_reports_a_missing_backup_loudly(app, qtbot):
    from slate_server.gui.views.operations_view import OperationsView

    view = OperationsView()
    qtbot.addWidget(view)

    view.set_backups([], "No backup has ever been taken", overdue=True)
    assert "No backup" in view.lbl_last_backup.text()
    assert view.table_backups.rowCount() == 0


def test_the_operations_screen_lists_backups(app, qtbot):
    from datetime import datetime
    from pathlib import Path
    from slate_server.gui.views.operations_view import OperationsView

    view = OperationsView()
    qtbot.addWidget(view)

    view.set_backups([
        {"path": Path("x/slate_ut_vfx_20260911_120000.dump"),
         "name": "slate_ut_vfx_20260911_120000.dump", "database": "ut_vfx",
         "taken_at": datetime(2026, 9, 11, 12, 0), "age_days": 0,
         "size_bytes": 5 * 1024 * 1024},
    ], "Last backup less than a day ago", overdue=False)

    assert view.table_backups.rowCount() == 1
    assert view.table_backups.item(0, 1).text() == "today"
    assert view.table_backups.item(0, 3).text() == "5.0 MB"


def test_the_operations_screen_shows_a_job_that_never_ran(app, qtbot):
    from slate_server.core.maintenance import MaintenanceLog
    from slate_server.gui.views.operations_view import OperationsView

    view = OperationsView()
    qtbot.addWidget(view)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        view.set_jobs(MaintenanceLog(Path(tmp) / "m.json").summary())

    assert view.table_jobs.rowCount() == 4
    states = {view.table_jobs.item(r, 2).text() for r in range(4)}
    assert states == {"Never run"}           # last run and state in one column


# ----------------------------------------------------------------- the session

def test_the_session_list_shows_how_long_and_flags_the_stuck_one(app, qtbot):
    from slate_server.gui.views.analytics_view import AnalyticsView

    view = AnalyticsView()
    qtbot.addWidget(view)

    view.set_sessions([
        {"pid": 101, "client": "192.168.0.31", "application": "Slate",
         "state": "idle in transaction", "idle_seconds": 900,
         "transaction_seconds": 900, "query": "UPDATE tracking_shots ..."},
        {"pid": 102, "client": "192.168.0.32", "application": "Slate",
         "state": "idle", "idle_seconds": 12, "transaction_seconds": 0,
         "query": ""},
    ])

    assert view.table.rowCount() == 2
    assert view.table.item(0, 4).text() == "15 min"
    assert "locks" in view.table.item(0, 5).text().lower()
    assert view.table.item(1, 5).text() == ""


def test_a_session_can_be_picked_for_disconnection(app, qtbot):
    from slate_server.gui.views.analytics_view import AnalyticsView

    view = AnalyticsView()
    qtbot.addWidget(view)
    view.set_sessions([
        {"pid": 101, "client": "192.168.0.31", "application": "Slate",
         "state": "idle", "idle_seconds": 1, "transaction_seconds": 0, "query": ""},
    ])

    assert view.selected_pid() is None, "nothing is selected to begin with"
    view.table.selectRow(0)
    assert view.selected_pid()["pid"] == 101


def _session(pid, client):
    return {"pid": pid, "client": client, "application": "Slate", "state": "idle",
            "idle_seconds": 1, "transaction_seconds": 0, "query": "", "user": "ut_vfx_app",
            "database": "ut_vfx"}


def test_disconnect_ends_the_session_shown_in_the_selected_row(app, qtbot, monkeypatch):
    """
    B1: the poll drew the table from server_facts.sessions() and then overwrote
    it from a second query in another order, so the selected row's pid was not
    the session on screen - Disconnect could end somebody else's connection.
    """
    import types
    from PySide6.QtWidgets import QMessageBox
    from slate_server.core import server_facts
    from slate_server.gui import app_window as module
    from slate_server.gui.views.analytics_view import AnalyticsView

    view = AnalyticsView()
    qtbot.addWidget(view)
    logged = []
    fake = types.SimpleNamespace(analytics_view=view, _log=logged.append, _db_port=5999,
                                 _poll_database_stats=lambda: None)
    show = lambda facts: module.UTServerWindow._show_poll(fake, facts)   # noqa: E731
    show({"sessions": [_session(101, "192.168.0.31"), _session(202, "192.168.0.32")],
          "stats": None, "refusals": []})
    view.table.selectRow(1)
    # A refresh that puts a new session first: the selection follows 202.
    show({"sessions": [_session(303, "192.168.0.33"), _session(101, "192.168.0.31"),
                       _session(202, "192.168.0.32")], "stats": None, "refusals": []})
    selected = {i.row() for i in view.table.selectedIndexes()}
    assert len(selected) == 1
    shown = view.table.item(selected.pop(), 0).text()
    assert shown == "202" and view.selected_pid()["pid"] == 202

    ended = []
    monkeypatch.setattr(server_facts, "terminate",
                        lambda port, pid: (ended.append(pid), (True, "ok"))[1])
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    module.UTServerWindow._on_disconnect_session(fake)
    assert ended == [int(shown)] == [202]
    assert fake._client_count == 3


def test_the_poll_takes_its_rows_from_server_facts_only(monkeypatch):
    from slate_server.core import server_facts
    from slate_server.gui import app_window as module
    rows = [_session(7, "10.0.0.5")]
    monkeypatch.setattr(server_facts, "sessions", lambda port: rows)
    monkeypatch.setattr(module.psycopg2, "connect",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("no database")))
    facts = module.poll_facts(5999)
    assert facts["sessions"] is rows and facts["stats"] is None


def test_the_poll_logs_what_a_log_only_server_switch_would_refuse(app, qtbot):
    import types
    from slate_server.gui import app_window as module
    from slate_server.gui.views.analytics_view import AnalyticsView
    view = AnalyticsView()
    qtbot.addWidget(view)
    logged = []
    fake = types.SimpleNamespace(analytics_view=view, _log=logged.append)
    facts = {"sessions": [], "stats": None,
             "refusals": ["strict_pg_hba would refuse postgres from 10.0.0.5 into ut_vfx"]}
    module.UTServerWindow._show_poll(fake, facts)
    module.UTServerWindow._show_poll(fake, facts)
    assert len(logged) == 1 and "10.0.0.5" in logged[0], "once, not every three seconds"


# ------------------------------------------------- saving settings never lies

def _settings_fake(tmp_path, qtbot, config_text):
    import types
    from slate_server.gui import app_window as module
    from slate_server.gui.views.settings_view import SettingsView
    view = SettingsView()
    qtbot.addWidget(view)
    view.input_db_path.setText(str(tmp_path / "not-there-yet"))
    view.input_db_password.setText("typed-password")
    config = tmp_path / "slate_server_config.json"
    config.write_text(config_text, encoding="utf-8")
    notes, logged = [], []
    fake = types.SimpleNamespace(settings_view=view, config_path=str(config),
                                 _settings_saved_note=notes.append, _log=logged.append,
                                 server_running=lambda: False,
                                 _build_engine=lambda *a, **k: object())
    fake._server_config = lambda: module.UTServerWindow._server_config(fake)
    return module, fake, config, notes


def test_a_damaged_server_settings_file_is_not_overwritten(app, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    module, fake, config, notes = _settings_fake(tmp_path, qtbot, '{"db_path": "D:\\\\Stu')
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    module.UTServerWindow._on_save_settings(fake)
    assert config.read_text(encoding="utf-8") == '{"db_path": "D:\\\\Stu', "left as it was"
    assert warned and "not overwritten" in warned[0]
    assert notes and notes[-1].startswith("NOT saved")


def test_settings_that_could_not_be_written_do_not_say_saved(app, qtbot, tmp_path,
                                                             monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from slate.core.infra import local_secrets
    module, fake, config, notes = _settings_fake(tmp_path, qtbot, '{"keep": 1}')
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(local_secrets, "write_local_config",
                        lambda values: (_ for _ in ()).throw(PermissionError("read-only")))
    module.UTServerWindow._on_save_settings(fake)
    assert notes[-1].startswith("NOT saved"), notes
    assert warned and "password" in warned[0] and "NOT" in warned[0]
    import json
    assert json.loads(config.read_text(encoding="utf-8"))["keep"] == 1, "other keys kept"


# ----------------------------------------------------------------- the settings

def test_the_settings_screen_exposes_what_the_server_actually_runs_on(app, qtbot):
    from slate_server.gui.views.settings_view import SettingsView

    view = SettingsView()
    qtbot.addWidget(view)

    # Two fields used to be the whole of it. Everything else could only be
    # changed by editing a file whose location was written down nowhere.
    for field in ("input_db_path", "input_port", "input_pooler_port",
                  "input_db_name", "input_max_conn", "lbl_config_source"):
        assert hasattr(view, field), "Settings is missing %s" % field


# ------------------------------------------------- a window that is too short

def test_no_settings_field_can_be_squeezed_below_its_own_text(app, qtbot):
    """
    Settings asks for 758 pixels and a 762-pixel window has about 720 to give
    it. A QLineEdit's minimum height is smaller than the height its text needs,
    so the layout took the difference out of every field on the screen - and a
    few pixels off the top and bottom of every value reads as a broken theme
    rather than as a window that wants scrolling.
    """
    from slate_server.gui.views.settings_view import SettingsView

    view = SettingsView()
    qtbot.addWidget(view)

    for name in ("input_db_path", "input_port", "input_pooler_port",
                 "input_db_name", "input_db_password", "input_max_conn"):
        field = getattr(view, name)
        assert field.minimumHeight() >= field.sizeHint().height(), \
            "%s can still be shrunk until its text clips" % name


def test_the_form_panel_does_not_style_its_own_labels(app, qtbot):
    """
    The panel's stylesheet used a bare QWidget selector, which every child
    matches - so each label was given the panel's background, its border and
    its rounded corners, and drew as a box over the field beside it.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent.parent / "slate_server"
              / "gui" / "views" / "settings_view.py").read_text(encoding="utf-8")

    assert "QWidget#formPanel" in source
    assert "QWidget {{" not in source, \
        "a bare QWidget selector styles every child widget too"


def test_the_maintenance_panel_shows_every_job(app, qtbot):
    """
    There are four jobs and there always will be. The layout gave the table 51
    pixels for 192 pixels of rows, so a panel about jobs nobody can tell have
    stopped was hiding three of them.
    """
    import tempfile
    from pathlib import Path
    from slate_server.core.maintenance import MaintenanceLog
    from slate_server.gui.views.operations_view import OperationsView

    view = OperationsView()
    qtbot.addWidget(view)
    with tempfile.TemporaryDirectory() as tmp:
        view.set_jobs(MaintenanceLog(Path(tmp) / "m.json").summary())

    rows = view.table_jobs.rowCount()
    needed = (view.table_jobs.horizontalHeader().height()
              + view.table_jobs.verticalHeader().defaultSectionSize() * rows)
    assert view.table_jobs.minimumHeight() >= needed, \
        "the table can be shrunk until it hides jobs"


def test_a_stat_value_has_room_for_its_descenders(app, qtbot):
    """
    The stylesheet asked for 28px and Qt reserved exactly 28 pixels - the
    height of the letters, not of the line. "49.8 MB" and "stopped" were both
    cut off at the bottom on a studio screen.
    """
    from PySide6.QtGui import QFontMetrics
    from slate_server.gui.components.stat_card import StatCard

    card = StatCard("Cluster size", "49.8 MB")
    qtbot.addWidget(card)

    line = QFontMetrics(card.lbl_value.font()).height()
    assert card.lbl_value.minimumHeight() > line - 1, \
        "the value label reserves less than one line of text"


def test_every_screen_scrolls_rather_than_crushing_itself():
    """
    A QStackedWidget gives each page exactly the room it has, and Qt honours
    that by squeezing widgets below the size they asked for. A scroll area is
    what turns "too short" into a scrollbar instead of into clipped text.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent.parent / "slate_server"
              / "gui" / "app_window.py").read_text(encoding="utf-8")

    assert "def _scrollable" in source
    for view in ("self.dashboard", "self.settings_view", "self.analytics_view",
                 "self.operations_view"):
        assert "_scrollable(%s)" % view in source, \
            "%s is added to the stack unwrapped" % view


def test_the_daily_backup_runs_by_itself_and_keeps_to_the_retention(qtbot):
    """Maintenance.due() had no caller: the daily backup was only ever taken by hand."""
    from types import SimpleNamespace
    from slate_server.gui import app_window as module
    calls = []

    class Engine:
        def is_available(self): return True
        def back_up(self): return {"ok": True, "message": "Backed up x.dump"}
        def prune(self, days, least, apply=False):
            calls.append(("prune", days, least, apply))
            return []

    class Jobs:
        def __init__(self, due): self._due = due
        def due(self): return self._due
        def record_backup(self, ok, message): calls.append(("record", ok, message))

    spin = lambda n: SimpleNamespace(value=lambda: n)  # noqa: E731
    fake = SimpleNamespace(_backup_worker=None, server_running=lambda: True,
                           _maintenance=lambda: Jobs(["backup"]), _backup_engine=Engine,
                           _log=lambda message: None, _refresh_operations=lambda: calls.append("refresh"),
                           operations_view=SimpleNamespace(spin_keep_days=spin(30), spin_keep_least=spin(7)))
    fake._on_scheduled_backup_done = lambda r: module.UTServerWindow._on_scheduled_backup_done(fake, r)

    assert module.UTServerWindow._scheduled_backup(fake) is not None
    qtbot.waitUntil(lambda: "refresh" in calls, timeout=10000)
    assert ("record", True, "Scheduled: Backed up x.dump") in calls
    assert ("prune", 30, 7, True) in calls

    fake._backup_worker = None
    fake._maintenance = lambda: Jobs([])                 # backed up today: nothing to do
    assert module.UTServerWindow._scheduled_backup(fake) is None
    fake.server_running = lambda: False                  # database down: not attempted
    fake._maintenance = lambda: Jobs(["backup"])
    assert module.UTServerWindow._scheduled_backup(fake) is None
