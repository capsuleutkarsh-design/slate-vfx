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


# ------------------------------------------------------------ round 2 (HR2-0xx)

def _view(repo, who="hr.meera", stage="HR"):
    from slate.gui.tabs.leave_approvals_view import LeaveApprovalsView
    return LeaveApprovalsView(who, stage=stage, db_manager=repo.db)


def test_hr_sees_requests_stranded_with_a_supervisor_who_left(repo):
    """HR2-037: they sat in 'Elsewhere in the chain', outside HR's default queue."""
    from slate.gui.tabs.leave_approvals_view import STRANDED
    day = _monday()
    sent = repo.submit("aarav", "Casual", day, day, False, "a")
    repo.db.execute_update("UPDATE ut_users SET last_day = %s WHERE username = %s",
                           (date(2026, 1, 31), "sam"))
    view = _view(repo)
    assert view.filter_state.currentData() == "mine"
    assert [row["id"] for row in view._rows] == [sent.request_id]
    assert _texts(view.table, view.COLUMNS.index("Waiting on")) == [STRANDED]
    assert view.card_mine.value_text() == "1"


def test_a_supervisor_is_you_in_their_own_queue(repo):
    """HR2-052: every row said 'Sam Rao' to Sam."""
    day = _monday()
    repo.submit("aarav", "Casual", day, day, False, "a")
    view = _view(repo, "sam", "Supervisor")
    assert _texts(view.table, view.COLUMNS.index("Waiting on")) == ["You"]


def test_a_long_name_does_not_push_the_queue_off_screen(repo):
    """HR2-038: Person was sized to its contents; one long name took ~390 px."""
    repo.db.execute_update("UPDATE ut_users SET display_name = %s WHERE username = %s",
                           ("Venkataraghavan Subramanian Iyer-Ramachandran Krishnamurthy Jr",
                            "aarav"))
    from slate.core.domain import people
    people.refresh()
    day = _monday()
    repo.submit("aarav", "Casual", day, day, False, "a")
    view = _view(repo, "sam", "Supervisor")
    assert view.table.columnWidth(0) <= 200
    assert "Venkataraghavan" in view.table.item(0, 0).toolTip()


def test_balance_after_is_per_request(repo):
    """HR2-042: a 3-day and a 10-day request both showed the balance after both."""
    first = _monday()
    short = repo.submit("aarav", "Casual", first, first + timedelta(days=2), False, "a")
    later = first + timedelta(days=7)
    long_ = repo.submit("aarav", "Casual", later, later + timedelta(days=11), False, "b")
    view = _view(repo, "sam", "Supervisor")
    rows = {row["id"]: row for row in view._rows}
    before = view.balance_before(rows[short.request_id])
    for sent in (short, long_):
        row = rows[sent.request_id]
        assert view.balance_after(row) == before - float(row["days_charged"])
    assert view.balance_after(rows[short.request_id]) != view.balance_after(rows[long_.request_id])


def test_approving_into_the_red_says_so(repo):
    """HR2-043: Approve asked nothing about the person going overdrawn."""
    first = _monday()
    sent = repo.submit("aarav", "Casual", first, first + timedelta(days=40), False, "long")
    view = _view(repo, "sam", "Supervisor")
    row = [r for r in view._rows if r["id"] == sent.request_id][0]
    assert view.balance_after(row) < 0
    lines = view.overdrawn_lines([row])
    assert lines and lines[0].startswith("Aarav Sharma will be overdrawn by")
    assert view.overdrawn_lines([]) == []


def test_reasons_are_asked_for_in_a_slate_dialog_that_checks_in_place(repo):
    """HR2-058: a bare one-line box, and an empty reason opened a second message."""
    from slate.gui.tabs.leave_approvals_view import ReasonDialog
    dialog = ReasonDialog("Reject leave", "Reject 1 request?", "Reject")
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert not dialog.note.isHidden()
    dialog.text.setPlainText("  Diwali week is booked  ")
    dialog._accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.reason() == "Diwali week is booked"
    assert dialog.text.property("prose") is True


def test_revoking_leave_already_taken_says_it_becomes_absence(repo, monkeypatch):
    """HR2-048: revoking past leave turned the days into absences without a word."""
    from slate.gui.tabs import leave_approvals_view as module
    from slate.gui.components.table_tools import select_keys
    day = date.today() - timedelta(days=1)
    while not lp.is_working_day(day):
        day -= timedelta(days=1)
    sent = repo.submit("aarav", "Casual", day, day, False, "a")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    seen = []

    class Dialog:
        def __init__(self, title, intro, action, parent=None):
            seen.append(intro)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(module, "ReasonDialog", Dialog)
    view = _view(repo)
    view.filter_state.setCurrentIndex(view.filter_state.findData("all"))
    select_keys(view.table, [sent.request_id])
    view.revoke_requests()
    assert seen and "absent in attendance" in seen[0]


def test_the_request_dialog_starts_at_the_joining_date(repo):
    """HR2-036 / HR2-040: the earliest date offered was 14 Sep 1752."""
    from slate.gui.tabs.my_leave_view import RequestLeaveDialog
    dialog = RequestLeaveDialog(repo, 10, 0, "aarav")
    assert dialog.start.minimumDate().toPython() == date(2026, 1, 1)


