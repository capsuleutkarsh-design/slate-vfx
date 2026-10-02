"""
Who may give whom what, the new abilities, and accounts that end.

Each test is a hole the audit found: HR ticking Full access on their own role,
a supervisor creating a Developer account, a role losing a right on upgrade,
a deleted person's leave still sitting in the queue.
"""
import json
from datetime import date, timedelta

import pytest

from slate.core.domain import access
from slate.core.domain import permissions_catalog as catalog


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A database of its own - the same isolation as tests/test_roles_permissions.py."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "perms.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    access.reset_cache()
    try:
        yield manager
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None
        access.reset_cache()


@pytest.fixture
def users(db):
    from slate.core.domain.user_manager import UserManager
    manager = UserManager(db=db)
    manager.add_user("hr.meera", "pw", ["HR"], "Meera", "HR")
    manager.add_user("sup.rajesh", "pw", ["Supervisor"], "Rajesh", "Comp")
    manager.add_user("it.sam", "pw", ["IT"], "Sam", "IT")
    manager.add_user("kabir", "pw", ["Compositor"], "Kabir Nair", "Comp")
    access.reset_cache()
    return manager


# ------------------------------------------------------------- new abilities

NEW_ABILITIES = ("assignable", "dashboard_view_all", "schedule_write", "approve_bid",
                 "delete_project", "view_licences", "manage_system", "studio_settings",
                 "tester_destructive")


@pytest.mark.parametrize("ability", NEW_ABILITIES)
def test_new_abilities_are_declared_and_tickable(ability):
    assert ability in catalog.ABILITY_KEYS
    assert access.roles_for(ability), f"{ability} has no default roles"


def test_defaults_follow_the_decisions(users):
    assert access.can(["Production Head"], "schedule_write")
    assert access.can(["Production Coordinator"], "schedule_write")
    assert access.can(["Production Head"], "approve_bid")
    assert not access.can(["Production Coordinator"], "approve_bid")
    assert access.can(["Compositor"], "assignable")
    assert access.can(["Team Lead"], "assignable")
    assert not access.can(["Supervisor"], "assignable")
    assert access.can(["Supervisor"], "dashboard_view_all")
    assert access.can(["Production Head"], "view_licences")
    assert access.can(["Admin"], "manage_system") and not access.can(["Supervisor"], "manage_system")
    assert access.can(["IT"], "studio_settings") and not access.can(["Artist"], "studio_settings")
    assert access.can(["Developer"], "tester_destructive")
    assert not access.can(["Tester"], "tester_destructive")
    assert access.can(["Producer"], "dashboard_write")
    assert access.can_delete_project(["Admin"]) and not access.can_delete_project(["Coordinator"])


def test_compositor_sees_own_shots_but_supervisor_sees_all(users):
    """Only the literal 'Artist' role used to be limited to its own shots."""
    assert not access.can_view_all_shots(["Compositor"])
    assert not access.can_view_all_shots(["Roto Artist"])
    assert access.can_view_all_shots(["Supervisor"])
    assert access.can_view_all_shots(["Editor"])


def test_supervisors_no_longer_manage_users_or_see_everyones_attendance(users):
    assert not access.can(["Supervisor"], "manage_users")
    assert not access.can(["Supervisor"], "view_team_attendance")
    assert access.can(["Supervisor"], "approve_leave")        # their team's queue stays


# ------------------------------------------------------- the one-time upgrade

def test_upgrade_adds_and_never_removes(db):
    """A role that could edit the schedule or decide bids keeps doing so."""
    from slate.core.domain.user_manager import UserManager
    db.execute_update("CREATE TABLE IF NOT EXISTS ut_roles (role_name TEXT PRIMARY KEY, permissions TEXT)")
    db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                      ("Line Producer", json.dumps(["Scheduling", "Bidding", "Dashboard"])))
    db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                      ("IT Intern", json.dumps(["IT", "Settings"])))
    db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                      ("FX Artist", json.dumps(["Dashboard", "can:artist_own_status"])))
    manager = UserManager(db=db)
    producer = manager.role_permissions("Line Producer")
    assert {"Scheduling", "Bidding", "Dashboard"} <= set(producer)
    assert "can:schedule_write" in producer and "can:approve_bid" in producer
    assert "can:dashboard_view_all" in producer
    assert "can:manage_it" in manager.role_permissions("IT Intern")
    assert "can:assignable" in manager.role_permissions("FX Artist")


