"""The Hardware tab on screen (IT-001 ... IT-046)."""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QAbstractItemView, QApplication, QDialog, QLabel

from slate.core.domain import hardware as hw


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain import people

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "hw.db"))
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    manager.execute_update("INSERT INTO ut_users (username, display_name, roles) VALUES (%s, %s, %s)",
                           ("rahul.s", "Rahul Sharma", '["Artist"]'))
    people.refresh()
    try:
        yield manager
    finally:
        people.refresh()
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


_KEEP = []


def _tab(db):
    from slate.gui.tabs.it_inventory_tab import ItInventoryTab
    tab = ItInventoryTab(user_data={"username": "it.sana"})
    _KEEP.append(tab)
    tab.resize(1280, 720)
    tab.show()
    return tab


def _seed(tab):
    repo = tab.repo
    repo.add("WS-A", {"cpu": "Xeon", "gpu": "RTX 4090", "location": "Comp bay 2"})
    repo.add("WS-B" + "X" * 55, {})
    repo.add("WS-C", {}, status=hw.REPAIR)
    repo.add("WS-OLD", {})
    repo.set_status("WS-OLD", hw.RETIRED)
    repo.service.issue_machine("WS-A", "rahul.s", "it.sana")
    tab.load_data()


def _rows(tab):
    return [tab.grid.item(r, 0).text() for r in range(tab.grid.rowCount()) if not tab.grid.isRowHidden(r)]


def test_the_status_filter_filters(db, app):
    """IT-001, IT-015, IT-041."""
    tab = _tab(db)
    _seed(tab)
    assert tab.filter_cb.itemText(0) == "All statuses"
    assert "Retired" in [tab.filter_cb.itemText(i) for i in range(tab.filter_cb.count())]
    tab.filter_cb.setCurrentIndex(tab.filter_cb.findData(hw.REPAIR))
    assert _rows(tab) == ["WS-C"]
    assert not [l for l in tab.findChildren(QLabel) if "Filter by Status" in l.text()]


def test_retired_machines_hide_until_asked_for(db, app):
    """IT-016."""
    tab = _tab(db)
    _seed(tab)
    assert "WS-OLD" not in _rows(tab)
    tab.show_retired.setChecked(True)
    assert "WS-OLD" in _rows(tab)


def test_figures_and_cells(db, app):
    """IT-014, IT-029, IT-039."""
    from slate.core.infra.gate import Gate
    tab = _tab(db)
    _seed(tab)
    assert (tab.fig_total.value_text(), tab.fig_available.value_text(),
            tab.fig_repair.value_text(), tab.fig_loan.value_text()) == ("3", "1", "1", "1")
    texts = [tab.grid.item(r, c).text() for r in range(tab.grid.rowCount())
             for c in range(tab.grid.columnCount())]
    assert "None" not in texts and "N/A" not in texts
    for r in range(tab.grid.rowCount()):
        if tab.grid.item(r, 9).text() == hw.AVAILABLE:
            assert tab.grid.item(r, 9).foreground().color().name().lower() == Gate.INFO.lower()


def test_a_long_name_does_not_take_the_width(db, app):
    """IT-007."""
    tab = _tab(db)
    _seed(tab)
    assert tab.grid.columnWidth(0) <= 200
    long_row = next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text().startswith("WS-BX"))
    assert tab.grid.item(long_row, 0).toolTip().startswith("WS-BX")


def test_buttons_follow_the_selection(db, app):
    """IT-017, IT-019, IT-043."""
    tab = _tab(db)
    _seed(tab)
    assert tab.grid.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
    assert [b.text() for b in (tab.add_btn, tab.edit_btn, tab.issue_btn, tab.collect_btn, tab.delete_btn)] \
        == ["Add machine", "Edit", "Issue to…", "Collect", "Delete"]
    tab.grid.clearSelection()
    assert not tab.issue_btn.isEnabled() and not tab.collect_btn.isEnabled()
    row_a = next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text() == "WS-A")
    tab.grid.selectRow(row_a)
    assert tab.collect_btn.isEnabled() and not tab.issue_btn.isEnabled()      # already out


