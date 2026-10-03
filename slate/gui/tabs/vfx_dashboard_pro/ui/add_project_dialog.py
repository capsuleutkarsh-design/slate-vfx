"""
Add a project: its code, name and root folder.

It used to ask for "the Production Tracker" and a sheet, never read either, and
then wrote backup rows into that tracker in its own column layout. A new
project's Excel backup is created by Slate itself, in the central Tracking
folder; a different file can be set later in Edit project.
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QVBoxLayout

from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, style_button

from .edit_project_dialog import _PathField


class AddProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add project")
        self.setMinimumWidth(560)

        self.main_layout = QVBoxLayout(self)
        self.form = form_layout()

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("e.g. KLC")
        self.code_input.setToolTip("Letters, digits, _ and -; stored in capitals. It names the project's "
                                   "folders and files, and cannot be changed later.")
        self.form.addRow("Code", self.code_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Kaalchakra")
        self.form.addRow("Name", self.name_input)

        self.folder_field = _PathField("", self.browse_folder)
        self.folder_input = self.folder_field.edit
        self.form.addRow("Project root", self.folder_field)
        self.main_layout.addLayout(self.form)

        note = QLabel("Slate keeps the project's Excel backup itself, in the central Tracking folder. "
                      "Edit project can point it at another file later.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_2};")
        self.main_layout.addWidget(note)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {Gate.BAD};")
        self.main_layout.addWidget(self.message)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Create project")
        style_button(ok, "primary")
        style_button(self.buttons.button(QDialogButtonBox.StandardButton.Cancel), "secondary")
        self.buttons.accepted.connect(self.validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        self.main_layout.addWidget(self.buttons)

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Project root folder", self.folder_input.text())
        if path:
            self.folder_field.setText(path)

    def validate_and_accept(self):
        from slate.core.domain.naming import shot_name_problem
        code = self.code_input.text().strip().upper()
        problem = shot_name_problem(code, "The project code") if code else "The project needs a code."
        if not problem and not self.name_input.text().strip():
            problem = "The project needs a name."
        if problem:
            self.message.setText(problem)
            return
        self.accept()

    def get_data(self):
        return {
            "code": self.code_input.text().strip().upper(),
            "name": self.name_input.text().strip(),
            "excel_path": "",
            "folder_base": self.folder_input.text().strip(),
            "sheet_name": "MASTER",
            "header_row": 2,
            "data_start_row": 3,
            "column_mapping": None,
        }
