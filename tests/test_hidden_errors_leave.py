"""
Hidden errors in leave, comp-off, attendance and joining (hardening 2.2.1).

Each test is one place where a failed read or write used to look like
"nothing there" or "done":

    comp-off crediting read "already credited" as empty and paid every day again
    a day of attendance could be credited twice (no unique rule)
    lapsing comp-off counted refused updates as lapsed
    holidays / clash read as empty: holidays charged as leave, overlaps accepted
    approving Comp Off said "approved" while the ledger was not reduced
    a refund whose spend row stayed behind could be refunded twice
    year end showed "0 closed" when the people list or the inserts failed
    attendance cached an empty holiday set / leave for the session
    a leaver's last day not saved while the checklist started
    rerouting waiting leave to HR ignored while the admin was told "moved"
"""

from datetime import date, timedelta

import pytest

from slate.core.domain import leave_policy as lp
from slate.core.infra.db_results import DatabaseReadError, WriteResult
from slate.core.infra.leave_repository import LeaveRepository


class ReadRefused:
    """A reachable database that refuses every read (and every write)."""

    def execute_query(self, sql, params=None, fetch="all", strict=False):
        if strict:
            raise DatabaseReadError("refused", kind="read")
        return None

    def execute_update(self, sql, params=None):
        return WriteResult(False, error="refused")


