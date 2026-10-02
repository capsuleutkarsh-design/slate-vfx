"""Settings tab (SYS-106/108/112/113/115-118/120-122/124-129/131-135)."""

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from slate.gui.tabs import settings_tab as st


class FakeConfig:
    def __init__(self):
        self.settings = {"global_settings": {"restore_last_paths": True, "dry_run_enabled": False,
                                             "ui_scale_override": 0.0, "max_concurrent_operations": 4},
                         "last_project_dir": "C:/old/project", "last_excel_file": "C:/old/x.xlsx"}
        self.saved = []

    @property
    def default_global_settings(self):
        return {"restore_last_paths": True, "dry_run_enabled": False, "ui_scale_override": 0.0}

    def save_settings(self, settings):
        self.saved.append(dict(settings))
        return True

    def update_global_settings(self, values):
        self.settings["global_settings"] = dict(values)
        return True


@pytest.fixture
def make_tab(qtbot, monkeypatch):
    monkeypatch.setattr(st.SettingsTab, "_toast", lambda self, m, level="success": setattr(self, "last_message", m))

    def build(roles=("Artist",)):
        tab = st.SettingsTab(FakeConfig(), roles=list(roles))
        qtbot.addWidget(tab)
        return tab
    return build


def test_artists_see_no_studio_cards(make_tab):
    artist = make_tab(["Artist"])
    assert artist.card_paths.isHidden() and artist.card_money.isHidden()
    assert artist.btn_update.isHidden() and artist.btn_audit.isHidden()
    admin = make_tab(["Admin"])
    assert not admin.card_paths.isHidden() and not admin.btn_audit.isHidden()
    it = make_tab(["IT"])
    assert not it.card_paths.isHidden() and it.btn_audit.isHidden()


def test_performance_card_is_gone(make_tab):
    tab = make_tab()
    assert not hasattr(tab, "max_concurrent_sb") and not hasattr(tab, "chk_adv")


def test_dirty_discard_and_save(make_tab):
    tab = make_tab()
    assert not tab.has_unsaved_changes() and not tab.btn_save.isEnabled()
    tab.ui_scale_sb.setValue(1.1)
    assert tab.has_unsaved_changes() and tab.lbl_dirty.text() == "Unsaved changes"
    tab.discard_changes()
    assert tab.ui_scale_sb.value() == 0.0 and not tab.has_unsaved_changes()
    tab.dry_run_default_cb.setChecked(True)
    tab.project_root_input.clear()
    assert tab.save_all()
    cfg = tab.config_manager
    assert cfg.settings["global_settings"]["dry_run_enabled"] is True
    assert "last_project_dir" not in cfg.settings
    assert "max_concurrent_operations" in cfg.settings["global_settings"]
    assert not tab.has_unsaved_changes() and tab.last_message.startswith("Settings saved")


def test_reset_to_defaults(make_tab):
    tab = make_tab()
    tab.dry_run_default_cb.setChecked(True)
    tab.ui_scale_sb.setValue(1.2)
    tab.reset_to_defaults()            # conftest answers Yes
    assert not tab.dry_run_default_cb.isChecked() and tab.ui_scale_sb.value() == 0.0


def test_ui_scale_snaps_to_what_is_applied(make_tab):
    assert st.snap_ui_scale(0.10) == 0.75 and st.snap_ui_scale(0) == 0.0 and st.snap_ui_scale(3) == 1.5
    tab = make_tab()
    tab.ui_scale_sb.setValue(0.10)
    tab._snap_scale()
    assert tab.ui_scale_sb.value() == 0.75 and "0.75" in tab.lbl_scale_applied.text()
    assert tab.ui_scale_sb.maximum() == 1.5


def test_db_host_validation_and_port_field(make_tab, monkeypatch):
    assert not st.valid_db_host("not a host!!")
    assert st.valid_db_host("10.100.104.15") and st.valid_db_host("db-server.studio")
    assert not st.valid_db_host("300.1.1.1")
    tab = make_tab(["Admin"])
    assert tab.db_port_input.validator() is not None
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    tab.db_host_input.setText("not a host!!")
    assert tab.save_all() is False and "not a host name" in warned[0]


def test_test_connection_reports_both_ways(make_tab, qtbot):
    tab = make_tab(["Admin"])
    tab.db_host_input.setText("127.0.0.1")
    tab.db_port_input.setText("1")           # nothing listens there
    tab.db_name_input.setText("postgres")
    tab.db_user_input.setText("postgres")
    tab.test_connection()
    qtbot.waitUntil(lambda: tab.btn_test_db.isEnabled(), timeout=15000)
    assert tab.lbl_test.text().startswith("Could not connect")
    tab.db_port_input.setText("55432")
    tab.test_connection()
    qtbot.waitUntil(lambda: tab.btn_test_db.isEnabled(), timeout=15000)
    assert tab.lbl_test.text().startswith("Connected"), tab.lbl_test.text()


