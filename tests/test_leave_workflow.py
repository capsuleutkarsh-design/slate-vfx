"""
The leave chain from end to end, on both databases.

Each test is a finding from the September 2026 audit (HR-0xx), written as the
thing that went wrong:

    a supervisor approving a request that had been withdrawn meanwhile
    requests from people with no manager waiting for ever
    leave booked for next month not held against the balance
    180 days available for somebody who joined in 2019
    HR approving their own leave
    HR's note wiping the supervisor's
    asking for more days costing fewer (the sandwich rule dodged)
    year end closing admin and tester, inventing balances, in any order
    approved leave that could never be withdrawn
    a Sunday-only request in the queue as '0 days'
    pending comp-off requests not held against the comp-off balance
"""

from datetime import date, timedelta

import pytest

from slate.core.domain import leave_policy as lp
from slate.core.infra.leave_repository import LeaveRepository


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request):
    db = request.getfixturevalue("mock_db" if request.param == "sqlite" else "pg_db")
    from slate.core.domain import access, people
    access.reset_cache()
    people.refresh()
    lp.set_overrides({})
    yield LeaveRepository(db)
    lp.set_overrides({})
    access.reset_cache()
    people.refresh()


def _person(repo, username, roles=("Artist",), **fields):
    from slate.core.domain.user_manager import UserManager
    UserManager(db=repo.db).add_user(username, "pw", list(roles), username.title(),
                                     "Comp", **fields)


def _team(repo):
    _person(repo, "sam", roles=("Supervisor",))
    _person(repo, "hr.meera", roles=("HR",))
    _person(repo, "hr.kavya", roles=("HR",))
    _person(repo, "jo", reports_to="sam", joined_on="2026-01-01")
    _person(repo, "alex", reports_to="sam", joined_on="2026-01-01")


def _status(repo, request_id):
    return lp.normalise_status(repo.request(request_id).get("status"))


def _future_monday(weeks=3):
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) + 7 * (weeks - 1))


# --------------------------------------------------------------- HR-058

