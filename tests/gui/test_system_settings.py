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
    assert not tab.has_unsaved_changes() and tab.last_message == "Saved your preferences."


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


def test_back_up_a_project_is_gone(make_tab):
    """SYS2-042: the card copied whole projects to C: with no size check or Cancel; removed."""
    tab = make_tab(["Admin"])
    assert "Back up a project" not in [c.title() for c in tab.maint_cards]
    assert not hasattr(tab, "create_backup") and not hasattr(tab, "backup_manager")


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


def test_studio_policy_edits_are_tracked_and_saved_by_the_bar(make_tab, qtbot, monkeypatch):
    from slate.gui.components import work_guard
    tab = make_tab(["Admin"])
    tab.show()
    QApplication.processEvents()
    editor = tab.studio_policy_editor
    assert editor.btn_save.isHidden() and not tab.has_unsaved_changes()
    editor.accrual.setValue(editor.accrual.value() + 0.25)
    assert tab.has_unsaved_changes() and tab.lbl_dirty.text() == "Unsaved changes"
    unsaved, _busy = work_guard.pending_work({"Settings": tab})
    assert unsaved and "studio policy" in tab.unsaved_summary()
    tab.discard_changes()
    assert not tab.has_unsaved_changes()
    saved = []
    monkeypatch.setattr(type(editor), "save",
                        lambda self, quiet=False: saved.append(quiet) or (self._mark_clean() or True))
    editor.accrual.setValue(editor.accrual.value() + 0.25)
    assert tab.save_all() and saved == [True] and not tab.has_unsaved_changes()
    # One message, naming what was saved (SYS2-044).
    assert tab.last_message == "Saved the studio policy."


# ------------------------------------------------------------- round 2
def test_clearing_the_server_root_is_refused(make_tab, monkeypatch):
    """SYS2-043 / SYS2-049."""
    from slate.core.infra.global_config import GlobalConfig
    tab = make_tab(["Admin"])
    tab._snapshot["server_root"] = "Z:/Slate_Central"
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    tab.server_root_input.setText("")
    assert tab.save_all() is False and "needs the studio folder" in warned[0]
    assert tab.has_unsaved_changes()
    tab.server_root_input.setText("Z:/Slate_Central")
    tab.dry_run_default_cb.setChecked(not tab.dry_run_default_cb.isChecked())
    monkeypatch.setattr(GlobalConfig, "set", lambda *a, **k: None)
    assert tab.save_all() and "next time" not in tab.last_message


def test_forgotten_punch_out_before_the_day_is_refused(qtbot, monkeypatch):
    """SYS2-045."""
    from PySide6.QtCore import QTime
    from slate.gui.tabs.studio_settings_cards import StudioPolicyEditor
    editor = StudioPolicyEditor()
    qtbot.addWidget(editor)
    editor.late_cutoff.setTime(QTime(10, 45))
    editor.auto_logout.setTime(QTime(9, 0))
    assert "before the day starts" in editor.lbl_forgotten.text()
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    assert editor.save() is False and "09:00" in warned[0]
    editor.auto_logout.setTime(QTime(19, 30))
    assert editor.lbl_forgotten.text().startswith("a forgotten day counts 8 h 45 min")


def test_one_working_week(qtbot, mock_db, monkeypatch):
    """SYS2-046: no 'Working days' on the money card; the migration carries them over."""
    from slate.gui.tabs.studio_settings_cards import StudioMoneyEditor
    from slate.core.infra.studio_settings import StudioSettings
    from slate.core.infra.migrations import system_settings
    StudioSettings.invalidate()
    store = StudioSettings()
    store.set("working_hours", {"start": "10:00", "end": "19:00", "days": [0, 1, 2, 3, 4]})
    assert system_settings.one_working_week(mock_db) is not False
    StudioSettings.invalidate()
    assert StudioSettings().get("attendance_policy")["weekly_offs"] == [5, 6]
    assert StudioSettings().get("working_hours")["days"] == [0, 1, 2, 3, 4]    # old value kept
    assert "kept" in system_settings.one_working_week(mock_db)
    editor = StudioMoneyEditor()
    qtbot.addWidget(editor)
    assert not hasattr(editor, "work_days")
    editor.load()
    assert "days" not in editor.values()["working_hours"]      # the policy's week is the one week


