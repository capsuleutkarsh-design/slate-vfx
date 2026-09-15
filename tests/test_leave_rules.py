"""
Leave, where the numbers have to be right.

Every test here is a bug that was live. The balance on the artist's screen, the
queue on HR's, and the ledger underneath both had ways of disagreeing about the
same person, and the ways they disagreed all cost somebody days.

    the same week asked for twice, both held against the balance
    a half day taken off a five-day request
    a request sent by mistake with no way to withdraw it
    a year end that counted next January against the year being closed
    comp off approved and never taken out of the ledger
    a holiday query the local database could not run, so no holidays at all
    every supervisor able to approve every request in the studio
"""

from datetime import date, timedelta

import pytest

from slate.core.domain import leave_policy as lp
from slate.core.infra.leave_repository import LeaveRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """
    A leave repository on a database of its own.

    Not the shared ``mock_db`` fixture: that builds a DatabaseManager which
    reads the studio's configuration, and on a configured machine that means
    postgres - so the fixture hands back a manager pointed at a real server.
    """
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    lp.set_overrides({})
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "leave.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"

    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield LeaveRepository(manager)
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None
        lp.set_overrides({})


def _person(repo, username, **fields):
    from slate.core.domain.user_manager import UserManager
    UserManager(db=repo.db).add_user(username, "pw", ["Artist"], username, "Comp",
                                     **fields)


# ------------------------------------------------------------------ overlaps

def test_the_same_days_cannot_be_asked_for_twice(repo):
    """
    The bug: nothing stopped it, and both requests were held against the
    balance - so a fortnight disappeared for one week away.
    """
    start, end = date(2026, 9, 14), date(2026, 9, 16)
    assert repo.submit("jo", "Casual", start, end, False, "wedding")

    assert repo.clash("jo", start, end), "the clash must be visible to the dialog"
    assert not repo.submit("jo", "Casual", start, end, False, "again"), \
        "the repository must refuse it even if a screen does not"

    assert len(repo.for_user("jo")) == 1


def test_a_partial_overlap_is_still_an_overlap(repo):
    assert repo.submit("jo", "Casual", date(2026, 9, 14), date(2026, 9, 18), False, "a")
    assert repo.clash("jo", date(2026, 9, 17), date(2026, 9, 21))


def test_a_cancelled_request_frees_its_days_again(repo):
    first = date(2026, 9, 14)
    assert repo.submit("jo", "Casual", first, first, False, "a")
    request_id = repo.for_user("jo")[0]["id"]

    assert repo.cancel(request_id, "jo")
    assert not repo.clash("jo", first, first)
    assert repo.submit("jo", "Casual", first, first, False, "properly this time")


# ----------------------------------------------------------------- half days

def test_a_half_day_on_a_range_is_not_a_discount_on_the_whole_request(repo):
    """
    The bug: half_day subtracted half a day from any range, so a five-day
    request marked half day cost 4.5 days.
    """
    start, end = date(2026, 9, 14), date(2026, 9, 18)   # Mon to Fri
    assert repo.submit("jo", "Casual", start, end, True, "half day by mistake")

    charged = float(repo.for_user("jo")[0]["days_charged"])
    assert charged == 5.0, "a five day request costs five days"


def test_a_half_day_on_one_day_still_costs_half(repo):
    day = date(2026, 9, 14)
    assert repo.submit("jo", "Casual", day, day, True, "dentist")
    assert float(repo.for_user("jo")[0]["days_charged"]) == 0.5


# ------------------------------------------------------------------- cancel

def test_only_the_person_who_asked_can_withdraw_it(repo):
    day = date(2026, 9, 14)
    repo.submit("jo", "Casual", day, day, False, "a")
    request_id = repo.for_user("jo")[0]["id"]

    assert not repo.cancel(request_id, "someone_else")
    assert lp.normalise_status(repo.for_user("jo")[0]["status"]) == \
        lp.STATUS_PENDING_SUPERVISOR


def test_an_approved_request_cannot_be_withdrawn(repo):
    day = date(2026, 9, 14)
    repo.submit("jo", "Casual", day, day, False, "a")
    request_id = repo.for_user("jo")[0]["id"]

    repo.decide(request_id, "Supervisor", True, "sup")
    repo.decide(request_id, "HR", True, "hr")

    assert not repo.cancel(request_id, "jo"), \
        "an approved request is somebody else's decision"


# ------------------------------------------------------------------- balance

def test_a_pending_request_is_held_against_the_balance(repo):
    _person(repo, "jo", joined_on="2026-01-01")
    repo.submit("jo", "Casual", date(2026, 3, 2), date(2026, 3, 3), False, "a")

    balance = repo.balance("jo", as_of=date(2026, 6, 30))
    assert balance["pending_from_pool"] == 2
    assert balance["available"] == balance["accrued"] - 2


def test_the_balance_ignores_leave_that_has_not_happened_yet(repo):
    """
    The bug: a year-end preview run on 31 December counted January's approved
    leave against the year being closed, so the closing balance was short and
    the shortfall lapsed.
    """
    _person(repo, "jo", joined_on="2026-01-01")
    repo.submit("jo", "Casual", date(2027, 1, 11), date(2027, 1, 12), False, "next year")
    request_id = repo.for_user("jo")[0]["id"]
    repo.decide(request_id, "Supervisor", True, "sup")
    repo.decide(request_id, "HR", True, "hr")

    at_year_end = repo.balance("jo", as_of=date(2026, 12, 31))
    assert at_year_end["spent_from_pool"] == 0, "January is not this year's"
    assert at_year_end["accrued"] == 24

    later = repo.balance("jo", as_of=date(2027, 1, 31))
    assert later["spent_from_pool"] == 2


