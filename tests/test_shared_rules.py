"""
Part 3 (rules): one copy of each rule that used to exist in several
disagreeing copies. Each test pins the shared function, and that the old
names still lead to it.
"""
from datetime import date, datetime, timedelta

import pytest

from slate.core.domain import people

TODAY = date(2026, 10, 5)


# ------------------------------------------------------------ B4: active
@pytest.mark.parametrize("stored, active", [
    (None, True), ("", True), (1, True), ("1", True), (True, True), ("t", True),
    ("true", True), ("True", True), ("yes", True), ("something odd", True),
    (0, False), ("0", False), (0.0, False), ("0.0", False), (False, False),
    ("f", False), ("F", False), ("false", False), ("FALSE", False), ("no", False),
    ("off", False), (" 0 ", False),
])
def test_every_stored_form_of_the_active_flag(stored, active):
    assert people.account_active({"active": stored}, TODAY) is active
    assert people.switched_off(stored) is (not active)


@pytest.mark.parametrize("last_day, active", [
    (None, True), ("", True), ("not a date", True), ("31/12/2025", True),
    (TODAY, True), (TODAY.isoformat(), True), (TODAY + timedelta(days=1), True),
    (TODAY - timedelta(days=1), False), ((TODAY - timedelta(days=1)).isoformat(), False),
    (datetime(2026, 10, 4, 18, 0), False), ("2026-10-04 18:00:00", False),
])
def test_a_last_day_that_has_passed(last_day, active):
    assert people.account_active({"active": 1, "last_day": last_day}, TODAY) is active


def test_every_old_name_asks_the_one_rule():
    from slate.core.security import admin_guard
    from slate.core.domain.user_manager import UserManager
    from slate.core.domain import onboarding_service
    assert admin_guard.account_active is people.account_active
    for record in ({"active": "f"}, {"active": 0}, {"active": None}, {"active": ""},
                   {"last_day": "2000-01-01"}):
        expected = people.account_active(record)
        assert UserManager._flag_active(record) is expected
        assert onboarding_service._is_active(record) is expected


# ------------------------------------------------- shared helpers (item 5)
def test_one_stored_date_reader_and_one_roles_reader():
    from slate.core.domain import dates, attendance_rules, central_attendance, licence_compliance
    from slate.core.domain import onboarding_service
    from slate.core.infra import leave_repository
    from slate.core.security import admin_guard
    from slate.core.domain.user_manager import UserManager
    for copy in (leave_repository.as_date, licence_compliance.as_date, people._as_date,
                 onboarding_service._as_day, attendance_rules._as_day,
                 central_attendance.CentralAttendance._as_date):
        assert copy is dates.as_date
    assert dates.as_date("2026-10-05 09:00") == date(2026, 10, 5)
    assert dates.as_date("05/10/2026") is None          # stored dates are ISO; nothing else counts
    assert UserManager._parse_roles is admin_guard.parse_roles
    assert admin_guard.parse_roles('["HR", "Artist"]') == ["HR", "Artist"]
    assert admin_guard.parse_roles("Artist") == ["Artist"] and admin_guard.parse_roles(None) == []
