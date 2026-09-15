"""
The employment record: joining date, employment type, manager, location.

Every one of these is read by something that had nowhere to read it from.
Accrual counts completed months since ``joined_on``, the holiday calendar needs
a location to know whose holidays apply, the first approval stage needs
``reports_to`` to know whose queue a request lands in, and offboarding needs
``last_day`` to know when a machine is actually overdue.

None of it was ever written. The Users & Roles tab had no field for any of it,
so accrual fell back to 1 January for the whole studio and a supervisor was
shown every request in the building.
"""

import pytest

from slate.core.domain.user_manager import UserManager


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    """
    A genuinely local database, for tests that write to one.

    The shared ``mock_db`` fixture does not give you this. It builds a
    DatabaseManager and the manager reads the studio's own configuration, which
    says postgres - so on any machine with a configured studio the fixture
    hands back a postgres manager pointed at a server the test machine has no
    business touching, and every write either fails or lands somewhere real.
    Forcing the mode here is the difference between a test that passes because
    it was isolated and one that passes because nothing happened.
    """
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "record.db"))
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


def test_a_joining_date_survives_the_round_trip(local_db):
    """
    The bug: there was no way to record one, so accrued_by fell back to
    1 January of the current year for every person in the studio.
    """
    um = UserManager(db=local_db)
    assert um.add_user("jo", "pw", ["Artist"], "Jo Artist", "Comp",
                       joined_on="2024-03-15", employment="Staff",
                       reports_to="sam", location="Mumbai")

    record = um.get_all_users()["jo"]
    assert str(record["joined_on"])[:10] == "2024-03-15"
    assert record["employment"] == "Staff"
    assert record["reports_to"] == "sam"
    assert record["location"] == "Mumbai"


def test_editing_a_person_keeps_every_role(local_db):
    """
    The bug: the edit dialog read its starting values out of the table, whose
    Roles cell is one comma-joined string, and put only the first entry back.
    Somebody who was a Lead and a Supervisor came out a Lead.
    """
    um = UserManager(db=local_db)
    um.add_user("multi", "pw", ["Lead", "Supervisor", "Coordinator"], "Multi", "Comp")

    # What the tab now does: read the record, hand the whole list back.
    held = um.get_all_users()["multi"]["roles"]
    assert len(held) == 3

    um.add_user("multi", "KEEP_OLD", held, "Multi Renamed", "Comp")
    assert um.get_all_users()["multi"]["roles"] == ["Lead", "Supervisor", "Coordinator"]


def test_a_caller_that_knows_nothing_of_these_fields_cannot_wipe_them(local_db):
    """
    A dozen places call add_user with five positional arguments. If the new
    columns were written unconditionally, every one of those calls would blank
    the joining date as a side effect of renaming somebody.
    """
    um = UserManager(db=local_db)
    um.add_user("keeper", "pw", ["Artist"], "Keeper", "Roto",
                joined_on="2023-01-09", location="Chennai")

    # The old-style call: no employment arguments at all.
    um.add_user("keeper", "KEEP_OLD", ["Artist"], "Keeper Renamed", "Roto")

    record = um.get_all_users()["keeper"]
    assert str(record["joined_on"])[:10] == "2023-01-09"
    assert record["location"] == "Chennai"
    assert record["display_name"] == "Keeper Renamed"


def test_a_deleted_emp0012_stays_deleted(local_db):
    """
    The bug: EMP0012 was re-created on every start, with a known password and
    Developer rights. Deleting it on the Users tab appeared to work and the
    account was back on the next launch.
    """
    um = UserManager(db=local_db)
    um.add_user("EMP0012", "pw", ["Developer"], "Dev User", "Dev")
    assert um.delete_user("EMP0012")

    # Starting the application again is what used to resurrect it.
    UserManager(db=local_db)

    assert "EMP0012" not in um.get_all_users()


def test_the_administrator_account_is_still_guaranteed(local_db):
    """
    Dropping EMP0012 must not drop the one account that genuinely stops a
    studio locking itself out.
    """
    UserManager(db=local_db)
    assert "admin" in UserManager(db=local_db).get_all_users()