def test_upgrade_runs_once_and_leaves_later_edits_alone(db):
    from slate.core.domain.user_manager import UserManager
    manager = UserManager(db=db)
    manager.update_role_permissions("Production Coordinator", ["Scheduling", "Settings"])
    UserManager(db=db)                              # the next start
    assert manager.role_permissions("Production Coordinator") == ["Scheduling", "Settings"]


def test_upgraded_permissions_only_adds():
    before = ["Scheduling", "Image Editor", "can:dashboard_write"]
    after = __import__("slate.core.domain.user_manager", fromlist=["UserManager"]) \
        .UserManager.upgraded_permissions("Coordinator", before)
    assert after[:len(before)] == before


# ------------------------------------------------------------ granting rules

def test_hr_cannot_tick_full_access_or_sensitive_abilities(users):
    """As HR, ticking Full access on the HR role saved ['ALL', ...]."""
    hr = ["HR"]
    assert not access.can_grant(hr, ["ALL"])
    assert not access.can_grant(hr, ["can:wipe_fleet_caches"])
    assert not access.can_grant(hr, ["can:manage_system"])
    assert access.can_grant(hr, ["HRMS", "Settings", "can:manage_leave"])   # what HR holds
    assert not access.can_grant(hr, ["Dashboard"])                          # what HR does not
    assert access.can_grant(["Developer"], ["ALL", "can:manage_system"])


def test_nobody_but_admin_changes_a_role_they_hold(users):
    why = access.role_change_refusal(["HR"], "HR", users.role_permissions("HR"),
                                     users.role_permissions("HR") + ["ALL"])
    assert "hold" in why
    assert access.role_change_refusal(["Admin"], "Admin", ["Settings"], ["Settings", "ALL"]) == ""


def test_hr_cannot_change_a_more_powerful_role(users):
    why = access.role_change_refusal(["HR"], "Admin", users.role_permissions("Admin"), ["Settings"])
    assert why


def test_the_manager_refuses_even_when_the_screen_is_bypassed(users):
    from slate.core.domain.access import GrantRefused
    users.set_acting_user("hr.meera")
    with pytest.raises(GrantRefused):
        users.update_role_permissions("HR", users.role_permissions("HR") + ["ALL"])
    with pytest.raises(GrantRefused):
        users.update_role_permissions("Compositor", ["Dashboard", "can:manage_system"])
    assert "ALL" not in users.role_permissions("HR")


def test_supervisor_cannot_create_a_developer(users):
    """Rajesh (Supervisor) created 'sneaky.dev' with role Developer."""
    from slate.core.domain.access import GrantRefused
    users.set_acting_user("sup.rajesh")
    with pytest.raises(GrantRefused):
        users.add_user("sneaky.dev", "pw", ["Developer"], "Sneaky", "Dev")
    assert "sneaky.dev" not in users.get_all_users()


def test_hr_gives_ordinary_roles_but_not_admin_ones(users):
    users.set_acting_user("hr.meera")
    offered = {r.lower() for r in users.assignable_roles()}
    assert {"compositor", "artist", "supervisor", "team lead", "hr"} <= offered
    assert not offered & {"developer", "admin", "it"}
    assert users.add_user("new.hire", "pw", ["Compositor"], "New Hire", "Comp")


def test_hr_cannot_reset_a_developers_password(users):
    from slate.core.domain.access import GrantRefused
    users.set_acting_user("hr.meera")
    with pytest.raises(GrantRefused):
        users.add_user("admin", "taken-over", ["Developer"], "System Admin", "Dev")


def test_role_editor_disables_what_the_editor_cannot_give(users, qapp):
    from slate.gui.role_editor import RoleEditor
    users.update_role_permissions("Runner", ["Settings"])
    editor = RoleEditor(users, editor_username="hr.meera")
    editor.refresh_roles(select="Runner")
    assert not editor.cb_all.isEnabled()
    assert not editor.ability_boxes["manage_system"].isEnabled()
    assert not editor.tab_boxes["Dashboard"].isEnabled()
    assert editor.tab_boxes["HRMS"].isEnabled()
    editor.refresh_roles(select="HR")                       # their own role
    assert not any(cb.isEnabled() for cb in editor.tab_boxes.values())


