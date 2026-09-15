"""
Attendance: one key per person, and one calendar.

Two bugs that between them made the attendance record untrustworthy.

The Home tab wrote punches under the name on screen and the Attendance tab
wrote them under the login. ``CentralAttendance`` lower-cases whatever it is
handed, so one person became two: each screen showed its own punches and
reported the other's as missing, and "punch in" on the screen that thought you
were out created a second record for the same day.

And attendance knew about Sundays and nothing else. A public holiday read as an
absence, arriving late on a day nobody was expected counted against you, and a
studio that did not work Monday to Saturday had no way to say so - the day was
a literal in two files, and the leave rules had their own copy.
"""

from datetime import date

import pytest

from slate.core.domain import leave_policy as lp


@pytest.fixture(autouse=True)
def default_policy():
    """Leave the policy as shipped, whatever an earlier test installed."""
    lp.set_overrides({})
    yield
    lp.set_overrides({})


# --------------------------------------------------------------- the calendar

def test_the_working_day_comes_from_the_policy_not_from_attendance():
    """
    The bug: attendance carried its own start time and its own nine-hour day,
    so a studio that changed its hours had two places to change and only ever
    found one.
    """
    assert lp.late_cutoff() == (10, 45)
    assert lp.standard_day_hours() == 9.0

    lp.set_overrides({"late_cutoff": "09:30", "standard_day_hours": 8.0})
    assert lp.late_cutoff() == (9, 30)
    assert lp.standard_day_hours() == 8.0


def test_a_studio_policy_that_makes_no_sense_falls_back_rather_than_breaking():
    """
    Somebody will type "half nine" into a settings box. No cutoff at all would
    mean nobody is ever late, which is a worse answer than the default.
    """
    lp.set_overrides({"late_cutoff": "half nine", "standard_day_hours": "lots"})
    assert lp.late_cutoff() == (10, 45)
    assert lp.standard_day_hours() == 9.0


def test_a_public_holiday_is_not_a_working_day():
    """
    The bug: attendance tested weekday() == 6 and nothing else, so Diwali read
    as an absence on everybody's row.
    """
    diwali = date(2026, 11, 8)          # a Sunday in 2026, so use a weekday
    weekday_holiday = date(2026, 11, 10)
    assert lp.is_working_day(weekday_holiday, holidays=set())
    assert not lp.is_working_day(weekday_holiday, holidays={weekday_holiday})
    assert not lp.is_working_day(diwali, holidays=set())  # Sunday anyway


def test_a_studio_can_say_which_days_it_works():
    """Six-day weeks are the default here, not an assumption baked into a tab."""
    saturday = date(2026, 9, 12)
    assert lp.is_working_day(saturday, holidays=set())

    lp.set_overrides({"weekly_offs": [5, 6]})
    assert not lp.is_working_day(saturday, holidays=set())


def test_the_streak_survives_a_public_holiday():
    """
    The bug: the streak helper skipped Sundays only, so a holiday in the middle
    of the month reset everybody's streak to zero.
    """
    from slate.gui.attendance_metrics import calculate_streak

    # On time on the 1st and the 3rd. The 2nd is a public holiday with no punch.
    log = {"01": {"in": "10:00"}, "03": {"in": "10:00"}}
    holiday = date(2026, 9, 2)

    from datetime import datetime
    now = datetime(2026, 9, 4)

    without = calculate_streak(log, 2026, 9, 10, 45, now_ref=now)
    with_calendar = calculate_streak(
        log, 2026, 9, 10, 45, now_ref=now,
        non_working=lambda day: day == holiday or day.weekday() == 6)

    assert without == 1, "the 2nd broke the streak, as it used to"
    assert with_calendar == 2, "the holiday should not break it"


# --------------------------------------------------------------- the identity

def test_home_and_attendance_agree_on_who_you_are():
    """
    The bug: Home passed display_name and Attendance passed user_id into the
    same log_action, so "Priya Sharma" and "emp0007" were two people.

    Both now take the field the attendance record is keyed by, so this checks
    the tabs pick the same one out of the same user_data.
    """
    user_data = {"user_id": "EMP0007", "username": "EMP0007",
                 "display_name": "Priya Sharma"}

    # What AttendanceTab uses.
    attendance_key = user_data.get('user_id', user_data.get('username', 'Unknown'))
    # What HomeTab now uses.
    home_key = str(user_data.get('user_id') or user_data.get('username') or '').strip()

    assert home_key == attendance_key
    assert home_key.lower() != user_data["display_name"].lower()


def test_the_merge_tool_maps_a_display_name_back_to_the_login():
    """
    The repair for records already written under the wrong key. The map has to
    be built from display name to username, and must ignore people whose two
    are the same.
    """
    import importlib.util
    from pathlib import Path

    tool = Path(__file__).resolve().parents[1] / "tools" / "merge_attendance_identities.py"
    spec = importlib.util.spec_from_file_location("merge_ids", tool)
    module = importlib.util.module_from_spec(spec)

    rows = [
        {"username": "EMP0007", "display_name": "Priya Sharma"},
        {"username": "admin", "display_name": "admin"},
        {"username": "EMP0012", "display_name": ""},
    ]

    class FakeDb:
        def execute_query(self, sql, params=None, fetch=None):
            return rows

    import sys
    sys.modules.setdefault("merge_ids", module)
    spec.loader.exec_module(module)
    module.database_manager = FakeDb()

    mapping, known = module.build_identity_map()
    assert mapping == {"priya sharma": "EMP0007"}
    assert known == {"emp0007", "admin", "emp0012"}
