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
    states = {view.table_jobs.item(r, 3).text() for r in range(4)}
    assert states == {"never run"}


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
