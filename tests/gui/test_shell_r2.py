"""
Round-2 shell fixes: shots open with their project, the palette finds the shot
that was asked for, Home shows only the leave a person may see, RV verdicts go
through the dashboard's own edit model.
"""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QComboBox

from slate.gui.components.quick_search_controller import QuickSearchControllerMixin as Palette
from slate.gui.tabs.home_tab import HomeLoaderWorker, HomeTab


# ------------------------------------------------------------------ palette

def test_the_placeholder_example_and_exact_names_win():
    assert Palette._extract_shot_query("shot 042") == "042"
    assert Palette.rank_shot_names("042", ["SH041", "SH042", "SH0420"])[0] == "SH042"
    assert Palette.rank_shot_names("SH010", ["SH0100", "SH010"])[0] == "SH010"
    assert Palette.rank_shot_names("SH010", ["SH0100", "SH010_comp"]) == ["SH010_comp", "SH0100"]
    assert Palette.rank_shot_names("sh01", ["SH010", "XX"]) == ["SH010"]


class FakeDashboard:
    def __init__(self, shots_by_project):
        self.shots_by_project = shots_by_project
        self.current_project = None
        self.all_shots = []
        self.opened = []
        self.project_combo = QComboBox()
        self.project_combo.addItem("Select a project…", None)
        for code in shots_by_project:
            self.project_combo.addItem(code, code)
        self.project_combo.currentIndexChanged.connect(
            lambda _i: self.switch_project(self.project_combo.currentData()))

    def switch_project(self, code):
        self.current_project = SimpleNamespace(code=code)
        self.all_shots = [SimpleNamespace(shot_name=n) for n in self.shots_by_project[code]]

    def open_detail_dock(self, shot):
        self.opened.append((self.current_project.code, shot.shot_name))


class Host(Palette):
    def __init__(self, dashboard, locations):
        self.dashboard, self.locations, self.said = dashboard, locations, []

    def _switch_to_tab_label(self, label):
        return label == "VFX Dashboard"

    def _get_tab_instance(self, label, create=False):
        return self.dashboard

    def _palette_tab_labels(self):
        return ["VFX Dashboard"]

    def show_feedback(self, message, level="info", duration=None, details="", action=None):
        self.said.append((level, message))

    def _remember_omnibar_shot(self, name):
        pass

    def _shot_locations(self, query, project_code=""):
        names = self.rank_shot_names(query, {n for n, _c in self.locations})
        top = names[0].lower() if names else None
        return [(n, c) for n, c in self.locations
                if n.lower() == top and (not project_code or c == project_code)]


def test_a_shot_opens_its_project_first(qtbot):
    """Home and the palette ended on 'No shots are loaded - pick a project first'."""
    dash = FakeDashboard({"ALPHA": ["SH010"], "BETA": ["SH010", "SH020"]})
    host = Host(dash, [("SH010", "ALPHA"), ("SH010", "BETA"), ("SH020", "BETA")])
    assert host._jump_to_shot_in_review("SH020")
    assert dash.opened[-1] == ("BETA", "SH020")
    # The same name in two shows: Home says which one.
    assert host._jump_to_shot_in_review("SH010", project_code="ALPHA")
    assert dash.opened[-1] == ("ALPHA", "SH010")
    assert host._jump_to_shot_in_review("shot 020")
    assert dash.opened[-1] == ("BETA", "SH020")


# ------------------------------------------------------------------ Home

class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def execute_query(self, sql, params=None, fetch=None):
        if "CURRENT_DATE" in sql or "date('now')" in sql:
            return {"d": "2026-10-03"}
        if "FROM leave_requests" in sql:
            rows = self.rows
            if params:
                rows = [r for r in rows if r["user_id"].lower() == str(params[0]).lower()]
            return rows
        if "reports_to" in sql:
            return [{"username": "rahul"}]
        return []


