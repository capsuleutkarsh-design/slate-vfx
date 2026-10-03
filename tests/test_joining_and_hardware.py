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
    OnboardingService, JOINING, LEAVING, OFFBOARD_TASKS,
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
    assert record["employment"] == "Freelance", "one spelling, as Users & Roles"


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


# ------------------------------------------------------------ audit 2026-09

def test_the_joining_date_changes_only_when_asked(db, service):
    """HR-103: the typed date was ignored silently when one existed."""
    UserManager(db=db).add_user("sai", "pw", ["Artist"], "Sai", "Paint", joined_on="2025-12-20")
    service.start("sai", JOINING, effective_date=date(2026, 10, 5))
    assert service.joined_on("sai") == date(2025, 12, 20)
    service.start("sai", JOINING, effective_date=date(2026, 10, 5), overwrite_joined=True)
    assert service.joined_on("sai") == date(2026, 10, 5)


def test_workstation_returned_waits_for_every_machine(db, service):
    """HR-105: collecting one of three machines ticked 'Workstation returned'."""
    UserManager(db=db).add_user("diya", "pw", ["Artist"], "Diya", "Roto")
    for name in ("WS-1", "WS-2", "WS-3"):
        _machine(db, name)
        assert service.issue_machine(name, "diya", "it")
    service.start("diya", LEAVING, effective_date=date.today() + timedelta(days=10))
    names = {t["task_name"] for t in service.tasks_for("diya", LEAVING)}
    assert {"Return WS-1", "Return WS-2", "Return WS-3"} <= names

    assert service.return_machine("WS-1", "diya", "it.sana")
    tasks = {t["task_name"]: t for t in service.tasks_for("diya", LEAVING)}
    assert tasks["Return WS-1"]["is_completed"]
    assert not tasks["Workstation returned"]["is_completed"]

    service.return_machine("WS-2", "diya")
    service.return_machine("WS-3", "diya")
    tasks = {t["task_name"]: t for t in service.tasks_for("diya", LEAVING)}
    assert tasks["Workstation returned"]["is_completed"]


def test_a_mistaken_checklist_can_be_cancelled(db, service):
    """HR-106."""
    UserManager(db=db).add_user("krishna", "pw", ["Artist"], "Krishna", "Comp")
    service.start("krishna", LEAVING, effective_date=date(2026, 12, 31))
    first = service.tasks_for("krishna", LEAVING)[0]
    service.complete(first["id"], True, by="hr.meera")
    removed = service.cancel_checklist("krishna", LEAVING, "hr.meera", clear_last_day=True)
    left = service.tasks_for("krishna", LEAVING)
    assert removed == len(OFFBOARD_TASKS) - 1 and [t["id"] for t in left] == [first["id"]]
    assert service.last_day("krishna") is None


def test_no_machine_for_somebody_who_has_left(db, service):
    """HR-109."""
    UserManager(db=db).add_user("diya", "pw", ["Artist"], "Diya", "Roto")
    _machine(db, "WS-9")
    service.start("diya", LEAVING, effective_date=date.today() - timedelta(days=5))
    assert service.issue_refusal("diya")
    assert not service.issue_machine("WS-9", "diya", "it")
    assert service.issue_machine("WS-9", "diya", "it", override=True)


def test_a_tick_records_who_and_when(db, service):
    """HR-110."""
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    service.start("jo", JOINING)
    task = service.tasks_for("jo", JOINING)[0]
    service.complete(task["id"], True, by="hr.meera")
    again = service.tasks_for("jo", JOINING)[0]
    assert again["completed_by"] == "hr.meera" and again["completed_at"]
    service.complete(task["id"], False)
    assert service.tasks_for("jo", JOINING)[0]["completed_by"] is None


def test_collecting_a_machine_nobody_has_is_not_reported_done(db, service):
    """HR-114."""
    _machine(db, "WS-7")
    assert not service.return_machine("WS-7", "jo")


# ------------------------------------------------------------ round 2

def _tick_all(service, username, direction):
    for task in service.tasks_for(username, direction):
        service.complete(task["id"], True, by="hr")


def test_a_rehire_gets_a_new_joining_list(db, service):
    """HR2-075: a finished list no longer blocks the next one; a half-done one is not doubled."""
    UserManager(db=db).add_user("neha", "pw", ["Artist"], "Neha", "Comp", employment="Staff")
    first = service.start("neha", JOINING, effective_date=date(2021, 4, 1))
    assert service.start("neha", JOINING) == 0, "still open: nothing doubled"
    _tick_all(service, "neha", JOINING)
    second = service.start("neha", JOINING, effective_date=date(2026, 10, 5))
    assert second == first
    current = service.tasks_for("neha", JOINING)
    assert len(current) == first and not any(t["is_completed"] for t in current)
    assert service.progress("neha", JOINING) == {"total": first, "done": 0,
                                                 "outstanding": first, "complete": False}
    everything = db.execute_query("SELECT COUNT(*) AS n FROM onboarding_workflows "
                                  "WHERE user_id = 'neha'", fetch="one")["n"]
    assert everything == 2 * first, "the finished list is kept as it was"


def test_starting_a_list_keeps_the_persons_employment(db, service):
    """HR2-076: leaving never writes employment; joining without one keeps it."""
    um = UserManager(db=db)
    um.add_user("pari", "pw", ["Artist"], "Pari", "Comp", employment="Freelance")
    service.start("pari", LEAVING, effective_date=date.today() + timedelta(days=20))
    assert um.get_all_users()["pari"]["employment"] == "Freelance"
    names = {t["task_name"] for t in service.tasks_for("pari", LEAVING)}
    assert "Final settlement processed" not in names, "the record's employment decides"
    um.add_user("ananya", "pw", ["Artist"], "Ananya", "Comp", employment="Contract")
    service.start("ananya", JOINING)
    assert um.get_all_users()["ananya"]["employment"] == "Contract"


def test_a_last_day_before_joining_is_refused(db, service):
    """HR2-079."""
    UserManager(db=db).add_user("jo", "pw", ["Artist"], "Jo", "Comp", joined_on="2026-04-06")
    with pytest.raises(ValueError):
        service.start("jo", LEAVING, effective_date=date(2026, 1, 1))
    assert service.last_day("jo") is None


def test_kit_with_somebody_who_left_is_chased_without_a_leaving_list(db, service):
    """HR2-077: a last day set on Users & Roles, or a deactivated account."""
    um = UserManager(db=db)
    um.add_user("kabir", "pw", ["Artist"], "Kabir", "Comp")
    um.add_user("rohan", "pw", ["Artist"], "Rohan", "Comp")
    _machine(db, "WS-101")
    _machine(db, "WS-102")
    assert service.issue_machine("WS-101", "kabir", "it")
    assert service.issue_machine("WS-102", "rohan", "it")
    assert service.unreturned() == []
    um.update_user("kabir", last_day=(date.today() - timedelta(days=5)).isoformat())
    # (deactivate_user refuses while a machine is out; switched off by hand here)
    db.execute_update("UPDATE ut_users SET active = 0 WHERE username = 'rohan'")
    assert {r["machine_name"] for r in service.unreturned()} == {"WS-101", "WS-102"}
    # Leaving can still be started for somebody who has already gone.
    assert service.start("kabir", LEAVING) > 0