# ------------------------------------------------------------------ comp off

def test_approving_comp_off_takes_it_out_of_the_ledger(repo):
    """
    The bug: comp-off leave was approved and the ledger was never touched, so
    the same comp-off day could be spent for ever.
    """
    lp.set_overrides({"comp_off_enabled": True})
    earned = date(2026, 8, 2)
    assert repo.credit_comp_off("jo", earned, 2.0, "Worked a weekly off")
    assert repo.comp_off_balance("jo") == 2.0

    day = date(2026, 9, 14)
    assert repo.submit("jo", "Comp Off", day, day, False, "taking it back")
    request_id = repo.for_user("jo")[0]["id"]

    repo.decide(request_id, "Supervisor", True, "sup")
    assert repo.comp_off_balance("jo") == 2.0, "not spent until HR agree"

    repo.decide(request_id, "HR", True, "hr")
    assert repo.comp_off_balance("jo") == 1.0, "one day taken out of the ledger"


def test_comp_off_is_spent_oldest_first(repo):
    """The day closest to expiring is the one to use. Days held back are lost."""
    lp.set_overrides({"comp_off_enabled": True})
    repo.credit_comp_off("jo", date(2026, 7, 1), 1.0, "older")
    repo.credit_comp_off("jo", date(2026, 8, 1), 1.0, "newer")

    day = date(2026, 9, 14)
    repo.submit("jo", "Comp Off", day, day, False, "one day")
    request_id = repo.for_user("jo")[0]["id"]
    repo.decide(request_id, "Supervisor", True, "sup")
    repo.decide(request_id, "HR", True, "hr")

    rows = repo.db.execute_query(
        "SELECT earned_on, consumed FROM comp_off_ledger "
        "WHERE LOWER(user_id) = 'jo' ORDER BY earned_on", fetch="all")
    spent = {str(dict(r)["earned_on"])[:10]: float(dict(r)["consumed"]) for r in rows}
    assert spent["2026-07-01"] == 1.0
    assert spent["2026-08-01"] == 0.0


def test_ordinary_leave_never_touches_the_comp_off_ledger(repo):
    lp.set_overrides({"comp_off_enabled": True})
    repo.credit_comp_off("jo", date(2026, 8, 2), 2.0, "Worked a weekly off")

    day = date(2026, 9, 14)
    repo.submit("jo", "Casual", day, day, False, "ordinary")
    request_id = repo.for_user("jo")[0]["id"]
    repo.decide(request_id, "Supervisor", True, "sup")
    repo.decide(request_id, "HR", True, "hr")

    assert repo.comp_off_balance("jo") == 2.0


# ------------------------------------------------------------------ holidays

def test_the_holiday_query_works_on_the_local_database(repo):
    """
    The bug: the year filter used EXTRACT(YEAR FROM ...), which only PostgreSQL
    understands. On SQLite it raised, the holidays came back empty, and the
    sandwich rule silently stopped applying - so leave was under-charged.
    """
    assert repo.add_holiday(date(2026, 11, 10), "Diwali", "All")
    assert repo.holidays(2026) == {date(2026, 11, 10)}
    assert repo.holidays(2025) == set()


def test_a_holiday_belongs_to_a_location(repo):
    """One office's festival is an ordinary working day in another."""
    repo.add_holiday(date(2026, 11, 10), "Local festival", "Chennai")
    repo.add_holiday(date(2026, 1, 26), "Republic Day", "All")

    chennai = repo.holidays(2026, "Chennai")
    mumbai = repo.holidays(2026, "Mumbai")

    assert date(2026, 11, 10) in chennai
    assert date(2026, 11, 10) not in mumbai
    assert date(2026, 1, 26) in chennai and date(2026, 1, 26) in mumbai


def test_a_request_over_new_year_sees_both_years_of_holidays(repo):
    """
    The bug: holidays were loaded for the start year only, so the far side of
    the boundary had no holidays and the sandwich rule missed them.
    """
    repo.add_holiday(date(2027, 1, 1), "New Year", "All")
    found = repo.holidays_for("jo", date(2026, 12, 28), date(2027, 1, 4))
    assert date(2027, 1, 1) in found


# ----------------------------------------------------------------- approvals

def test_a_supervisor_sees_only_their_own_team(repo):
    """
    The bug: every supervisor saw, and could approve, every request in the
    studio. A queue that is not scoped is not a queue.
    """
    _person(repo, "sam")
    _person(repo, "jo", reports_to="sam")
    _person(repo, "alex", reports_to="other_manager")

    assert repo.reports_to("sam") == {"jo"}
    assert repo.reports_to("nobody_at_all") == set()


def test_a_rejection_stops_the_chain(repo):
    day = date(2026, 9, 14)
    repo.submit("jo", "Casual", day, day, False, "a")
    request_id = repo.for_user("jo")[0]["id"]

    repo.decide(request_id, "Supervisor", False, "sup", "not this week")
    assert lp.normalise_status(repo.for_user("jo")[0]["status"]) == lp.STATUS_REJECTED
    assert repo.balance("jo")["pending_from_pool"] == 0
