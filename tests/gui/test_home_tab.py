"""
Home: your own shots, figures you can act on, tiles for screens you have,
an honest load, and a background that stops when it is not needed.
"""
import json
import os
import time
from datetime import date, datetime, time as dtime, timedelta

import pytest
from PySide6.QtCore import Qt

from slate.gui.tabs import home_tab
from slate.gui.tabs.home_tab import (HomeLoaderWorker, HomeTab, QuickActionBtn, greeting,
                                      people_online, punch_text, shot_artists)


class FakeDB:
    """Answers by the first words of the query it recognises."""

    def __init__(self, answers=None, fail=()):
        self.answers = answers or {}
        self.fail = fail
        self.queries = []

    def execute_query(self, sql, params=None, fetch=None):
        self.queries.append((sql, params))
        for key in self.fail:
            if key in sql:
                raise RuntimeError("broken query")
        for key, value in self.answers.items():
            if key in sql:
                return value
        return None


def _shot(name, status, artist="", dept_artist=""):
    data = {"shot_name": name, "status": status, "assigned_artist": artist,
            "departments": {"comp": {"artist": dept_artist}} if dept_artist else {}}
    return {"shot_name": name, "status": status, "data_json": json.dumps(data)}


def test_my_shots_are_mine_only():
    rows = [_shot("A_010", "WIP", artist="priya"), _shot("A_020", "WIP", artist="rahul"),
            _shot("A_030", "Retake", dept_artist="Priya Sharma"), _shot("A_040", "Review")]
    db = FakeDB({"FROM tracking_shots s": rows})
    worker = HomeLoaderWorker("priya", None, mode="vfx", identities={"priya", "Priya Sharma"},
                              figures=("shots", "my_open_shots", "my_in_review"), db=db)
    assert [s["title"] for s in worker.my_shots()] == ["A_010", "A_030"]
    sql = db.queries[0][0]
    assert "ORDER BY s.last_updated DESC" in sql
    assert worker.telemetry() == {"my_open_shots": 2, "my_in_review": 0}


def test_one_failing_figure_leaves_the_others():
    db = FakeDB({"FROM tracking_projects": {"c": 4}}, fail=("it_tickets",))
    worker = HomeLoaderWorker("x", None, figures=("active_projects", "open_tickets"), db=db)
    assert worker.telemetry() == {"active_projects": 4, "open_tickets": None}


def test_open_tickets_whatever_the_casing():
    db = FakeDB({"FROM it_tickets": [{"status": "open", "c": 1}, {"status": "Open", "c": 3},
                                     {"status": "Resolved", "c": 9}]})
    worker = HomeLoaderWorker("x", None, figures=("open_tickets",), db=db)
    assert worker.telemetry() == {"open_tickets": 4}


def test_upcoming_leave_is_after_today_and_within_two_weeks(monkeypatch):
    monkeypatch.setattr(home_tab, "db_today", lambda db=None: date(2026, 10, 2))
    db = FakeDB({"FROM leave_requests": {"c": 1}})
    worker = HomeLoaderWorker("x", None, figures=("upcoming_leave",), db=db)
    assert worker.telemetry() == {"upcoming_leave": 1}
    sql, params = db.queries[-1]
    assert "start_date > %s AND start_date <= %s" in sql
    assert params == ("2026-10-02", "2026-10-16")


def test_people_online_counts_fresh_named_workstations(tmp_path):
    now = time.time()
    for pc, user, seen in (("PC1", "Priya", now - 10), ("PC2", "Priya", now - 20),
                           ("PC3", "Rahul", now - 3600), ("PC4", "Locked", now), ("PC5", "Asha", now)):
        (tmp_path / f"{pc}.json").write_text(json.dumps({"user": user, "last_seen": seen}))
    assert people_online(str(tmp_path), now=now) == 2


def test_an_outage_is_reported_not_raised(qtbot, monkeypatch):
    from slate.core.infra.postgres_manager import DatabaseUnavailableError

    class Down:
        def ping_sync(self):
            raise DatabaseUnavailableError("down")

    worker = HomeLoaderWorker("x", None, figures=("shots",), db=Down())
    failed = []
    worker.load_failed.connect(failed.append)
    worker.run()
    assert failed == ["down"]


def test_no_pretend_waiting(monkeypatch):
    def no_sleep(_seconds):
        raise AssertionError("Home must not sleep")
    monkeypatch.setattr(time, "sleep", no_sleep)
    db = FakeDB({"FROM tracking_projects": {"c": 1}, "FROM tracking_shots": []})
    worker = HomeLoaderWorker("x", None, figures=("shots", "active_projects"), db=db)
    done = []
    worker.data_loaded.connect(lambda items, punch: done.append(items))
    worker.run()
    assert done == [[]]
    assert "load_library" not in open(home_tab.__file__, encoding="utf-8").read()


