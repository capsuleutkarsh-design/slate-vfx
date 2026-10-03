"""
Build & Ingest on screen (ING-001 ... ING-079), offscreen Qt, scratch drives only.

The survey and the worker are run on the test's own thread (their start()
is replaced by run()), so a whole ingest happens inside one test.
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMainWindow, QStackedWidget

from slate.core.workers import structure
from slate.gui.tabs import folder_creator_tab as fct
from slate.gui.dialogs import ingest_preflight_dialog as ipd
from slate.gui.dialogs.custom_template_dialog import CustomTemplateDialog

STANDARD = {
    "name": "Standard Pipeline (Studio)", "description": "Standard",
    "base_folders": ["01_Frm Client", "04_Production", "05_Reels", "07_Outsource/01_To_Outsource"],
    "production_subfolders": ["01_ARTIST_SOW_SHEET"], "outsource_subfolders": [],
    "shot_folders": ["01_Scan", "07_Comp/Script", "08_Deliver/EXR"], "scan_version_folders": ["Denoise"],
}


class Config:
    """A ConfigManager stand-in with the same template rules (built-ins are never saved)."""

    def __init__(self, user=None):
        self.default_templates = {"standard": STANDARD}
        self.user_templates = dict(user or {})
        self.templates = {**self.default_templates, **self.user_templates}
        self.settings = {"global_settings": {}}
        self.format_mapping = {}
        self.saved_settings = 0
        self.saved_templates = 0

    def get_templates(self):
        return list(self.templates)

    def get_available_templates(self):
        return list(self.templates)

    def save_settings(self, settings):
        self.saved_settings += 1
        return True

    def save_templates(self, templates):
        self.saved_templates += 1
        self.user_templates = {k: v for k, v in templates.items() if k not in self.default_templates}
        self.templates = {**self.default_templates, **self.user_templates}
        return True


@pytest.fixture
def sync(monkeypatch, mock_db):
    """Run background threads inline and keep message boxes out of the way."""
    monkeypatch.setattr(structure, "database_manager", mock_db)
    monkeypatch.setattr(fct.SurveyWorker, "start", lambda self: self.run())
    monkeypatch.setattr(fct.RetryWorker, "start", lambda self: self.run())
    monkeypatch.setattr(structure.FolderCreationWorker, "start", lambda self: self.run())
    messages = []
    monkeypatch.setattr(fct.FolderCreatorTab, "_message",
                        lambda self, title, text, level, report=None, folder=None:
                        messages.append(SimpleNamespace(title=title, text=text, level=level, report=report)))
    monkeypatch.setattr("slate.gui.components.feedback.toast", lambda *a, **k: None)
    monkeypatch.setattr("slate.gui.components.feedback.warn", lambda *a, **k: messages.append(a))
    return messages


@pytest.fixture
def tab(qtbot, sync):
    widget = fct.FolderCreatorTab(Config(), user_data={"username": "coord1", "roles": ["Coordinator"]})
    qtbot.addWidget(widget)
    widget.resize(1600, 900)
    return widget


def _drive(root: Path):
    for rel in ("REEL_01/SH_010", "REEL_01/SH_020", "REEL_02/SH_030"):
        folder = root / rel
        folder.mkdir(parents=True, exist_ok=True)
        for frame in range(1001, 1004):
            (folder / f"{Path(rel).name}.{frame}.exr").write_bytes(b"exr")
    return root


def _fill(tab, tmp_path, code="DEMO_PRJ"):
    projects = tmp_path / "Projects"
    projects.mkdir(exist_ok=True)
    drive = _drive(tmp_path / "drive_A")
    tab.project_dir_input.setText(str(projects))
    tab.project_name_input.setText(code)
    tab.scan_source_input.setText(str(drive))
    tab.check_destination_status()
    return projects, drive


def _files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.exists() else set()


# ------------------------------------------------------------------ templates
def test_picking_a_second_template_does_not_loop(qtbot, sync):
    """ING-001: the combo, the tab and the main window re-emitted each other until Slate died."""
    config = Config({"client_x": dict(STANDARD, name="Client X")})
    widget = fct.FolderCreatorTab(config)
    qtbot.addWidget(widget)
    # The old main-window wiring, to prove a reload no longer fires anything.
    widget.template_changed.connect(lambda *_: widget.load_templates_to_ui())
    index = widget.template_combo.findData("client_x")
    widget.template_combo.setCurrentIndex(index)
    widget.template_combo.activated.emit(index)
    assert widget.template_combo.currentData() == "client_x"
    assert config.saved_settings == 1
    widget.load_templates_to_ui()
    assert widget.template_combo.currentData() == "client_x"     # selection kept


def test_opening_the_tab_saves_nothing(qtbot, sync):
    """ING-060."""
    config = Config()
    widget = fct.FolderCreatorTab(config)
    qtbot.addWidget(widget)
    assert config.saved_settings == 0


def test_saving_a_template_returns_and_selects_it(tab, monkeypatch):
    """ING-002 / ING-024: one entry, selected, preview from the saved folders."""
    def fake_exec(dialog):
        dialog.name_input.setText("Client X (v2)")
        dialog._add_path(dialog.sections["base_folders"], "02_Other")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CustomTemplateDialog, "exec", fake_exec)
    tab.create_custom_template()
    tab.create_custom_template()          # again: replaces, never a duplicate entry
    keys = [tab.template_combo.itemData(i) for i in range(tab.template_combo.count())]
    assert keys.count("client_x_(v2)") == 1
    assert tab.template_combo.currentData() == "client_x_(v2)"
    top = [tab.folder_preview_tree.topLevelItem(0).child(i).text(0)
           for i in range(tab.folder_preview_tree.topLevelItem(0).childCount())]
    assert "02_Other" in top


def test_a_builtin_name_is_refused_in_the_dialog(qtbot):
    """ING-023 / ING-046."""
    dialog = CustomTemplateDialog(None, builtin_keys={"standard"})
    qtbot.addWidget(dialog)
    assert not dialog.ok_btn.isEnabled()                     # empty name
    dialog.name_input.setText("Standard")
    assert not dialog.ok_btn.isEnabled()
    assert "built-in" in dialog.problems()[0]
    dialog.name_input.setText("Client X")
    assert dialog.ok_btn.isEnabled()
    assert dialog.get_template_data()["name"] == "Client X"


def test_template_folders_keep_their_place_and_path(qtbot):
    """ING-025: sections are explicit; nested paths survive a round trip."""
    template = {"name": "T", "description": "For client X",
                "base_folders": ["Layout/SHOT_A", "01_Scan/EXR", "02_Dmp/Work/PSD"],
                "shot_folders": ["01_Scan"]}
    dialog = CustomTemplateDialog(None, template)
    qtbot.addWidget(dialog)
    data = dialog.get_template_data()
    assert data["base_folders"] == ["Layout/SHOT_A", "01_Scan/EXR", "02_Dmp/Work/PSD"]
    assert data["outsource_subfolders"] == []
    assert data["description"] == "For client X"                 # ING-047


def test_editing_and_deleting_a_user_template(tab, monkeypatch):
    """ING-044."""
    tab.config_manager.save_templates({"mine": dict(STANDARD, name="Mine")})
    tab.load_templates_to_ui(select_key="mine")

    def fake_exec(dialog):
        dialog.description_input.setText("changed")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CustomTemplateDialog, "exec", fake_exec)
    tab.edit_current_template()
    assert tab.config_manager.templates["mine"]["description"] == "changed"
    count = tab.template_combo.count()

    monkeypatch.setattr("slate.gui.components.feedback.confirm", lambda *a, **k: True)
    tab.delete_current_template()
    assert "mine" not in tab.config_manager.templates
    assert tab.template_combo.count() == count - 1


def test_a_template_without_shot_folders_says_so(tab):
    """ING-026."""
    tab.config_manager.save_templates({"bare": {"name": "Bare", "base_folders": ["01_Frm Client"]}})
    tab.load_templates_to_ui(select_key="bare")
    assert tab.preview_warning.isVisibleTo(tab)
    assert "07_Comp" in tab.preview_warning.text()


def test_preview_is_the_real_layout(tab):
    """ING-040 / ING-041 / ING-065."""
    tab.project_name_input.setText("DEMO")
    root = tab.folder_preview_tree.topLevelItem(0)
    assert root.text(0) == "DEMO"
    names = [root.child(i).text(0) for i in range(root.childCount())]
    assert names.count("04_Production") == 1 and names.count("05_Reels") == 1
    outsource = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == "07_Outsource")
    assert outsource.child(0).text(0) == "01_To_Outsource"
    reels = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == "05_Reels")
    shot = reels.child(0).child(0)
    scan = next(shot.child(i) for i in range(shot.childCount()) if shot.child(i).text(0) == "01_Scan")
    assert scan.child(0).text(0) == "v001"
    assert tab.folder_preview_tree.isHeaderHidden()


# ------------------------------------------------------------------ checking
def test_a_bad_project_code_is_refused_not_changed(tab, tmp_path):
    """ING-009 / ING-063."""
    _fill(tab, tmp_path, code="PRJ:01*?")
    assert tab.project_name_input.text() == "PRJ:01*?"
    assert not tab.create_btn.isEnabled()
    assert tab.project_error.isVisibleTo(tab) and "can only use" in tab.project_error.text()
    assert tab.progress_label.text() == fct.NOT_READY          # ING2-038: never 'Ready' beside a problem
    tab.start_creation_process()
    assert tab.folder_creation_thread is None and tab._phase == "idle"


def test_a_source_inside_the_project_is_refused(tab, tmp_path):
    """ING-064."""
    projects = tmp_path / "Projects"
    inside = projects / "DEMO_PRJ" / "01_Frm Client"
    inside.mkdir(parents=True)
    tab.project_dir_input.setText(str(projects))
    tab.project_name_input.setText("DEMO_PRJ")
    tab.scan_source_input.setText(str(inside))
    tab.check_destination_status()
    assert "inside the project" in tab.source_error.text()
    assert not tab.create_btn.isEnabled()


def test_the_projects_folder_can_be_typed(tab, tmp_path):
    """ING-043."""
    assert not tab.project_dir_input.isReadOnly()
    tab.project_dir_input.setText(str(tmp_path / "nope"))
    tab.check_destination_status()
    assert "does not exist" in tab.root_error.text()
    tab.project_dir_input.setText(str(tmp_path))
    tab.check_destination_status()
    assert not tab.root_error.isVisibleTo(tab)
    assert tab.project_dir_input.toolTip() == str(tmp_path)          # ING-071


def test_a_differently_spelled_project_folder_is_shown(tab, tmp_path):
    """ING-062."""
    picked = tmp_path / "My-Show" / "x"
    picked.mkdir(parents=True)
    tab.project_dir_input.setText(str(picked))
    tab.project_name_input.setText("MYSHOW")
    tab.check_destination_status()
    assert "My-Show" in tab.destination_label.text() and "MYSHOW" in tab.destination_label.text()


def test_one_name_for_the_client_drive_and_plain_copy(tab):
    """ING-069 / ING-078 / ING-005."""
    texts = " ".join(w.text() for w in tab.findChildren(fct.QLabel)) + tab.scan_source_input.placeholderText()
    assert "Client Source" not in texts and "Incoming Drive" not in texts
    assert "Info:" not in tab.note_label.text() and ">" not in tab.note_label.text()
    assert "&" not in tab.create_btn.text()


def test_controls_have_tooltips(tab):
    """ING-076."""
    for widget in (tab.project_dir_input, tab.scan_source_input, tab.create_btn, tab.pause_btn,
                   tab.stop_btn, tab.clear_btn, tab.create_custom_btn):
        assert widget.toolTip()


def test_the_primary_button_has_no_inline_style_tricks(tab, tmp_path):
    """ING-015: the theme's disabled look applies."""
    _fill(tab, tmp_path)
    sheet = tab.create_btn.styleSheet()
    assert "qlineargradient" not in sheet and tab.create_btn.property("kind") == "primary"


