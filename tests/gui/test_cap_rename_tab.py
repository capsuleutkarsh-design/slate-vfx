"""CAP Rename on screen (ING-080 ... ING-123), offscreen Qt."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest

from slate.core.domain import batch_rename as br
from slate.gui import cap_rename_tab as crt


class _Config(SimpleNamespace):
    def __init__(self, app_dir):
        super().__init__(settings={}, app_data_dir=app_dir, saved=0)

    def save_settings(self, settings):
        self.saved += 1
        return True


@pytest.fixture
def tab(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(crt.CapRenameTab, "_confirm", lambda self, *a, **k: True)
    monkeypatch.setattr("slate.gui.components.feedback.toast", lambda *a, **k: None)
    monkeypatch.setattr("slate.gui.components.feedback.warn", lambda *a, **k: None)
    config = _Config(tmp_path / "app")
    widget = crt.CapRenameTab(config, user_data={"username": "priya"})
    qtbot.addWidget(widget)
    widget.resize(1280, 720)
    return widget


def _make(folder: Path, names):
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name in names:
        (folder / name).write_text(name, encoding="utf-8")
        out.append(folder / name)
    return out


def _run_sync(monkeypatch):
    """Run the worker on the calling thread so the test can look at the result."""
    monkeypatch.setattr(crt.RenameWorker, "start", lambda self: self.run())


def _statuses(tab):
    return [tab.table.item(r, 2).text() for r in range(tab.table.rowCount())]


def test_rename_files_renames_and_the_list_shows_the_new_names(tab, tmp_path, monkeypatch):
    files = _make(tmp_path / "p", ["IMG_1.JPG", "IMG_2.JPG"])
    audit = []
    monkeypatch.setattr("slate.core.infra.audit_logger.AuditLogger.log_event",
                        lambda self, kind, user, details, status="SUCCESS": audit.append((kind, user, details)))
    _run_sync(monkeypatch)
    tab._add_paths(files)
    tab.search_edit.setText("IMG")
    tab.replace_edit.setText("SH")
    assert tab.rename_btn.isEnabled()

    tab.execute_rename()

    assert sorted(p.name for p in (tmp_path / "p").iterdir()) == ["SH_1.JPG", "SH_2.JPG"]
    # ING-106: the list keeps the files, under their new names.
    assert [tab.table.item(r, 0).text() for r in range(2)] == ["SH_1.JPG", "SH_2.JPG"]
    # ING-109: recorded with who did it.
    assert audit and audit[0][0] == "RENAME" and audit[0][1] == "priya" and "2 file(s)" in audit[0][2]
    # ING-082: undo is offered from the journal; nothing next to the plates.
    assert tab.undo_btn.isEnabled()
    assert not list((tmp_path / "p").glob("*.bat"))

    tab.undo_last_rename()
    assert sorted(p.name for p in (tmp_path / "p").iterdir()) == ["IMG_1.JPG", "IMG_2.JPG"]
    assert [tab.table.item(r, 0).text() for r in range(2)] == ["IMG_1.JPG", "IMG_2.JPG"]


def test_confirm_shows_the_folder_and_examples(tab, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(crt.CapRenameTab, "_confirm",
                        lambda self, title, text, yes: seen.update(text=text, yes=yes) or False)
    tab._add_paths(_make(tmp_path / "p", ["IMG_1.JPG"]))
    tab.search_edit.setText("IMG")
    tab.replace_edit.setText("SH")
    tab.execute_rename()
    assert str(tmp_path / "p") in seen["text"]
    assert "IMG_1.JPG  ->  SH_1.JPG" in seen["text"]
    assert "Undo last rename" in seen["text"]
    assert (tmp_path / "p" / "IMG_1.JPG").exists()


def test_while_running_the_inputs_cannot_start_a_second_run(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", ["IMG_1.JPG"]))
    tab.search_edit.setText("IMG")
    tab.replace_edit.setText("SH")
    tab._busy = True
    tab._set_running(True)
    tab.search_edit.setText("IM")          # editing used to re-enable Rename
    assert not tab.rename_btn.isEnabled()
    assert not tab.mode_tabs.isEnabled() and not tab.load_btn.isEnabled()
    assert tab.cancel_btn.isVisible() or tab.cancel_btn.isEnabled()


def test_delete_removes_the_selected_files_from_the_list(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", ["a.exr", "b.exr", "c.exr"]))
    tab.show()
    tab.table.selectRow(1)
    tab.table.setFocus()
    QTest.keyClick(tab.table, Qt.Key.Key_Delete)
    assert [f.name for f in tab.files] == ["a.exr", "c.exr"]
    assert tab.table.rowCount() == 2
    assert (tmp_path / "p" / "b.exr").exists()      # nothing on disk changes
    tab.clear_list()
    assert tab.files == [] and tab.table.rowCount() == 0


def test_two_loads_add_up_and_a_drop_adds_files(tab, tmp_path):
    first = _make(tmp_path / "p", ["a.exr"])
    second = _make(tmp_path / "q", ["b.exr", "c.exr"])
    tab._add_paths(first)
    tab._add_paths(second + first)            # duplicates ignored
    assert [f.name for f in tab.files] == ["a.exr", "b.exr", "c.exr"]
    # ING-123: remembered for the next dialog.
    assert tab.config_manager.settings["cap_rename"]["last_folder"] == str(tmp_path / "q")
    assert tab._start_folder() == str(tmp_path / "q")

    dropped = _make(tmp_path / "r", ["d.exr"])
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "r"))])
    event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    tab.dropEvent(event)
    assert tab.files[-1] == dropped[0]


def test_natural_order_on_load(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", [f"IMG_{i}.JPG" for i in (10, 2, 1)]))
    assert [f.name for f in tab.files] == ["IMG_1.JPG", "IMG_2.JPG", "IMG_10.JPG"]
    tab.table.selectRow(2)
    tab.move_selected(-1)
    assert [f.name for f in tab.files] == ["IMG_1.JPG", "IMG_10.JPG", "IMG_2.JPG"]
    assert tab.sort_combo.currentText() == "Custom order"


def test_collisions_and_the_summary_and_filter(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", [f"IMG_{i}.JPG" for i in range(1, 13)] + ["keep.exr"]))
    tab.regex_cb.setChecked(True)
    tab.search_edit.setText(r"IMG_\d+")
    tab.replace_edit.setText("IMG_X")
    assert _statuses(tab).count(br.CONFLICT) == 12
    assert "12 conflict(s)" in tab.summary_label.text() and "13 file(s)" in tab.summary_label.text()
    tab.filter_combo.setCurrentIndex(2)
    assert tab.table.isRowHidden(12) and not tab.table.isRowHidden(0)
    assert "cannot be renamed" in tab.rename_btn.toolTip()


def test_status_words_are_one_case(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", ["IMG_1.JPG", "a.exr", "IMG_2.JPG"]))
    tab.search_edit.setText("IMG_1")
    tab.replace_edit.setText("IMG_2")
    assert set(_statuses(tab)) <= {br.WILL_RENAME, br.UNCHANGED, br.CONFLICT}


def test_a_bad_pattern_shows_its_reason_under_find(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", ["IMG_1.JPG"]))
    tab.regex_cb.setChecked(True)
    tab.search_edit.setText("(")
    assert tab.pattern_error_label.isVisibleTo(tab)
    assert "missing )" in tab.pattern_error_label.text()
    assert not tab.rename_btn.isEnabled()
    assert _statuses(tab) == [br.UNCHANGED]


def test_rename_button_says_why_it_is_off(tab, tmp_path):
    assert tab.rename_btn.toolTip() == "Load files first."
    tab._add_paths(_make(tmp_path / "p", ["a.exr"]))
    assert tab.rename_btn.toolTip() == "No names change with these rules."
    tab.mode_tabs.setCurrentIndex(1)
    assert not tab.rename_btn.isEnabled()
    assert tab.rename_btn.toolTip() == "Enter a base name."


def test_sequence_controls_are_labelled_outside(tab):
    assert tab.seq_start.prefix() == "" and tab.seq_step.prefix() == "" and tab.seq_padding.prefix() == ""
    labels = {w.text() for w in tab.tab_sequence.findChildren(crt.QLabel)}
    assert {"Base name", "Start frame", "Step", "Padding"} <= labels


def test_padding_overflow_is_flagged_with_a_fix(tab, tmp_path):
    tab._add_paths(_make(tmp_path / "p", ["a.exr", "b.exr"]))
    tab.mode_tabs.setCurrentIndex(1)
    tab.seq_base.setText("s_")
    tab.seq_start.setValue(999999)
    assert tab.padding_warning.isVisibleTo(tab)
    tab.padding_fix_btn.click()
    assert tab.seq_padding.value() == 7
    assert not tab.padding_warning.isVisibleTo(tab)


def test_names_are_consistent(tab):
    assert [tab.mode_tabs.tabText(i) for i in range(2)] == ["Find and replace", "Number as a sequence"]
    assert "\\1" in tab.replace_edit.toolTip() and "case" in tab.search_edit.toolTip().lower()


def test_help_has_no_emoji_and_describes_undo(tab):
    text = tab.help_text()
    assert "Undo last rename" in text
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", text)


def test_keep_extension_is_remembered(tab):
    tab.file_only_cb.setChecked(False)
    assert tab.config_manager.settings["cap_rename"]["keep_extension"] is False


def test_building_the_tab_saves_nothing(qtbot, tmp_path):
    config = _Config(tmp_path / "app")
    widget = crt.CapRenameTab(config)
    qtbot.addWidget(widget)
    assert config.saved == 0