def test_words_on_home():
    assert greeting("Priya Sharma", datetime(2026, 10, 2, 9)) == "Good morning, Priya"
    assert "Welcome back" not in greeting("A")
    text = punch_text(dtime(9, 4, 58), None, now=datetime(2026, 10, 2, 11, 14))
    assert text == "In since 09:04 (2 h 10 min)"
    assert punch_text("09:04:58", "18:30:01") == "Punched out at 18:30"
    assert HomeTab.status_colour("Retake") != HomeTab.status_colour("Review")
    assert shot_artists({"assigned_artist": "A", "departments": {"roto": {"artist": "B"}}}) == {"a", "b"}


def test_a_tile_keeps_its_hover_and_answers_the_keyboard(qtbot):
    tile = QuickActionBtn("VFX Dashboard", "Shots", "chart")
    qtbot.addWidget(tile)
    tile.show()
    clicks = []
    tile.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(tile, Qt.MouseButton.LeftButton)
    assert ":hover" in tile.styleSheet() and clicks == [1]
    tile.setFocus()
    qtbot.keyClick(tile, Qt.Key.Key_Return)
    assert clicks == [1, 1]


def test_punch_in_again_after_punching_out(qtbot):
    tab = HomeTab(user_data={"username": "hr1", "display_name": "HR One"}, mode="ops")
    qtbot.addWidget(tab)
    tab._refresh_punch_buttons({"punch_in": "09:00:00", "punch_out": "13:00:00"})
    assert tab.btn_punch_in.isEnabled() and not tab.btn_punch_out.isEnabled()
    tab._refresh_punch_buttons({"punch_in": "14:00:00", "punch_out": None})
    assert not tab.btn_punch_in.isEnabled() and tab.btn_punch_out.isEnabled()
    assert ":disabled" in tab.btn_punch_in.styleSheet()
    assert hasattr(tab, "refresh")


def test_closing_asks_the_loader_to_stop(qtbot, monkeypatch):
    tab = HomeTab(user_data={"username": "a"}, mode="vfx")
    qtbot.addWidget(tab)
    calls = []

    class Busy:
        def isRunning(self):
            return True

        def requestInterruption(self):
            calls.append("interrupt")

        def wait(self, ms):
            calls.append("wait")
            return True

        def terminate(self):
            calls.append("terminate")

    tab.loader_worker = Busy()
    tab.close()
    assert calls == ["interrupt", "wait"]


ARTIST = {"username": "artist", "user_id": "artist", "display_name": "Test Artist",
          "roles": ["Artist"], "role": "Artist"}
OPS = {"username": "admin", "user_id": "admin", "display_name": "System Admin",
       "roles": ["Developer"], "role": "Developer"}


def test_tiles_are_for_screens_you_have(qtbot, mock_db):
    from slate.gui.vfx_studio_window import VFXStudioWindow
    win = VFXStudioWindow(dict(ARTIST))
    qtbot.addWidget(win)
    home = HomeTab(user_data=ARTIST, main_window=win, mode="vfx")
    qtbot.addWidget(home)
    have = set(home._available_tabs())
    assert all(tile.title in have for tile in home.tiles)
    assert "open_tickets" not in home.figures and "pending_leave" not in home.figures


def test_ops_tiles_open_their_screens(qtbot, mock_db):
    from slate.gui.studio_ops_window import StudioOpsWindow
    win = StudioOpsWindow(dict(OPS))
    qtbot.addWidget(win)
    home = HomeTab(user_data=OPS, main_window=win, mode="ops")
    qtbot.addWidget(home)
    titles = [tile.title for tile in home.tiles]
    assert "Leave" in titles and "IT Support" in titles
    assert home._trigger_tab("Leave")
    assert win.tab_coordinator.tab_labels[win.sidebar_nav.currentRow()] == "Leave"


def test_the_full_suite_home_has_the_punch_panel(qtbot, mock_db):
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp(dict(OPS), app_mode="all")
    qtbot.addWidget(win)
    home = HomeTab(user_data=OPS, main_window=win, mode="all")
    qtbot.addWidget(home)
    assert home.has_punch_panel and hasattr(home, "btn_punch_in")


