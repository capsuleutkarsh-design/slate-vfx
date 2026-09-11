"""
User-management dialogs extracted from admin_panel.py.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from ..core.infra.design_tokens import (
    ColorTokens as C,
    RadiusTokens as R,
    SpacingTokens as S,
)
from ..core.infra.style_builder import StyleBuilder


STYLE_BTN_PRIMARY = StyleBuilder.primary_button()
STYLE_INPUT = StyleBuilder.input_field()


class AddUserDialog(QDialog):
    def __init__(self, parent=None, edit_mode=False, user_data=None):
        super().__init__(parent)
        self.setWindowTitle("User Details")
        self.setMinimumSize(400, 500)
        self.setStyleSheet(f"background-color: {C.BG_PRIMARY}; color: white;")
        layout = QFormLayout(self)
        self.inp_login = QLineEdit()
        self.inp_name = QLineEdit()

        # MULTI-ROLE SELECTION
        self.lbl_roles = QLabel("Assign Roles:")
        self.list_roles = QListWidget()
        self.list_roles.setFixedHeight(100)
        self.list_roles.setStyleSheet(
            f"QListWidget {{ background: {C.BG_ELEVATED}; border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px; }} "
            f"QListWidget::item {{ padding: {S.XS}px; }}"
        )

        # Populate Roles
        available_roles = [
            "Artist", "Coordinator", "Lead", "Supervisor", "Developer", "Tester"
        ]
        if hasattr(parent, "user_manager"):
            available_roles = parent.user_manager.get_available_roles()

        for role in available_roles:
            item = QListWidgetItem(role)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.list_roles.addItem(item)

        self.inp_job = QLineEdit()
        self.inp_pass = QLineEdit()
        self.inp_pass.setEchoMode(QLineEdit.EchoMode.Password)

        # Avatar Selection
        self.avatar_path = ""
        self.btn_avatar = QPushButton("Select Avatar Image")
        self.btn_avatar.setStyleSheet(STYLE_INPUT)
        self.btn_avatar.clicked.connect(self.select_avatar)
        self.lbl_avatar = QLabel("No image selected")
        self.lbl_avatar.setStyleSheet(f"color: {C.TEXT_TERTIARY}; font-size: 11px;")

        for widget in [self.inp_login, self.inp_name, self.inp_job, self.inp_pass]:
            widget.setStyleSheet(STYLE_INPUT)

        if edit_mode and user_data:
            self.inp_login.setText(user_data[0])
            self.inp_login.setReadOnly(True)
            self.inp_name.setText(user_data[1])

            # user_data[2] is list or legacy string.
            current_roles = user_data[2]
            if isinstance(current_roles, str):
                current_roles = [current_roles]
            if not isinstance(current_roles, list):
                current_roles = []

            for i in range(self.list_roles.count()):
                item = self.list_roles.item(i)
                if item.text() in current_roles:
                    item.setCheckState(Qt.Checked)

            self.inp_job.setText(user_data[3])
            if len(user_data) > 4:
                self.avatar_path = user_data[4]
                self.lbl_avatar.setText(Path(self.avatar_path).name if self.avatar_path else "No avatar set")

        layout.addRow("Login ID:", self.inp_login)
        layout.addRow("Full Name:", self.inp_name)
        layout.addRow("Roles:", self.list_roles)
        layout.addRow("Job Title:", self.inp_job)
        layout.addRow("Password:", self.inp_pass)
        layout.addRow("Profile Pic:", self.btn_avatar)
        layout.addRow("", self.lbl_avatar)

        btn = QPushButton("Save User")
        btn.setStyleSheet(STYLE_BTN_PRIMARY)
        btn.clicked.connect(self.validate_and_accept)
        layout.addRow(btn)

    def validate_and_accept(self):
        # Validate that at least one role is selected
        selected_roles = []
        for i in range(self.list_roles.count()):
            item = self.list_roles.item(i)
            if item.checkState() == Qt.CheckState.Checked or item.checkState() == Qt.Checked:
                selected_roles.append(item.text())
        if not selected_roles:
            QMessageBox.warning(self, "Validation Error", "Please select at least one role.")
            return
        self.accept()


    def select_avatar(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Profile Picture",
            "",
            "Images (*.png *.jpg *.jpeg)",
        )
        if path:
            self.avatar_path = path
            self.lbl_avatar.setText(Path(path).name)

    def get_data(self):
        # Collect checked roles
        selected_roles = []
        for i in range(self.list_roles.count()):
            item = self.list_roles.item(i)
            if item.checkState() == Qt.Checked:
                selected_roles.append(item.text())

        return (
            self.inp_login.text(),
            self.inp_pass.text(),
            selected_roles,
            self.inp_name.text(),
            self.inp_job.text(),
            self.avatar_path,
        )
