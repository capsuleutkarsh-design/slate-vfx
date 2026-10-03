"""
Round-2 audit fixes for the Scheduling and Bidding screens (PRD2-*),
offscreen Qt on a scratch SQLite database.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from slate.core.domain import bidding as DB
from slate.core.domain import scheduling as DS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def default_figures():
    DB.set_overrides({})
    yield
    DB.set_overrides({})


ADMIN = {"username": "admin", "roles": ["Admin"]}
VIEWER = {"username": "vic", "roles": ["Viewer"]}


# ------------------------------------------------------------------ scheduling

def _dialog(milestones, editing=None, calendar=None):
    from slate.gui.tabs.milestone_dialog import MilestoneDialog
    return MilestoneDialog(None, milestones=milestones, projects=[("A", "A")], milestone=editing,
                           default_project="A", calendar=calendar)


def test_PRD2_001_opening_edit_never_moves_the_start(qtbot, app):
    parent = DS.Milestone(id=1, project_code="A", name="Roto", start=date(2026, 10, 1), end=date(2026, 10, 17))
    child = DS.Milestone(id=2, project_code="A", name="Review", start=date(2026, 10, 12),
                         end=date(2026, 10, 20), depends_on_id=1)
    dialog = _dialog([parent, child], editing=child)
    qtbot.addWidget(dialog)
    assert dialog.milestone().start == date(2026, 10, 12)
    assert "starts before" in dialog.start_hint.text().lower()
    assert not dialog.save_button.isEnabled()


def test_PRD2_002_picking_a_dependency_starts_on_the_next_working_day(qtbot, app):
    friday = date(2026, 10, 16)
    parent = DS.Milestone(id=1, project_code="A", name="Roto", start=date(2026, 10, 1), end=friday)
    dialog = _dialog([parent], calendar=DS.WorkCalendar((5, 6)))
    qtbot.addWidget(dialog)
    dialog.dep_cb.setCurrentIndex(dialog.dep_cb.findData(1))
    assert dialog.milestone().start == date(2026, 10, 19)


def test_PRD2_009_030_031_completed_warns_and_empty_name_says_required(qtbot, app):
    from PySide6.QtWidgets import QAbstractSpinBox
    parent = DS.Milestone(id=1, project_code="A", name="Roto", start=date(2026, 10, 1), end=date(2026, 10, 5))
    child = DS.Milestone(id=2, project_code="A", name="Comp", start=date(2026, 10, 6),
                         end=date(2026, 10, 9), depends_on_id=1)
    dialog = _dialog([parent, child], editing=child)
    qtbot.addWidget(dialog)
    dialog.status_cb.setCurrentIndex(dialog.status_cb.findData(DS.COMPLETED))
    assert "not complete yet" in dialog.status_hint.text()
    dialog.ms_input.setText("")
    assert dialog.name_hint.text() == "Required."
    assert dialog.effort_input.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons


def test_PRD2_016_read_only_dialog(qtbot, app):
    m = DS.Milestone(id=1, project_code="A", name="Roto", start=date(2026, 10, 1), end=date(2026, 10, 5))
    from slate.gui.tabs.milestone_dialog import MilestoneDialog
    dialog = MilestoneDialog(None, milestones=[m], projects=[("A", "A")], milestone=m, read_only=True)
    qtbot.addWidget(dialog)
    assert not dialog.ms_input.isEnabled() and not dialog.save_button.isVisibleTo(dialog)
    assert dialog.windowTitle() == "Milestone"


def test_PRD2_004_010_zoom_box_follows_fit_and_legend_follows_rights(qtbot, app):
    from slate.gui.tabs.schedule_timeline import ScheduleTimeline
    view = ScheduleTimeline(editable=False)
    qtbot.addWidget(view)
    view.resize(1200, 600)
    m = DS.Milestone(id=1, project_code="A", name="Long", start=date(2026, 1, 1), end=date(2026, 12, 31))
    view.gantt.show_milestones([m], DS.WorkCalendar())
    view.gantt.fit()
    assert view.zoom_cb.currentData() == view.gantt.zoom == DS.ZOOM_MONTH
    assert "Drag" not in view.legend.toolTip()
    assert "Drag" in ScheduleTimeline(editable=True).legend.toolTip()


def test_PRD2_026_028_shift_dialog_wording_and_skipped_rows(qtbot, app):
    from slate.gui.tabs.shift_dates_dialog import ShiftDatesDialog
    a = DS.Milestone(id=1, project_code="A", name="A", start=date(2026, 10, 1), end=date(2026, 10, 5))
    done = DS.Milestone(id=2, project_code="A", name="Done", start=date(2026, 10, 6),
                        end=date(2026, 10, 8), depends_on_id=1, status=DS.COMPLETED)
    dialog = ShiftDatesDialog(None, milestones=[a, done], root_id=1, days=3)
    qtbot.addWidget(dialog)
    rows = {dialog.preview.item(r, 0).text(): r for r in range(dialog.preview.rowCount())}
    assert dialog.preview.item(rows["Done"], 1).text() != ""
    assert dialog.preview.item(rows["Done"], 2).text() == "stays"


# ------------------------------------------------------------------ bidding

def _line(label="SH010 comp", days=3, rate=8000):
    return DB.BidLine(label=label, department="comp", complexity="Medium", shot_count=1,
                      days_per_shot=Decimal(days), day_rate=Decimal(rate))


@pytest.fixture
def seeded(mock_db):
    from slate.core.infra.bid_repository import Bid, BidRepository
    mock_db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES ('AVTR3', 'Avatar', 1)")
    repo = BidRepository(mock_db, username="admin")
    draft = repo.create(Bid(project_code="AVTR3", currency="INR", margin=Decimal(20)), [_line()])
    return {"db": mock_db, "repo": repo, "draft": draft}


def _tab(qtbot, user=ADMIN):
    from slate.gui.tabs.prod_bidding_tab import ProdBiddingTab
    tab = ProdBiddingTab(user_data=user)
    qtbot.addWidget(tab)
    tab.resize(1400, 860)
    tab.show()
    return tab


def test_PRD2_045_060_068_title_cards_and_own_bid(qtbot, app, seeded):
    from slate.gui.components.table_tools import select_keys
    tab = _tab(qtbot, {"username": "admin", "roles": ["Production Head"]})
    assert tab.findChild(type(tab.grid)) is not None
    assert tab.lbl_approved.value_text() == "—"
    select_keys(tab.grid, [seeded["draft"]])
    assert not tab.won_button.isEnabled() and "somebody else" in tab.won_button.toolTip()


def test_PRD2_056_read_only_bidding(qtbot, app, seeded):
    tab = _tab(qtbot, VIEWER)
    assert not tab.new_button.isVisibleTo(tab) and tab.read_only_badge.isVisibleTo(tab)


def test_PRD2_042_043_052_editor_enter_moves_and_lines_keep_their_rates(qtbot, app, seeded):
    from PySide6.QtTest import QTest
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog, L_LABEL
    repo = seeded["repo"]
    dialog = BidEditorDialog(None, repo=repo, projects=repo.projects(), username="admin")
    qtbot.addWidget(dialog)
    dialog.show()
    editor = dialog.table.cellWidget(0, L_LABEL)
    editor.setText("SH100 comp")
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert dialog.result() != QDialog.DialogCode.Accepted and dialog.saved_id is None
    # Moving lines keeps every line's own values.
    dialog.add_line()
    dialog.lines[1].label = "second"
    dialog.table.selectRow(1)
    dialog.move_line(-1)
    assert [l.label for l in dialog.lines] == ["second", "SH100 comp"]
    assert dialog.table.cellWidget(0, L_LABEL).text() == "second"
    # A currency with no studio rate keeps the rates typed.
    rate = dialog.lines[0].day_rate
    dialog.currency_cb.setCurrentIndex(dialog.currency_cb.findData("EUR"))
    assert dialog.lines[0].day_rate == rate > 0


def test_PRD2_081_tracking_exports_numbers(qtbot, app, seeded, tmp_path):
    import csv
    from slate.gui.tabs.bid_tracking_view import BidTrackingView
    repo = seeded["repo"]
    bid = repo.get(seeded["draft"])
    view = BidTrackingView()
    qtbot.addWidget(view)
    view.show_tracking(bid, DB.track(repo.lines(bid.id), []))
    assert view.table.item(view.table.rowCount() - 1, 0).text() == "Total"
    path = view.export(path=str(tmp_path / "t.csv"))
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    assert rows[0][-1] == "Currency" and rows[1][-1] == "INR" and rows[1][1] in ("3", "3.00")
