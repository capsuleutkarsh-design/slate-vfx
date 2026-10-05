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


# -------------------------------------------------- who reports to whom (item 2)
def test_one_reporting_line_rule():
    users = {"Sam": {}, "lead.kiran": {"reports_to": "sam"}, "Ira": {"reports_to": " Lead.Kiran "},
             "jo": {"reports_to": "SAM"}, "loop.a": {"reports_to": "loop.b"},
             "loop.b": {"reports_to": "loop.a"}, "self": {"reports_to": "self"}}
    assert people.reports_under(users, "sam") == {"lead.kiran", "jo"}
    assert people.reports_under(users, "sam", all_the_way_down=True) == {"lead.kiran", "jo", "ira"}
    assert people.reports_under(users, "loop.a", all_the_way_down=True) == {"loop.b"}
    assert people.reports_under(users, "self") == set() and people.reports_under(users, "") == set()


def test_leave_and_users_ask_the_reporting_rule(mock_db):
    from slate.core.domain.user_manager import UserManager
    from slate.core.infra.leave_repository import LeaveRepository
    um = UserManager(db=mock_db)
    um.add_user("sup.vikram", "password1", ["Supervisor"], "Vikram", "Comp")
    um.add_user("aarav", "password1", ["Artist"], "Aarav", "Comp", reports_to="sup.vikram")
    um.add_user("gone", "password1", ["Artist"], "Gone", "Comp", reports_to="SUP.VIKRAM",
                last_day="2000-01-01")
    assert LeaveRepository(mock_db).reports_to("sup.vikram") == {"aarav", "gone"}
    assert um.reports_of("sup.vikram") == ["aarav"]         # active people only


# ------------------------------------------------ a lead's department (item 4)
def test_the_grid_and_the_handler_share_the_department_scope(mock_db):
    from slate.core.domain import departments
    from slate.gui.tabs.vfx_dashboard_pro.core.sqlite_handler import SQLiteHandler
    from slate.gui.tabs.vfx_dashboard_pro.ui.dashboard_widget import DashboardWidget
    from types import SimpleNamespace
    assert departments.family_of("Head of Paint") == "prep"
    assert departments.family_of("Trainee") is None and departments.family_of(None) is None
    assert departments.scope_keys(["Supervisor"], "roto") is None        # not scoped
    assert departments.scope_keys(["Lead"], "") == set()                   # scoped, no department
    roto = departments.scope_keys(["Lead"], "roto")
    assert roto and all(departments.get_department(k).family == "roto" for k in roto)
    grid = SimpleNamespace(user_data={"job_title": "Roto Lead"}, user_roles=["Lead"])
    grid._department_family = lambda: DashboardWidget._detect_user_department_family(grid)
    handler = SQLiteHandler("P3", db_manager=mock_db, user_role=["Lead"],
                            department_family=grid._department_family())
    assert DashboardWidget._department_scope(grid) == handler._scoped_department_keys() == roto
    assert handler._family_name() == DashboardWidget._family_name("roto") == departments.family_name("roto")


# ---------------------------------------------- user_manager duplicates (item 6)
def test_the_account_columns_have_one_owner():
    import inspect
    from slate.core.domain.user_manager import UserManager
    from slate.core.infra.migrations.workplace_schema import COLUMNS
    owned = {c for t, c, _pg, _lite in COLUMNS if t == "ut_users"}
    assert {"must_change_password", "active", "deactivated_on", "deactivated_by"} <= owned
    assert "ALTER TABLE" not in inspect.getsource(UserManager._ensure_schema)
    assert not hasattr(UserManager, "load_users")
