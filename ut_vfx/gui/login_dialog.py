from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QFrame,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, QPropertyAnimation, QPoint, QThread, Signal, QSettings
from PySide6.QtGui import QColor

from ..core.infra.app_context import AppContext
from ..core.infra.global_config import GlobalConfig
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


class LoginDialog(QDialog):
    def __init__(self, user_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.user_manager = user_manager or self.app_context.user_manager()
        self.user_data = None
        self.auth_worker = None
        self._is_closing = False
        self._reconfigure_in_progress = False  # Guard flag to prevent accidental auto-opening
        self.settings = QSettings("UTStudio", "UTVFX")

        self.setWindowTitle("Slate - Studio Login")
        self.setMinimumSize(380, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setStyleSheet(
            """
            QDialog { background: transparent; }
            QFrame#MainFrame {
                background-color: #16161A;
                border: 1px solid #26262D;
                border-radius: 15px;
            }
            QLabel {
                color: #E8E6E1;
                font-family: 'Segoe UI';
                background: transparent;
            }
            QLineEdit {
                padding: 12px; border: 1px solid #2C2C34;
                border-radius: 6px; background: #1D1D22; color: white;
                font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #3EA8BF; background: #1D1D22; }
            QPushButton {
                background-color: #3EA8BF; color: #000; font-weight: bold;
                padding: 12px; border-radius: 6px; font-size: 14px;
            }
            QPushButton:hover { background-color: #3EA8BF; }
            QPushButton#CloseBtn {
                background: transparent; color: #87857F; font-size: 16px; border: none;
            }
            QPushButton#CloseBtn:hover { color: #E8E6E1; }
            """
        )

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.frame = QFrame()
        self.frame.setObjectName("MainFrame")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(0)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.frame.setGraphicsEffect(shadow)

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(30, 20, 30, 40)
        frame_layout.setSpacing(15)

        close_btn = QPushButton("X")
        close_btn.setObjectName("CloseBtn")
        close_btn.setMinimumSize(30, 30)
        close_btn.setAutoDefault(False)
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.clicked.connect(self.reject)
        frame_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

        # The mark above the name - the same vector the icons are cut from.
        from .core.icons_brand import slate_mark
        from ..core.infra.gate import Gate
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(slate_mark(Gate.ACCENT, 52).pixmap(52, 52))
        logo.setStyleSheet("background: transparent; border: none;")
        frame_layout.addWidget(logo)

        title = QLabel("SLATE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"font-family: {Gate.FONT_LABEL_STRONG}; font-size: 30px; font-weight: 600; "
            f"color: {Gate.TEXT}; letter-spacing: 5px; background: transparent;")
        frame_layout.addWidget(title)

        subtitle = QLabel(f"VFX Production  {APP_VERSION}")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #87857F; font-size: 11px; margin-bottom: 20px; text-transform: uppercase;")
        frame_layout.addWidget(subtitle)

        frame_layout.addWidget(QLabel("Employee ID"))
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("e.g. EMP0012")
        last_user = self.settings.value("last_login_user", "", str)
        if last_user:
            self.user_input.setText(last_user)
        frame_layout.addWidget(self.user_input)

        frame_layout.addWidget(QLabel("Password"))
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("******")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.returnPressed.connect(self.handle_login)
        frame_layout.addWidget(self.pass_input)

        self.status_lbl = QLabel("")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet(
            """
            color: #E8E6E1;
            font-size: 12px;
            background-color: rgba(255, 85, 85, 0.2);
            border: 1px solid #D9635F;
            border-radius: 4px;
            padding: 8px;
            font-weight: bold;
            """
        )
        self.status_lbl.setVisible(False)
        frame_layout.addWidget(self.status_lbl)

        self.btn_login = QPushButton("LOGIN")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self.handle_login)
        frame_layout.addWidget(self.btn_login)

        # Reconfigure link - re-opens first-run setup to fix server/DB details
        self.reconfigure_btn = QPushButton("\u2699\uFE0F Reconfigure Server / DB")
        self.reconfigure_btn.setObjectName("CloseBtn")  # Reuse transparent style
        self.reconfigure_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reconfigure_btn.setAutoDefault(False)  # Fix auto-trigger on Enter key
        self.reconfigure_btn.setFocusPolicy(Qt.NoFocus)  # Prevent tab-focus auto-trigger
        self.reconfigure_btn.setStyleSheet(
            "color: #87857F; font-size: 11px; background: transparent;"
            " border: none; text-decoration: underline; padding: 4px;"
        )
        self.reconfigure_btn.clicked.connect(self._open_reconfigure)
        frame_layout.addWidget(self.reconfigure_btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        frame_layout.addStretch(1)

        layout.addWidget(self.frame)

    def handle_login(self):
        try:
            if self.auth_worker and self.auth_worker.isRunning():
                return

            username = self.user_input.text().strip()
            password = self.pass_input.text().strip()

            logging.info(f"Attempting login for user: {username}")

            if not username or not password:
                self.show_error("Please enter both ID and Password")
                return

            self.btn_login.setText("VERIFYING...")
            self.btn_login.setEnabled(False)
            self.user_input.setEnabled(False)
            self.pass_input.setEnabled(False)
            self.status_lbl.setText("Verifying credentials...")
            self.status_lbl.setVisible(True)
            self.repaint()

            self.auth_worker = LoginAuthWorker(self.user_manager, username, password)
            self.auth_worker.auth_result.connect(self._on_auth_result)
            self.auth_worker.finished.connect(self._on_auth_worker_finished)
            self.auth_worker.start()

        except Exception as exc:
            logging.critical(f"Critical Login Dialog Error: {exc}")
            QMessageBox.critical(self, "Login Error", f"Critical Error: {exc}")
            self._on_auth_worker_finished()

    def _on_auth_result(self, user, error_text):
        if self._is_closing:
            return

        username = self.user_input.text().strip()
        if error_text:
            logging.error(f"Authentication Error: {error_text}")
            self.show_error(f"System Error: {error_text}")
            return

        if user:
            logging.info(f"Login successful: {user.get('user_id')}")
            
            # Authenticate API client
            # (Legacy API client removed)
            
            self.user_data = user
            self.settings.setValue("last_login_user", username)
            # Reset reconfigure flag before closing dialog (prevents auto-triggering)
            self._reconfigure_in_progress = False
            self.accept()
            return

        logging.warning(f"Login failed for {username}")
        self.show_error("Invalid credentials. Please try again.")
        self.pass_input.selectAll()
        self.shake_window()

    def _on_auth_worker_finished(self):
        if self.auth_worker:
            self.auth_worker.deleteLater()
            self.auth_worker = None

        if not self._is_closing and self.isVisible():
            self.btn_login.setEnabled(True)
            self.btn_login.setText("LOGIN")
            self.user_input.setEnabled(True)
            self.pass_input.setEnabled(True)
            self.pass_input.setFocus()

    def show_error(self, message):
        self.status_lbl.setText(message)
        self.status_lbl.setVisible(True)
        self.pass_input.selectAll()
        self.pass_input.setFocus()

    def shake_window(self):
        original_pos = self.pos()

        animation = QPropertyAnimation(self, b"pos")
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

                QMessageBox.information(
                    self, "Saved",
                    "Configuration updated.\nPlease restart the application.",
                )
        except Exception as exc:
            logging.exception("Reconfigure dialog failed: %s", exc)
            QMessageBox.critical(self, "Error", f"Could not open setup dialog:\n{exc}")
        finally:
            self._reconfigure_in_progress = False

    def closeEvent(self, event):
        self._is_closing = True
        super().closeEvent(event)

