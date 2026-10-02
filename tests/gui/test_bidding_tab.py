"""
The Bidding tab and bid editor on screen (offscreen Qt, scratch SQLite).

    PRD-076/080  a legacy row with NULLs opens; every cell comes from its record
    PRD-084/089  lines with their own shot counts; totals update as you type
    PRD-086      the bid's currency everywhere; rupee bids start with 18% GST
    PRD-093/136  Save off with the reason while the bid makes no sense
    PRD-094      export the list (CSV) and a bid (PDF)
    PRD-097      Won asks first with the names
    PRD-099      Won/Lost hidden without approve_bid
    PRD-100/137  'New bid' / 'Edit bid – KALKI2 v1'; 'Create bid' / 'Save changes'
    PRD-102      a complexity the studio removed is shown, not turned into Medium
    PRD-105      money right-aligned
    PRD-108      currency filter
    PRD-112      empty state
    PRD-113/114  figures are labels: cost, margin earned, price, tax, total
    PRD-115      the studio's margin is the starting margin
    PRD-118      the editor is wide enough
    PRD-124      a won bid shows its tracking below the table
    PRD-131      buttons follow the selection
    PRD-090      the Help describes what is built
"""

import os
import sys
from decimal import Decimal

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QAbstractSpinBox

from slate.core.domain import bidding as DB
from slate.core.infra.bid_repository import Bid, BidRepository


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def default_figures():
    DB.set_overrides({})
    yield
    DB.set_overrides({})


ADMIN = {"username": "admin", "roles": ["Admin"]}
COORD = {"username": "coord", "roles": ["Production Coordinator"]}


def line(label="SH010 comp", days=3, rate=8000, shots=1, dept="comp", shot=""):
    return DB.BidLine(label=label, department=dept, complexity="Medium", shot_count=shots,
                      days_per_shot=Decimal(str(days)), day_rate=Decimal(str(rate)), shot_name=shot)


@pytest.fixture
def seeded(mock_db):
    db = mock_db
    for code, name in (("AVTR3", "Avatar 3"), ("KALKI2", "Kalki Chapter 2")):
        db.execute_update("INSERT INTO tracking_projects (code, name, active) VALUES (%s, %s, 1)",
                          (code, name))
    repo = BidRepository(db, username="priya")
    draft = repo.create(Bid(project_code="AVTR3", currency="INR", margin=Decimal(20), tax=Decimal(18),
                            client_name="Studio X"), [line(), line("Roto", days=2, shots=10, dept="roto")])
    usd = repo.create(Bid(project_code="KALKI2", currency="USD", margin=Decimal(25)),
                      [line("Hard shots", days=7, rate=450, shots=17)])
    BidRepository(db, roles=["Admin"], username="admin").set_status([usd], DB.WON)
    # A row from before any of this: only the old base columns.
    db.execute_update("INSERT INTO prod_bidding (project_name, estimated_budget, status) "
                      "VALUES ('OLDJOB', 5000, 'Draft')")
    return {"db": db, "repo": repo, "draft": draft, "won": usd}


def make_tab(qtbot, user=ADMIN):
    from slate.gui.tabs.prod_bidding_tab import ProdBiddingTab
    tab = ProdBiddingTab(user_data=user)
    qtbot.addWidget(tab)
    tab.resize(1400, 860)
    tab.show()
    return tab


def test_PRD_076_PRD_080_the_tab_opens_with_a_legacy_row(qtbot, app, seeded):
    from slate.gui.tabs import prod_bidding_tab as T
    tab = make_tab(qtbot)
    assert tab.grid.rowCount() == 3
    projects = [tab.grid.item(r, T.C_PROJECT).text() for r in range(3)]
    assert "AVTR3" in projects and "KALKI2" in projects
    legacy = next(r for r in range(3) if tab.grid.item(r, T.C_PROJECT).text() == "OLDJOB")
    assert tab.grid.item(legacy, T.C_LINES).text() == "—"
    assert tab.grid.item(legacy, T.C_STATUS).text() == "Draft"
    won = next(r for r in range(3) if tab.grid.item(r, T.C_PROJECT).text() == "KALKI2")
    assert tab.grid.item(won, T.C_STATUS).text() == "Won"
    assert tab.grid.item(won, T.C_PRICE).text() == "$71,400.00"
    avtr = next(r for r in range(3) if tab.grid.item(r, T.C_PROJECT).text() == "AVTR3")
    assert tab.grid.item(avtr, T.C_TOTAL).text().startswith("₹")
    assert tab.grid.item(avtr, T.C_PRICE).textAlignment() & Qt.AlignmentFlag.AlignRight


