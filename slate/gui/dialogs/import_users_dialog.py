"""Add many people at once from an Excel or CSV list: pick the file, check the preview, import."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout
)

from slate.core.domain import user_import

_STATUS_TEXT = {
    "new": "Will be created",
    "created": "Created",
    "exists": "Skipped",
    "duplicate": "Skipped",
    "invalid": "Skipped",
    "failed": "Failed",
}
_STATUS_COLOUR = {
    "new": "#5FBF8F", "created": "#5FBF8F", "exists": "#D9A441",
    "duplicate": "#D9A441", "invalid": "#D9635F", "failed": "#D9635F",
}


class ImportUsersDialog(QDialog):
    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.plan = None
        self.imported = False

        self.setWindowTitle("Import people from Excel or CSV")
        self.setMinimumSize(820, 560)
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QVBoxLayout(self)

        intro = QLabel(
            "The file needs two columns: <b>Username</b> and <b>Display Name</b> (one person per row). "
            "Everybody gets the role and first password below, and chooses their own password the first "
            "time they sign in. People already in Slate are skipped, never changed. Fill in department, "
            "joining date and the rest afterwards on the Users list.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #B4B1AA;")
        layout.addWidget(intro)

        pick = QHBoxLayout()
        self.path_label = QLabel("No file chosen")
        self.path_label.setStyleSheet("color: #87857F;")
        choose = QPushButton("Choose file…")
        choose.clicked.connect(self.choose_file)
        template = QPushButton("Download template")
        template.clicked.connect(self.save_template)
        pick.addWidget(choose)
        pick.addWidget(self.path_label, 1)
        pick.addWidget(template)
        layout.addLayout(pick)

        form = QFormLayout()
        self.role_input = QComboBox()
        self.role_input.setStyleSheet("background: #26262D; padding: 4px;")
        roles = sorted(self.user_manager.get_available_roles(), key=lambda r: str(r).lower())
        self.role_input.addItems(roles)
        form.addRow("Give everyone the role:", self.role_input)

        pw_row = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(
            f"At least {self.user_manager.MIN_PASSWORD_LENGTH} characters, e.g. Welcome@2026")
        self.password_input.setStyleSheet("background: #26262D; padding: 4px;")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        pw_row.addWidget(self.password_input, 1)
        pw_row.addWidget(show)
        form.addRow("First password for everyone:", pw_row)
        layout.addLayout(form)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Row", "Username", "Display Name", "Result", "Why"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.report_btn = QPushButton("Save report…")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self.save_report)
        buttons.addWidget(self.report_btn)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.import_btn = QPushButton("Import")
        self.import_btn.setEnabled(False)
        self.import_btn.setStyleSheet("background-color: #3EA8BF; color: black; font-weight: bold; padding: 5px 14px;")
        self.import_btn.clicked.connect(self.run_import)
        buttons.addWidget(close)
        buttons.addWidget(self.import_btn)
        layout.addLayout(buttons)

    # --------------------------------------------------------------- file
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the list of people", "", "Excel or CSV (*.xlsx *.csv);;All files (*)")
        if not path:
            return
        try:
            existing = list((self.user_manager.get_all_users() or {}).keys())
            self.plan = user_import.plan_import(path, existing)
        except Exception as exc:
            QMessageBox.warning(self, "Cannot read the file", f"This file could not be read:\n\n{exc}")
            return
        self.path_label.setText(path)
        self.path_label.setStyleSheet("color: white;")
        self._show_plan()

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save template", "slate_people.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            user_import.write_template(path)
        except Exception as exc:
            QMessageBox.warning(self, "Not saved", f"The template could not be saved:\n\n{exc}")
            return
        QMessageBox.information(self, "Template saved",
                                "Fill in the People sheet, save it, then choose it here with 'Choose file…'.")

    # --------------------------------------------------------------- preview
    def _show_plan(self):
        rows = self.plan.rows if self.plan else []
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            status = _STATUS_TEXT.get(row.status, row.status)
            cells = [str(row.line), row.username, row.display_name, status, row.reason]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3:
                    item.setForeground(QColor(_STATUS_COLOUR.get(row.status, "#FFFFFF")))
                self.table.setItem(r, c, item)
        self.summary_label.setText(user_import.summary(self.plan) if self.plan else "")
        self.import_btn.setEnabled(bool(self.plan and self.plan.to_create) and not self.imported)
        self.report_btn.setEnabled(bool(rows))

    # ---------------------------------------------------------------- import
    def run_import(self):
        if not (self.plan and self.plan.to_create):
            return
        role = self.role_input.currentText()
        password = self.password_input.text()
        if len(password) < self.user_manager.MIN_PASSWORD_LENGTH:
            QMessageBox.warning(self, "First password",
                                f"Type a first password of at least {self.user_manager.MIN_PASSWORD_LENGTH} characters.")
            return
        count = len(self.plan.to_create)
        answer = QMessageBox.question(
            self, "Import",
            f"Create {count} people with the role {role}?\n\n"
            "Each will be asked to choose their own password the first time they sign in.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            user_import.apply_import(self.user_manager, self.plan, role, password)
        except Exception as exc:
            QMessageBox.warning(self, "Import stopped", str(exc))
            return
        self.imported = True
        self._show_plan()
        QMessageBox.information(
            self, "Import finished",
            user_import.summary(self.plan) + ".\n\nTell each person their username and the first password. "
            "Then open each one on the Users list to fill in department, joining date and the rest.")

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "import_report.csv", "CSV (*.csv)")
        if path:
            user_import.write_report_csv(self.plan, path)
