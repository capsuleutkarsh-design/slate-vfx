"""
The biometric import dialog: skipped lines with their reasons (HR-039), the
column-role pickers kept in sight above the preview (HR-045), plain plurals
(HR-046) and every failed day listed, not only the first (HR-002).
"""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

FILE = ("AC-No.,Name,Time,State\n"
        + "".join("EMP0001,Priya,2026-09-%02d 09:12:33,C/In\nEMP0001,Priya,2026-09-%02d 18:45:59,C/Out\n"
                  % (d, d) for d in range(1, 11))
        + "EMP0001,Priya,2026-02-31 09:00:00,C/In\n")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path, monkeypatch):
    from slate.core.domain import biometric_import as bio
    from slate.gui.dialogs.biometric_import_dialog import BiometricImportDialog
    monkeypatch.setattr(bio, "find_profile", lambda header, profiles=None: None)
    path = tmp_path / "door.csv"
    path.write_text(FILE, encoding="utf-8")
    dialog = BiometricImportDialog(attendance=None, known_ids=["EMP0001"])
    dialog.load_file(path)
    return dialog


def test_skipped_lines_are_listed_with_the_reason(app, mock_db, tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    assert not dialog.btn_skipped.isHidden()
    assert dialog.skipped_table.rowCount() == 1
    assert "2026-02-31" in dialog.skipped_table.item(0, 1).text()
    assert "could not be read" in dialog.skipped_table.item(0, 2).text()


def test_role_pickers_are_not_in_the_scrolling_preview(app, mock_db, tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    assert dialog.roles_row.cellWidget(0, 0) is not None
    assert dialog.table.cellWidget(0, 0) is None
    assert dialog.table.rowCount() == 21


def test_status_uses_real_plurals(app, mock_db, tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    text = dialog.mapping_status.text()
    assert "20 punches read" in text and "10 days for 1 person" in text
    assert "(es)" not in text and "person/people" not in text


def test_every_failed_day_is_listed(app, mock_db, tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    dialog.show_failures([("EMP0001", "2026-09-01", "refused"), ("EMP0002", "2026-09-02", "refused")])
    assert not dialog.failures_table.isHidden()
    assert dialog.failures_table.rowCount() == 2


def test_one_unknown_code_matches_and_the_toggle_says_hide(app, mock_db, tmp_path, monkeypatch):
    from slate.core.domain import biometric_import as bio
    from slate.gui.dialogs.biometric_import_dialog import BiometricImportDialog
    monkeypatch.setattr(bio, "find_profile", lambda header, profiles=None: None)
    path = tmp_path / "door.csv"
    path.write_text(FILE + "GUEST9,Guest,2026-09-02 10:00:00,C/In\n", encoding="utf-8")
    dialog = BiometricImportDialog(attendance=None, known_ids=["EMP0001"])
    dialog.load_file(path)
    assert "1 code matches nobody" in dialog.mapping_status.text()
    dialog.btn_skipped.setChecked(True)
    assert dialog.btn_skipped.text().startswith("Hide")
    assert dialog.unknown_table.minimumHeight() >= 90
