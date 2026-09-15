"""
Joining, leaving, and the machines that go with them.

The point of putting both directions on one screen is that a machine issued on
day one is named on the row when the person leaves. That only works if the loan
ledger and the inventory agree, and they had three ways not to:

    the dialog collected employment and a department and discarded both, so
    leave accrual had no joining date to count from

    leaving never asked when, so "kit not returned" fired on the day notice was
    given and kept firing while the person was still using the machine

    the inventory let you type an owner into a text box, which wrote the
    inventory and not the ledger - so a machine issued that way was never asked
    for back, and deleting it left a loan behind with nothing to return
"""

from datetime import date, timedelta

import pytest

from slate.core.domain.onboarding_service import (
    OnboardingService, JOINING, LEAVING,
)
from slate.core.domain.user_manager import UserManager


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A database of this test's own. See test_leave_rules for why not mock_db."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "joining.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"

    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None


@pytest.fixture
def service(db):
    return OnboardingService(db)


def _machine(db, name, status="Available"):
    db.execute_update(
        "INSERT INTO hardware_inventory (machine_name, type, status) "
        "VALUES (%s, 'Workstation', %s)", (name, status))


# ------------------------------------------------------------ the record kept

def test_starting_somebody_joining_records_when(db, service):
    """
    The bug: the dialog asked for employment and a department and threw the
    answers away, so leave accrual had no joining date and fell back to
    1 January for everybody.
    """
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")

    made = service.start("jo", JOINING, employment="freelance",
                         department="Comp", effective_date=date(2026, 4, 6))
    assert made > 0

    record = UserManager(db=db).get_all_users()["jo"]
    assert str(record["joined_on"])[:10] == "2026-04-06"
    assert record["employment"] == "freelance"


def test_relaying_the_checklist_does_not_move_the_joining_date(db, service):
    """
    A checklist can be laid down again months later. Moving the joining date
    then would quietly change how much leave the person has accrued.
    """
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    service.start("jo", JOINING, effective_date=date(2026, 4, 6))
    service.start("jo", JOINING, effective_date=date(2026, 9, 1))

    assert str(UserManager(db=db).get_all_users()["jo"]["joined_on"])[:10] == "2026-04-06"


def test_starting_somebody_leaving_records_the_last_day(db, service):
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    service.start("jo", LEAVING, effective_date=date(2026, 10, 31))
    assert service.last_day("jo") == date(2026, 10, 31)


def test_a_freelancer_skips_the_payroll_lines(db, service):
    UserManager(db=db).add_user("free", "pw", ["Artist"], "Free", "Comp")
    service.start("free", JOINING, employment="freelance")
    names = {t["task_name"] for t in service.tasks_for("free", JOINING)}
    assert "Added to payroll" not in names
    assert "Workstation issued" in names


# --------------------------------------------------------------- kit chasing

def test_kit_is_not_chased_before_the_person_has_left(db, service):
    """
    The bug: "kit not returned" listed anybody with an open leaving checklist,
    so it fired the day notice was given and kept firing for the whole notice
    period - while the person was still sitting at the machine using it.
    """
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")

    tomorrow = date.today() + timedelta(days=30)
    service.start("jo", LEAVING, effective_date=tomorrow)

    assert service.unreturned() == [], "still working here, still using it"


def test_kit_is_chased_once_the_last_day_has_passed(db, service):
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")
    service.start("jo", LEAVING, effective_date=date.today() - timedelta(days=1))

    stranded = service.unreturned()
    assert [row["machine_name"] for row in stranded] == ["WS-01"]


def test_kit_is_chased_when_the_checklist_says_they_have_gone(db, service):
    """
    Somebody with no last day recorded - an older record - still has to be
    chased once IT tick the line that says they have gone.
    """
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")
    service.start("jo", LEAVING)
    db.execute_update("UPDATE ut_users SET last_day = NULL WHERE username = 'jo'")

    assert service.unreturned() == []

    for task in service.tasks_for("jo", LEAVING):
        if task["task_name"] == "Last working day confirmed":
            service.complete(task["id"], True)

    assert [row["machine_name"] for row in service.unreturned()] == ["WS-01"]


# ------------------------------------------------------------------ the ledger

def test_issuing_a_machine_writes_both_the_ledger_and_the_inventory(db, service):
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")

    assert service.issue_machine("WS-01", "jo", "it")

    assert [h["user_id"] for h in service.held_by_machine("WS-01")] == ["jo"]
    row = dict(db.execute_query(
        "SELECT assigned_to, status FROM hardware_inventory WHERE machine_name = 'WS-01'",
        fetch="one"))
    assert row["assigned_to"] == "jo"
    assert row["status"] == "Active"


def test_collecting_a_machine_frees_it_for_the_next_person(db, service):
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")

    assert service.return_machine("WS-01", "jo")
    assert service.held_by_machine("WS-01") == []
    assert "WS-01" in service.available_machines()


def test_a_machine_in_for_repair_is_not_offered_to_anybody(db, service):
    _machine(db, "WS-BROKEN", status="Repair")
    assert "WS-BROKEN" not in service.available_machines()


def test_an_issued_machine_is_not_offered_to_anybody_else(db, service):
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")
    assert "WS-01" not in service.available_machines()


def test_the_ledger_knows_who_has_a_machine_not_just_who_has_what(db, service):
    """
    held_by answers "what does this person have". Deleting a machine needs the
    other direction - "who has this machine" - and without it the tab asked the
    inventory's own copy, which the old free-text owner box could contradict.
    """
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    _machine(db, "WS-01")
    service.issue_machine("WS-01", "jo", "it")

    assert service.held_by_machine("WS-01")[0]["user_id"] == "jo"
    assert service.held_by_machine("WS-NOBODY") == []
