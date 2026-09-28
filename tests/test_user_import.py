"""
Adding people from an Excel or CSV list, and choosing your own password.

The list only ever adds: somebody already in Slate is skipped, never changed.
Imported people must choose their own password at first sign-in; nobody else
is forced to.
"""
import csv

import pytest

from slate.core.domain import access
from slate.core.domain import user_import


@pytest.fixture
def users(tmp_path, monkeypatch):
    """A user store on a database of its own (see tests/test_roles_permissions.py)."""
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain.user_manager import UserManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "users.db"))
    assert manager.active_mode == "sqlite", "the fixture failed to isolate"
    with db_module._manager_lock:
        previous = db_module._manager_instance
        db_module._manager_instance = manager
    access.reset_cache()
    try:
        yield UserManager(db=manager)
    finally:
        with db_module._manager_lock:
            db_module._manager_instance = previous
        SQLiteManager._instance = None
        access.reset_cache()


def _csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


# ------------------------------------------------------------------ reading

def test_the_template_reads_back(tmp_path):
    template = user_import.write_template(tmp_path / "people.xlsx")
    from openpyxl import load_workbook
    book = load_workbook(template)
    book["People"].append(["Ravi.K", "Ravi Kumar"])
    book.save(template)
    plan = user_import.plan_import(template, existing_usernames=[])
    assert [(r.username, r.display_name, r.status) for r in plan.rows] == [("ravi.k", "Ravi Kumar", "new")]


def test_a_csv_with_headers_in_any_order(tmp_path):
    path = _csv(tmp_path / "people.csv", [["Name", "Username"], ["Asha Rao", "asha"], ["", ""], ["Dev M", "devm"]])
    plan = user_import.plan_import(path, existing_usernames=[])
    assert [(r.username, r.display_name) for r in plan.rows] == [("asha", "Asha Rao"), ("devm", "Dev M")]


def test_a_csv_without_headers_is_username_then_name(tmp_path):
    path = _csv(tmp_path / "people.csv", [["emp0101", "Ravi Kumar"], ["emp0102"]])
    plan = user_import.plan_import(path, existing_usernames=[])
    assert [(r.username, r.display_name) for r in plan.rows] == [("emp0101", "Ravi Kumar"), ("emp0102", "emp0102")]


def test_existing_duplicate_and_invalid_rows_are_skipped(tmp_path):
    path = _csv(tmp_path / "people.csv", [
        ["Username", "Display Name"],
        ["Admin", "Someone"],           # already exists (any case)
        ["asha", "Asha"],
        ["ASHA", "Asha again"],         # twice in the file
        ["bad name", "Spaces"],         # not a valid username
        ["x", "Too short"],
    ])
    plan = user_import.plan_import(path, existing_usernames=["admin"])
    assert [r.status for r in plan.rows] == ["exists", "new", "duplicate", "invalid", "invalid"]
    assert len(plan.to_create) == 1


def test_an_unsupported_file_is_refused(tmp_path):
    path = tmp_path / "people.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(ValueError):
        user_import.plan_import(path, existing_usernames=[])


# ------------------------------------------------------------------ applying

def test_import_creates_people_with_role_and_first_password(tmp_path, users):
    path = _csv(tmp_path / "people.csv", [["Username", "Display Name"], ["asha", "Asha Rao"], ["devm", "Dev M"]])
    plan = user_import.plan_import(path, list(users.get_all_users()))
    user_import.apply_import(users, plan, "Compositor", "Welcome@2026")

    assert [r.status for r in plan.rows] == ["created", "created"]
    record = users.get_all_users()["asha"]
    assert record["roles"] == ["Compositor"] and record["display_name"] == "Asha Rao"
    signed_in = users.authenticate("asha", "Welcome@2026")
    assert signed_in and signed_in["must_change_password"] is True


def test_import_never_changes_an_existing_person(tmp_path, users):
    users.add_user("asha", "OwnPass1", ["Comp Supervisor"], "Asha (real)", "Comp")
    path = _csv(tmp_path / "people.csv", [["Username", "Display Name"], ["ASHA", "Wrong Name"]])
    plan = user_import.plan_import(path, list(users.get_all_users()))
    user_import.apply_import(users, plan, "Compositor", "Welcome@2026")

    record = users.get_all_users()["asha"]
    assert record["display_name"] == "Asha (real)" and record["roles"] == ["Comp Supervisor"]
    assert users.authenticate("asha", "OwnPass1")