# -------------------------------------------------------- account lifecycle

def test_deactivate_keeps_history_and_hides_the_person(users, db):
    users.set_acting_user("hr.meera")
    ok, message = users.deactivate_user("kabir")
    assert ok, message
    assert not users.is_active("kabir")
    assert "kabir" in users.get_all_users()                       # still there
    assert "kabir" not in users.active_users()
    ok, _ = users.reactivate_user("kabir")
    assert ok and users.is_active("kabir")


def test_you_cannot_deactivate_or_delete_yourself(users):
    users.set_acting_user("hr.meera")
    ok, message = users.deactivate_user("hr.meera")
    assert not ok and "own" in message
    assert not users.delete_user("hr.meera")


def test_open_leave_blocks_deactivation(users, db):
    from slate.core.infra.migrations.workplace_schema import apply_migration
    apply_migration(db)
    db.execute_update(
        "INSERT INTO leave_requests (user_id, type, start_date, end_date, status) "
        "VALUES (%s, %s, %s, %s, %s)", ("kabir", "Casual", "2026-10-01", "2026-10-02", "Pending HR"))
    ok, message = users.deactivate_user("kabir", by="hr.meera")
    assert not ok and "leave" in message


def test_a_passed_last_day_counts_as_inactive(users, db):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    users.update_user("kabir", last_day=yesterday)
    assert not users.is_active("kabir")
    assert users.get_all_users()["kabir"]["active"] is False


def test_delete_is_refused_for_anybody_with_history(users, db):
    db.execute_update(
        "CREATE TABLE IF NOT EXISTS attendance_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id TEXT NOT NULL, day_date TEXT NOT NULL, punch_in TEXT, punch_out TEXT, "
        "pc_name TEXT DEFAULT '', metadata TEXT DEFAULT '{}')")
    db.execute_update("INSERT INTO attendance_log (user_id, day_date) VALUES (%s, %s)",
                      ("kabir", "2026-09-01"))
    assert not users.delete_user("kabir")
    assert "Deactivate" in users.last_error
    users.add_user("typo.account", "pw", ["Artist"], "Typo", "")
    assert users.delete_user("typo.account")


def test_leave_stops_accruing_after_the_last_day():
    from slate.core.domain.leave_policy import accrued_by
    joined = date(2026, 1, 1)
    full = accrued_by(date(2026, 12, 31), joined)
    left = accrued_by(date(2026, 12, 31), joined, left=date(2026, 6, 30))
    assert left < full
    assert left == accrued_by(date(2026, 6, 30), joined)


def test_joining_is_refused_for_a_deactivated_account(users, db):
    """Ticking a joining list for somebody Slate treats as gone changes nothing."""
    from slate.core.infra.migrations.workplace_schema import apply_migration
    from slate.core.domain.onboarding_service import (
        InactivePerson, LEAVING, JOINING, OnboardingService)
    apply_migration(db)
    users.deactivate_user("kabir", by="hr.meera")
    service = OnboardingService(db=db)
    with pytest.raises(InactivePerson):
        service.start("kabir", JOINING)
    # A person who has left may still have kit to collect.
    assert service.start("kabir", LEAVING) > 0
    users.reactivate_user("kabir", by="hr.meera")
    assert service.start("kabir", JOINING) > 0


def test_machine_and_task_pickers_leave_out_leavers(users, db):
    from slate.core.infra.migrations.workplace_schema import apply_migration
    from slate.core.domain.onboarding_service import OnboardingService
    apply_migration(db)
    users.deactivate_user("kabir", by="hr.meera")
    service = OnboardingService(db=db)
    everybody = {p["username"]: p["active"] for p in service.people()}
    assert everybody["kabir"] is False and everybody["hr.meera"] is True
    assert "kabir" not in {p["username"] for p in service.people(active_only=True)}


def test_year_end_leaves_out_people_who_left_before_the_year(users, db):
    from slate.core.infra.migrations.workplace_schema import apply_migration
    from slate.core.infra.leave_repository import LeaveRepository
    apply_migration(db)
    users.update_user("kabir", last_day="2025-03-31")
    users.update_user("it.sam", last_day="2026-06-30")     # left during the year
    names = {row["user_id"] for row in LeaveRepository(db=db).preview_close(2026)}
    assert "kabir" not in names
    assert "it.sam" in names and "hr.meera" in names


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