def test_the_real_queries_run_on_postgresql(pg_db):
    pg_db.execute_update("INSERT INTO tracking_projects (code, name, config_json, active) "
                         "VALUES ('DEMO', 'Demo', '{}', 1)")
    pg_db.execute_update(
        "INSERT INTO tracking_shots (project_code, reel, shot_name, status, data_json) VALUES "
        "('DEMO', '', 'D_010', 'Review', %s)", (json.dumps({"assigned_artist": "priya"}),))
    worker = HomeLoaderWorker("priya", None, identities={"priya"}, db=pg_db,
                              figures=("shots", "active_projects", "my_in_review", "waiting_review",
                                       "upcoming_leave", "pending_leave", "open_tickets", "punch", "pulse"))
    assert worker.my_shots()[0]["title"] == "D_010"
    figures = worker.telemetry()
    assert figures["active_projects"] == 1 and figures["my_in_review"] == 1
    assert figures["waiting_review"] == 1
    assert figures["upcoming_leave"] == 0 and figures["pending_leave"] == 0
    assert figures["open_tickets"] == 0
    assert worker.todays_punch() == {}
    assert worker.leave_pulse() == []


def test_home_text_is_light_on_its_dark_sky_in_every_theme():
    """In Light the greeting was dark text on the always-dark background."""
    from slate.core.infra.gate import Gate as ThemeGate
    previous = ThemeGate.MODE
    try:
        ThemeGate.use("Light")
        dark = ThemeGate.palette("Dark")
        assert home_tab.Gate.TEXT == dark["TEXT"] != ThemeGate.TEXT
        assert home_tab.Gate.overlay(0.1) == ThemeGate.tint("#" + "F" * 6, 0.1)
        assert home_tab.Gate.STATUS["RETAKE"] == dark["BAD"]
    finally:
        ThemeGate.use(previous)


def test_home_greeting_uses_the_dark_text(qtbot):
    from slate.core.infra.gate import Gate as ThemeGate
    previous = ThemeGate.MODE
    try:
        ThemeGate.use("Light")
        tab = HomeTab(user_data={"username": "a", "display_name": "Asha"}, mode="vfx")
        qtbot.addWidget(tab)
        assert ThemeGate.palette("Dark")["TEXT"] in tab.greeting_label.styleSheet()
    finally:
        ThemeGate.use(previous)


def test_open_tickets_come_from_the_it_desk(monkeypatch):
    """SHL-095: Home counts through TicketRepository.open_count, the desk's own rule."""
    from slate.core.infra.ticket_repository import TicketRepository
    db = FakeDB({"FROM it_tickets": [("open", 2), ("Waiting on You", 1), ("closed", 5)]})
    assert TicketRepository(db).open_count() == 3
    calls = []
    monkeypatch.setattr(TicketRepository, "open_count", lambda self: calls.append(self.db) or 7)
    worker = HomeLoaderWorker("x", None, figures=("open_tickets",), db=db)
    assert worker.telemetry() == {"open_tickets": 7} and calls == [db]


def test_licence_renewals_on_home(monkeypatch, qtbot):
    """IT-067: licences expired or renewing in the window, names in the tooltip."""
    from slate.core.domain import licence_compliance as lc
    monkeypatch.setattr(home_tab, "db_today", lambda db=None: date(2026, 10, 2))
    monkeypatch.setattr(lc, "renewal_window", lambda db=None: 30)
    db = FakeDB({"FROM software_licenses": [
        {"id": 1, "software_name": "Nuke", "expiration_date": "2026-10-12"},
        {"id": 2, "software_name": "Maya", "expiration_date": "2026-09-30"},
        {"id": 3, "software_name": "Houdini", "expiration_date": "2027-06-01"},
        {"id": 4, "software_name": "Resolve", "expiration_date": None}]})
    worker = HomeLoaderWorker("x", None, figures=("licence_renewals",), db=db)
    value = worker.telemetry()["licence_renewals"]
    assert value["value"] == 2 and value["warn"]
    assert value["tip"].splitlines() == ["Maya - Expired 2 days ago", "Nuke - Renews in 10 days"]

    tab = HomeTab(user_data={"username": "it1", "roles": ["IT"]}, mode="ops")
    qtbot.addWidget(tab)
    assert "licence_renewals" in tab.stat_labels
    tab._update_telemetry_ui({"licence_renewals": value})
    label = tab.stat_labels["licence_renewals"]
    assert label.text() == "2" and "Nuke" in label.toolTip()


def test_licence_figure_only_for_people_who_see_licences(qtbot, mock_db):
    from slate.gui.studio_ops_window import StudioOpsWindow
    win = StudioOpsWindow(dict(OPS))
    qtbot.addWidget(win)
    home = HomeTab(user_data=OPS, main_window=win, mode="ops")
    qtbot.addWidget(home)
    assert ("Licences" in home._available_tabs()) == ("licence_renewals" in home.figures)
    assert "licence_renewals" in home.figures                    # a developer sees Licences
    artist = dict(ARTIST)
    home2 = HomeTab(user_data=artist, mode="ops")                 # no window: by the role
    qtbot.addWidget(home2)
    assert "licence_renewals" not in home2.figures