def test_import_needs_a_real_first_password(tmp_path, users):
    path = _csv(tmp_path / "people.csv", [["asha", "Asha"]])
    plan = user_import.plan_import(path, [])
    with pytest.raises(ValueError):
        user_import.apply_import(users, plan, "Compositor", "123")


def test_export_lists_everyone_without_passwords(tmp_path, users):
    users.add_user("asha", "OwnPass1", ["Compositor"], "Asha Rao", "Comp")
    out = user_import.export_users_csv(users, tmp_path / "users.csv")
    text = out.read_text(encoding="utf-8-sig")
    assert "asha" in text and "Asha Rao" in text and "Compositor" in text
    assert "password" not in text.lower() and "$2" not in text


# ------------------------------------------------------------------ passwords

def test_choosing_your_own_password_clears_the_flag(tmp_path, users):
    path = _csv(tmp_path / "people.csv", [["asha", "Asha"]])
    user_import.apply_import(users, user_import.plan_import(path, []), "Compositor", "Welcome@2026")

    ok, _ = users.change_own_password("asha", "Welcome@2026", "MyOwn#99")
    assert ok
    signed_in = users.authenticate("asha", "MyOwn#99")
    assert signed_in and signed_in["must_change_password"] is False
    assert not users.authenticate("asha", "Welcome@2026")


def test_changing_a_password_needs_the_current_one(users):
    users.add_user("asha", "OwnPass1", ["Compositor"], "Asha", "Comp")
    assert users.change_own_password("asha", "wrong", "NewPass22")[0] is False
    assert users.change_own_password("asha", "OwnPass1", "abc")[0] is False          # too short
    assert users.change_own_password("asha", "OwnPass1", "OwnPass1")[0] is False     # unchanged
    assert users.change_own_password("asha", "OwnPass1", "NewPass22")[0] is True


def test_people_added_by_hand_are_not_forced_to_change(users):
    users.add_user("asha", "OwnPass1", ["Compositor"], "Asha", "Comp")
    assert users.authenticate("asha", "OwnPass1")["must_change_password"] is False


# ------------------------------------------------------------------ the tab

def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_users_and_roles_shows_each_person_their_half(users, monkeypatch):
    _qapp()
    import slate.gui.tabs.admin_users_tab as tab_module
    monkeypatch.setattr(tab_module, "UserManager", lambda: users)
    from slate.gui.tabs.admin_users_tab import AdminUsersTab

    def titles(tab):
        return [tab.tabs.tabText(i).replace("&&", "&") for i in range(tab.tabs.count())]

    hr = AdminUsersTab(user_role="HR", user_data={"user_id": "hr1", "roles": ["HR"]})
    assert titles(hr) == ["Users", "Roles & Permissions"]

    it = AdminUsersTab(user_role="IT", user_data={"user_id": "it1", "roles": ["IT"]})
    assert titles(it) == ["Roles & Permissions"]

    artist = AdminUsersTab(user_role="Compositor", user_data={"user_id": "c1", "roles": ["Compositor"]})
    assert artist.users_panel is None and artist.role_editor is None


def test_admin_panel_no_longer_manages_users():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "slate" / "gui" / "admin_panel.py").read_text(encoding="utf-8")
    assert '"User Mgmt"' not in source and '"Permissions"' not in source


def test_roles_dropdown_keeps_every_role(users):
    """A dropdown, but with tick boxes: someone with two roles must keep both."""
    _qapp()
    from slate.gui.tabs.admin_users_tab import UserDialog
    users.add_user("mira", "pw1234", ["Team Lead", "Compositor"], "Mira", "Comp")
    record = users.get_all_users()["mira"]
    dialog = UserDialog(users, username="mira", record=record, all_usernames=["mira"])
    assert dialog.selected_roles() == ["Compositor", "Team Lead"]
    assert dialog.roles_input.currentText() == "Compositor, Team Lead"

    new = UserDialog(users, all_usernames=[])
    assert new.selected_roles() == []           # nothing ticked - never Developer by default
    new.roles_input.set_checked(["roto artist"])
    assert new.selected_roles() == ["Roto Artist"]
