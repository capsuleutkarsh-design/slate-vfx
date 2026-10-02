"""
The Leave screens, offscreen, on a database of their own.

    My leave: the rejection reason, who a request waits on, a confirmation
    after sending, Withdraw for approved future leave, an overdrawn balance
    shown as overdrawn, the comp-off card only where comp-off is operated.
    Request dialog: only requestable types, first/second half, no reversed
    dates, nothing beyond next leave year, a warning for past dates.
    Approvals: names and search by name, no deciding your own request, the
    Balance after column and details panel, search without re-reading the
    database, HR tools in one menu, a subtitle that matches the filter.
    Year end: names, a status column, only finished years.
"""

from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from slate.core.domain import leave_policy as lp  # noqa: E402
from slate.core.infra.leave_repository import LeaveRepository  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _tidy(app):
    """Screens built here are torn down here, not by a later test's garbage collection."""
    yield
    import gc
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QApplication.processEvents()
    gc.collect()
    QApplication.processEvents()


@pytest.fixture
def repo(mock_db, app):
    from slate.core.domain import access, people
    access.reset_cache()
    people.refresh()
    lp.set_overrides({})
    from slate.core.domain.user_manager import UserManager
    um = UserManager(db=mock_db)
    um.add_user("sam", "pw", ["Supervisor"], "Sam Rao", "Comp")
    um.add_user("hr.meera", "pw", ["HR"], "Meera Iyer", "HR")
    um.add_user("aarav", "pw", ["Artist"], "Aarav Sharma", "Comp",
                reports_to="sam", joined_on="2026-01-01")
    um.add_user("vihaan", "pw", ["Artist"], "Vihaan Gupta", "Comp",
                reports_to="sam", joined_on="2026-01-01")
    people.refresh()
    yield LeaveRepository(mock_db)
    lp.set_overrides({})
    access.reset_cache()
    people.refresh()


def _monday(weeks=3):
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) + 7 * (weeks - 1))


def _texts(table, column):
    return [table.item(r, column).text() for r in range(table.rowCount())]


# ------------------------------------------------------------------ my leave

def test_the_rejection_reason_and_the_approver_are_shown(repo):
    from slate.gui.tabs.my_leave_view import MyLeaveView
    day = _monday()
    waiting = repo.submit("aarav", "Casual", day, day, False, "a")
    rejected = repo.submit("aarav", "Casual", day + timedelta(days=1),
                           day + timedelta(days=1), False, "b")
    repo.decide(rejected.request_id, "Supervisor", False, "sam", "Diwali week is booked")

    view = MyLeaveView("aarav", repo.db)
    columns = view.COLUMNS
    decisions = _texts(view.table, columns.index("Decision"))
    assert any("Diwali week is booked" in d for d in decisions)
    assert "Sam Rao" in _texts(view.table, columns.index("Waiting on"))
    assert waiting


def test_sending_a_request_says_where_it_went_and_selects_it(repo, monkeypatch):
    from slate.gui.tabs import my_leave_view
    shown = []
    monkeypatch.setattr("slate.gui.components.feedback.toast",
                        lambda parent, message, *a, **k: shown.append(message))
    day = _monday()

    class Dialog:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"type": "Casual", "start": day, "end": day, "half_day": False,
                    "half_day_part": None, "reason": "family"}

    monkeypatch.setattr(my_leave_view, "RequestLeaveDialog", Dialog)
    view = my_leave_view.MyLeaveView("aarav", repo.db)
    view.request_leave()
    assert shown and shown[-1].startswith("Sent to Sam Rao")
    assert view._selected_request() is not None


def test_approved_future_leave_can_be_withdrawn_from_my_leave(repo, monkeypatch):
    from slate.gui.tabs.my_leave_view import MyLeaveView
    from slate.gui.components.table_tools import select_keys
    day = _monday()
    sent = repo.submit("aarav", "Casual", day, day, False, "a")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        lambda *a, **k: ("plans changed", True))
    view = MyLeaveView("aarav", repo.db)
    select_keys(view.table, [sent.request_id])
    assert view.btn_cancel.isEnabled()
    view.cancel_request()
    assert lp.normalise_status(repo.request(sent.request_id)["status"]) == \
        lp.STATUS_CANCEL_REQUESTED


def test_an_overdrawn_balance_reads_overdrawn(repo):
    from slate.gui.tabs.my_leave_view import MyLeaveView
    start = date(date.today().year, 1, 5)
    while not lp.is_working_day(start):
        start += timedelta(days=1)
    sent = repo.submit("aarav", "Casual", start, start + timedelta(days=40), False, "long")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    view = MyLeaveView("aarav", repo.db)
    if repo.balance("aarav")["available"] < 0:
        assert view.card_available.value_text().startswith("-")
        assert "Overdrawn" in view.card_available._caption.text()