def test_the_cost_line_for_unpaid_and_half_days(repo):
    """HR2-041 / HR2-050 / HR2-051: Unpaid 'cost' days; a half day pulled in the weekend."""
    from slate.gui.tabs.my_leave_view import RequestLeaveDialog
    friday = _monday() + timedelta(days=4)
    saturday = friday + timedelta(days=1)
    repo.add_holiday(friday, "Festival", "All")
    dialog = RequestLeaveDialog(repo, 10, 0, "aarav")
    dialog.start.setDate(QDate(saturday.year, saturday.month, saturday.day))
    dialog.end.setDate(QDate(saturday.year, saturday.month, saturday.day))
    assert "two days off counts those days too" in dialog.cost.text()
    dialog.half_day.setCurrentIndex(1)
    assert "0.5 day" in dialog.cost.text()
    assert "does not count the days off either side" in dialog.cost.text()
    dialog.half_day.setCurrentIndex(0)
    dialog.kind.setCurrentText("Unpaid")
    assert "unpaid - not taken from your balance" in dialog.cost.text()
    assert "cost" not in dialog.cost.text()
    assert dialog.reason.property("prose") is True


def test_withdraw_looks_like_a_button(repo):
    """HR2-057: a disabled ghost button read as a label."""
    from slate.gui.tabs.my_leave_view import MyLeaveView
    view = MyLeaveView("aarav", repo.db)
    assert view.btn_cancel.property("kind") == "secondary"


def test_year_end_shows_what_was_recorded_and_offers_only_useful_years(repo):
    """HR2-045 / HR2-053 / HR2-054."""
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain import people
    from slate.gui.tabs.leave_admin import YearEndDialog
    UserManager(db=repo.db).add_user("kiran", "pw", ["Artist"], "Kiran Rao", "Comp",
                                     joined_on="2024-01-01")
    people.refresh()
    sent = repo.submit("kiran", "Casual", date(2025, 6, 2), date(2025, 6, 3), False, "x")
    assert repo.close_year(2025, "hr.meera", today=date(2026, 3, 1))["closed"] >= 1
    recorded = [r for r in repo.closes(2025) if r["user_id"] == "kiran"][0]
    assert repo.cancel(sent.request_id, "kiran")             # the live figure moves by 2

    dialog = YearEndDialog("hr.meera", repo)
    labels = [dialog.year.itemText(i) for i in range(dialog.year.count())]
    assert labels[-1] == "2025 (closed)", "no earlier years that can only be refused"
    names = _texts(dialog.table, 0)
    row = names.index("Kiran Rao")
    assert dialog.table.item(row, 2).text() == "%g" % float(recorded["closing_balance"])
    assert dialog.table.item(row, 1).text().startswith("Closed - now")
    assert "no joining date" in dialog.state.text() and "1 people" not in dialog.state.text()
    assert dialog.btn_cancel.text() == "Close"


def test_comp_off_review_rows_can_be_unticked(repo):
    """HR2-039 / HR2-055: an open Sunday earned a day; credit all or nothing; 'day(s)'."""
    import json
    from PySide6.QtCore import Qt
    from slate.gui.tabs.leave_admin import CompOffReviewDialog
    lp.set_overrides({"comp_off_enabled": True})
    sunday = date.today() - timedelta(days=date.today().weekday() + 8)
    for who, t_out in (("aarav", "17:00:00"), ("vihaan", None)):
        repo.db.execute_update(
            "INSERT INTO attendance_log (user_id, day_date, punch_in, punch_out, pc_name, "
            "metadata) VALUES (%s, %s, %s, %s, %s, %s)",
            (who, sunday.isoformat(), "10:00:00", t_out, "TEST", json.dumps({})))
    dialog = CompOffReviewDialog(repo)
    assert dialog.table.rowCount() == 1, "the open Sunday earns nothing"
    assert "1 day worked qualifies" in dialog.note.text()
    assert "(s)" not in dialog.note.text()
    assert dialog.btn_credit.isEnabled()
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.ticked() == []
    assert not dialog.btn_credit.isEnabled()


def test_granting_project_rest_shows_the_working_days(repo):
    """HR2-047: no day count, and a studio-specific placeholder."""
    from slate.gui.tabs.leave_admin import GrantProjectRestDialog
    dialog = GrantProjectRestDialog("hr.meera", repo)
    monday = _monday()
    wednesday = monday + timedelta(days=2)
    dialog.start.setDate(QDate(monday.year, monday.month, monday.day))
    dialog.end.setDate(QDate(wednesday.year, wednesday.month, wednesday.day))
    assert dialog.cost.text().startswith("3 working days")
    assert "KLC" not in dialog.reason.placeholderText()


def test_a_holiday_place_is_snapped_or_questioned(repo, monkeypatch):
    """HR2-046: 'mumbai' beside 'Mumbai'; a holiday for a place nobody works in."""
    from PySide6.QtWidgets import QMessageBox
    from slate.gui.tabs.leave_admin import HolidayCalendarDialog
    repo.db.execute_update("UPDATE ut_users SET location = %s WHERE username = %s",
                           ("Mumbai", "aarav"))
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog = HolidayCalendarDialog(repo)
    day = _monday()
    dialog.day.setDate(QDate(day.year, day.month, day.day))
    dialog.name.setText("Local fair")
    dialog.location.setCurrentText("Pune")
    dialog.add()
    assert asked and "Pune" in asked[0]
    assert repo.holiday_rows(day.year) == []
    dialog.location.setCurrentText("mumbai")
    dialog.add()
    assert [r["location"] for r in repo.holiday_rows(day.year)] == ["Mumbai"]