def test_a_withdrawn_request_cannot_be_approved_back_to_life(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    assert sent and sent.detail["status"] == lp.STATUS_PENDING_SUPERVISOR
    assert repo.cancel(sent.request_id, "jo")

    outcome = repo.decide(sent.request_id, "Supervisor", True, "sam")
    assert not outcome and outcome.code == "stale"
    assert _status(repo, sent.request_id) == lp.STATUS_CANCELLED


# --------------------------------------------------------------- HR-059

def test_nobody_to_approve_sends_the_request_straight_to_hr(repo):
    _person(repo, "sai")                                   # no manager
    _person(repo, "artie")
    _person(repo, "kavya", reports_to="artie")             # an artist cannot approve
    _person(repo, "hr.meera", roles=("HR",))
    _person(repo, "ishaan", reports_to="hr.meera")         # HR decides at the HR stage
    day = _future_monday()
    for who in ("sai", "kavya", "ishaan"):
        sent = repo.submit(who, "Casual", day, day, False, "x")
        assert sent.detail["status"] == lp.STATUS_PENDING_HR, who
        assert repo.request(sent.request_id)["route_note"]


def test_hr_can_decide_a_stuck_request_for_the_supervisor(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    outcome = repo.decide(sent.request_id, "HR", True, "hr.meera", "fine")
    assert outcome
    row = repo.request(sent.request_id)
    assert lp.normalise_status(row["status"]) == lp.STATUS_APPROVED
    assert row["supervisor_by"] == "hr.meera" and "on behalf" in row["supervisor_note"]


# --------------------------------------------------------------- HR-062

def test_nobody_decides_their_own_request(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("hr.meera", "Casual", day, day, False, "mine")
    outcome = repo.decide(sent.request_id, "HR", True, "hr.meera")
    assert not outcome and outcome.code == "own"
    assert repo.decide(sent.request_id, "HR", True, "hr.kavya")


# --------------------------------------------------------------- HR-063

def test_hr_keeps_the_supervisors_note(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    assert repo.decide(sent.request_id, "Supervisor", True, "sam", "OK from my side")
    assert repo.decide(sent.request_id, "HR", True, "hr.meera", "")
    row = repo.request(sent.request_id)
    assert row["supervisor_note"] == "OK from my side"
    assert (row["hr_note"] or "") == ""


# --------------------------------------------------------------- HR-060

def test_booked_leave_is_held_against_the_live_balance(repo):
    _team(repo)
    before = repo.balance("jo")
    start = _future_monday(5)
    sent = repo.submit("jo", "Casual", start, start + timedelta(days=2), False, "trip")
    assert sent
    after = repo.balance("jo")
    assert after["pending_from_pool"] == before["pending_from_pool"] + 3
    assert after["available"] == pytest.approx(before["available"] - 3)

    assert repo.decide(sent.request_id, "Supervisor", True, "sam")
    assert repo.decide(sent.request_id, "HR", True, "hr.meera")
    booked = repo.balance("jo")
    assert booked["booked_from_pool"] == 3
    assert booked["available"] == pytest.approx(before["available"] - 3)


# --------------------------------------------------------------- HR-061

def test_unclosed_years_still_apply_the_carry_forward_cap(repo):
    _person(repo, "aarav", joined_on="2019-04-01")
    as_of = date(2026, 8, 31)
    balance = repo.balance("aarav", as_of=as_of)
    cap = lp.policy()["carry_forward_cap"]
    assert balance["accrued"] == cap + 16            # 12 carried + Jan..Aug 2026
    assert 2019 in balance["years_walked"] and 2025 in balance["years_walked"]


# --------------------------------------------------------------- HR-065

def test_asking_for_more_days_cannot_lower_the_sandwich_charge():
    friday, saturday, sunday = date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4)
    holidays = {friday}
    alone = lp.days_charged(saturday, saturday, holidays)
    with_sunday = lp.days_charged(saturday, sunday, holidays)
    assert alone["total"] == 3
    assert with_sunday["total"] == 3


def test_a_friday_off_after_a_worked_thursday_does_not_cost_the_weekend():
    friday = date(2026, 9, 18)
    assert lp.days_charged(friday, friday, set())["total"] == 1


# --------------------------------------------------------------- HR-074

def test_an_overdrawn_balance_is_shown_negative(repo):
    _team(repo)
    start = date(2026, 2, 2)
    sent = repo.submit("jo", "Casual", start, start + timedelta(days=9), False, "long")
    assert sent
    balance = repo.balance("jo", as_of=date(2026, 2, 28))
    assert balance["available"] < 0


# --------------------------------------------------------------- HR-082

def test_a_request_with_no_working_days_is_refused(repo):
    _team(repo)
    sunday = date(2026, 9, 13)
    outcome = repo.submit("jo", "Casual", sunday, sunday, False, "sunday")
    assert not outcome and outcome.code == "no_working_days"
    assert repo.for_user("jo") == []


# --------------------------------------------------------------- HR-077

def test_the_half_of_a_half_day_is_kept(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, True, "dentist", half_day_part="second")
    row = repo.request(sent.request_id)
    assert row["half_day_part"] == "second"
    assert float(row["days_charged"]) == 0.5


# --------------------------------------------------------------- HR-080

def test_approved_future_leave_can_be_withdrawn_with_hr_agreeing(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    repo.decide(sent.request_id, "Supervisor", True, "sam")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    held = repo.balance("jo")["spent_from_pool"]

    assert repo.request_cancellation(sent.request_id, "jo", "plans changed")
    assert _status(repo, sent.request_id) == lp.STATUS_CANCEL_REQUESTED
    assert repo.balance("jo")["spent_from_pool"] == held, "still deducted until HR agree"

    assert repo.decide(sent.request_id, "HR", True, "hr.meera")
    assert _status(repo, sent.request_id) == lp.STATUS_CANCELLED
    assert repo.balance("jo")["spent_from_pool"] == held - 1


def test_hr_can_refuse_a_withdrawal(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    repo.request_cancellation(sent.request_id, "jo", "")
    assert repo.decide(sent.request_id, "HR", False, "hr.meera", "we need you")
    assert _status(repo, sent.request_id) == lp.STATUS_APPROVED


def test_hr_revoking_comp_off_gives_the_ledger_day_back(repo):
    lp.set_overrides({"comp_off_enabled": True})
    _team(repo)
    day = _future_monday()
    assert repo.credit_comp_off("jo", date.today() - timedelta(days=5), 1.0, "Sunday")
    sent = repo.submit("jo", "Comp Off", day, day, False, "back")
    repo.decide(sent.request_id, "Supervisor", True, "sam")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    assert repo.comp_off_balance("jo") == 0

    assert not repo.revoke(sent.request_id, "hr.meera", "")      # a reason is required
    assert repo.revoke(sent.request_id, "hr.meera", "approved by mistake")
    assert repo.comp_off_balance("jo") == 1.0


def test_leave_that_has_started_cannot_be_withdrawn_by_the_person(repo):
    _team(repo)
    day = date.today() - timedelta(days=1)
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    if not sent:                       # yesterday was a Sunday
        day -= timedelta(days=1)
        sent = repo.submit("jo", "Casual", day, day, False, "a")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    outcome = repo.request_cancellation(sent.request_id, "jo")
    assert not outcome and outcome.code == "started"


# --------------------------------------------------------------- HR-087 / comp off

def test_pending_comp_off_is_held_against_the_comp_off_balance(repo):
    lp.set_overrides({"comp_off_enabled": True})
    _team(repo)
    repo.credit_comp_off("jo", date.today() - timedelta(days=3), 1.0, "Sunday")
    day = _future_monday()
    assert repo.submit("jo", "Comp Off", day, day, False, "one")
    balance = repo.balance("jo")
    assert balance["comp_off"] == 1.0
    assert balance["comp_off_available"] == 0.0


def test_comp_off_is_judged_on_the_day_of_the_leave_not_the_day_of_approval(repo):
    """
    The root cause behind test_comp_off_is_spent_oldest_first failing once the
    calendar moved past 29 September: the July day had expired *by the day of
    approval* and was skipped, although it was valid on the day of the leave.
    """
    lp.set_overrides({"comp_off_enabled": True})
    _team(repo)
    long_ago = date.today() - timedelta(days=200)
    repo.credit_comp_off("jo", long_ago, 1.0, "old")
    repo.credit_comp_off("jo", long_ago + timedelta(days=30), 1.0, "newer")
    leave_day = long_ago + timedelta(days=20)
    while not lp.is_working_day(leave_day):
        leave_day += timedelta(days=1)
    sent = repo.submit("jo", "Comp Off", leave_day, leave_day, False, "back then")
    repo.decide(sent.request_id, "HR", True, "hr.meera")
    rows = repo.db.execute_query(
        "SELECT earned_on, consumed FROM comp_off_ledger ORDER BY earned_on", fetch="all")
    consumed = [float(dict(r)["consumed"]) for r in rows]
    assert consumed == [1.0, 0.0]


# --------------------------------------------------------------- HR-066 / HR-071

def test_year_end_leaves_out_service_accounts_and_flags_missing_joining_dates(repo):
    _person(repo, "aditya")                                  # no joining date
    _person(repo, "diya", joined_on="2020-01-01", last_day="2024-06-30")
    _person(repo, "jo", joined_on="2025-01-01")
    names = {e["user_id"]: e for e in repo.preview_close(2025)}
    assert "admin" not in names and "tester" not in names
    assert "diya" not in names
    # The seeded sample artist has no joining date, so it is flagged, not closed.
    assert names.get("artist", {}).get("status", "no_joining_date") == "no_joining_date"
    assert names["aditya"]["status"] == "no_joining_date"
    assert names["jo"]["status"] == "ok"

    result = repo.close_year(2025, "hr.meera", today=date(2026, 3, 1))
    assert result["closed"] == 1 and result["no_joining_date"] >= 1
    assert {r["user_id"] for r in repo.closes(2025)} == {"jo"}


def test_years_close_in_order_and_only_once_finished(repo):
    _person(repo, "jo", joined_on="2024-01-01")
    today = date(2026, 3, 1)
    assert repo.close_refusal(2026, today)
    assert repo.close_year(2026, "hr", today=today)["refused"]
    assert repo.close_year(2024, "hr", today=today)["closed"] == 1
    assert "Close 2025 first" not in repo.close_refusal(2025, today)
    assert repo.close_refusal(2023, today)


# --------------------------------------------------------------- HR-084

def test_pending_requests_are_recosted_after_a_holiday_is_removed(repo):
    _team(repo)
    repo.add_holiday(date(2026, 10, 2), "Gandhi Jayanti", "All")
    sent = repo.submit("jo", "Casual", date(2026, 10, 1), date(2026, 10, 2), False, "x")
    assert float(repo.request(sent.request_id)["days_charged"]) == 1
    holiday = repo.holiday_rows(2026)[0]
    assert repo.remove_holiday(holiday["id"])
    assert repo.recharge_pending(date(2026, 10, 2), date(2026, 10, 2)) == 1
    assert float(repo.request(sent.request_id)["days_charged"]) == 2


# --------------------------------------------------------------- HR-069

def test_also_away_lists_the_teams_overlapping_leave(repo):
    _team(repo)
    day = _future_monday()
    mine = repo.submit("jo", "Casual", day, day + timedelta(days=1), False, "a")
    theirs = repo.submit("alex", "Sick", day + timedelta(days=1), day + timedelta(days=1), False, "b")
    assert mine and theirs
    away = repo.also_away(repo.request(mine.request_id))
    assert [r["user_id"] for r in away] == ["alex"]


# --------------------------------------------------------------- HR-076

def test_project_rest_is_granted_by_hr_not_requested(repo):
    _team(repo)
    assert "Project Rest" not in lp.requestable_types()
    assert "Comp Off" not in lp.requestable_types()
    lp.set_overrides({"comp_off_enabled": True})
    assert "Comp Off" in lp.requestable_types()

    day = _future_monday()
    granted = repo.grant_project_rest("jo", day, day, "hr.meera", "after the KLC delivery")
    assert granted
    assert _status(repo, granted.request_id) == lp.STATUS_APPROVED


# --------------------------------------------------------------- HR-079

def test_waiting_on_names_the_person(repo):
    _team(repo)
    day = _future_monday()
    sent = repo.submit("jo", "Casual", day, day, False, "a")
    assert repo.waiting_on(repo.request(sent.request_id)) == "Sam"
