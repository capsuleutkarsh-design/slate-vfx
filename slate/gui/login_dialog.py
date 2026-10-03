from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QLayout,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QPoint, QThread, Signal, QSettings, QTimer, QEvent
from PySide6.QtGui import QColor, QAction

from ..core.infra.app_context import AppContext
from ..core.infra.global_config import GlobalConfig
# Imported here, at the top. The colour tokens are read by the stylesheet in
# __init__, before setup_ui runs - with the import only inside setup_ui the
# sign-in window raised NameError and never opened at all.
from ..core.infra.gate import Gate
from .. import __version__ as APP_VERSION
import logging


class LoginAuthWorker(QThread):
    """Background authenticator so the login dialog stays responsive."""

    auth_result = Signal(object, str)  # user_data, error_text

    def __init__(self, user_manager, username: str, password: str):
        super().__init__()
        self.user_manager = user_manager
        self.username = username
        self.password = password

    def run(self):
        try:
            user = self.user_manager.authenticate(self.username, self.password)
            self.auth_result.emit(user, "")
        except Exception as exc:
            self.auth_result.emit(None, str(exc))


def _database_unavailable():
    """The error meaning the database is not there, or nothing if it cannot be imported."""
    try:
        from slate.core.infra.postgres_manager import DatabaseUnavailableError
        return DatabaseUnavailableError
    except Exception:                       # pragma: no cover - import guard
        return ()


# What each application is called under the name on the sign-in window. The
# window had no idea which application opened it, so Slate Operations said
# "VFX Production" too.
APP_NAMES = {"vfx": "VFX", "ops": "Operations", "all": "Studio"}


def login_settings() -> QSettings:
    """Where the last user name is remembered (one function, so tests can swap it)."""
    return QSettings("UTStudio", "Slate")


def version_text(raw: str = APP_VERSION) -> str:
    """'BETA 2.0.32' -> 'Version 2.0.32 beta'; '2.1.0' -> 'Version 2.1.0'."""
    words = str(raw or "").split()
    tags = [w.lower() for w in words if not any(ch.isdigit() for ch in w)]
    numbers = [w for w in words if any(ch.isdigit() for ch in w)]
    text = "Version " + (" ".join(numbers) or str(raw or "").strip())
    if tags:
        text += " " + " ".join(tags)
    return text


def friendly_auth_error(error_text: str) -> str:
    """
    A sentence somebody at the sign-in screen can act on, for an error raised
    while checking their password. The raw text goes to the log; it used to be
    shown as "System Error: <psycopg2 message>".
    """
    text = str(error_text or "").lower()
    unreachable = ("could not connect", "connection refused", "unavailable",
                   "server closed", "timeout", "timed out", "no route", "network",
                   "could not translate host", "operationalerror", "connection")
    if any(word in text for word in unreachable):
        return "Cannot reach the studio database right now. Try again in a moment."
    return "Something went wrong signing in. Try again - the details are in the log."