def test_PRD_087_PRD_130_cards(qtbot, app, seeded):
    tab = make_tab(qtbot)
    assert tab.lbl_total.value_text() == "3"
    assert tab.lbl_approved.value_text() == "$71.4K"
    assert "₹" in tab.lbl_value.value_text() and "$" in tab.lbl_value.value_text()


def test_PRD_108_currency_filter(qtbot, app, seeded):
    tab = make_tab(qtbot)
    tab.currency_filter.setCurrentIndex(tab.currency_filter.findData("INR"))
    assert [b.project_code for b in tab.visible_bids()] == ["AVTR3"]
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("Won"))
    assert tab.visible_bids() == []
    assert tab.empty.is_filtered()


def test_PRD_112_empty_state(qtbot, app, mock_db):
    tab = make_tab(qtbot)
    assert tab.empty.isVisible() and "No bids yet" in tab.empty._heading.text()


def test_PRD_099_PRD_131_buttons(qtbot, app, seeded):
    from slate.gui.components.table_tools import select_keys
    coord = make_tab(qtbot, COORD)
    assert not coord.won_button.isVisible() and not coord.lost_button.isVisible()
    admin = make_tab(qtbot)
    assert not admin.edit_button.isEnabled() and not admin.archive_button.isEnabled()
    select_keys(admin.grid, [seeded["draft"]])
    assert admin.edit_button.isEnabled() and admin.won_button.isEnabled()
    assert admin.edit_button.text() == "Edit…"
    select_keys(admin.grid, [seeded["won"]])
    assert admin.edit_button.text() == "Open…"
    assert not admin.won_button.isEnabled() and admin.lost_button.isEnabled()
    select_keys(admin.grid, [seeded["won"], seeded["draft"]])
    assert not admin.edit_button.isEnabled() and admin.archive_button.isEnabled()


def test_PRD_124_a_won_bid_shows_its_tracking(qtbot, app, seeded):
    from slate.gui.components.table_tools import select_keys
    tab = make_tab(qtbot)
    select_keys(tab.grid, [seeded["won"]])
    assert tab.tracking.table.isVisible()
    assert tab.tracking.title.text() == "Tracking – KALKI2 v1"
    assert "Bid $53,550.00" in tab.tracking.summary.text()
    select_keys(tab.grid, [seeded["draft"]])
    assert "tracking starts once a bid is won" in tab.tracking.hint.text()


def test_PRD_097_won_asks_first(qtbot, app, seeded, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.components.table_tools import select_keys
    tab = make_tab(qtbot)
    asked = []
    monkeypatch.setattr(feedback, "confirm", lambda parent, title, text, **k: asked.append(text) or False)
    select_keys(tab.grid, [seeded["draft"]])
    tab.update_status(DB.WON)
    assert asked and "AVTR3 v1" in asked[0]
    assert seeded["repo"].get(seeded["draft"]).status == DB.DRAFT, "cancelled = unchanged"
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: None)
    tab.update_status(DB.WON)
    assert seeded["repo"].get(seeded["draft"]).status == DB.WON


