"""
The sign-in window: it opens, explains itself, recovers from a database that
was down, and treats a password the same way everywhere.
"""
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLineEdit

from slate.gui import login_dialog as login_module
from slate.gui.login_dialog import LoginDialog, friendly_auth_error, version_text


class FakeUsers:
    MIN_PASSWORD_LENGTH = 6

    def __init__(self, user=None, fresh=False):
        self.user = user
        self.fresh = fresh
        self.calls = []

    def authenticate(self, username, password):
        self.calls.append((username, password))
        return dict(self.user) if self.user else None

    def is_fresh_seed(self):
        return self.fresh


class FakeContext:
    def __init__(self, users=None, fail_times=0):
        self.users = users or FakeUsers()
        self.fail_times = fail_times

    def user_manager(self):
        if self.fail_times:
            self.fail_times -= 1
            from slate.core.infra.postgres_manager import DatabaseUnavailableError
            raise DatabaseUnavailableError("down")
        return self.users


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """The remembered user name goes to a scratch file, never the registry."""
    store = QSettings(str(tmp_path / "login.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(login_module, "login_settings", lambda: store)
    return store


def _dialog(qtbot, settings, users=None, **kw):
    dialog = LoginDialog(users or FakeUsers(), app_context=FakeContext(users), **kw)
    qtbot.addWidget(dialog)
    return dialog


def test_the_window_opens(qtbot, settings):
    # The colour tokens were read before Gate was imported: NameError, no window.
    dialog = _dialog(qtbot, settings)
    dialog.show()
    assert dialog.isVisible()


def test_a_long_message_wraps_and_the_window_grows(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.show()
    short = dialog.height()
    dialog.show_error("This studio's database is new and has no accounts yet. Sign in as the "
                      "administrator to create them, then change that password from the Users tab.")
    qtbot.wait(10)
    assert dialog.status_lbl.wordWrap()
    assert dialog.height() > short
    label = dialog.status_lbl
    assert label.height() >= label.heightForWidth(label.width()) - 1


def test_progress_is_not_an_error(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.user_input.setText("artist")
    dialog.pass_input.setText("artist123")
    dialog.handle_login()
    assert dialog.status_lbl.property("state") == "info"
    qtbot.waitUntil(lambda: dialog.auth_worker is None, timeout=3000)
    # FakeUsers refused it: now, and only now, it is an error.
    assert dialog.status_lbl.property("state") == "error"


def test_disabled_controls_look_disabled(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    sheet = dialog.styleSheet()
    assert "QPushButton#SignInBtn:disabled" in sheet and "QLineEdit:disabled" in sheet


def test_labels_say_user_name_and_the_empty_name_gets_the_caret(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.show()
    assert dialog.user_label.text() == "User name"
    dialog.user_input.setText("")
    dialog.pass_input.setText("whatever")
    dialog.handle_login()
    assert dialog.status_lbl.text() == "Enter your user name and password."
    qtbot.waitUntil(lambda: dialog.focusWidget() is dialog.user_input, timeout=1000)


def test_operations_says_operations(qtbot, settings):
    dialog = _dialog(qtbot, settings, app_mode="ops")
    text = dialog.subtitle.text()
    assert "Operations" in text and "VFX" not in text and "  " not in text
    assert version_text("BETA 2.0.32") == "Version 2.0.32 beta"


def test_a_remembered_name_puts_the_caret_in_the_password(qtbot, settings):
    settings.setValue("last_login_user", "artist")
    dialog = _dialog(qtbot, settings)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.focusWidget() is dialog.pass_input, timeout=1000)


def test_errors_are_sentences_not_driver_text():
    for raw in ("psycopg2.OperationalError: could not connect to server: Connection refused",
                "KeyError: 'password_hash'"):
        text = friendly_auth_error(raw)
        assert "psycopg2" not in text and "System Error" not in text and "KeyError" not in text


def test_show_password_and_controls_are_drawn(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.show_password_action.trigger()
    assert dialog.pass_input.echoMode() == QLineEdit.EchoMode.Normal
    dialog.show_password_action.trigger()
    assert dialog.pass_input.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.close_btn.text() == "" and not dialog.close_btn.icon().isNull()
    assert dialog.close_btn.toolTip() == "Close Slate"
    assert dialog.reconfigure_btn.text() == "Reconfigure server / database"
    assert not dialog.reconfigure_btn.icon().isNull()
    assert not (dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert dialog.btn_login.text() == "Sign in"


def test_the_account_name_is_remembered_not_what_was_typed(qtbot, settings):
    users = FakeUsers(user={"user_id": "admin", "display_name": "Admin"})
    dialog = _dialog(qtbot, settings, users=users)
    dialog.user_input.setText("  Admin ")
    dialog._on_auth_result({"user_id": "admin", "display_name": "Admin"}, "")
    assert settings.value("last_login_user") == "admin"


def test_try_again_once_the_database_is_back(qtbot, settings, monkeypatch):
    users = FakeUsers()
    context = FakeContext(users, fail_times=2)
    monkeypatch.setattr(LoginDialog, "_reload_database", staticmethod(lambda: None))
    dialog = LoginDialog(app_context=context)
    qtbot.addWidget(dialog)
    dialog.show()
    # The window is up at once; the database is reached behind it.
    assert dialog.btn_login.text() == "Connecting…" and not dialog.btn_login.isEnabled()
    qtbot.waitUntil(lambda: dialog.database_unavailable, timeout=3000)
    assert not dialog.btn_login.isEnabled() and dialog.retry_btn.isVisible()

    dialog.retry_database()                          # still down
    qtbot.waitUntil(lambda: dialog.retry_btn.isEnabled() and not dialog.is_connecting(), timeout=3000)
    assert dialog.retry_btn.isVisible() and dialog.status_lbl.property("state") == "error"
    with qtbot.waitSignal(dialog.database_ready, timeout=3000):
        dialog.retry_database()                      # back
    assert dialog.user_manager is users
    assert dialog.btn_login.isEnabled() and dialog.user_input.isEnabled()
    assert not dialog.status_lbl.isVisible() and not dialog.retry_btn.isVisible()


def test_working_on_the_local_copy_is_said_on_the_window(qtbot, settings):
    class LocalContext(FakeContext):
        def db_manager(self):
            return type("Db", (), {"is_local_mode": lambda self: True})()

    dialog = LoginDialog(app_context=LocalContext())
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.database_ready, timeout=3000):
        pass
    assert dialog.btn_login.isEnabled()
    assert dialog.status_lbl.text() == login_module.OFFLINE_TEXT
    assert dialog.status_lbl.alignment() & Qt.AlignmentFlag.AlignLeft   # a paragraph, left-aligned


def test_escape_does_not_quit(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.show()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible() and dialog.result() == 0


def test_a_message_does_not_move_the_sign_in_button(qtbot, settings):
    dialog = _dialog(qtbot, settings)
    dialog.show()
    qtbot.wait(10)
    before = dialog.btn_login.mapTo(dialog, dialog.btn_login.rect().topLeft())
    dialog.show_error("That user name and password do not match. Try again.")
    qtbot.wait(10)
    assert dialog.btn_login.mapTo(dialog, dialog.btn_login.rect().topLeft()) == before


def test_the_forced_change_gets_the_password_that_signed_in(qtbot, settings, monkeypatch):
    users = FakeUsers(user={"user_id": "artist", "must_change_password": True})
    dialog = _dialog(qtbot, settings, users=users)
    seen = {}

    class Recorder:
        class DialogCode:
            Accepted = 1

        def __init__(self, manager, username, forced=False, current_password=None, parent=None,
                     display_name=""):
            seen["name"] = display_name
            seen["current"] = current_password

        def exec(self):
            return 1

    import slate.gui.dialogs.change_password_dialog as cpd
    monkeypatch.setattr(cpd, "ChangePasswordDialog", Recorder)
    dialog.user_input.setText("artist")
    dialog.pass_input.setText(" artist123 ")
    dialog.handle_login()
    qtbot.waitUntil(lambda: "current" in seen, timeout=3000)
    assert seen["current"] == " artist123 "
    assert users.calls[-1][1] == " artist123 "


def test_the_caps_lock_hint_goes_away_when_caps_lock_is_turned_off(qtbot, settings, monkeypatch):
    """It stayed on screen: the Caps Lock key itself does not always reach the field."""
    state = {"on": True}
    monkeypatch.setattr(LoginDialog, "caps_lock_on", staticmethod(lambda: state["on"]))
    dialog = _dialog(qtbot, settings)
    dialog.show()
    # The offscreen test screen never gives a window real focus: say it has it.
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    monkeypatch.setattr(dialog.pass_input, "hasFocus", lambda: True)
    dialog.eventFilter(dialog.pass_input, QFocusEvent(QEvent.Type.FocusIn))
    qtbot.waitUntil(lambda: dialog.caps_hint.isVisible(), timeout=2000)
    state["on"] = False                     # no key event at all
    qtbot.waitUntil(lambda: not dialog.caps_hint.isVisible(), timeout=2000)