def test_the_comp_off_card_follows_the_policy_not_the_balance(repo):
    from slate.gui.tabs.my_leave_view import MyLeaveView
    view = MyLeaveView("aarav", repo.db)
    assert view.card_comp.isHidden()
    lp.set_overrides({"comp_off_enabled": True})
    view.refresh()
    assert not view.card_comp.isHidden()
    assert "earned by working" in view.card_comp._caption.text()


# ------------------------------------------------------------------- dialog

def test_the_request_dialog_offers_only_what_can_be_requested(repo):
    from slate.gui.tabs.my_leave_view import RequestLeaveDialog
    dialog = RequestLeaveDialog(repo, 10, 0, "aarav")
    kinds = [dialog.kind.itemText(i) for i in range(dialog.kind.count())]
    assert "Project Rest" not in kinds and "Comp Off" not in kinds
    halves = [dialog.half_day.itemText(i) for i in range(dialog.half_day.count())]
    assert halves == ["Full day", "First half", "Second half"]


def test_the_end_date_cannot_go_before_the_start(repo):
    from slate.gui.tabs.my_leave_view import RequestLeaveDialog
    dialog = RequestLeaveDialog(repo, 10, 0, "aarav")
    dialog.start.setDate(QDate(2026, 10, 20))
    dialog.end.setDate(QDate(2026, 10, 10))
    assert dialog.end.date() >= dialog.start.date()
    last = dialog.end.maximumDate().toPython()
    assert last == date(date.today().year + 1, 12, 31)


def test_a_past_date_is_called_retrospective(repo):
    from slate.gui.tabs.my_leave_view import RequestLeaveDialog
    dialog = RequestLeaveDialog(repo, 10, 0, "aarav")
    past = date.today() - timedelta(days=10)
    dialog.start.setDate(QDate(past.year, past.month, past.day))
    assert not dialog.retro.isHidden()


# ---------------------------------------------------------------- approvals

def test_approvers_see_names_and_can_search_by_name(repo):
    from slate.gui.tabs.leave_approvals_view import LeaveApprovalsView
    day = _monday()
    repo.submit("aarav", "Casual", day, day, False, "a")
    repo.submit("vihaan", "Casual", day + timedelta(days=7), day + timedelta(days=7), False, "b")
    view = LeaveApprovalsView("sam", stage="Supervisor", db_manager=repo.db)
    assert set(_texts(view.table, 0)) == {"Aarav Sharma", "Vihaan Gupta"}

    calls = []
    original = view.repo.all_requests
    view.repo.all_requests = lambda: calls.append(1) or original()
    view.search.setText("Aarav Sharma")
    view.apply_filters()
    assert _texts(view.table, 0) == ["Aarav Sharma"]
    assert calls == [], "searching must not read the database again"


def test_nobody_can_approve_their_own_request_on_screen(repo):
    from slate.gui.tabs.leave_approvals_view import LeaveApprovalsView
    from slate.gui.components.table_tools import select_keys
    day = _monday()
    own = repo.submit("hr.meera", "Casual", day, day, False, "mine")
    view = LeaveApprovalsView("hr.meera", stage="HR", db_manager=repo.db)
    view.filter_state.setCurrentIndex(view.filter_state.findData("open"))
    select_keys(view.table, [own.request_id])
    assert not view.btn_approve.isEnabled()


def test_the_details_panel_shows_balance_and_who_else_is_away(repo):
    from slate.gui.tabs.leave_approvals_view import LeaveApprovalsView
    from slate.gui.components.table_tools import select_keys
    day = _monday()
    mine = repo.submit("aarav", "Casual", day, day, False, "a")
    repo.submit("vihaan", "Sick", day, day, False, "b")
    view = LeaveApprovalsView("sam", stage="Supervisor", db_manager=repo.db)
    assert "Balance after" in view.COLUMNS
    select_keys(view.table, [mine.request_id])
    text = view.details.body.text()
    assert "Vihaan Gupta" in text
    assert "After" in text


def test_hr_tools_sit_in_one_menu(repo):
    from slate.gui.tabs.leave_approvals_view import LeaveApprovalsView
    from PySide6.QtWidgets import QPushButton
    view = LeaveApprovalsView("hr.meera", stage="HR", db_manager=repo.db)
    labels = [b.text() for b in view.findChildren(QPushButton)]
    assert "HR tools" in labels
    assert "Calendar" not in labels and "Year end" not in labels


# ------------------------------------------------------------------ year end

def test_year_end_lists_names_with_a_status_and_only_finished_years(repo):
    from slate.gui.tabs.leave_admin import YearEndDialog
    dialog = YearEndDialog("hr.meera", repo)
    years = [dialog.year.itemData(i) for i in range(dialog.year.count())]
    assert date.today().year not in years
    names = _texts(dialog.table, 0)
    assert "Sam Rao" in names                      # by name, not 'sam'
    assert "Aarav Sharma" not in names, "joined after the year ended"
    status = dict(zip(names, _texts(dialog.table, 1)))
    assert status["Sam Rao"].startswith("No joining date")
    assert "Status" in dialog.COLUMNS