def test_money_card_by_ability(make_tab, qtbot):
    """SYS2-047: IT edits the hours, not the rates; a producer sees the card for the rates."""
    from slate.gui.tabs.studio_settings_cards import StudioMoneyEditor
    editor = StudioMoneyEditor()
    qtbot.addWidget(editor)
    assert editor.may_edit(["it"])
    assert editor.can_hours and not editor.can_money
    editor.set_editable(True)
    assert editor.day_start.isEnabled() and not editor.gst.isEnabled()
    assert not make_tab(["IT"]).card_money.isHidden()
    assert make_tab(["Artist"]).card_money.isHidden()


def test_money_and_number_display(qtbot, monkeypatch):
    """SYS2-050 / SYS2-051 / SYS2-059."""
    from PySide6.QtCore import QTime
    from slate.gui.tabs import studio_settings_cards as cards
    editor = cards.StudioMoneyEditor()
    qtbot.addWidget(editor)
    editor.rates["INR"].setValue(150000)
    assert editor.rates["INR"].text().endswith("1,50,000")
    editor.day_start.setTime(QTime(19, 0))
    editor.day_end.setTime(QTime(9, 0))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    assert editor.save() is False
    assert "Working hours:" in warned[0] and "working_hours" not in warned[0]
    assert "border" in editor.day_start.styleSheet()
    policy = cards.StudioPolicyEditor()
    qtbot.addWidget(policy)
    policy.comp_half.setValue(12)
    assert policy.comp_half.text() == "12 hours"
    policy.comp_weekly.setValue(1)
    assert policy.comp_weekly.text() == "1 day"
    policy.accrual.setValue(2)
    assert policy.accrual.text() == "2 days a month"
    assert cards.number_text(0.25) == "0.25" and cards.number_text(2.0) == "2"


def test_read_only_policy_is_a_summary(qtbot):
    """SYS2-052."""
    from slate.gui.tabs.studio_settings_cards import StudioPolicyEditor
    editor = StudioPolicyEditor()
    qtbot.addWidget(editor)
    editor.set_editable(False)
    editor.lbl_summary.setText(editor.summary_text())
    assert editor.form_box.isHidden() and not editor.lbl_summary.isHidden()
    assert "Late after:" in editor.lbl_summary.text() and "Only HR" in editor.lbl_who.text()


def test_artist_preferences_follow_their_tabs(make_tab):
    """SYS2-053."""
    tab = make_tab(["Artist"])
    tab.apply_access(["Artist"], ["Dashboard", "Stock Browser"])
    assert all(row.isHidden() for row in tab.ingest_rows)
    assert tab.btn_templates.isHidden() and tab.btn_report.isHidden()
    tab.apply_access(["Admin"], ["ALL"])
    assert not any(row.isHidden() for row in tab.ingest_rows) and not tab.btn_report.isHidden()


def test_layout_details(make_tab):
    """SYS2-054/055/057/060."""
    tab = make_tab(["Admin"])
    assert tab.db_port_input.maximumWidth() == 90
    assert tab.project_root_input.cursorPosition() == 0
    assert tab.btn_logs.title() == "Open my log folder"
    assert str(st.Gate.SIZE_MD) in tab.runtime_db_label.styleSheet()
    tab.ui_scale_sb.setValue(0.0)
    tab.ui_scale_sb.stepBy(1)
    assert tab.ui_scale_sb.value() == 0.75
    tab.ui_scale_sb.stepBy(-1)
    assert tab.ui_scale_sb.value() == 0.0


@pytest.fixture(autouse=True)
def _closed_circuit_breaker():
    """
    The PostgreSQL circuit breaker is shared by every manager in the process. A
    test elsewhere that reaches for an unconfigured database opens it, and the
    tests here would then fail for two minutes for a reason that is not theirs.
    """
    from slate.core.infra.postgres_manager import PostgresManager
    PostgresManager._circuit_breaker.reset()
    yield