def test_the_add_dialog(db, app):
    """IT-003, IT-023, IT-024, IT-026."""
    from slate.gui.tabs.it_inventory_tab import AddPCDialog
    dialog = AddPCDialog(hub=None)
    _KEEP.append(dialog)
    items = [dialog.inp_status.itemText(i) for i in range(dialog.inp_status.count())]
    assert "Active" not in items and dialog.inp_status.currentText() == "Available"
    assert not dialog.ok_btn.isEnabled()
    dialog.inp_name.setText("WS-NEW-01")
    assert dialog.ok_btn.isEnabled()
    assert dialog.inp_ram.minimum() == 0
    dialog.inp_ram.setValue(64)
    assert dialog.values()["ram"] == "64 GB"
    notes = [l.text() for l in dialog.findChildren(QLabel)]
    assert any(t.startswith("To hand this machine to someone") for t in notes)


def test_the_edit_dialog_offers_in_service_not_active(db, app):
    """IT-004, IT-025."""
    from slate.gui.tabs.it_inventory_tab import AddPCDialog
    dialog = AddPCDialog(hub=None, edit_data={"machine_name": "WS-A", "status": "Active"})
    _KEEP.append(dialog)
    items = [dialog.inp_status.itemText(i) for i in range(dialog.inp_status.count())]
    assert items == list(hw.EDIT_STATUSES) and dialog.inp_status.currentText() == hw.IN_SERVICE
    assert dialog.name_label.text() == "WS-A" and dialog.btn_rename.text() == "Rename…"


def test_issue_uses_the_person_picker_and_collect_words_repair(db, app, monkeypatch):
    """IT-020, IT-021, IT-022."""
    from slate.gui.tabs import it_inventory_tab as module
    from slate.gui.components import feedback
    tab = _tab(db)
    tab.repo.add("WS-I", {})
    tab.load_data()

    class Picked(module.IssueDialog):
        def exec(self):
            self.person.set_username("rahul.s")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "IssueDialog", Picked)
    toasts = []
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: toasts.append(a[1]))
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    tab.grid.selectRow(next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text() == "WS-I"))
    tab.issue_selected()
    assert tab.repo.holder("WS-I") == "rahul.s"
    assert "Rahul Sharma" in toasts[-1]
    db.execute_update("UPDATE hardware_inventory SET status = 'Repair' WHERE machine_name = 'WS-I'")
    tab.load_data()
    tab.grid.selectRow(next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text() == "WS-I"))
    tab.collect_selected()
    assert "still marked for repair" in toasts[-1]


def test_issue_to_a_leaver_says_why_and_offers_the_override(db, app, monkeypatch):
    """Integration: issue_machine refuses leavers; the tab shows the reason and can override."""
    from datetime import date, timedelta
    from slate.gui.tabs import it_inventory_tab as module
    from slate.gui.components import feedback
    from slate.core.domain.onboarding_service import LEAVING
    tab = _tab(db)
    tab.repo.add("WS-L1", {})
    tab.load_data()
    tab._service().start("rahul.s", LEAVING, effective_date=date.today() + timedelta(days=10))   # still here, leaving soon

    class Picked(module.IssueDialog):
        def exec(self):
            self.person.set_username("rahul.s")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "IssueDialog", Picked)
    asked, warned, toasts = [], [], []
    answer = {"yes": False}
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append((a[2], k)) or answer["yes"])
    monkeypatch.setattr(feedback, "warn", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: toasts.append(a[1]))

    def pick():
        tab.grid.selectRow(next(r for r in range(tab.grid.rowCount())
                                if tab.grid.item(r, 0).text() == "WS-L1"))
        tab.issue_selected()

    pick()
    assert asked and "Rahul Sharma" in asked[-1][0] and "leaving list" in asked[-1][0]
    assert asked[-1][1]["yes_label"] == "Issue anyway"
    assert tab.repo.holder("WS-L1") is None and not warned      # cancelled, no "not saved"
    answer["yes"] = True
    pick()
    assert tab.repo.holder("WS-L1") == "rahul.s" and not warned and "Rahul Sharma" in toasts[-1]


