"""
Keeping the holiday calendar, which is HR's job and had no way to do it.

The calendar decides three things that all read as the software being wrong
when it is out of date: which days the attendance grid shades, whether a day
counts as an absence against somebody, and what a leave request costs. It could
be added to and deleted from, and that was all - so a date announced wrongly or
a name typed wrongly had to be removed and put back. The removal succeeds on
its own, and an interruption between the two steps loses the day silently.

The list of places a holiday could apply to was three city names belonging to
the studio this was written for. A holiday applies to a location only when the
spelling matches the user records exactly, so offering the wrong three is worse
than offering none.
"""

from datetime import date

import pytest

from slate.core.domain import leave_policy as lp
from slate.core.infra.leave_repository import LeaveRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository on a database of its own - see tests/test_leave_rules.py."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    lp.set_overrides({})
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "holidays.db"))
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


# ------------------------------------------------------------------ editing

def test_a_holiday_can_be_corrected_without_losing_it(repo):
    """
    The missing operation. Remove-then-add is two steps of which the
    destructive one succeeds alone, and between them every leave request
    spanning that day quietly changes price.
    """
    repo.add_holiday(date(2026, 11, 8), "Diwali", "All")
    row = repo.holiday_rows(2026)[0]

    assert repo.update_holiday(row["id"], date(2026, 11, 9), "Diwali", "All")

    rows = repo.holiday_rows(2026)
    assert len(rows) == 1, "correcting a holiday must not duplicate it"
    assert str(rows[0]["holiday_date"]) == "2026-11-09"
    assert rows[0]["name"] == "Diwali"


def test_a_name_can_be_corrected(repo):
    repo.add_holiday(date(2026, 1, 26), "Repulic Day", "All")
    row = repo.holiday_rows(2026)[0]

    repo.update_holiday(row["id"], date(2026, 1, 26), "Republic Day", "All")
    assert repo.holiday_rows(2026)[0]["name"] == "Republic Day"


def test_a_holiday_can_be_narrowed_to_one_location(repo):
    repo.add_holiday(date(2026, 4, 14), "Local festival", "All")
    row = repo.holiday_rows(2026)[0]

    repo.update_holiday(row["id"], date(2026, 4, 14), "Local festival", "Pune")

    assert repo.holiday_rows(2026)[0]["location"] == "Pune"
    assert date(2026, 4, 14) in repo.holidays(2026, "Pune")
    assert date(2026, 4, 14) not in repo.holidays(2026, "Mumbai")


def test_a_correction_that_lands_on_another_holiday_is_refused(repo):
    """
    Two holidays cannot share a date for the same place - the table says so.
    The screen has to survive being told no.
    """
    repo.add_holiday(date(2026, 11, 8), "Diwali", "All")
    repo.add_holiday(date(2026, 11, 9), "Bhai Dooj", "All")
    second = repo.holiday_rows(2026)[1]

    assert repo.update_holiday(second["id"], date(2026, 11, 8), "Bhai Dooj",
                               "All") is False
    assert len(repo.holiday_rows(2026)) == 2, "and nothing is lost"


# ------------------------------------------------------------------ the list

def test_the_calendar_can_be_read_one_year_at_a_time(repo):
    """
    Without this it is every holiday the studio has ever had, and the year
    somebody came to fix is somewhere in the middle of it.
    """
    repo.add_holiday(date(2025, 12, 25), "Christmas", "All")
    repo.add_holiday(date(2026, 1, 1), "New Year", "All")
    repo.add_holiday(date(2026, 11, 8), "Diwali", "All")

    assert len(repo.holiday_rows()) == 3
    assert [r["name"] for r in repo.holiday_rows(2026)] == ["New Year", "Diwali"]
    assert [r["name"] for r in repo.holiday_rows(2025)] == ["Christmas"]


def test_the_years_with_something_in_them_are_listed_newest_first(repo):
    repo.add_holiday(date(2025, 12, 25), "Christmas", "All")
    repo.add_holiday(date(2026, 1, 1), "New Year", "All")

    assert repo.holiday_years() == [2026, 2025]


def test_an_empty_calendar_has_no_years(repo):
    assert repo.holiday_years() == []


# ------------------------------------------------------------- the locations

def test_the_places_offered_are_the_studios_own(repo):
    """
    They were three fixed city names. A holiday applies to a location only when
    the spelling matches what the user records say, so a studio typing its own
    in had to guess at the software's spelling of its own offices.
    """
    from slate.core.domain.user_manager import UserManager

    users = UserManager(db=repo.db)
    users.add_user("jo", "pw", ["Artist"], "Jo", "Comp", location="Pune")
    users.add_user("sam", "pw", ["Artist"], "Sam", "Comp", location="Hyderabad")
    users.add_user("ali", "pw", ["Artist"], "Ali", "Comp", location="Pune")

    assert repo.locations() == ["Hyderabad", "Pune"]


def test_a_studio_that_records_no_locations_is_offered_none(repo):
    from slate.core.domain.user_manager import UserManager

    UserManager(db=repo.db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")
    assert repo.locations() == []


def test_all_is_not_offered_as_a_place(repo):
    """It is already the first entry, and twice reads as two different things."""
    from slate.core.domain.user_manager import UserManager

    UserManager(db=repo.db).add_user("jo", "pw", ["Artist"], "Jo", "Comp",
                                     location="All")
    assert repo.locations() == []


# ------------------------------------------------- what the calendar is for

def test_a_corrected_holiday_changes_what_leave_costs(repo):
    """
    The reason any of this matters. The calendar is not a list of notes - it is
    the thing the day count is charged against.
    """
    from slate.core.domain.user_manager import UserManager

    UserManager(db=repo.db).add_user("jo", "pw", ["Artist"], "Jo", "Comp")

    monday, friday = date(2026, 6, 8), date(2026, 6, 12)
    assert lp.days_charged(monday, friday, repo.holidays(2026))["total"] == 5

    repo.add_holiday(date(2026, 6, 10), "Studio day", "All")
    assert lp.days_charged(monday, friday, repo.holidays(2026))["total"] == 4

    # Moved to a Wednesday in July, well clear of the request - so it is not
    # only off the working days but out of reach of the sandwich rule, which
    # would otherwise absorb the weekend beside it and make the week cost more
    # rather than less.
    row = repo.holiday_rows(2026)[0]
    repo.update_holiday(row["id"], date(2026, 7, 15), "Studio day", "All")
    assert lp.days_charged(monday, friday, repo.holidays(2026))["total"] == 5, \
        "moving it out of the week gives the day back"