def test_PRD_091_PRD_096_archive_several_with_undo(qtbot, app, seeded, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.components.table_tools import select_keys
    tab = make_tab(qtbot)
    toasts = []
    monkeypatch.setattr(feedback, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(feedback, "toast", lambda parent, msg, level="info", action=None, **k:
                        toasts.append((msg, action)))
    select_keys(tab.grid, [seeded["draft"], seeded["won"]])
    tab.archive_bids()
    assert tab.grid.rowCount() == 1
    message, (label, undo) = toasts[-1]
    assert message == "Archived 2 bids." and label == "Undo"
    undo()
    assert tab.grid.rowCount() == 3


def test_PRD_100_PRD_113_PRD_114_PRD_115_the_new_bid_editor(qtbot, app, seeded):
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
    DB.set_overrides({"margin_percent": 27})
    repo = seeded["repo"]
    dialog = BidEditorDialog(None, repo=repo, projects=repo.projects(), username="priya")
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "New bid" and dialog.save_button.text() == "Create bid"
    assert dialog.minimumWidth() >= 900
    assert dialog.margin_input.value() == 27.0
    assert dialog.currency() == "INR" and dialog.tax_input.value() == 18.0
    assert dialog.tax_label_input.text() == "GST"
    assert not [w for w in dialog.findChildren(QAbstractSpinBox) if w.isReadOnly()]
    # One empty line to start: Save off with the reason.
    assert not dialog.save_button.isEnabled()
    assert "description" in dialog.problems.text()
    dialog.table.cellWidget(0, 0).setText("SH010 comp")
    assert dialog.save_button.isEnabled()
    dialog.table.cellWidget(0, 4).setValue(40)               # shots
    dialog.table.cellWidget(0, 5).setValue(2.5)              # days per shot
    totals = dialog.totals()
    assert totals.days == Decimal("100.00") and totals.cost == Decimal("800000.00")
    assert dialog.total_labels["cost"][1].text() == "₹8,00,000.00"
    assert dialog.total_labels["total"][1].text() == money_text(totals.total)
    assert "₹" in dialog.total_labels["margin"][1].text()
    # Foreign clients start with no tax.
    dialog.currency_cb.setCurrentIndex(dialog.currency_cb.findData("USD"))
    assert dialog.tax_input.value() == 0.0 and dialog.tax_label_input.text() == "Tax"
    assert dialog.lines[0].day_rate == Decimal("300.00"), "lines at the default rate follow it"
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    saved = repo.get(dialog.saved_id)
    assert saved.currency == "USD" and saved.shot_count == 40


def money_text(value):
    from slate.core.domain.money import format_money
    return format_money(value, "INR")


def test_PRD_093_the_margin_limit_and_warning(qtbot, app, seeded):
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
    repo = seeded["repo"]
    dialog = BidEditorDialog(None, repo=repo, projects=repo.projects())
    qtbot.addWidget(dialog)
    assert dialog.margin_input.maximum() == 80.0
    dialog.margin_input.setValue(55)
    assert dialog.margin_hint.isVisibleTo(dialog)


def test_PRD_079_PRD_081_a_won_bid_opens_read_only_with_new_revision(qtbot, app, seeded):
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
    repo = seeded["repo"]
    bid = repo.get(seeded["won"])
    dialog = BidEditorDialog(None, repo=repo, bid=bid, lines=repo.lines(bid.id))
    qtbot.addWidget(dialog)
    assert dialog.read_only and dialog.save_button is None
    assert dialog.revise_button.text() == "Create new revision"
    assert not dialog.margin_input.isEnabled() and dialog.project_cb is None
    assert "was won" in dialog.banner.text()
    dialog._revise()
    assert repo.get(seeded["won"]).status == DB.SUPERSEDED
    assert repo.get(dialog.revision_id).revision == 2


def test_PRD_100_PRD_102_editing_a_draft_keeps_an_unknown_complexity(qtbot, app, seeded):
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog, L_COMPLEXITY
    repo = seeded["repo"]
    bid = repo.get(seeded["draft"])
    lines = repo.lines(bid.id)
    lines[0].complexity = "Very Hard"
    lines[0].days_per_shot = Decimal(12)
    dialog = BidEditorDialog(None, repo=repo, bid=bid, lines=lines)
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Edit bid – AVTR3 v1"
    assert dialog.save_button.text() == "Save changes"
    combo = dialog.table.cellWidget(0, L_COMPLEXITY)
    assert combo.currentText() == "Very Hard (not in studio settings)"
    assert dialog.lines[0].days_per_shot == Decimal(12)


def test_legacy_bid_opens_with_its_figures(qtbot, app, seeded):
    from slate.gui.tabs.bid_editor_dialog import BidEditorDialog
    db, repo = seeded["db"], seeded["repo"]
    db.execute_update("INSERT INTO prod_bidding (project_code, project_name, shot_count, complexity, "
                      "estimated_days, target_margin, estimated_cost, estimated_budget, status, currency) "
                      "VALUES ('AVTR3', 'AVTR3', 17, 'Hard', 119, 25, 53550, 71400, 'Draft', 'USD')")
    bid = next(b for b in repo.list() if b.estimated_budget == Decimal("71400.00") and b.project_code == "AVTR3")
    dialog = BidEditorDialog(None, repo=repo, bid=bid, lines=repo.lines(bid.id))
    qtbot.addWidget(dialog)
    assert dialog.totals().price == Decimal("71400.00")
    assert dialog.rate_input.value() == 450.0
    dialog._save()
    again = repo.get(bid.id)
    assert again.estimated_budget == Decimal("71400.00") and again.has_lines


def test_PRD_094_exports(qtbot, app, seeded, tmp_path, monkeypatch):
    from slate.gui.components import feedback
    from slate.gui.components.table_tools import select_keys
    monkeypatch.setattr(feedback, "toast", lambda *a, **k: None)
    tab = make_tab(qtbot)
    csv_path = tab.export_list(path=str(tmp_path / "bids.csv"))
    text = open(csv_path, encoding="utf-8-sig").read()
    assert text.splitlines()[0].startswith("Project,Project name,Client")
    assert "KALKI2" in text and "Won" in text
    select_keys(tab.grid, [seeded["draft"]])
    pdf = tab.export_pdf(path=str(tmp_path / "bid.pdf"))
    assert pdf and os.path.getsize(pdf) > 1000
    assert open(pdf, "rb").read(5) == b"%PDF-"


def test_PRD_116_bidding_settings_save_for_the_studio(qtbot, app, seeded):
    from slate.gui.tabs.bid_settings_dialog import BiddingSettingsDialog
    from slate.core.infra.studio_settings import StudioSettings
    dialog = BiddingSettingsDialog(None, username="admin")
    qtbot.addWidget(dialog)
    dialog._add_row("Very Hard", 12)
    dialog.margin_input.setValue(27)
    dialog.rate_inputs["INR"].setValue(9000)
    assert dialog.save()
    StudioSettings.invalidate()
    store = StudioSettings()
    assert store.get("bidding")["multipliers"]["Very Hard"] == 12
    assert store.get("day_rates")["INR"] == 9000
    assert "Very Hard" in DB.complexities() and DB.margin_percent() == 27
    bad = BiddingSettingsDialog(None, username="admin")
    qtbot.addWidget(bad)
    bad.margin_input.setValue(90)
    bad.max_margin_input.setValue(60)
    assert not bad.save() and "between 0% and 60%" in bad.error.text()


def test_compare_dialog(qtbot, app, seeded):
    from slate.gui.tabs.bid_compare_dialog import CompareDialog
    repo = seeded["repo"]
    v2 = repo.revise(seeded["draft"])
    bid = repo.get(v2)
    repo.update(bid, [line(days=4), line("CG", days=5, dept="cg")])
    dialog = CompareDialog(None, repo.revisions(bid.bid_group), repo.lines)
    qtbot.addWidget(dialog)
    kinds = sorted(c.kind for c in dialog.changes)
    assert kinds == ["added", "changed", "removed"]
    assert "v1" in dialog.summary.text() and "v2" in dialog.summary.text()


def test_PRD_090_the_help_describes_what_is_built():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    page = json.loads((root / "slate/core/help_content.json").read_text(encoding="utf-8"))["bidding"]["content"]
    for words in ("line", "revision", "GST", "Won", "Archive", "Tracking", "Create shots", "Approve bids"):
        assert words.casefold() in page.casefold(), words
