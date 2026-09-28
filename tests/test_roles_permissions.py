"""
Roles, the tabs they open and what they may do.

Each test is a way this went wrong, or would have: a permission screen that
fell behind the sidebar, an edit that wiped what the screen did not show, a
person with an unknown role seeing every tab, a deleted role coming back.
"""
import json
import re
from pathlib import Path

import pytest

from slate.core.domain import access
from slate.core.domain import permissions_catalog as catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A database of its own - the same isolation as tests/test_holiday_calendar.py."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "roles.db"))
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
    return UserManager(db=db)


# ------------------------------------------------------------- the catalogue

def test_every_permission_the_sidebar_checks_can_be_granted():
    """The screen had its own list of eleven and fell behind the sidebar."""
    source = (ROOT / "slate" / "gui" / "components" / "main_window_builder.py").read_text(encoding="utf-8")
    used = set(re.findall(r'permission_key="([^"]+)"', source))
    assert used, "no permission keys found - has the sidebar moved?"
    assert used <= catalog.TAB_KEYS, f"not grantable on the Permissions screen: {used - catalog.TAB_KEYS}"


def test_studio_roles_use_only_known_keys():
    for role, perms in catalog.STUDIO_ROLES.items():
        for p in perms:
            if p.startswith(catalog.ABILITY_PREFIX):
                assert p[len(catalog.ABILITY_PREFIX):] in catalog.ABILITY_KEYS, (role, p)
            else:
                assert p in catalog.TAB_KEYS, (role, p)


def test_the_requested_roles_exist_and_old_ones_stay():
    wanted = {"IT", "HR", "Admin", "Production Head", "Production Coordinator",
              "Roto Prep Supervisor", "Comp Supervisor", "Team Lead", "Roto Artist",
              "Paint Artist", "Deage Artist", "AI Artist", "Compositor", "DMP", "CG", "Editor"}
    assert wanted <= set(catalog.STUDIO_ROLES)


# --------------------------------------------------------------- seeding

def test_new_roles_are_created_beside_the_old_ones(users):
    roles = {r.lower() for r in users.get_available_roles()}
    for role in ["developer", "supervisor", "coordinator", "lead", "artist", "tester"]:
        assert role in roles
    for role in catalog.STUDIO_ROLES:
        assert role.lower() in roles


def test_a_deleted_default_role_stays_deleted(db, users):
    from slate.core.domain.user_manager import UserManager
    assert users.delete_role("DMP")
    UserManager(db=db)                          # the next start
    assert not users.role_exists("DMP")


def test_a_customised_role_is_not_overwritten_at_start(db, users):
    from slate.core.domain.user_manager import UserManager
    users.update_role_permissions("Compositor", ["Settings"])
    UserManager(db=db)
    assert users.role_permissions("Compositor") == ["Settings"]


def test_a_role_in_use_cannot_be_deleted(users):
    users.add_user("mira", "pw", ["Compositor"], "Mira", "Comp")
    assert users.users_with_role("compositor") == ["mira"]
    assert not users.delete_role("Compositor")
    assert users.role_exists("Compositor")


# ------------------------------------------------------------- tab access

def test_unknown_roles_open_nothing(qapp_like):
    """An empty permission list used to skip the check and open every tab."""
    from slate.gui.components.tab_coordinator import TabCoordinator
    from PySide6.QtWidgets import QListWidget, QStackedWidget, QWidget
    host = QWidget()
    coordinator = TabCoordinator(host, QListWidget(), QStackedWidget())
    assert not coordinator.register_tab_factory("Admin Panel", QWidget, permission_key="Admin Panel",
                                                user_role="Ghost", allowed_tabs=[])
    assert not coordinator.register_tab_factory("Admin Panel", QWidget, permission_key="Admin Panel",
                                                user_role=None, allowed_tabs=None)
    assert coordinator.register_tab_factory("Home", QWidget, permission_key=None,
                                            user_role="Ghost", allowed_tabs=[])


def test_the_new_roles_open_their_tabs(users):
    assert set(users.get_allowed_tabs(["Compositor"])) >= {"Dashboard", "Stock Browser", "Settings"}
    assert "Admin Panel" not in users.get_allowed_tabs(["Compositor"])
    assert "IT" in users.get_allowed_tabs(["IT"])
    assert "HRMS" in users.get_allowed_tabs(["HR"])


# ------------------------------------------------------------- abilities

def test_ticked_abilities_are_honoured(users):
    """Role names alone matched nothing for the new roles."""
    assert access.can_edit_own_status(["Roto Artist"])
    assert not access.can_edit_dashboard(["Roto Artist"])
    assert access.can_edit_dashboard(["Comp Supervisor"])
    assert access.can(["Team Lead"], "approve_leave")
    assert access.can(["HR"], "manage_users")
    assert access.can(["IT"], "manage_it")


def test_supervisors_and_leads_edit_only_their_department(users):
    assert access.is_department_scoped(["Comp Supervisor"])
    assert access.is_department_scoped(["Roto Prep Supervisor"])
    assert access.is_department_scoped(["Team Lead"])
    assert not access.is_department_scoped(["Production Head"])
    # a wider role alongside lifts the limit, as before
    assert not access.is_department_scoped(["Team Lead", "Production Head"])


def test_full_access_grants_abilities_but_not_the_department_limit(users):
    assert access.can(["Developer"], "manage_permissions")
    assert not access.is_department_scoped(["Developer"])


def test_old_roles_keep_working_by_name(users):
    assert access.can_edit_dashboard(["Supervisor"])
    assert access.is_department_scoped(["Lead"])
    assert access.can(["Supervisor"], "approve_leave")


def test_who_may_edit_permissions(users):
    for role in ["Admin", "IT", "HR", "Developer"]:
        assert access.can([role], "manage_permissions"), role
    for role in ["Supervisor", "Compositor", "Production Head"]:
        assert not access.can([role], "manage_permissions"), role


# ------------------------------------------------------------- the screen

def test_saving_keeps_what_the_screen_does_not_show(users, qapp_like):
    """Ticking one box used to rewrite the list and drop everything unseen."""
    from slate.gui.role_editor import RoleEditor
    users.update_role_permissions("Artist", ["Dashboard", "Image Editor", "can:dashboard_write"])
    editor = RoleEditor(users)
    editor.refresh_roles(select="Artist")
    editor.tab_boxes["Settings"].setChecked(True)          # saves
    stored = users.role_permissions("Artist")
    assert "Image Editor" in stored and "Settings" in stored and "Dashboard" in stored
    assert "can:dashboard_write" in stored


def test_developer_cannot_be_changed_on_the_screen(users, qapp_like):
    from slate.gui.role_editor import RoleEditor
    editor = RoleEditor(users)
    editor.refresh_roles(select="Developer")
    assert not editor.cb_all.isEnabled()
    assert editor.cb_all.isChecked()


def test_someone_without_the_right_sees_it_read_only(users, qapp_like):
    from slate.gui.role_editor import RoleEditor
    users.add_user("sup", "pw", ["Supervisor"], "Sup", "Comp")
    editor = RoleEditor(users, editor_username="sup")
    assert not editor.can_edit
    editor.refresh_roles(select="Compositor")
    assert not any(cb.isEnabled() for cb in editor.tab_boxes.values())


@pytest.fixture
def qapp_like():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
