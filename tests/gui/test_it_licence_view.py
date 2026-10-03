"""The Licences screen (IT-051 ... IT-076)."""

import os
import sys
from datetime import date, datetime, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog

from slate.core.domain import licence_compliance as lc


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "lic.db"))
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


_KEEP = []


def _keep(w):
    _KEEP.append(w)
    return w


def _in(days):
    return date.today() + timedelta(days=days)


def _view(db, read_only=False):
    from slate.gui.tabs.licence_view import LicenceView
    view = _keep(LicenceView("it.sana", db_manager=db, read_only=read_only))
    view.resize(1280, 720)
    view.show()
    return view


def test_read_only_shows_no_changing_buttons(db, app):
    """IT-076."""
    view = _view(db, read_only=True)
    texts = {b.text() for b in view.findChildren(type(view.btn_edit)) if b.isVisibleTo(view)}
    assert "Readings…" in texts and "Export…" in texts
    assert not texts & {"Record usage", "Add licence", "Edit", "Remove",
                        "Import from licence server…"}


def test_the_reading_picker_tells_two_contracts_apart(db, app):
    """IT-052, IT-058."""
    from slate.gui.tabs.licence_view import ReadingDialog
    rows = [{"id": 1, "software_name": "Nuke", "total_seats": 20, "active_seats": 7,
             "expiration_date": date(2027, 4, 18)},
            {"id": 2, "software_name": "Nuke", "total_seats": 5, "active_seats": 2,
             "expiration_date": None}]
    dialog = _keep(ReadingDialog(rows, preset={"id": 2, "software_name": "Nuke"}))
    labels = [dialog.software.itemText(i) for i in range(2)]
    assert labels[0] != labels[1]
    assert labels[0] == "Nuke - 20 seats, renews 18 Apr 2027"
    assert dialog.software.currentData()["id"] == 2
    assert dialog.in_use.value() == 2                           # the last reading, not 0


def test_a_reading_above_the_seats_asks_first(db, app, monkeypatch):
    """IT-051."""
    from slate.gui.components import feedback
    from slate.gui.tabs.licence_view import ReadingDialog
    dialog = _keep(ReadingDialog([{"id": 1, "software_name": "NukeX", "total_seats": 8}]))
    dialog.in_use.setValue(25)
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append(a) or False)
    accepted = []
    dialog.accept = lambda: accepted.append(True)
    dialog._record()
    assert asked and not accepted


def test_the_licence_dialog_checks_what_it_saves(db, app, monkeypatch):
    """IT-056, IT-057, IT-058, IT-059."""
    from slate.gui.components import feedback
    from slate.gui.tabs.licence_view import LicenceDialog, LicenceView
    view = _view(db)
    view.repo.save("Nuke", 10, _in(200))
    dialog = _keep(LicenceDialog(parent=None, repo=view.repo))
    assert dialog.seats.minimum() == 1 and dialog.seats.value() == 1
    dialog.name.setText("nuke")
    assert "contract" in dialog.problem()
    dialog.contract.setText("Project KLC")
    assert dialog.problem() == ""
    dialog.perpetual.setChecked(True)
    assert dialog.payload()[2] is None

    dialog.perpetual.setChecked(False)
    dialog.expiry.setDate(QDate.currentDate().addDays(-30))
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: asked.append(a) or False)
    accepted = []
    dialog.accept = lambda: accepted.append(True)
    dialog._save()
    assert asked and not accepted


def test_figures_use_one_vocabulary_and_skip_expired_seats(db, app):
    """IT-054, IT-061, IT-070, IT-073."""
    view = _view(db)
    repo = view.repo
    repo.save("Substance", 10, _in(-400))
    repo.save("NukeX", 8, _in(30))
    repo.save("Maya", 30, _in(300))
    ids = {r["software_name"]: r["id"] for r in repo.licences()}
    repo.record("NukeX", 12, 8, licence_id=ids["NukeX"])
    repo.record("Maya", 10, 30, licence_id=ids["Maya"])
    view.refresh()
    assert view.fig_over._label.text() == "OVER-SUBSCRIBED"
    assert view.fig_spare._label.text() == "SPARE SEATS"
    assert view.fig_renew.value_text() == "2"                    # NukeX (over) and Substance
    assert view.fig_bought.value_text() == "38"                  # not the expired 10
    states = {view.table.item(r, 0).text(): view.table.item(r, 1).text()
              for r in range(view.table.rowCount())}
    assert states["NukeX"] == lc.OVER