class LoginDialog(QDialog):
    def __init__(self, user_manager=None, app_context=None, app_mode: str = "all"):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.app_mode = str(app_mode or "all").lower()

        # Building the user manager runs the schema migration, which reads from
        # the database. With the database down that raised here - inside the
        # constructor of the first window the application opens - so there was
        # no window to put a message in and the whole program died with a stack
        # trace before anybody saw anything.
        #
        # The dialog is built either way now. Without a database it cannot
        # authenticate, so it says so and offers Try again and Reconfigure.
        self.database_unavailable = False
        try:
            self.user_manager = user_manager or self.app_context.user_manager()
        except _database_unavailable() as exc:
            logging.warning("Login: the database did not answer: %s", exc)
            self.user_manager = None
            self.database_unavailable = True

        self.user_data = None
        self.auth_worker = None
        self._is_closing = False
        self._reconfigure_in_progress = False  # Guard flag to prevent accidental auto-opening
        # The password exactly as it was checked, so the forced first change
        # is given the same value that signed in (see _on_auth_result).
        self._password_checked = ""
        self.settings = login_settings()

        self.setWindowTitle("Slate - Sign in")
        self.setMinimumWidth(380)
        # Not always on top: it hid the e-mail or password manager that holds
        # somebody's first password. It is raised once when shown instead.
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setStyleSheet(
            f"""
            QDialog {{ background: transparent; }}
            QFrame#MainFrame {{
                background-color: {Gate.PANEL};
                border: 1px solid {Gate.RAISED_HI};
                border-radius: 15px;
            }}
            QLabel {{
                color: {Gate.TEXT};
                font-family: 'Segoe UI';
                background: transparent;
            }}
            QLineEdit {{
                padding: 12px; border: 1px solid {Gate.LINE};
                border-radius: 6px; background: {Gate.RAISED}; color: {Gate.TEXT};
                font-size: 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {Gate.ACCENT}; background: {Gate.RAISED}; }}
            QLineEdit:disabled {{
                background: {Gate.PANEL}; color: {Gate.TEXT_DIM}; border: 1px solid {Gate.LINE};
            }}
            QPushButton#SignInBtn {{
                background-color: {Gate.ACCENT}; color: {Gate.TEXT_ON_ACCENT}; font-weight: bold;
                padding: 12px; border-radius: 6px; font-size: 14px; border: none;
            }}
            QPushButton#SignInBtn:hover {{ background-color: {Gate.mix(Gate.ACCENT, Gate.TEXT, 0.15)}; }}
            QPushButton#SignInBtn:disabled {{
                background-color: {Gate.RAISED}; color: {Gate.TEXT_DIM};
                border: 1px solid {Gate.LINE};
            }}
            QPushButton#RetryBtn {{
                background: transparent; color: {Gate.ACCENT}; font-weight: bold;
                padding: 8px; border-radius: 6px; font-size: 13px;
                border: 1px solid {Gate.ACCENT};
            }}
            QPushButton#RetryBtn:hover {{ background: {Gate.tint(Gate.ACCENT, 0.12)}; }}
            QPushButton#CloseBtn {{
                background: transparent; border: none; border-radius: 6px; padding: 4px;
            }}
            QPushButton#CloseBtn:hover {{ background: {Gate.overlay(0.08)}; }}
            QPushButton#LinkBtn {{
                color: {Gate.TEXT_DIM}; font-size: 11px; background: transparent;
                border: none; text-decoration: underline; padding: 4px;
            }}
            QPushButton#LinkBtn:hover {{ color: {Gate.TEXT}; }}
            QLabel#StatusBox {{
                font-size: 12px; border-radius: 4px; padding: 8px;
            }}
            QLabel#StatusBox[state="info"] {{
                color: {Gate.TEXT}; background-color: {Gate.ACCENT_SURFACE};
                border: 1px solid {Gate.LINE}; font-weight: normal;
            }}
            QLabel#StatusBox[state="error"] {{
                color: {Gate.TEXT}; background-color: {Gate.BAD_SURFACE};
                border: 1px solid {Gate.BAD}; font-weight: bold;
            }}
            QLabel#CapsHint {{ color: {Gate.WARN}; font-size: 11px; }}
            """
        )

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        # The window takes the height its contents need. It used to be a fixed
        # 380x480 frame: a message of more than one line squeezed the logo and
        # pushed the Password label onto the field above, and the message
        # itself was cut to its first line.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.frame = QFrame()
        self.frame.setObjectName("MainFrame")
        self.frame.setFixedWidth(380)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(0)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.frame.setGraphicsEffect(shadow)

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(30, 20, 30, 30)
        frame_layout.setSpacing(6)

        from .core.icons import icon as draw_icon
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setIcon(draw_icon("close", Gate.TEXT_DIM, 16))
        self.close_btn.setToolTip("Close Slate")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setAutoDefault(False)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.reject)
        frame_layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignRight)

        # The mark above the name - the same vector the icons are cut from.
        from .core.icons_brand import slate_mark
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(slate_mark(Gate.ACCENT, 52).pixmap(52, 52))
        logo.setFixedHeight(56)
        logo.setStyleSheet("background: transparent; border: none;")
        frame_layout.addWidget(logo)

        title = QLabel("SLATE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-family: {Gate.FONT_LABEL_STRONG}; font-size: 30px; font-weight: 600; "
            f"color: {Gate.TEXT}; letter-spacing: 5px; background: transparent;")
        frame_layout.addWidget(title)

        app_name = APP_NAMES.get(self.app_mode, APP_NAMES["all"])
        self.subtitle = QLabel(f"{app_name} · {version_text()}")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px;")
        frame_layout.addWidget(self.subtitle)
        frame_layout.addSpacing(18)

        # Accounts are user names ("priya.sharma", "admin"), not employee
        # numbers, whatever the old label said.
        self.user_label = QLabel("User name")
        frame_layout.addWidget(self.user_label)
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("e.g. priya.sharma")
        last_user = self.settings.value("last_login_user", "", str)
        if last_user:
            self.user_input.setText(last_user)
        self.user_input.returnPressed.connect(self._next_from_user)
        frame_layout.addWidget(self.user_input)
        frame_layout.addSpacing(8)

        frame_layout.addWidget(QLabel("Password"))
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("Your password")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.returnPressed.connect(self.handle_login)
        # Show the password while checking it - with a slow check and a shake
        # on failure, a typo nobody can see is expensive.
        self.show_password_action = QAction(self)
        self.show_password_action.setCheckable(True)
        self.show_password_action.setToolTip("Show password")
        self.show_password_action.setIcon(draw_icon("eye", Gate.TEXT_DIM, 16))
        self.show_password_action.toggled.connect(self._toggle_password_visible)
        self.pass_input.addAction(self.show_password_action, QLineEdit.ActionPosition.TrailingPosition)
        self.pass_input.installEventFilter(self)
        frame_layout.addWidget(self.pass_input)

        self.caps_hint = QLabel("Caps Lock is on")
        self.caps_hint.setObjectName("CapsHint")
        self.caps_hint.setVisible(False)
        frame_layout.addWidget(self.caps_hint)
        frame_layout.addSpacing(8)

        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("StatusBox")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setVisible(False)
        self._set_status_state("info")
        frame_layout.addWidget(self.status_lbl)

        self.btn_login = QPushButton("Sign in")
        self.btn_login.setObjectName("SignInBtn")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self.handle_login)
        frame_layout.addSpacing(6)
        frame_layout.addWidget(self.btn_login)

        # Shown only while the database cannot be reached: try again without
        # quitting and starting Slate over.
        self.retry_btn = QPushButton("Try again")
        self.retry_btn.setObjectName("RetryBtn")
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.setAutoDefault(False)
        self.retry_btn.clicked.connect(self.retry_database)
        self.retry_btn.setVisible(False)
        frame_layout.addWidget(self.retry_btn)

        # Reconfigure link - re-opens first-run setup to fix server/DB details
        self.reconfigure_btn = QPushButton("Reconfigure server / database")
        self.reconfigure_btn.setObjectName("LinkBtn")
        self.reconfigure_btn.setIcon(draw_icon("gear", Gate.TEXT_DIM, 14))
        self.reconfigure_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reconfigure_btn.setAutoDefault(False)  # Fix auto-trigger on Enter key
        self.reconfigure_btn.setFocusPolicy(Qt.NoFocus)  # Prevent tab-focus auto-trigger
        self.reconfigure_btn.clicked.connect(self._open_reconfigure)
        frame_layout.addWidget(self.reconfigure_btn, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.frame)

        if self.database_unavailable:
            self._say_the_database_is_down()

    # ------------------------------------------------------------- helpers
    def _set_status_state(self, state: str):
        """'info' for progress and explanations, 'error' only for a failure."""
        self.status_lbl.setProperty("state", state)
        style = self.status_lbl.style()
        style.unpolish(self.status_lbl)
        style.polish(self.status_lbl)

    def _show_status(self, message: str, state: str = "info"):
        self._set_status_state(state)
        self.status_lbl.setText(message)
        self.status_lbl.setVisible(bool(message))
        self.adjustSize()

    def _hide_status(self):
        self.status_lbl.setText("")
        self.status_lbl.setVisible(False)
        self.adjustSize()

    def _toggle_password_visible(self, shown: bool):
        from .core.icons import icon as draw_icon
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Normal if shown
                                    else QLineEdit.EchoMode.Password)
        self.show_password_action.setIcon(draw_icon("eye-off" if shown else "eye", Gate.TEXT_DIM, 16))
        self.show_password_action.setToolTip("Hide password" if shown else "Show password")

    @staticmethod
    def caps_lock_on() -> bool:
        """Whether Caps Lock is on (Windows only; elsewhere we cannot tell)."""
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
        except Exception:
            return False

    def _update_caps_hint(self):
        on = self.pass_input.hasFocus() and self.caps_lock_on()
        if on != self.caps_hint.isVisible():
            self.caps_hint.setVisible(on)
            self.adjustSize()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "pass_input", None) and event.type() in (
                QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
                QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            QTimer.singleShot(0, self, self._update_caps_hint)
            # The Caps Lock key itself does not always reach the field, so a
            # hint shown once could stay after Caps Lock went off. While the
            # password has focus the state is looked at again every 250 ms.
            if event.type() == QEvent.Type.FocusIn:
                self._caps_timer().start()
            elif event.type() == QEvent.Type.FocusOut:
                self._caps_timer().stop()
        return super().eventFilter(watched, event)

    def _caps_timer(self) -> QTimer:
        timer = getattr(self, "_caps_poll", None)
        if timer is None:
            timer = self._caps_poll = QTimer(self)
            timer.setInterval(250)
            timer.timeout.connect(self._update_caps_hint)
        return timer

    def _next_from_user(self):
        """Enter in the user name moves on to the password."""
        self.pass_input.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        # Raised once, instead of staying on top of everything for good.
        self.raise_()
        self.activateWindow()
        # A remembered user name: the caret goes where the typing starts.
        if self.user_input.isEnabled():
            target = self.pass_input if self.user_input.text().strip() else self.user_input
            QTimer.singleShot(0, self, lambda: target.setFocus())

    # ------------------------------------------------------ database down
    def _say_the_database_is_down(self):
        """
        Put the dialog into a state that explains itself.

        Deliberately not a message box: a modal that has to be dismissed before
        the window behind it appears reads like a crash. This leaves the login
        screen up, says why it cannot be used, and offers Try again - and
        Reconfigure, the one thing that can fix a moved server from here.
        """
        self.database_unavailable = True
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Database unavailable")
        for field in (self.user_input, self.pass_input):
            field.setEnabled(False)
        self.retry_btn.setVisible(True)
        self.retry_btn.setEnabled(True)
        self.retry_btn.setText("Try again")
        self._show_status(
            "Cannot reach the studio database, so nobody can be signed in yet. "
            "Check that Slate Server is running, then choose Try again. If the "
            "server has moved, use Reconfigure below.", "info")

    @staticmethod
    def _reload_database():
        """Read the connection settings again and reconnect."""
        from ..core.infra.database_manager import database_manager
        try:
            database_manager.reload_from_config()
        except Exception as exc:
            logging.warning("Login: database reload failed: %s", exc)

    def retry_database(self) -> bool:
        """
        Try to reach the database again, without restarting Slate.
        True when it answered and the fields are usable again.
        """
        self.retry_btn.setEnabled(False)
        self.retry_btn.setText("Trying…")
        self.repaint()
        try:
            self._reload_database()
            self.user_manager = self.app_context.user_manager()
        except Exception as exc:
            logging.warning("Login: the database still did not answer: %s", exc)
            self.user_manager = None
            self._say_the_database_is_down()
            self._show_status(
                "Still cannot reach the studio database. Check that Slate Server "
                "is running, then choose Try again.", "error")
            return False

        self.database_unavailable = False
        self.retry_btn.setVisible(False)
        self.btn_login.setEnabled(True)
        self.btn_login.setText("Sign in")
        for field in (self.user_input, self.pass_input):
            field.setEnabled(True)
        self._hide_status()
        (self.pass_input if self.user_input.text().strip() else self.user_input).setFocus()
        return True

    # ------------------------------------------------------------- sign in
    def handle_login(self):
        try:
            if self.auth_worker and self.auth_worker.isRunning():
                return

            username = self.user_input.text().strip()
            # Passed to authenticate as typed: it trims the same way every
            # password is trimmed when it is set (see UserManager.clean_password),
            # and still accepts an older password saved with spaces.
            password = self.pass_input.text()

            logging.info(f"Attempting login for user: {username}")

            if self.user_manager is None:
                self._say_the_database_is_down()
                return

            if not username or not password.strip():
                self.show_error("Enter your user name and password.",
                                field=self.user_input if not username else self.pass_input)
                return

            self._password_checked = password
            self.btn_login.setText("Signing in…")
            self.btn_login.setEnabled(False)
            self.user_input.setEnabled(False)
            self.pass_input.setEnabled(False)
            self._show_status("Checking your user name and password…", "info")
            self.repaint()

            self.auth_worker = LoginAuthWorker(self.user_manager, username, password)
            self.auth_worker.auth_result.connect(self._on_auth_result)
            self.auth_worker.finished.connect(self._on_auth_worker_finished)
            self.auth_worker.start()

        except Exception as exc:
            logging.exception("Sign-in dialog error: %s", exc)
            self.show_error("Something went wrong signing in. Try again - the details are in the log.")
            self._on_auth_worker_finished()

    def _on_auth_result(self, user, error_text):
        if self._is_closing:
            return

        username = self.user_input.text().strip()
        if error_text:
            logging.error(f"Authentication Error: {error_text}")
            self.show_error(friendly_auth_error(error_text))
            return

        if user:
            logging.info(f"Login successful: {user.get('user_id')}")

            # Imported with the shared first password: they choose their own
            # before anything opens. Closing the window means not signing in.
            if user.get("must_change_password"):
                from .dialogs.change_password_dialog import ChangePasswordDialog
                # Exactly the value that was just accepted - not the field's
                # text again, which is what a stray space used to break.
                dialog = ChangePasswordDialog(self.user_manager, user.get("user_id") or username,
                                              forced=True, current_password=self._password_checked,
                                              parent=self)
                if dialog.exec() != dialog.DialogCode.Accepted:
                    self.show_error("Choose your own password to sign in.")
                    self.pass_input.clear()
                    return
                user["must_change_password"] = False

            self.user_data = user
            # The account's own name, not what was typed ("  Admin " -> "admin").
            self.settings.setValue("last_login_user", user.get("user_id") or username)
            # Reset reconfigure flag before closing dialog (prevents auto-triggering)
            self._reconfigure_in_progress = False
            self.accept()
            return

        logging.warning(f"Login failed for {username}")
        try:
            fresh = self.user_manager.is_fresh_seed()
        except Exception:
            fresh = False
        if fresh and username.lower() != "admin":
            # A new studio database. The account being tried does not exist
            # here yet, and the one that does is the built-in administrator.
            self.show_error(
                "This studio's database is new and has no accounts yet. "
                "Sign in as  admin  /  admin123  to create them, then change "
                "that password from the Users tab.")
        else:
            self.show_error("Invalid credentials. Please try again.")
        self.pass_input.selectAll()
        self.shake_window()

    def _on_auth_worker_finished(self):
        if self.auth_worker:
            self.auth_worker.deleteLater()
            self.auth_worker = None

        if not self._is_closing and self.isVisible() and not self.database_unavailable:
            self.btn_login.setEnabled(True)
            self.btn_login.setText("Sign in")
            self.user_input.setEnabled(True)
            self.pass_input.setEnabled(True)
            if self.status_lbl.property("state") != "error":
                self._hide_status()
            focus = getattr(self, "_error_field", None) or self.pass_input
            focus.setFocus()

    def show_error(self, message, field=None):
        """Show a failure and put the caret in the field to fix (the password by default)."""
        self._show_status(message, "error")
        field = field or self.pass_input
        self._error_field = field
        if field is self.pass_input:
            self.pass_input.selectAll()
        field.setFocus()

    def shake_window(self):
        original_pos = self.pos()

        animation = QPropertyAnimation(self, b"pos", self)
        animation.setDuration(500)
        animation.setLoopCount(1)

        animation.setKeyValueAt(0, original_pos)
        animation.setKeyValueAt(0.1, original_pos + QPoint(10, 0))
        animation.setKeyValueAt(0.2, original_pos + QPoint(-10, 0))
        animation.setKeyValueAt(0.3, original_pos + QPoint(10, 0))
        animation.setKeyValueAt(0.4, original_pos + QPoint(-10, 0))
        animation.setKeyValueAt(0.5, original_pos + QPoint(5, 0))
        animation.setKeyValueAt(0.6, original_pos + QPoint(-5, 0))
        animation.setKeyValueAt(0.7, original_pos + QPoint(3, 0))
        animation.setKeyValueAt(0.8, original_pos + QPoint(-3, 0))
        animation.setKeyValueAt(0.9, original_pos + QPoint(1, 0))
        animation.setKeyValueAt(1, original_pos)

        animation.start()

    def mousePressEvent(self, event):
        self.oldPos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if not hasattr(self, "oldPos"):
            return
        delta = event.globalPosition().toPoint() - self.oldPos
        self.move(self.x() + delta.x(), self.y() + delta.y())
        self.oldPos = event.globalPosition().toPoint()

    def _open_reconfigure(self):
        """Re-open FirstRunSetupDialog so the user can fix server/DB details.

        GUARD: Only opens when user explicitly clicks the Configure button.
        Prevents accidental auto-opening after login.
        """
        # Only allow button as sender
        if self.sender() is not self.reconfigure_btn:
            logging.debug("_open_reconfigure called by %s, ignoring", repr(self.sender()))
            return

        # Prevent multiple simultaneous dialogs
        if self._reconfigure_in_progress:
            logging.debug("Configuration dialog already in progress, ignoring duplicate request")
            return

        self._reconfigure_in_progress = True

        try:
            from ..gatekeeper_main import FirstRunSetupDialog
            dlg = FirstRunSetupDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                values = dlg.values()
                for key in ("SERVER_ROOT", "db_host", "db_port", "db_name", "db_user"):
                    GlobalConfig.set(key, values[key])
                if values.get("db_password"):
                    GlobalConfig.set("db_password", values["db_password"])

                config_instance = GlobalConfig._instance or GlobalConfig()
                flag_path = config_instance.local_app_data / ".setup_complete"
                try:
                    flag_path.parent.mkdir(parents=True, exist_ok=True)
                    flag_path.write_text("configured=true\n", encoding="utf-8")
                except OSError as flag_exc:
                    logging.debug("Could not write setup-complete flag: %s", flag_exc)

                # Use the new details straight away rather than asking for a
                # restart.
                if self.retry_database():
                    self._show_status("Settings saved and the studio database answered. "
                                      "You can sign in.", "info")
        except Exception as exc:
            logging.exception("Reconfigure dialog failed: %s", exc)
            QMessageBox.warning(self, "Reconfigure server / database",
                                "The setup window could not be opened. The details are in the log.")
        finally:
            self._reconfigure_in_progress = False

    def closeEvent(self, event):
        self._is_closing = True
        super().closeEvent(event)
