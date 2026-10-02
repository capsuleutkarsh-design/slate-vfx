"""
The main window's shell: sidebar, shortcuts, footer, sign-out and close.
"""
import re

import pytest
from PySide6.QtWidgets import QApplication, QLabel

ADMIN = {"username": "admin", "user_id": "admin", "display_name": "System Admin",
         "roles": ["Developer"], "role": "Developer", "job_title": None}


@pytest.fixture
def window(qtbot, mock_db):
    from slate.gui.main_window import VFXFolderCreatorApp
    win = VFXFolderCreatorApp(dict(ADMIN))
    qtbot.addWidget(win)
    win.resize(1366, 768)
    win.show()
    qtbot.wait(50)
    # The sidebar state is remembered between windows: start each test from
    # the first-run state, and leave it that way.
    win.tab_coordinator.restore_folds([])
    if not win.sidebar_collapsed:
        win.toggle_sidebar()
    yield win
    win._remember_sidebar("sidebar_folded_groups", [])
    win._remember_sidebar("sidebar_collapsed", True)


def _vfx(qtbot, user=ADMIN):
    from slate.gui.vfx_studio_window import VFXStudioWindow
    win = VFXStudioWindow(dict(user))
    qtbot.addWidget(win)
    return win


def test_ctrl_numbers_count_screens_not_headings(window):
    tc = window.tab_coordinator
    assert window.switch_to_nth_tab(1)
    assert tc.get_current_tab_name() == "Home" or tc.tab_labels[window.sidebar_nav.currentRow()] == "Home"
    assert window.switch_to_nth_tab(2)
    assert tc.tab_labels[window.sidebar_nav.currentRow()] == tc.tab_labels[tc.tab_rows()[1]]
    assert not any(tc.tab_labels[r].startswith("__HEADER__") for r in tc.tab_rows())


def test_nothing_folds_by_itself(window, qtbot):
    tc = window.tab_coordinator
    window._switch_to_tab_label("Settings")
    qtbot.wait(50)
    window.resize(1366, 600)
    qtbot.wait(50)
    assert tc.folded_groups() == []


def test_a_folded_group_is_remembered(window, qtbot):
    tc = window.tab_coordinator
    group = tc.groups[1]
    tc.toggle_group(group["header_row"])
    assert window.global_settings.get("sidebar_folded_groups") == [group["label"]]
    assert group["widget"].is_folded()
    tc.restore_folds([])
    tc.restore_folds(window.global_settings["sidebar_folded_groups"])
    assert tc.folded_groups() == [group["label"]]


def test_every_entry_is_named_and_drawn(window):
    for entry in window.tab_coordinator.nav_items:
        tip = entry["item"].toolTip()
        assert tip.startswith(f"<b>{entry['label'].replace('&', '&amp;')}</b>"), tip
        assert not entry["item"].icon().isNull(), entry["label"]


def test_tab_switched_says_the_name_when_folded_to_icons(window, qtbot):
    seen = []
    window.tab_coordinator.tab_switched.connect(seen.append)
    assert window.sidebar_collapsed
    window._switch_to_tab_label("Settings")
    assert seen and seen[-1] == "Settings"


def test_the_sidebar_state_is_remembered(window):
    before = window.sidebar_collapsed
    window.toggle_sidebar()
    assert window.global_settings["sidebar_collapsed"] is (not before)
    window.toggle_sidebar()
    assert window.sidebar_toggle_btn.toolTip() in ("Expand sidebar", "Collapse sidebar")


def test_one_bottom_bar_and_the_credit_line_stays(window):
    from slate import licence
    assert window.statusBar() is window.status_bar
    assert window.status_bar.parent() is not window       # inside the footer, not the window's own bar
    assert licence.window_problems(window) == []
    window.show_status("Saved", "success")
    assert window.status_bar.currentMessage() == "Saved"
    credits = [l for l in window.findChildren(QLabel) if l.text() == licence.CREDIT_LINE]
    assert len(credits) == 1 and credits[0].isVisible()


def test_refresh_messages_are_plain(window):
    window._switch_to_tab_label("Settings")
    window.refresh_current_tab()
    message = window.status_bar.currentMessage()
    assert message and not re.search(r"[☀-⟿\U0001F300-\U0001FAFF]", message)
    assert "  " not in message


def test_title_has_no_none(window):
    assert "(None)" not in window.windowTitle()
    assert window.windowTitle().endswith("(Developer)")


