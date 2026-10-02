"""
Roles & Permissions (audit 2026-09): removing Full access keeps only the
role's own ticks (HR-123), keyboard selection switches the role (HR-140),
ticks wait for Save / Revert (HR-141), Delete waits for a role (HR-152),
counts and wording (HR-153), Rename (HR-154).
"""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def users(mock_db, app):
    from slate.core.domain import access
    from slate.core.domain.user_manager import UserManager
    access.reset_cache()
    manager = UserManager(db=mock_db)
    yield manager
    access.reset_cache()


def _editor(users):
    from slate.gui.role_editor import RoleEditor
    return RoleEditor(users)


def _row_of(editor, role):
    from slate.gui.role_editor import ROLE_NAME_ROLE
    for row in range(editor.role_list.count()):
        if editor.role_list.item(row).data(ROLE_NAME_ROLE) == role:
            return row
    raise KeyError(role)


def test_removing_full_access_keeps_only_the_roles_own_ticks(users):
    users.update_role_permissions("Runner", ["ALL", "HRMS"])
    editor = _editor(users)
    editor.refresh_roles(select="Runner")
    editor.cb_all.setChecked(False)                 # the question is answered Yes in tests
    assert editor.save_changes()
    assert users.role_permissions("Runner") == ["HRMS"]


def test_keyboard_selection_switches_the_role(users):
    editor = _editor(users)
    editor.role_list.setCurrentRow(_row_of(editor, "Artist"))
    assert editor.current_role == "Artist"
    editor.role_list.setCurrentRow(_row_of(editor, "Lead"))
    assert editor.current_role == "Lead"


def test_ticks_wait_for_save_and_revert_puts_them_back(users):
    editor = _editor(users)
    editor.refresh_roles(select="Artist")
    before = users.role_permissions("Artist")
    box = editor.tab_boxes["Bidding"]
    box.setChecked(True)
    assert editor.has_unsaved_changes() and editor.btn_save.isEnabled()
    assert users.role_permissions("Artist") == before
    editor.revert()
    assert not box.isChecked() and not editor.has_unsaved_changes()


def test_delete_and_rename_wait_for_a_role(users):
    editor = _editor(users)
    assert not editor.btn_delete.isEnabled() and not editor.btn_rename.isEnabled()
    editor.refresh_roles(select="Artist")
    assert editor.btn_delete.isEnabled()
    editor.refresh_roles(select="Developer")
    assert not editor.btn_delete.isEnabled(), "Developer cannot be deleted"


def test_counts_read_cleanly_and_the_hr_key_is_unchanged(users):
    from slate.core.domain import permissions_catalog as catalog
    users.add_user("a1", "pw1234", ["Artist"], "A", "Comp")
    editor = _editor(users)
    text = editor.role_list.item(_row_of(editor, "Artist")).text()
    assert "   " not in text and text.startswith("Artist (")
    hr = next(t for t in catalog.TABS if t.key == "HRMS")
    assert hr.label == "HR (people, leave, joining)"


def test_rename_from_the_screen(users, monkeypatch):
    users.update_role_permissions("Runner", ["Settings"])
    editor = _editor(users)
    editor.refresh_roles(select="Runner")
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText",
                        lambda *a, **k: ("Production Runner", True))
    editor.rename_role()
    assert users.role_exists("Production Runner") and editor.current_role == "Production Runner"
