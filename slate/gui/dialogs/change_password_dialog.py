"""A person changing their own password - by choice, or at first sign-in after an import."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
)


class ChangePasswordDialog(QDialog):
    """
    forced=True is the first sign-in of somebody imported with the shared
    first password: the current password is already known (they just typed
    it), and closing the window means not signing in.
    """

    def __init__(self, user_manager, username, forced=False, current_password=None, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.username = username
        self.forced = forced
        self._known_current = current_password if forced else None

        self.setWindowTitle("Choose your password" if forced else "Change password")
        self.setMinimumWidth(420)
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        if forced:
            self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Welcome, {username}. You signed in with the first password you were given.\n"
            "Choose your own password to continue." if forced else
            f"Change the password for {username}.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #B4B1AA; margin-bottom: 6px;")
        layout.addWidget(intro)

        form = QFormLayout()
        self.current_input = None
        if not forced:
            self.current_input = self._password_field()
            form.addRow("Current password:", self.current_input)
        self.new_input = self._password_field()
        form.addRow("New password:", self.new_input)
        self.repeat_input = self._password_field()
        form.addRow("New password again:", self.repeat_input)
        layout.addLayout(form)

        hint = QLabel(f"At least {user_manager.MIN_PASSWORD_LENGTH} characters.")
        hint.setStyleSheet("color: #87857F; font-size: 11px;")
        layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #D9635F;")
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Don't sign in" if forced else "Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save password")
        save.setDefault(True)
        save.setStyleSheet("background-color: #3EA8BF; color: black; font-weight: bold; padding: 5px 12px;")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    @staticmethod
    def _password_field():
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setStyleSheet("background: #26262D; padding: 4px;")
        return field

    def _save(self):
        new = self.new_input.text()
        if new != self.repeat_input.text():
            self.error_label.setText("The two new passwords are not the same.")
            return
        current = self._known_current if self.forced else self.current_input.text()
        ok, message = self.user_manager.change_own_password(self.username, current or "", new)
        if not ok:
            self.error_label.setText(message)
            return
        self.accept()