def test_diagnostics_in_plain_words_and_copied(window, monkeypatch):
    text = window.diagnostics_text()
    assert "True" not in text and "False" not in text
    assert "Slate version" in text and "password" not in text.lower()
    window.show_runtime_diagnostics()
    dialog = window._diagnostics_dialog
    copy = [b for b in dialog.findChildren(type(window.sidebar_toggle_btn)) if b.text() == "Copy"][0]
    copy.click()
    assert QApplication.clipboard().text() == text


def test_every_shortcut_is_on_the_sheet(window):
    from PySide6.QtGui import QShortcut
    from slate.gui.main_window import SHORTCUTS
    window.show_shortcuts()
    sheet = " ".join(l.text() for l in window._shortcuts_dialog.findChildren(QLabel))
    keys = {s.key().toString() for s in window.findChildren(QShortcut)}
    keys |= {"F1", "F11"}
    for key in keys:
        if re.fullmatch(r"Ctrl\+\d", key):
            assert "Ctrl+1" in sheet
        else:
            assert key in sheet, key
    assert len(SHORTCUTS) >= 9


def test_sign_out_asks_in_plain_words(window, monkeypatch):
    from slate.gui.components import feedback
    asked = {}

    def fake(parent, title, text, yes_label="Continue", **kw):
        asked.update(title=title, text=text, yes=yes_label)
        return False

    monkeypatch.setattr(feedback, "confirm", fake)
    window.logout_user()
    assert asked == {"title": "Sign out", "text": "Sign out of Slate?", "yes": "Sign out"}


def test_signing_in_again_keeps_the_application(qtbot, mock_db):
    win = _vfx(qtbot)
    new = win.window_for_mode(dict(ADMIN))
    qtbot.addWidget(new)
    assert type(new) is type(win) and new.app_mode == "vfx"


def test_closing_never_punches_out(qtbot, mock_db, monkeypatch):
    from slate.core.domain import central_attendance
    calls = []
    monkeypatch.setattr(central_attendance.CentralAttendance, "log_action",
                        lambda self, user, action: calls.append(action))
    win = _vfx(qtbot)
    win._logout_requested = False
    monkeypatch.setattr(QApplication, "quit", lambda *a: None)
    win.close()
    assert "out" not in calls


def test_geometry_keeps_the_maximised_state(qtbot, mock_db):
    win = _vfx(qtbot)
    win.showMaximized()
    qtbot.wait(20)
    win.save_window_geometry()
    assert "window_state" in win.global_settings
    other = _vfx(qtbot)
    other.global_settings = dict(win.global_settings)
    other.restore_window_geometry()
    assert other._geometry_restored


def test_task_progress_says_what_is_running():
    from slate.gui.components.main_window_builder import MainWindowBuilderMixin

    class Task:
        def __init__(self, name, progress):
            self.name, self.progress = name, progress

    assert MainWindowBuilderMixin.task_progress_text([Task("Copying plates", 37)]) == "Copying plates 37%"
    assert MainWindowBuilderMixin.task_progress_text([Task("a", 1), Task("b", 2)]) == "2 tasks"


def test_workspace_info_shows_the_facts(qtbot):
    from slate.gui.plugins.workspace_info_plugin import WorkspaceInfoPlugin
    from slate import __version__
    page = WorkspaceInfoPlugin()
    qtbot.addWidget(page)
    page.initialize({"user_data": {"display_name": "A", "username": "a"}, "user_role": "Artist"})
    facts = page.details()
    assert facts["version"] == __version__
    assert facts["config"] and facts["config"] != "Unavailable"
    page.copy_details()
    assert __version__ in QApplication.clipboard().text()
    assert page.plugin_icon == "info"
    texts = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "Plugin Runtime Status" not in texts and "context" not in texts.lower()


def test_starting_does_not_log_every_permission(qtbot, mock_db, caplog):
    import logging
    from slate.gui.main_window import VFXFolderCreatorApp
    with caplog.at_level(logging.INFO):
        win = VFXFolderCreatorApp(dict(ADMIN))
        qtbot.addWidget(win)
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "Has Permission" not in text and "Permission: '" not in text and "[ROLES]" not in text


def test_getting_started_agrees_with_the_shortcut_sheet():
    from slate.core.help_content import HELP_CONTENT
    text = HELP_CONTENT["getting_started"]["content"]
    assert "group headings count as rows" not in text
    assert "group headings are skipped" in text