LEAVE = [
    {"id": 1, "user_id": "priya", "type": "Sick", "status": "Approved",
     "start_date": "2026-10-05", "end_date": "2026-10-06"},
    {"id": 2, "user_id": "rahul", "type": "Medical", "status": "Pending Supervisor",
     "start_date": "2026-10-07", "end_date": "2026-10-08"},
    {"id": 3, "user_id": "rahul", "type": "Earned", "status": "Rejected",
     "start_date": "2026-10-04", "end_date": "2026-10-04"},
    {"id": 4, "user_id": "artist", "type": "Earned", "status": "Pending HR",
     "start_date": "2026-10-09", "end_date": "2026-10-09"},
]


def _pulse(user, stage):
    worker = HomeLoaderWorker(user, None, figures=("pulse", "pending_leave"), db=FakeDB(LEAVE),
                              leave_stage=stage)
    return [i["title"] for i in worker.leave_pulse()], worker.telemetry()


def test_an_artist_sees_only_their_own_leave():
    titles, _ = _pulse("artist", "")
    assert len(titles) == 1 and titles[0].startswith("You - Earned")


def test_a_supervisor_sees_their_reports_without_the_reason():
    titles, figures = _pulse("sam", "Supervisor")
    assert len(titles) == 1 and "rahul" in titles[0] and "away" in titles[0]
    assert "Medical" not in " ".join(titles) and "Sick" not in " ".join(titles)
    assert figures["pending_leave"] == 1                # Pending Supervisor of a report


def test_hr_sees_everybody_but_not_rejected_requests():
    titles, figures = _pulse("hr", "HR")
    assert len(titles) == 3 and "Sick" in " ".join(titles)
    assert not any("Rejected" in t for t in titles)
    assert figures["pending_leave"] == 1                # Pending HR only, not the supervisor's


def test_figure_rows_fill_and_greeting_of_a_service_account():
    assert [HomeTab.figure_columns(n) for n in (1, 2, 3, 4, 5, 7)] == [1, 2, 3, 2, 3, 4]
    from slate.gui.tabs.home_tab import greeting
    from datetime import datetime
    assert greeting("System Admin", datetime(2026, 1, 1, 9), whole_name=True) == "Good morning, System Admin"


def test_service_accounts_get_no_punch_panel(qtbot):
    tab = HomeTab(user_data={"username": "admin", "display_name": "System Admin", "is_service": True,
                             "roles": ["Developer"]}, mode="ops")
    qtbot.addWidget(tab)
    assert not tab.has_punch_panel and "punch" not in tab.figures
    assert "Good" in tab.greeting_label.text() and "System Admin" in tab.greeting_label.text()


# ------------------------------------------------------------------ RV

def test_rv_verdicts_are_pending_edits_and_need_the_right(qtbot, mock_db, monkeypatch, tmp_path):
    from slate.core.domain import rv_feedback
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp({"username": "admin", "user_id": "admin", "display_name": "Admin",
                               "roles": ["Developer"], "role": "Developer"}, app_mode="vfx")
    qtbot.addWidget(win)
    shot = SimpleNamespace(shot_name="SH010", status="Review")
    edits = []

    class Model:
        def apply_edit(self, shots, fn, description):
            for s in shots:
                fn(s)
            edits.append(description)
            return True

    tab = SimpleNamespace(all_shots=[shot], current_project=None, table_model=Model(),
                          _user_can_edit=lambda: False, undo_last_edit=lambda: None)
    monkeypatch.setattr(win, "_get_tab_instance", lambda label, create=False: tab)
    feedback = SimpleNamespace(media_path=str(tmp_path / "SH010_comp_v002.mov"), dashboard_status="Approved")
    monkeypatch.setattr(rv_feedback, "read_feedback", lambda path: feedback)
    monkeypatch.setattr(rv_feedback, "match_shot", lambda shots, media: shot)
    monkeypatch.setattr(rv_feedback, "apply_feedback",
                        lambda s, fb, **k: setattr(s, "status", fb.dashboard_status) or True)
    said = []
    monkeypatch.setattr(win, "show_feedback", lambda message, level="info", *a, **k: said.append(message))

    win.on_rv_feedback("x")
    assert shot.status == "Review" and not edits and "supervisor" in said[-1]
    tab._user_can_edit = lambda: True
    win.on_rv_feedback("x")
    assert shot.status == "Approved" and edits and "Not saved yet" in said[-1]
