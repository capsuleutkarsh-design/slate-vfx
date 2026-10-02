"""A person changing their own password - by choice, or at first sign-in after an import."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout
)
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button, set_default_button


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
        self.setStyleSheet(f"QDialog {{ background-color: {Gate.RAISED}; }}")  # the dialog only: without a selector every field in it took this background
        if forced:
            self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Welcome, {username}. You signed in with the first password you were given. "
            "Choose your own password to continue. Cancel takes you back to the sign-in screen."
            if forced else f"Change the password for {username}.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Gate.TEXT_2}; margin-bottom: 6px;")
        layout.addWidget(intro)

        form = form_layout()
        self.current_input = None
        if not forced:
            self.current_input = self._password_field()
            form.addRow("Current password:", self.current_input)
        self.new_input = self._password_field()
        form.addRow("New password:", self.new_input)
        self.repeat_input = self._password_field()
        form.addRow("New password again:", self.repeat_input)
        layout.addLayout(form)

        # One line for the length rule: it turns red and says what is wrong,
        # rather than a second red line repeating it underneath.
        self.hint_text = f"At least {user_manager.MIN_PASSWORD_LENGTH} characters."
        self.hint = QLabel(self.hint_text)
        self.hint.setWordWrap(True)
        self._set_hint(self.hint_text, error=False)
        layout.addWidget(self.hint)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {Gate.BAD};")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = make_button("Cancel", "secondary", on_click=self.reject)
        self.save_button = make_button("Save password", "primary", on_click=self._save)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        set_default_button(self, self.save_button)

    @staticmethod
    def _password_field():
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        return field

    def _set_hint(self, text, error):
        self.hint.setText(text)
        colour = Gate.BAD if error else Gate.TEXT_DIM
        self.hint.setStyleSheet(f"color: {colour}; font-size: 11px;")

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _save(self):
        new = self.new_input.text()
        self._set_hint(self.hint_text, error=False)
        self._show_error("")

        # The rule for an acceptable password lives with the accounts
        # (UserManager.password_problem); its answer goes on the hint line.
        checker = getattr(self.user_manager, "password_problem", None)
        problem = checker(new) if callable(checker) else None
        if problem:
            self._set_hint(problem, error=True)
            self.new_input.setFocus()
            return
        clean = getattr(self.user_manager, "clean_password", lambda value: str(value or "").strip())
        if clean(new) != clean(self.repeat_input.text()):
            self._show_error("The two new passwords are not the same.")
            self.repeat_input.setFocus()
            return
        current = self._known_current if self.forced else self.current_input.text()
        ok, message = self.user_manager.change_own_password(self.username, current or "", new)
        if not ok:
            self._show_error(message)
            return
        self.accept()