@pytest.fixture
def lite(tmp_path, monkeypatch):
    """A SQLite database of this test's own (see test_leave_rules for why)."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain import access, people

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    lp.set_overrides({})
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "hidden.db"))
    assert manager.active_mode == "sqlite"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    access.reset_cache()
    people.refresh()
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None
        lp.set_overrides({})
        access.reset_cache()
        people.refresh()


@pytest.fixture(params=["sqlite", "postgres"])
def any_db(request):
    if request.param == "sqlite":
        return request.getfixturevalue("lite")
    return request.getfixturevalue("pg_db")


def _person(db, username, roles=("Artist",), **fields):
    from slate.core.domain.user_manager import UserManager
    UserManager(db=db).add_user(username, "pw", list(roles), username.title(), "Comp", **fields)


def _comp_off_request(db):
    """jo: one comp-off day earned, a one-day Comp Off request waiting on HR."""
    from slate.core.domain import people
    lp.set_overrides({"comp_off_enabled": True})
    _person(db, "hr.meera", roles=("HR",))
    _person(db, "jo", joined_on="2026-01-01")
    people.refresh()
    repo = LeaveRepository(db)
    assert repo.credit_comp_off("jo", date.today() - timedelta(days=3), 1.0, "Sunday")
    day = date.today() + timedelta(days=21)
    while not lp.is_working_day(day):
        day += timedelta(days=1)
    sent = repo.submit("jo", "Comp Off", day, day, False, "back")
    assert sent, sent
    return repo, sent.request_id


def _consumed(db):
    rows = db.execute_query("SELECT consumed FROM comp_off_ledger", fetch="all")
    return [float(dict(r)["consumed"]) for r in rows]


# ------------------------------------------------------------------ comp-off

def test_comp_off_crediting_fails_when_the_ledger_cannot_be_read():
    from slate.core.domain.comp_off_service import CompOffService
    lp.set_overrides({"comp_off_enabled": True})
    try:
        with pytest.raises(DatabaseReadError):
            CompOffService(db=ReadRefused(), repo=object()).run()
    finally:
        lp.set_overrides({})


def test_a_day_of_attendance_is_credited_once(any_db):
    repo = LeaveRepository(any_db)
    day = date.today() - timedelta(days=3)
    assert repo.credit_comp_off("jo", day, 1.0, "Sunday")
    assert not repo.credit_comp_off("JO", day, 1.0, "Sunday")


def test_old_duplicate_credits_do_not_stop_the_upgrade(lite):
    """The later copy is relabelled, not deleted, and the rule then holds."""
    from slate.core.infra.migrations import workplace_schema
    lite.execute_update("DROP INDEX uq_comp_off_attendance_day")
    for _ in range(2):
        assert lite.execute_update(
            "INSERT INTO comp_off_ledger (user_id, earned_on, days, source) "
            "VALUES ('jo', '2026-08-02', 1, 'attendance')")
    workplace_schema.apply_migration(lite)
    rows = lite.execute_query("SELECT source FROM comp_off_ledger ORDER BY id", fetch="all")
    assert [dict(r)["source"] for r in rows] == ["attendance", "attendance-duplicate"]
    assert not LeaveRepository(lite).credit_comp_off("jo", date(2026, 8, 2), 1.0, "again")


def test_a_refused_lapse_is_counted_as_failed():
    from slate.core.domain.comp_off_service import CompOffService

    class OneExpired(ReadRefused):
        def execute_query(self, sql, params=None, fetch="all", strict=False):
            return [{"id": 1, "days": 1, "consumed": 0, "expires_on": date(2020, 1, 1)}]

    assert CompOffService(db=OneExpired(), repo=object()).expire() == {"lapsed": 0, "failed": 1}


# --------------------------------------------------------- holidays, clashes

def test_holidays_and_clashes_are_never_read_as_none():
    repo = LeaveRepository(ReadRefused())
    with pytest.raises(DatabaseReadError):
        repo.holidays(2026)
    with pytest.raises(DatabaseReadError):
        repo.clash("jo", date(2026, 9, 1), date(2026, 9, 2))


def test_a_request_is_not_saved_when_its_checks_cannot_be_read():
    class WritesWork(ReadRefused):
        def execute_update(self, sql, params=None):
            return WriteResult(True, rows=1, last_id=1)

    outcome = LeaveRepository(WritesWork()).submit(
        "jo", "Casual", date(2026, 9, 1), date(2026, 9, 2), False, "trip")
    assert not outcome and outcome.code == "not_saved"


# --------------------------------------------------------- comp-off spending

def test_an_approval_whose_ledger_spend_fails_is_undone(lite):
    repo, request_id = _comp_off_request(lite)
    lite.execute_update("DROP TABLE comp_off_spends")
    outcome = repo.decide(request_id, "HR", True, "hr.meera")
    assert not outcome and outcome.code == "not_saved"
    assert lp.normalise_status(repo.request(request_id)["status"]) in lp.PENDING_STATUSES
    assert _consumed(lite) == [0.0]


def test_a_short_ledger_refuses_the_approval(lite, monkeypatch):
    """Even when the early check missed it (a race), approval and ledger agree."""
    repo, request_id = _comp_off_request(lite)
    lite.execute_update("UPDATE comp_off_ledger SET consumed = days")
    monkeypatch.setattr(repo, "comp_off_shortfall", lambda row: "")
    outcome = repo.decide(request_id, "HR", True, "hr.meera")
    assert not outcome and outcome.code == "comp_off_short"
    assert lp.normalise_status(repo.request(request_id)["status"]) in lp.PENDING_STATUSES


def test_a_revoke_whose_refund_fails_is_undone(lite):
    repo, request_id = _comp_off_request(lite)
    assert repo.decide(request_id, "HR", True, "hr.meera")
    assert _consumed(lite) == [1.0]
    lite.execute_update("ALTER TABLE comp_off_spends RENAME TO spends_away")
    assert not repo.revoke(request_id, "hr.meera", "approved by mistake")
    assert lp.normalise_status(repo.request(request_id)["status"]) == lp.STATUS_APPROVED
    lite.execute_update("ALTER TABLE spends_away RENAME TO comp_off_spends")
    assert repo.revoke(request_id, "hr.meera", "approved by mistake")
    assert _consumed(lite) == [0.0]
    assert lite.execute_query("SELECT id FROM comp_off_spends", fetch="all") == []


# ------------------------------------------------------------------ year end

def test_year_end_does_not_read_a_failed_people_list_as_nobody():
    with pytest.raises(DatabaseReadError):
        LeaveRepository(ReadRefused()).close_year(2025, "hr", today=date(2026, 3, 1))


def test_year_end_lists_the_closes_that_were_not_saved(lite):
    _person(lite, "jo", joined_on="2024-01-01")
    lite.execute_update("DROP TABLE leave_year_close")
    result = LeaveRepository(lite).close_year(2025, "hr", today=date(2026, 3, 1))
    assert result["closed"] == 0 and result["failed"] == ["jo"]


# ---------------------------------------------------------------- attendance

def test_approved_leave_is_never_read_as_none():
    with pytest.raises(DatabaseReadError):
        LeaveRepository(ReadRefused()).approved_leave(date(2026, 9, 1), date(2026, 9, 30))


def test_attendance_does_not_cache_a_failed_holiday_read(monkeypatch):
    """An empty set was kept for the session; every holiday showed as Absent."""
    from types import SimpleNamespace
    from slate.core.infra import leave_repository
    from slate.gui.attendance_tab import AttendanceTab

    monkeypatch.setattr(leave_repository, "LeaveRepository", lambda: LeaveRepository(ReadRefused()))
    said = []
    tab = SimpleNamespace(_holiday_cache={}, _unread=None, username="jo",
                          _notify=lambda message, level="info", details="": said.append(level))
    assert AttendanceTab._holidays(tab, 2026, "All") == set()
    assert tab._holiday_cache == {}
    assert AttendanceTab._leave(tab, date(2026, 9, 1), date(2026, 9, 30)) == {}
    assert AttendanceTab._report_unread(tab) and said == ["warning"]
    assert tab._unread is None


# ------------------------------------------------------------ joining, users

def test_a_leavers_last_day_that_was_not_saved_stops_the_checklist(lite, monkeypatch):
    from slate.core.domain.onboarding_service import LEAVING, OnboardingService
    _person(lite, "jo", joined_on="2024-01-01")
    service = OnboardingService(lite)
    lite.execute_update(
        "CREATE TRIGGER no_last_day BEFORE UPDATE OF last_day ON ut_users "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END")
    with pytest.raises(ValueError, match="last working day"):
        service.start("jo", LEAVING, effective_date=date(2026, 10, 31))
    assert service.tasks_for("jo", LEAVING) == []


def test_waiting_leave_that_could_not_go_to_hr_is_a_problem(lite):
    from slate.core.domain.user_manager import UserManager
    _person(lite, "sup.anjali", roles=("Supervisor",))
    _person(lite, "aarav", reports_to="sup.anjali")
    lite.execute_update("INSERT INTO leave_requests (user_id, type, start_date, end_date, status) "
                        "VALUES ('aarav', 'Casual', '2026-12-01', '2026-12-02', 'Pending Supervisor')")
    lite.execute_update(
        "CREATE TRIGGER no_reroute BEFORE UPDATE OF route_note ON leave_requests "
        "BEGIN SELECT RAISE(ABORT, 'refused'); END")
    moved, problems = UserManager(db=lite).move_reports("sup.anjali", "")
    assert moved == 1 and any("could not be sent to HR" in p for p in problems)