def test_reset_returns_to_the_starting_texts(tab, tmp_path):
    """ING-038 / ING-039."""
    start = (tab.progress_label.text(), tab.stats_label.text())
    _fill(tab, tmp_path)
    tab.move_radio.setChecked(True)
    tab.fast_mode_cb.setChecked(True)
    tab.clear_all()
    assert (tab.progress_label.text(), tab.stats_label.text()) == start
    assert tab.copy_radio.isChecked() and not tab.fast_mode_cb.isChecked()
    assert tab.create_btn.text() == "Build project"


# ------------------------------------------------------------------ a run
def test_a_whole_ingest_copies_by_default(tab, tmp_path, sync):
    projects, drive = _fill(tab, tmp_path)
    before = _files(drive)
    seen = {}
    real = ipd.IngestPreflightDialog.__init__

    def spy(self, *a, **k):
        real(self, *a, **k)
        seen["headline"] = self.headline.text()
        seen["button"] = self.go_btn.text()
    import unittest.mock as um
    with um.patch.object(ipd.IngestPreflightDialog, "__init__", spy):
        tab.start_creation_process()

    assert seen["headline"].startswith("Copy 9 file(s)") and seen["button"] == "Copy 9 file(s)"
    assert _files(drive) == before                                     # ING-031
    assert "REEL_01/SH_010/01_Scan/v001/EXR/SH_010.1001.exr" in _files(projects / "DEMO_PRJ" / "05_Reels")
    assert tab._phase == "idle" and not tab.stop_btn.isEnabled()
    assert tab.last_run_label.isVisible() or tab.last_run_label.isVisibleTo(tab)   # ING-011
    tab.check_destination_status()
    assert "Last run" in tab.last_run_label.text()                     # stays after the status refresh
    assert tab.create_btn.text() == "Update project"
    assert sync[-1].title == "Ingest finished" and sync[-1].report          # ING-055