def test_unreachable_server_root_warns(make_tab, monkeypatch, tmp_path):
    tab = make_tab(["Admin"])
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel)
    tab.server_root_input.setText("Q:\\does\\not\\exist")
    assert tab.save_all() is False and "cannot be reached" in asked[0]


def test_runtime_status_in_plain_words():
    lines = st.runtime_lines({"active_mode": "postgres", "fallback_used": False}, "Q:\\x", False, False)
    assert lines["database"] == "Connected to the studio database."
    assert "not reachable" in lines["folder"] and "limited" in lines["sync"]
    assert lines["exr"] == "EXR previews: off."
    local = st.runtime_lines({"active_mode": "sqlite", "fallback_used": True}, "", False, True)
    assert "Working locally" in local["database"] and "fallback" not in str(local)


def test_buttons_and_labels(make_tab):
    tab = make_tab(["Admin"])
    titles = [c.title() for c in tab.maint_cards]
    assert "Open my log folder" in titles and "Check for updates" in titles and "Reload templates" in titles
    texts = [b.text() for b in tab.findChildren(QPushButton) if b.text()]
    assert not any("&" in t and "&&" not in t for t in texts)
    assert "Save Paths & Connections" not in texts
    widths = {tab.ui_scale_sb.width(), tab.nuke_mode_combo.width(), tab.theme_combo.width()}
    assert widths == {st.CONTROL_WIDTH}


def test_reload_templates_emits_once(make_tab):
    tab = make_tab()
    got = []
    tab.templates_refresh_requested.connect(lambda: got.append(1))
    tab.btn_templates.click()
    assert got == [1]


def test_project_report_choices():
    from datetime import datetime
    choices = st.SettingsTab.project_choices([{"id": 1, "name": "Tom & Jerry",
                                               "created_at": datetime(2026, 9, 12, 10, 0)}])
    assert choices[0][0] == "Tom & Jerry - 12 Sep 2026"
    assert st.slug("Tom & Jerry's Show") == "Tom_Jerry_s_Show"


def test_backup_runs_on_a_worker(make_tab, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog
    tab = make_tab()
    project = tmp_path / "proj"
    project.mkdir()
    tab.config_manager.settings["last_project_dir"] = str(project)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("proj_backup", True))
    import threading
    seen = {}

    def fake_backup(dirs, name):
        seen["thread"] = threading.current_thread() is threading.main_thread()
        return True, "ok", tmp_path / "proj_backup.zip"
    monkeypatch.setattr(tab.backup_manager, "create_backup", fake_backup)
    shown = []
    monkeypatch.setattr(st.SettingsTab, "_show_backup_done", lambda self, p: shown.append(p))
    tab.create_backup()
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert seen["thread"] is False and str(shown[0]).endswith("proj_backup.zip")


def test_policy_limits_and_recount(make_tab, monkeypatch):
    from slate.gui.tabs.studio_settings_cards import StudioPolicyEditor
    editor = StudioPolicyEditor()
    assert editor.standard_day.maximum() == 12 and editor.standard_day.minimum() == 4
    tab = make_tab()
    got = []
    tab.studio_policy_saved.connect(lambda: got.append(1))
    tab.studio_policy_editor.saved.emit()
    assert got == [1]


def test_unusual_late_cutoff_is_asked(make_tab, monkeypatch, qtbot):
    from PySide6.QtCore import QTime
    from slate.gui.tabs.studio_settings_cards import StudioPolicyEditor
    editor = StudioPolicyEditor()
    qtbot.addWidget(editor)
    editor.late_cutoff.setTime(QTime(23, 59))
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel)
    assert editor.save() is False and "23:59" in asked[0]


def test_licence_renewal_window_is_a_studio_card_setting(qtbot, mock_db, monkeypatch):
    """IT notes: licence_renewal_days (register_key in licence_compliance) is edited on the studio card."""
    from slate.gui.tabs.studio_settings_cards import StudioMoneyEditor
    from slate.core.domain import licence_compliance as lc
    from slate.core.infra.studio_settings import StudioSettings
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a[2])))
    StudioSettings.invalidate()
    editor = StudioMoneyEditor()
    qtbot.addWidget(editor)
    editor.set_editable(True)
    editor.load()
    assert editor.renewal_days.value() == lc.RENEWAL_SOON_DAYS
    assert (editor.renewal_days.minimum(), editor.renewal_days.maximum()) == (1, 365)
    editor.renewal_days.setValue(30)
    assert editor.save()
    StudioSettings.invalidate()
    assert lc.renewal_window() == 30
    editor.renewal_days.setValue(1)
    editor.load()
    assert editor.renewal_days.value() == 30
