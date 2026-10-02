"""
One rule for spaces in passwords (SHL-003, SHL-022, SHL-023).

Sign-in trimmed the password and changing it did not, so "secret99 " could be
saved and never typed again, and six spaces were accepted as a password and
locked the account out. Now outer spaces are trimmed everywhere a password is
typed or set, and a password that is empty or only spaces is refused.
"""
import pytest

from slate.core.domain import access, user_import


@pytest.fixture
def users(tmp_path, monkeypatch):
    from slate.core.infra.global_config import GlobalConfig
    import slate.core.infra.database_manager as db_module
    from slate.core.infra.sqlite_manager import SQLiteManager
    from slate.core.domain.user_manager import UserManager

    monkeypatch.setattr(GlobalConfig, "get_db_mode", classmethod(lambda cls: "sqlite"))
    SQLiteManager._instance = None
    manager = db_module.DatabaseManager(db_path=str(tmp_path / "users.db"))
    assert manager.active_mode == "sqlite"
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


def test_a_new_password_with_outer_spaces_can_be_typed_at_sign_in(users):
    users.add_user("tester", "OwnPass1", ["Artist"], "Tester", "")
    ok, _ = users.change_own_password("tester", "OwnPass1", "secret99 ")
    assert ok
    # Sign-in trims; so did the change: both spellings open the account.
    assert users.authenticate("tester", "secret99")
    assert users.authenticate("tester", " secret99 ")


def test_spaces_only_is_not_a_password(users):
    users.add_user("admin2", "OwnPass1", ["Artist"], "Admin Two", "")
    ok, message = users.change_own_password("admin2", "OwnPass1", "      ")
    assert not ok and "spaces" in message
    assert users.authenticate("admin2", "OwnPass1")            # still the old one
    with pytest.raises(ValueError):
        users.add_user("nobody", "   ", ["Artist"], "Nobody", "")
    assert users.password_problem("") and users.password_problem("     ")
    assert users.password_problem("abc") and not users.password_problem("  abcdef  ")


def test_the_forced_change_works_after_a_stray_space(users):
    users.add_user("artist2", "artist123", ["Artist"], "Artist", "")
    users.set_must_change_password("artist2", True)
    typed = " artist123 "
    assert users.authenticate("artist2", typed)
    ok, message = users.change_own_password("artist2", typed, "MyOwnPass9")
    assert ok, message
    assert users.authenticate("artist2", "MyOwnPass9")


def test_an_old_password_saved_with_spaces_still_opens_its_account(users):
    users.add_user("legacy", "placeholder", ["Artist"], "Legacy", "")
    # As an account saved before the rule: the hash of the untrimmed text.
    users._get_db().execute_update(
        "UPDATE ut_users SET password_hash=%s WHERE username=%s",
        (users._hash_password("old pass "), "legacy"))
    assert users.authenticate("legacy", "old pass ")
    assert not users.authenticate("legacy", "old pass")


def test_an_import_refuses_a_first_password_of_spaces(users, tmp_path):
    import csv
    path = tmp_path / "people.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([["asha", "Asha"]])
    plan = user_import.plan_import(path, [])
    with pytest.raises(ValueError):
        user_import.apply_import(users, plan, "Compositor", "        ")
    user_import.apply_import(users, plan, "Compositor", " Welcome@2026 ")
    assert users.authenticate("asha", "Welcome@2026")


def test_the_dialog_puts_the_length_rule_on_one_line(qtbot, users):
    from slate.gui.dialogs.change_password_dialog import ChangePasswordDialog
    users.add_user("asha", "OwnPass1", ["Artist"], "Asha", "")
    dialog = ChangePasswordDialog(users, "asha")
    qtbot.addWidget(dialog)
    dialog.current_input.setText("OwnPass1")
    dialog.new_input.setText("abc")
    dialog.repeat_input.setText("abc")
    dialog._save()
    assert "6 characters" in dialog.hint.text()
    assert dialog.error_label.isHidden() and "6 characters" not in dialog.error_label.text()

    dialog.new_input.setText("      ")
    dialog.repeat_input.setText("      ")
    dialog._save()
    assert "spaces" in dialog.hint.text()


def test_the_forced_dialog_cancels_plainly(qtbot, users):
    from slate.gui.dialogs.change_password_dialog import ChangePasswordDialog
    dialog = ChangePasswordDialog(users, "asha", forced=True, current_password="x")
    qtbot.addWidget(dialog)
    assert dialog.cancel_button.text() == "Cancel"
