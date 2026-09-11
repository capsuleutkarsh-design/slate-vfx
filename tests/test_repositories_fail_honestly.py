"""
A repository must not turn a database failure into a plausible answer.

This is the test that was missing, and its absence is why the fault below
shipped. The policy modules were tested thoroughly and the layer that reads and
writes them was not tested at all, so nothing ever asked what these functions
do when the database says no.

What they used to do, measured before the fix:

    balance('somebody')  ->  accrued 16.0, used {}, pending {}, available 16.0

with every query raising. A transient fault reported a full, unspent leave
balance - no exception, no log line, nothing on screen. The database layer
raises DatabaseUnavailableError precisely so that cannot happen; the
repositories caught it and put it back.

Two rules, and every repository has to hold both:

    the database is unreachable   ->  raise. The caller must find out.
    some other fault              ->  fall back, but leave a trace.
"""

import logging

import pytest

from slate.core.infra.postgres_manager import DatabaseUnavailableError
from slate.core.infra.leave_repository import LeaveRepository
from slate.core.infra.licence_repository import LicenceRepository
from slate.core.domain.onboarding_service import OnboardingService


class Unreachable:
    """Accepts the call and reports the database is not there."""

    def execute_query(self, *args, **kwargs):
        raise DatabaseUnavailableError("the database is not responding")

    def execute_update(self, *args, **kwargs):
        raise DatabaseUnavailableError("the database is not responding")


class Broken:
    """Reachable, but the statement itself is wrong - a bug, not an outage."""

    def execute_query(self, *args, **kwargs):
        raise ValueError("column \"nonsense\" does not exist")

    def execute_update(self, *args, **kwargs):
        raise ValueError("column \"nonsense\" does not exist")


# --------------------------------------------------------------------- leave

LEAVE_READS = [
    ("holidays", lambda r: r.holidays()),
    ("holiday_rows", lambda r: r.holiday_rows()),
    ("joined_on", lambda r: r.joined_on("someone")),
    ("for_user", lambda r: r.for_user("someone")),
    ("all_requests", lambda r: r.all_requests()),
    ("comp_off_balance", lambda r: r.comp_off_balance("someone")),
    ("balance", lambda r: r.balance("someone")),
    ("last_close", lambda r: r.last_close("someone")),
    ("closes", lambda r: r.closes()),
]


@pytest.mark.parametrize("name,call", LEAVE_READS, ids=[n for n, _ in LEAVE_READS])
def test_leave_reads_refuse_when_the_database_is_unreachable(name, call):
    with pytest.raises(DatabaseUnavailableError):
        call(LeaveRepository(Unreachable()))


def test_a_balance_is_never_invented_from_an_outage():
    """
    The regression, stated as the thing that must not happen.

    An unreachable database once produced "16 days available, nothing used",
    which is worse than an error: it is a wrong answer that looks right, and
    somebody books leave against it.
    """
    with pytest.raises(DatabaseUnavailableError):
        LeaveRepository(Unreachable()).balance("someone")


def test_the_sandwich_rule_is_never_computed_against_no_holidays():
    """
    holidays() returning an empty set on failure silently changes what leave
    costs - the block either side of a skipped day stops being charged.
    """
    with pytest.raises(DatabaseUnavailableError):
        LeaveRepository(Unreachable()).holidays()


def test_a_write_that_did_not_happen_is_not_reported_as_success():
    repo = LeaveRepository(Unreachable())
    with pytest.raises(DatabaseUnavailableError):
        repo.add_holiday("2026-01-26", "Republic Day")


def test_another_kind_of_fault_still_falls_back_but_leaves_a_trace(caplog):
    """A bad statement is a bug. The screen survives it; the log records it."""
    repo = LeaveRepository(Broken())
    with caplog.at_level(logging.ERROR):
        assert repo.holidays() == set()
        assert repo.for_user("someone") == []
    assert caplog.records, "the reason was discarded"


# ------------------------------------------------------------------ licences

LICENCE_READS = [
    ("licences", lambda r: r.licences()),
    ("peaks", lambda r: r.peaks()),
    ("history", lambda r: r.history("Nuke")),
    ("compliance", lambda r: r.compliance()),
]


@pytest.mark.parametrize("name,call", LICENCE_READS, ids=[n for n, _ in LICENCE_READS])
def test_licence_reads_refuse_when_the_database_is_unreachable(name, call):
    with pytest.raises(DatabaseUnavailableError):
        call(LicenceRepository(Unreachable()))


def test_compliance_does_not_report_a_compliant_studio_during_an_outage():
    """
    An empty licence list reads as "nothing to worry about", which is the one
    conclusion an outage must never produce on this screen.
    """
    with pytest.raises(DatabaseUnavailableError):
        LicenceRepository(Unreachable()).compliance()


# ------------------------------------------------------------------- joining

JOINING_READS = [
    ("people", lambda s: s.people()),
    ("tasks_for", lambda s: s.tasks_for("someone")),
    ("open_tasks", lambda s: s.open_tasks()),
    ("held_by", lambda s: s.held_by("someone")),
    ("unreturned", lambda s: s.unreturned()),
    ("available_machines", lambda s: s.available_machines()),
]


@pytest.mark.parametrize("name,call", JOINING_READS, ids=[n for n, _ in JOINING_READS])
def test_joining_reads_refuse_when_the_database_is_unreachable(name, call):
    with pytest.raises(DatabaseUnavailableError):
        call(OnboardingService(Unreachable()))


def test_nothing_is_reported_as_still_held_during_an_outage():
    """
    unreturned() is the report that finds machines nobody asked back. Empty
    means "all accounted for", so an outage must not be able to say it.
    """
    with pytest.raises(DatabaseUnavailableError):
        OnboardingService(Unreachable()).unreturned()