def test_cancelling_the_preflight_leaves_everything_alone(tab, tmp_path, monkeypatch):
    """ING-008."""
    projects, drive = _fill(tab, tmp_path)
    monkeypatch.setattr(ipd.IngestPreflightDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    tab.start_creation_process()
    assert list(projects.iterdir()) == []
    assert tab._phase == "idle" and not tab.is_processing


def test_a_locked_project_leaves_the_buttons_and_log_alone(tab, tmp_path, sync):
    """ING-020 / ING-059: the lock is taken before anything says running."""
    from slate.core.domain.ingest_lock import IngestLock
    projects, drive = _fill(tab, tmp_path)
    tab.log_message("[INFO] previous run")
    other = IngestLock(projects / "DEMO_PRJ", holder="Someone Else (someone)").acquire()
    tab.start_creation_process()
    assert not tab.stop_btn.isEnabled() and not tab.pause_btn.isEnabled()
    assert "previous run" in tab.log_text.toPlainText()
    assert any("Someone Else" in str(m) for m in sync)
    other.release()


def test_a_stopped_run_is_reported_as_stopped(tab, tmp_path, sync, monkeypatch):
    """ING-003: the stop pressed during the run."""
    _fill(tab, tmp_path)
    real = structure.FolderCreationWorker._transfer

    def stop_soon(worker, *a, **k):
        status = real(worker, *a, **k)
        worker.stop()
        return status

    monkeypatch.setattr(structure.FolderCreationWorker, "_transfer", stop_soon)
    tab.start_creation_process()
    assert sync[-1].title == "Stopped"
    assert "of 9 files" in sync[-1].text
    assert tab.progress_bar.value() < 100


def test_pause_shows_paused_until_resumed(tab):
    """ING-013."""
    tab._set_phase("run")
    tab.folder_creation_thread = structure.FolderCreationWorker(target_dir=".", dry_run=True)
    tab.toggle_pause()
    tab.update_folder_creator_progress(5, "Copying 10 of 500 files")
    assert tab.progress_label.text() == "Paused"
    tab.toggle_pause()
    tab.update_folder_creator_progress(6, "Copying 11 of 500 files")
    assert tab.progress_label.text() == "Copying 11 of 500 files"
    tab.folder_creation_thread = None
    tab._set_phase("idle")


def test_progress_reaches_the_main_status_bar(qtbot, sync):
    """ING-057: the parent is a stacked widget, not the window."""
    window = QMainWindow()
    stack = QStackedWidget()
    window.setCentralWidget(stack)
    widget = fct.FolderCreatorTab(Config())
    stack.addWidget(widget)
    qtbot.addWidget(window)
    widget.update_folder_creator_progress(10, "Copying 1 of 10 files")
    assert window.statusBar().currentMessage() == "Copying 1 of 10 files"


def test_a_dry_run_completes_without_touching_the_projects_folder(tab, tmp_path, sync):
    projects, drive = _fill(tab, tmp_path)
    tab.dry_run_cb.setChecked(True)
    tab.start_creation_process()
    assert list(projects.iterdir()) == []
    assert sync[-1].title == "Dry run finished"
    assert "failed" not in sync[-1].text.lower()                   # ING-035
    assert tab.create_btn.text() == "Build project"                # still a new project


def test_failed_files_can_be_retried_later(qtbot, tmp_path, sync, monkeypatch):
    """ING-022: from the newest manifest, after a restart, in the background."""
    def fail(*a, **k):
        return False, "SIMULATED", 0
    real = structure.SafeFileOperations.safe_copy_with_verification
    monkeypatch.setattr(structure.SafeFileOperations, "safe_copy_with_verification", staticmethod(fail))
    first = fct.FolderCreatorTab(Config())
    qtbot.addWidget(first)
    projects, drive = _fill(first, tmp_path)
    first.start_creation_process()
    assert sync[-1].title == "Finished with problems"
    monkeypatch.setattr(structure.SafeFileOperations, "safe_copy_with_verification", staticmethod(real))

    again = fct.FolderCreatorTab(Config())                         # a new session
    qtbot.addWidget(again)
    _fill(again, tmp_path)
    assert again.retry_btn.isEnabled() and "9" in again.retry_btn.text()
    again.retry_failed_files()
    assert sync[-1].title == "Retry finished"
    assert len(_files(projects / "DEMO_PRJ" / "05_Reels")) >= 9
    assert not again.retry_btn.isEnabled()


def test_new_scans_on_tracked_shots_are_listed(tab, tmp_path, sync):
    """ING-056."""
    _fill(tab, tmp_path)
    tab.start_creation_process()
    # Same names, new files (a re-grade): new scan versions on tracked shots.
    for f in (tmp_path / "drive_A").rglob("*.exr"):
        f.write_bytes(b"regraded")
    tab.start_creation_process()
    assert "new scan v002" in sync[-1].text


def test_log_is_one_line_per_sequence_and_filters(tab, tmp_path, sync):
    """ING-050."""
    _fill(tab, tmp_path)
    tab.start_creation_process()
    lines = tab.log_text.toPlainText().splitlines()
    assert len(lines) < 30
    tab.log_message("[ERR] something broke")
    tab.errors_only_cb.setChecked(True)
    shown = tab.log_text.toPlainText()
    assert "something broke" in shown and "[OK]" not in shown


def test_enter_starts_the_check(tab, tmp_path, monkeypatch):
    """ING-077."""
    _fill(tab, tmp_path)
    started = []
    monkeypatch.setattr(fct.FolderCreatorTab, "start_creation_process", lambda self: started.append(1))
    QTest.keyClick(tab.project_name_input, Qt.Key.Key_Return)
    assert started


def test_admins_can_clear_a_stale_lock(tab, tmp_path, monkeypatch):
    """ING-059."""
    from slate.core.domain.ingest_lock import IngestLock, lock_info
    projects, _ = _fill(tab, tmp_path)
    IngestLock(projects / "DEMO_PRJ", holder="ghost").acquire()
    monkeypatch.setattr("slate.gui.components.feedback.confirm", lambda *a, **k: True)
    tab.user_data = {"username": "admin", "roles": ["Admin"]}
    assert tab._take_lock(projects / "DEMO_PRJ")
    assert lock_info(projects / "DEMO_PRJ").holder != "ghost"
    tab._release_ingest_lock()


def test_closing_while_busy_is_announced(tab):
    """ING-061 (foundation) still holds with the new threads."""
    assert tab.busy_reason() is None


def test_a_bad_code_is_not_shown_as_the_project_folder(tab):
    """NEW-ingest-4."""
    tab.project_name_input.setText("BAD/NAME")
    assert tab.folder_preview_tree.topLevelItem(0).text(0) == "Project"
    tab.project_name_input.setText("GOOD")
    assert tab.folder_preview_tree.topLevelItem(0).text(0) == "GOOD"


def test_preflight_shot_names_are_not_dimmed(qtbot, tmp_path):
    """NEW-ingest-3: names that will be used read as normal text."""
    from slate.core.domain.ingest_survey import survey_drive
    from slate.core.infra.gate import Gate
    drive = _drive(tmp_path / "d")
    survey = survey_drive(drive)
    dialog = ipd.IngestPreflightDialog(survey, project_code="P", project_path=tmp_path / "P")
    qtbot.addWidget(dialog)
    item = dialog.table.item(0, dialog.COL_SHOT)
    brush = item.foreground()
    assert brush.style() == Qt.BrushStyle.NoBrush or brush.color().name().lower() != Gate.TEXT_DIM.lower()
    assert "3 shot(s)" in dialog.headline.text()


def test_stitch_merge_is_a_real_checkbox(qtbot):
    """ING-048."""
    from slate.core.domain.stitch_detect import StitchGroup
    from slate.gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog
    from PySide6.QtWidgets import QCheckBox
    dialog = StitchConfirmDialog([StitchGroup("SH010", ["SH010_A", "SH010_B"], "R1")])
    qtbot.addWidget(dialog)
    item = dialog.tree.topLevelItem(0)
    box = dialog.tree.itemWidget(item, 0).findChild(QCheckBox)
    assert box.isChecked()
    box.setChecked(False)
    assert dialog.mapping() == {}
    item.setCheckState(0, Qt.CheckState.Checked)
    assert box.isChecked() and dialog.mapping()


def test_preflight_counts_unchanged_shots_by_destination(qtbot, tmp_path):
    """NEW-ingest-2 (round 3): a re-delivered shot already there is one shot, not two folders."""
    from slate.core.domain.ingest_survey import survey_drive
    drive = tmp_path / "d"
    for tag in ("ScanA", "ScanB"):
        folder = drive / "REEL_02" / f"SH_050_{tag}"
        folder.mkdir(parents=True)
        (folder / f"s.{tag}.1001.exr").write_bytes(b"x")
    survey = survey_drive(drive)
    for shot in survey.shots:
        shot.unchanged_from, shot.skip = "v001", True
    dialog = ipd.IngestPreflightDialog(survey, project_code="P", project_path=tmp_path / "P")
    qtbot.addWidget(dialog)
    # ING2-033: nothing to copy - the headline says so and the one button closes.
    assert "all 1 shot(s) are already in P" in dialog.headline.text()
    assert dialog.go_btn.text() == "Close"


# ------------------------------------------------------------------ round 2 (ING2-0xx)
def _survey_of(tmp_path, folders):
    from slate.core.domain.ingest_survey import survey_drive
    drive = tmp_path / "rd"
    for rel in folders:
        (drive / rel).mkdir(parents=True, exist_ok=True)
        for frame in (1001, 1002):
            (drive / rel / f"p.{frame}.exr").write_bytes(b"x")
    return survey_drive(drive), drive


def test_preflight_marks_clashing_rows_in_their_own_spelling(qtbot, tmp_path):
    """ING2-024 / ING2-025: both rows red, original spelling, sortable without losing the shot."""
    survey, _ = _survey_of(tmp_path, ["REEL_01/SH_0110", "REEL_01/SH_0120"])
    dialog = ipd.IngestPreflightDialog(survey, project_code="P", project_path=tmp_path / "P")
    qtbot.addWidget(dialog)
    dialog.table.sortItems(dialog.COL_SHOT, Qt.SortOrder.DescendingOrder)
    row = next(r for r in range(dialog.table.rowCount())
               if dialog.table.item(r, dialog.COL_SHOT).text() == "SH_0110")
    dialog.table.item(row, dialog.COL_SHOT).setText("SH_0120")
    assert [s.name for s in survey.shots] == ["SH_0120", "SH_0120"]
    assert "REEL_01/SH_0120" in dialog.error_label.text()
    marks = {dialog.table.item(r, dialog.COL_SHOT).data(Qt.ItemDataRole.UserRole + 99)
             for r in range(dialog.table.rowCount())}
    assert marks == {"bad"} and not dialog.go_btn.isEnabled()
    dialog.attention_cb.setChecked(True)
    assert not any(dialog.table.isRowHidden(r) for r in range(dialog.table.rowCount()))


def test_preflight_lists_client_material_and_loose_files(qtbot, tmp_path):
    """ING2-014 / ING2-032: a row for the material; loose files can become a shot."""
    from slate.core.domain.ingest_survey import survey_drive
    _survey, drive = _survey_of(tmp_path, ["REEL_01/SH_010"])
    (drive / "notes").mkdir()
    (drive / "notes/list.pdf").write_bytes(b"p")
    (drive / "ref.jpg").write_bytes(b"j")
    survey = survey_drive(drive)
    dialog = ipd.IngestPreflightDialog(survey, project_code="P", project_path=tmp_path / "P",
                                       client_folder="00_Incoming")
    qtbot.addWidget(dialog)
    assert dialog.table.rowCount() == 2                     # the shot and the client material
    assert any("00_Incoming/&lt;date&gt;_docs" in n for n in dialog.notes())
    assert dialog.loose_cb.isVisibleTo(dialog)
    dialog.loose_cb.setChecked(True)
    assert dialog.table.rowCount() == 3 and any(s.is_root for s in survey.shots)


def test_preflight_dry_run_can_go_for_real_and_says_twice_delivered(qtbot, tmp_path):
    """ING2-046 / ING2-043."""
    survey, _ = _survey_of(tmp_path, ["REEL_01/SH_050_ScanA", "REEL_01/SH_050_ScanB"])
    dialog = ipd.IngestPreflightDialog(survey, project_code="P", project_path=tmp_path / "P", dry_run=True)
    qtbot.addWidget(dialog)
    assert dialog.real_btn.isVisibleTo(dialog) and dialog.real_btn.text() == "Copy 4 file(s) for real"
    assert any(n.startswith("SH_050 arrives 2 times (ScanA, ScanB)") for n in dialog.notes())
    dialog._accept_for_real()
    assert dialog.dry_run is False


def test_template_dialog_round_two(qtbot):
    """ING2-007 / ING2-006 / ING2-016 / ING2-036."""
    dialog = CustomTemplateDialog(None, None)
    qtbot.addWidget(dialog)
    assert dialog.error_label.isVisibleTo(dialog) and "Enter a name" in dialog.error_label.text()
    assert not dialog.rename_btn.isEnabled() and not dialog.remove_btn.isEnabled()
    dialog.name_input.setText("Client X")
    dialog.client_input.setText("00_Incoming")
    version = dialog.sections["scan_version_folders"]
    dialog.tree_widget.setCurrentItem(version.child(0))
    assert dialog.remove_btn.isEnabled()
    dialog.remove_selected()
    data = dialog.get_template_data()
    assert data["scan_version_folders"] == [] and data["client_folder"] == "00_Incoming"
    assert data["description"] == ""
    assert fct.FolderCreatorTab._version_folders(data) == []
    assert fct.FolderCreatorTab._version_folders({"base_folders": []}) == ["Denoise"]


def test_stitch_dialog_refuses_an_emptied_name(qtbot):
    """ING2-040."""
    from slate.core.domain.stitch_detect import StitchGroup
    from slate.gui.dialogs.stitch_confirm_dialog import StitchConfirmDialog
    dialog = StitchConfirmDialog([StitchGroup("SH010", ["SH010_A", "SH010_B"], "R1")])
    qtbot.addWidget(dialog)
    dialog._rows[0][2].setText("")
    assert not dialog.confirm_btn.isEnabled() and "Enter the shot" in dialog.error_label.text()


def test_tab_round_two_bits(tab, tmp_path, sync):
    """ING2-042 / ING2-011 / ING2-012 / ING2-045 / ING2-031 / ING2-022."""
    assert not tab.save_log_btn.isEnabled()
    for n in range(200):
        tab.log_message(f"[INFO] line {n}")
    bar = tab.log_text.verticalScrollBar()
    assert tab.save_log_btn.isEnabled() and bar.value() == bar.maximum()
    assert tab.template_combo.itemText(0).endswith("(built-in)")
    projects, drive = _fill(tab, tmp_path)
    tab.start_creation_process()
    assert "Last run DEMO_PRJ," in tab.last_run_label.text()
    tab._set_phase("run")
    assert not tab.last_run_label.isVisibleTo(tab)
    tab._set_phase("idle")
    assert tab._settings()["last_project_code"] == "DEMO_PRJ"
    (projects / "My-Show").mkdir()
    assert fct.find_project_folder(str(projects), "MYSHOW") == projects / "My-Show"


def test_the_screen_keeps_the_lock_fresh(tab, tmp_path):
    """ING2-017: a timer touches the lock while the run is on screen, paused or not."""
    projects, _ = _fill(tab, tmp_path)
    assert tab._take_lock(projects / "DEMO_PRJ")
    assert tab._lock_timer.isActive()
    touched = []
    tab._ingest_lock.touch = lambda: touched.append(1)
    tab._lock_timer.timeout.emit()
    assert touched
    tab._release_ingest_lock()
    assert not tab._lock_timer.isActive()


def test_a_person_can_clear_their_own_lock(tab, tmp_path, monkeypatch):
    """ING2-018: the coordinator whose own Slate crashed does not need an admin."""
    from slate.core.domain.ingest_lock import IngestLock
    projects, _ = _fill(tab, tmp_path)
    IngestLock(projects / "DEMO_PRJ", holder=tab._holder()).acquire()
    monkeypatch.setattr("slate.gui.components.feedback.confirm", lambda *a, **k: True)
    assert tab._take_lock(projects / "DEMO_PRJ")
    tab._release_ingest_lock()


def test_stitch_answers_are_remembered(tab, tmp_path, monkeypatch):
    """ING2-015: the second run of a drive does not ask again."""
    from slate.core.domain.ingest_survey import survey_drive
    from slate.gui.dialogs import stitch_confirm_dialog as scd
    drive = tmp_path / "sd"
    for part in ("SH_0990_A", "SH_0990_B"):
        (drive / "REEL_01" / part).mkdir(parents=True)
        (drive / "REEL_01" / part / f"{part}.1001.exr").write_bytes(b"x")
    survey = survey_drive(drive)
    project = tmp_path / "PRJ"
    asked = []
    monkeypatch.setattr(scd.StitchConfirmDialog, "exec", lambda self: asked.append(1) or 1)
    mapping, decisions = tab._confirm_stitch_shots(survey, project, "01_Frm Client")
    tab._save_stitch_decisions(project, "01_Frm Client", decisions)
    again, more = tab._confirm_stitch_shots(survey, project, "01_Frm Client")
    assert len(asked) == 1 and again == mapping and more == {}


def test_a_stopped_run_offers_to_finish(tab, tmp_path, sync, monkeypatch):
    """ING2-003: the button says what it does after a stop."""
    _fill(tab, tmp_path)
    real = structure.FolderCreationWorker._transfer

    def stop_soon(worker, *a, **k):
        status = real(worker, *a, **k)
        worker.stop()
        return status

    monkeypatch.setattr(structure.FolderCreationWorker, "_transfer", stop_soon)
    tab.start_creation_process()
    tab.check_destination_status()
    assert tab.retry_btn.isEnabled() and tab.retry_btn.text().startswith("Finish the stopped run")