def test_cost_is_shown_in_rupees(db, app):
    """IT-063."""
    from decimal import Decimal
    view = _view(db)
    view.repo.save("Resolve", 4, _in(300), annual_cost=Decimal("150000"), currency="INR")
    view.refresh()
    assert view.table.item(0, 6).text() == "₹1,50,000"                  # IT2-061


def test_the_window_is_ordered_and_remembered(db, app):
    """IT-072."""
    view = _view(db)
    assert [view.window_pick.itemData(i) for i in range(view.window_pick.count())] == [30, 90, 365]
    view.window_pick.setCurrentIndex(2)
    again = _view(db)
    assert again.window_pick.currentData() == 365
    again.window_pick.setCurrentIndex(1)


def test_long_findings_get_taller_rows_and_long_names_do_not_take_the_width(db, app):
    """IT-065, IT-066."""
    view = _view(db)
    view.repo.save("Adobe After Effects Creative Cloud for Teams - Studio Contract 2026 Edition", 2, _in(20))
    view.refresh()
    assert view.table.columnWidth(0) <= 260
    assert view.table.item(0, 0).toolTip().startswith("Adobe After Effects")
    assert view.table.rowHeight(0) >= view.table.verticalHeader().defaultSectionSize()


def test_remove_counts_exactly_what_goes(db, app, monkeypatch):
    """IT-068."""
    from slate.gui.components import feedback
    view = _view(db)
    view.repo.save("Maya", 30, _in(300))
    lic = view.repo.licences()[0]
    view.repo.record("Maya", 14, 30, licence_id=None)
    view.repo.record("Maya", 5, 30, licence_id=lic["id"], taken_at=datetime.now() - timedelta(days=200))
    view.refresh()
    view.table.selectRow(0)
    said = []
    monkeypatch.setattr(feedback, "confirm", lambda parent, title, text, **k: said.append(text) or False)
    view.remove_licence()
    assert "2 usage readings" in said[0]


def test_the_readings_panel_deletes_a_typo(db, app, monkeypatch):
    """IT-051."""
    from slate.gui.components import feedback
    from slate.gui.tabs.licence_view import ReadingsDialog
    view = _view(db)
    view.repo.save("NukeX", 8, _in(300))
    lic = view.repo.licences()[0]
    view.repo.record("NukeX", 2, 8, licence_id=lic["id"], taken_at=datetime.now() - timedelta(hours=3))
    view.repo.record("NukeX", 25, 8, licence_id=lic["id"])
    dialog = _keep(ReadingsDialog(lic, view.repo))
    assert dialog.table.rowCount() == 2
    dialog.table.selectRow(0)
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    dialog.delete()
    assert dialog.table.rowCount() == 1
    view.refresh()
    assert view._rows[0]["peak"] == 2


def test_import_maps_products_to_licences(db, app):
    """IT-062."""
    from slate.gui.tabs.licence_view import ImportReadingsDialog
    rows = [{"id": 1, "software_name": "Nuke", "total_seats": 10},
            {"id": 2, "software_name": "Houdini", "total_seats": 4}]
    text = ("Users of nuke_i:  (Total of 10 licenses issued;  Total of 3 licenses in use)\n"
            "Users of nuke_r:  (Total of 5 licenses issued;  Total of 1 license in use)\n"
            "Users of mystery:  (Total of 2 licenses issued;  Total of 2 licenses in use)\n")
    dialog = _keep(ImportReadingsDialog(rows, text=text))
    # IT2-052: prefix matches are suggested, not ticked - a render pool is not
    # added into the interactive seats.
    assert dialog.chosen() == []
    assert dialog.rows[0][1].currentData()["id"] == 1 and not dialog.rows[0][0].isChecked()
    exact = _keep(ImportReadingsDialog(
        [{"id": 1, "software_name": "nuke_i", "total_seats": 10}], text=text))
    assert {lic["id"]: n for lic, n in exact.chosen()} == {1: 3}
    assert "1 ticked" in exact.summary.text() and exact.ok_btn.isEnabled()
    dialog.rows[0][0].setChecked(True)
    dialog.rows[1][0].setChecked(True)
    assert dialog.shared() == ["nuke_i and nuke_r all go to Nuke"]


