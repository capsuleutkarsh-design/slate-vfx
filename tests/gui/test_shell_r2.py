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
            return [{"username": "rahul", "reports_to": "sam"}]
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


def test_service_accounts_get_no_punch_panel(qtbot, mock_db):
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


# ------------------------------------------------------------------ sidebar, help, notifications

def test_a_screen_that_fails_to_open_leaves_the_sidebar_where_it_was(qtbot, mock_db, monkeypatch):
    from slate.gui.main_window import VFXFolderCreatorApp
    from slate.gui.components import feedback
    monkeypatch.setattr(feedback, "show_error", lambda *a, **k: None)
    win = VFXFolderCreatorApp({"username": "admin", "user_id": "admin", "display_name": "Admin",
                               "roles": ["Developer"], "role": "Developer"}, app_mode="all")
    qtbot.addWidget(win)
    tc = win.tab_coordinator
    win._switch_to_tab_label("Settings")
    showing = win.sidebar_nav.currentRow()
    label = "Bidding"
    tc.tab_factories[label]["factory"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    win._switch_to_tab_label(label)
    assert win.sidebar_nav.currentRow() == showing
    assert tc.get_current_tab_name() == "Settings"
    # Home sits above the groups; the operations group has one plain name.
    assert tc.tab_labels[0] == "Home" and "__HEADER__PEOPLE" in tc.tab_labels
    tips = [g["widget"].toolTip() for g in tc.groups]
    assert any(t.startswith("IT & Infra") for t in tips)


def test_help_offers_only_your_screens_and_esc_clears_the_search(qtbot):
    from PySide6.QtCore import Qt
    from slate.gui.help_dialog import HelpDialog
    dialog = HelpDialog(None, mode=None, screens=["Home", "VFX Dashboard", "Leave"])
    qtbot.addWidget(dialog)
    ids = {section for _item, section, _text in dialog._items}
    assert "admin_panel" not in ids and "users_roles" not in ids
    assert {"getting_started", "dashboard", "leave"} <= ids
    dialog.show()
    dialog.search.setText("leave")
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.search.text() == "" and dialog.isVisible()


def test_a_notification_opens_what_it_is_about(qtbot):
    from slate.gui.components.notification_center import NotificationsDialog, note_target
    assert note_target({"type": "assignment", "message": "You have been assigned to SH010 (Comp)."}) \
        == ("shot", "SH010")
    assert note_target({"type": "update", "message": "SH020 is now Approved."}) == ("shot", "SH020")
    assert note_target({"type": "ticket", "message": "Asha replied on ticket #4."}) == ("screen", "IT Support")
    notes = [{"message": "You have been assigned to SH010.", "type": "assignment", "read": False}]
    dialog = NotificationsDialog(notes)
    qtbot.addWidget(dialog)
    dialog._open_item(dialog.list.item(0))
    assert dialog.target == ("shot", "SH010")
    failed = NotificationsDialog([], failed=True)
    qtbot.addWidget(failed)
    assert "could not be loaded" in failed.empty.text() and "No notifications yet" not in failed.empty.text()


def test_a_toast_keeps_both_undo_and_details(qtbot):
    from PySide6.QtWidgets import QPushButton, QWidget
    from slate.gui.components.feedback import raw_toast
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 600)
    toast = raw_toast(parent, "Could not save.", "error", action=("Undo", lambda: None), details="why")
    names = [b.text() for b in toast.findChildren(QPushButton) if b.objectName() == "toastAction"]
    assert names == ["Undo", "Details"]


# ------------------------------------------------------------------ server console

def test_server_buttons_only_while_running_and_plain_counts(qtbot):
    import types
    from slate_server.gui import app_window
    from slate_server.gui.views.dashboard_view import DashboardView
    from slate_server.gui.views.operations_view import age, every
    view = DashboardView()
    qtbot.addWidget(view)
    fake = types.SimpleNamespace(dashboard=view)
    app_window.UTServerWindow._set_running_controls(fake, False)
    assert not view.btn_restart_pool.isEnabled()
    assert view.btn_restart_pool.toolTip() == "Start the server first"
    app_window.UTServerWindow._set_running_controls(fake, True)
    assert view.btn_restart_pool.isEnabled()
    # The web API is not started by the server any more (2.2.0), so there is no
    # button to open it.
    assert not hasattr(view, "btn_api_dashboard")
    assert (every(1), every(7), age(0), age(1), age(30)) == ("Every day", "Every 7 days", "today",
                                                             "1 day", "30 days")


def test_an_rv_note_says_note_and_a_verdict_judges_the_version(qtbot, mock_db, monkeypatch, tmp_path):
    """A4: a note showed 'RV: SH010 marked . Not saved yet'. A5: a verdict left the version waiting."""
    from slate.core.domain import rv_feedback, versions
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp({"username": "admin", "user_id": "admin", "display_name": "Admin",
                               "roles": ["Developer"], "role": "Developer"}, app_mode="vfx")
    qtbot.addWidget(win)
    shot = SimpleNamespace(shot_name="SH010", status="Review")
    judged = []

    class Model:
        def apply_edit(self, shots, fn, description):
            return True

    tab = SimpleNamespace(all_shots=[shot], current_project=None, table_model=Model(),
                          _user_can_edit=lambda: True, undo_last_edit=lambda: None,
                          _signed_in_name=lambda: "Sanjay",
                          on_version_verdict=lambda v, status: judged.append((v.id, status)))
    monkeypatch.setattr(win, "_get_tab_instance", lambda label, create=False: tab)
    feedback = rv_feedback.RVFeedback(status="note", note="flicker",
                                      media_path=str(tmp_path / "SH010_comp_v002.mov"))
    monkeypatch.setattr(rv_feedback, "read_feedback", lambda path: feedback)
    monkeypatch.setattr(rv_feedback, "match_shot", lambda shots, media: shot)
    said = []
    monkeypatch.setattr(win, "show_feedback",
                        lambda message, level="info", *a, **k: said.append((message, level)))

    win.on_rv_feedback("x")
    assert said[-1] == ("RV: SH010 given a note. Not saved yet - save it in the VFX Dashboard.", "info")

    version = versions.Version(id=7, shot_name="SH010", version_name="v002", status="In Review")
    writes = []

    class Store:
        def __init__(self, **kw):
            pass

        def list_for_shot(self, *a, **k):
            return [version]

        def update_version(self, vid, **fields):
            writes.append((vid, fields))
            return True

    monkeypatch.setattr(versions, "VersionStore", Store)
    tab.current_project = SimpleNamespace(code="PRJ", folder_base="")
    feedback.status, feedback.note = "approved", ""
    win.on_rv_feedback("x")
    assert writes == [(7, {"status": "Approved"})] and judged == [(7, "Approved")]
    assert said[-1] == ("RV: SH010 v002 set to Approved.", "success")