def test_sync_names_the_reports_it_could_not_read(db, app, tmp_path, monkeypatch):
    """IT-012."""
    from slate.gui.components import feedback
    tab = _tab(db)
    status = tmp_path / "status"
    status.mkdir()
    (status / "broken.json").write_text("{nope")
    (status / "WS-L.json").write_text(json.dumps({"ComputerName": "WS-L", "CPU": "i9"}))

    class Hub:
        def get_livestatus_dir(self):
            return status

    tab.hub = Hub()
    monkeypatch.setattr(feedback, "inform", lambda *a, **k: None)
    tab.sync_from_live_ops()
    assert "broken.json" in tab.last_sync_message and "could not be read" in tab.last_sync_message
    assert "WS-L" in _rows(tab)


def test_margins_match_the_other_it_screens(db, app):
    """IT-044."""
    from slate.gui.tabs.licence_view import LicenceView
    tab = _tab(db)
    lic = LicenceView("it.sana", db_manager=db)
    _KEEP.append(lic)
    assert tab.layout().contentsMargins() == lic.layout().contentsMargins()


def test_issue_is_off_for_a_machine_in_repair(db, app):
    """NEW-it-1."""
    tab = _tab(db)
    _seed(tab)
    row_c = next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text() == "WS-C")
    tab.grid.selectRow(row_c)
    assert not tab.issue_btn.isEnabled()



# ------------------------------------------------------------------ round 2

def test_figure_filters_show_what_they_count_and_columns_read_well(db, app):
    """IT2-036 / IT2-037 / IT2-041 / IT2-051 / IT2-064."""
    from PySide6.QtCore import Qt
    tab = _tab(db)
    _seed(tab)
    tab.repo.update("WS-C", {"asset_tag": "TAG-9277"})
    tab.load_data()
    tab.fig_loan.clicked.emit()
    assert _rows(tab) == ["WS-A"] and tab.fig_loan._selected
    tab.fig_total.clicked.emit()
    assert len(_rows(tab)) == 3
    tab.toolbar.search.setText("TAG-9277")
    tab.toolbar.filter.apply()
    assert _rows(tab) == ["WS-C"]
    assert tab.toolbar.count_label.text() == "1 of 4 machines"
    assert tab.fig_repair.toolTip()
    ram = tab.grid.item(0, 6)
    assert ram.textAlignment() & Qt.AlignmentFlag.AlignRight


def test_delete_says_why_before_it_is_pressed_and_read_only_hides_changes(db, app):
    """IT2-039 / IT2-081."""
    from slate.gui.tabs.it_inventory_tab import ItInventoryTab
    tab = _tab(db)
    _seed(tab)
    row_a = next(r for r in range(tab.grid.rowCount()) if tab.grid.item(r, 0).text() == "WS-A")
    tab.grid.selectRow(row_a)
    assert not tab.delete_btn.isEnabled() and "Retired" in tab.delete_btn.toolTip()
    reader = ItInventoryTab(user_data={"username": "intern"}, read_only=True)
    _KEEP.append(reader)
    assert not reader.add_btn.isVisibleTo(reader) and not reader.issue_btn.isVisibleTo(reader)
    assert reader.history_btn.isVisibleTo(reader)


def test_the_add_dialog_for_a_monitor_and_the_edit_dialog_has_no_gap(db, app):
    """IT2-044 / IT2-045 / IT2-046 / IT2-047 / IT2-050."""
    from slate.gui.tabs.it_inventory_tab import AddPCDialog
    dialog = AddPCDialog(locations=["Comp Floor 2", "Comp Floor 2", None])
    _KEEP.append(dialog)
    assert dialog.inp_location.completer() is not None
    dialog.inp_type.setCurrentText("Monitor")
    dialog.inp_name.setText("Dell U2723QE #3")
    assert dialog.ok_btn.isEnabled()
    assert not dialog.form.isRowVisible(dialog.inp_cpu)
    edit = AddPCDialog(edit_data={"machine_name": "WS-A", "status": "Active"})
    _KEEP.append(edit)
    assert not edit.name_hint.isVisibleTo(edit)