def test_search_finds_the_vendor_and_rows_are_never_cut(db, app):
    """NEW-it-2, IT-065."""
    from PySide6.QtCore import Qt
    view = _view(db)
    view.repo.save("Maya", 30, _in(300), vendor="Autodesk via Prime")
    view.repo.save("Nuke", 10, _in(300), vendor="Foundry")
    view.refresh()
    view.toolbar.search.setText("prime")
    view.toolbar.filter.apply()
    shown = [view.table.item(r, 0).text() for r in range(view.table.rowCount()) if not view.table.isRowHidden(r)]
    assert shown == ["Maya"]
    assert view.table.isColumnHidden(8)
    assert view.table.textElideMode() == Qt.TextElideMode.ElideNone
    view.toolbar.search.setText("")
    view.toolbar.filter.apply()
    view.resize(1280, 720)
    app.processEvents()
    view._fit_rows()
    for r in range(view.table.rowCount()):
        needed = view.table.sizeHintForRow(r)
        assert view.table.rowHeight(r) >= needed


def test_note_fields_use_the_interface_font(db, app):
    """NEW-it-4."""
    from slate.gui.tabs.licence_view import LicenceDialog
    dialog = _keep(LicenceDialog())
    assert dialog.notes.property("prose") is True



# ------------------------------------------------------------------ round 2

def test_screen_conventions(db, app):
    """IT2-060 / IT2-062 / IT2-063 / IT2-064 / IT2-065 / IT2-066."""
    from slate.core.infra.gate import Gate
    view = _view(db)
    assert view.fig_over._tone == "idle"
    view.repo.save("Houdini FX", 3, _in(-12))
    view.repo.save("Maya", 30, _in(300))
    ids = {r["software_name"]: r["id"] for r in view.repo.licences()}
    view.repo.record("Houdini FX", 2, 3, licence_id=ids["Houdini FX"])
    view.refresh()
    texts = {view.table.item(r, 0).text(): r for r in range(view.table.rowCount())}
    assert view.table.item(texts["Maya"], 3).text() == "—"
    used = view.table.item(texts["Houdini FX"], 4)
    assert used.foreground().color().name().lower() == Gate.TEXT_DIM.lower()
    assert view.toolbar.count_label.text() == "2 licences"


def test_a_failed_read_says_so(db, app, monkeypatch):
    """IT2-067."""
    view = _view(db)
    monkeypatch.setattr(view.repo, "compliance", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad query")))
    shown = []
    from slate.gui.tabs import licence_view as module
    monkeypatch.setattr(module, "show_load_error", lambda w, exc, **k: shown.append(str(exc)))
    view.refresh()
    assert shown == ["bad query"]


def test_correcting_a_reading_can_fix_its_time(db, app, monkeypatch):
    """IT2-054 / IT2-068."""
    from datetime import datetime, timedelta
    from slate.gui.tabs.licence_view import ReadingsDialog
    view = _view(db)
    view.repo.save("Nuke", 10, _in(300))
    lic = view.repo.licences()[0]
    view.repo.record("Nuke", 3, 10, licence_id=lic["id"])
    dialog = _keep(ReadingsDialog(view.repo.compliance(90)[0], view.repo))
    assert dialog.table.item(0, 2).text() == "Typed in"
    dialog.table.selectRow(0)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    earlier = datetime.now().replace(microsecond=0, second=0) - timedelta(days=2)

    from PySide6.QtWidgets import QDateTimeEdit
    real = QDateTimeEdit.dateTime
    monkeypatch.setattr(QDateTimeEdit, "dateTime",
                        lambda self: __import__("PySide6.QtCore", fromlist=["QDateTime"]).QDateTime(earlier))
    dialog.correct()
    monkeypatch.setattr(QDateTimeEdit, "dateTime", real)
    assert str(view.repo.history(lic)[0]["taken_at"])[:16] == earlier.strftime("%Y-%m-%d %H:%M")
