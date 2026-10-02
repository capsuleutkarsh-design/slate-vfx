"""Add many people at once from an Excel or CSV list: pick the file, check the preview, import."""

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from slate.core.domain import user_import
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button, form_layout
from slate.gui.core.table_style import style_table
from slate.gui.dialogs.biometric_import_dialog import ElidedPath

_STATUS_TEXT = {
    "new": "Will be created",
    "update": "Will be updated",
    "created": "Created",
    "updated": "Updated",
    "exists": "Skipped",
    "duplicate": "Skipped",
    "invalid": "Skipped",
    "failed": "Failed",
}


def _status_colour(status):
    return {"new": Gate.OK, "created": Gate.OK, "update": Gate.ACCENT, "updated": Gate.ACCENT,
            "exists": Gate.WARN, "duplicate": Gate.WARN, "invalid": Gate.BAD,
            "failed": Gate.BAD}.get(status, Gate.TEXT)


def importable_roles(user_manager) -> list:
    """
    The roles an import may hand out: what this person may give
    (assignable_roles), never Full access or a role with an admin-level
    ability. The list used to start on 'Admin', so one click made a hundred
    administrators.
    """
    from slate.core.domain import permissions_catalog as catalog
    roles = (user_manager.assignable_roles() if hasattr(user_manager, "assignable_roles")
             else user_manager.get_available_roles())
    out = []
    for role in roles:
        perms = user_manager.role_permissions(role) if hasattr(user_manager, "role_permissions") else []
        if catalog.has_all(perms) or (catalog.abilities_in(perms) & set(catalog.SENSITIVE_ABILITIES)):
            continue
        if str(role).strip().lower() in ("admin", "developer"):
            continue
        out.append(role)
    return sorted(out, key=lambda r: str(r).lower())


class ImportWorker(QThread):
    """apply_import off the screen's thread: bcrypt takes a moment per person."""

    progress = Signal(int, int)
    done = Signal(object)          # the exception, or None

    def __init__(self, user_manager, plan, role, password):
        super().__init__()
        self.user_manager, self.plan, self.role, self.password = user_manager, plan, role, password

    def run(self):
        try:
            user_import.apply_import(self.user_manager, self.plan, self.role, self.password,
                                     progress=lambda a, b: self.progress.emit(a, b))
            self.done.emit(None)
        except Exception as exc:              # noqa: BLE001 - reported on screen
            self.done.emit(exc)


class ImportUsersDialog(QDialog):
    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.plan = None
        self.imported = False
        self.path = ""
        self._worker = None

        self.setWindowTitle("Import people from Excel or CSV")
        self.setMinimumSize(880, 580)
        self.setObjectName("importUsers")
        self.setStyleSheet(f"QDialog#importUsers {{ background-color: {Gate.RAISED}; }}")
        layout = QVBoxLayout(self)

        intro = QLabel(
            "One person per row with a <b>Username</b> column (and usually <b>Display Name</b>). "
            "Optional columns: Department, Joined, Reports To, Location, Employment and Role - "
            "the template has them all. Everybody gets the first password below and chooses "
            "their own at first sign-in. People already in Slate are skipped unless you tick "
            "<i>Update existing people</i>.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(intro)

        pick = QHBoxLayout()
        # Cut in the middle with '...', the whole path in the tooltip - it ran
        # under the template button.
        self.path_label = ElidedPath("No file chosen")
        self.path_label.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        pick.addWidget(make_button("Choose file…", "secondary", on_click=self.choose_file))
        pick.addWidget(self.path_label, 1)
        pick.addWidget(make_button("Download template", "ghost", on_click=self.save_template))
        layout.addLayout(pick)

        form = form_layout()
        self.role_input = QComboBox()
        self.role_input.addItem("Choose a role", "")
        for role in importable_roles(self.user_manager):
            self.role_input.addItem(str(role), str(role))
        self.role_input.currentIndexChanged.connect(lambda *_: self._sync_import())
        form.addRow("Role for everyone", self.role_input)

        pw_row = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(
            f"At least {self.user_manager.MIN_PASSWORD_LENGTH} characters, e.g. Welcome@2026")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.password_input.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        pw_row.addWidget(self.password_input, 1)
        pw_row.addWidget(show)
        form.addRow("First password", pw_row)

        self.update_existing = QCheckBox(
            "Update existing people (profile fields only - never passwords or roles)")
        self.update_existing.toggled.connect(lambda *_: self._replan())
        form.addRow("", self.update_existing)
        layout.addLayout(form)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Row", "Username", "Display name", "Result", "Details"])
        style_table(self.table, {"Row": "contents", "Username": "contents",
                                 "Display name": ("interactive", 180), "Result": "contents",
                                 "Details": "stretch"})
        layout.addWidget(self.table, 1)

        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.report_btn = make_button("Save report…", "ghost", on_click=self.save_report)
        self.report_btn.setEnabled(False)
        buttons.addWidget(self.report_btn)
        buttons.addStretch()
        self.close_btn = make_button("Close", "secondary", on_click=self.accept)
        self.import_btn = make_button("Import", "primary", on_click=self.run_import)
        self.import_btn.setEnabled(False)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.import_btn)
        layout.addLayout(buttons)

    # --------------------------------------------------------------- file
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the list of people", "", "Excel or CSV (*.xlsx *.csv);;All files (*)")
        if path:
            self.load(path)

    def load(self, path, username_column=None):
        self.path = path
        self._username_column = username_column
        try:
            self.plan = self._plan()
        except Exception as exc:
            QMessageBox.warning(self, "Import people", f"This file could not be read:\n\n{exc}")
            return
        if self.plan.needs_column:
            # A header with no username column ('Name, Email') used to import
            # the header itself as a user called 'name'.
            choices = [c or "(column %d)" % (i + 1) for i, c in enumerate(self.plan.needs_column)]
            choice, ok = QInputDialog.getItem(
                self, "Which column is the username?",
                "This file has no Username column. Which column holds what people sign in with?",
                choices, 0, False)
            if not ok or choice not in choices:
                self.plan = None
                self._show_plan()
                return
            self._username_column = choices.index(choice)
            self.plan = self._plan()
        self.path_label.set_path(path)
        self.path_label.setStyleSheet(f"color: {Gate.TEXT};")
        self._show_plan()

    def _plan(self):
        existing = list((self.user_manager.get_all_users() or {}).keys())
        return user_import.plan_import(
            self.path, existing, user_manager=self.user_manager,
            allowed_roles=importable_roles(self.user_manager),
            update_existing=self.update_existing.isChecked(),
            username_column=getattr(self, "_username_column", None))

    def _replan(self):
        if self.path and not self.imported:
            try:
                self.plan = self._plan()
            except Exception:
                return
            self._show_plan()

    def save_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save template", "slate_people.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            user_import.write_template(path)
        except Exception as exc:
            QMessageBox.warning(self, "Save template", f"The template could not be saved:\n\n{exc}")
            return
        QMessageBox.information(self, "Save template",
                                "Fill in the People sheet, save it, then choose it here with 'Choose file…'.")

    # --------------------------------------------------------------- preview
    def _show_plan(self):
        rows = self.plan.rows if self.plan else []
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            details = row.reason
            if row.status == "new" and row.fields:
                details = "Also sets: " + ", ".join(
                    "%s %s" % (user_import.FIELD_LABELS.get(k, "Role" if k == "roles" else k),
                               ", ".join(v) if isinstance(v, list) else v)
                    for k, v in row.fields.items())
            cells = [str(row.line), row.username, row.display_name,
                     _STATUS_TEXT.get(row.status, row.status), details]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 3:
                    item.setForeground(QColor(_status_colour(row.status)))
                if c == 4:
                    item.setToolTip(text)
                self.table.setItem(r, c, item)
        text = user_import.summary(self.plan) if self.plan else ""
        if self.plan and self.plan.columns:
            text += ". Columns read: " + ", ".join(
                user_import.FIELD_LABELS.get(c, "Role" if c == "role" else c) for c in self.plan.columns)
        self.summary_label.setText(text)
        self.report_btn.setEnabled(bool(rows))
        self._sync_import()

    def _needs_role(self) -> bool:
        return bool(self.plan) and any(not r.fields.get("roles") for r in self.plan.to_create)

    def _sync_import(self):
        work = bool(self.plan and (self.plan.to_create or self.plan.to_update))
        role_ok = bool(self.role_input.currentData()) or not self._needs_role()
        self.import_btn.setEnabled(work and role_ok and not self.imported and self._worker is None)
        self.import_btn.setToolTip("" if role_ok else "Choose the role the new people get.")

    # ---------------------------------------------------------------- import
    def run_import(self):
        if not (self.plan and (self.plan.to_create or self.plan.to_update)):
            return
        role = self.role_input.currentData() or ""
        password = self.password_input.text()
        if self.plan.to_create and len(password) < self.user_manager.MIN_PASSWORD_LENGTH:
            QMessageBox.warning(self, "First password",
                                f"Type a first password of at least {self.user_manager.MIN_PASSWORD_LENGTH} characters.")
            return
        new, changed = len(self.plan.to_create), len(self.plan.to_update)
        parts = []
        if new:
            parts.append(f"create {new} {'person' if new == 1 else 'people'}"
                         + (f" (role {role} where the file gives none)" if role else ""))
        if changed:
            parts.append(f"update {changed} existing {'person' if changed == 1 else 'people'}")
        answer = QMessageBox.question(
            self, "Import people",
            "Import will %s.\n\nNew people choose their own password the first time they sign in."
            % " and ".join(parts),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.progress.setRange(0, new + changed)
        self.progress.setValue(0)
        self.progress.show()
        self.import_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        # Off the screen's thread, with a progress bar: a hundred people froze
        # the window for seventeen seconds.
        self._worker = ImportWorker(self.user_manager, self.plan, role, password)
        self._worker.progress.connect(lambda done, total: self.progress.setValue(done))
        self._worker.done.connect(self._finished)
        self._worker.start()

    def wait_for_import(self, ms=60000):
        """For callers (and tests) that need the result before going on."""
        from PySide6.QtCore import QElapsedTimer
        from PySide6.QtWidgets import QApplication
        timer = QElapsedTimer()
        timer.start()
        while self._worker is not None and timer.elapsed() < ms:
            QApplication.processEvents()
            if self._worker is not None:
                self._worker.wait(20)

    def _finished(self, error):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait(1000)
            worker.deleteLater()
        self.progress.hide()
        self.close_btn.setEnabled(True)
        if error is not None:
            QMessageBox.warning(self, "Import stopped", str(error))
            self._sync_import()
            return
        self.imported = True
        self._show_plan()
        QMessageBox.information(
            self, "Import finished",
            user_import.summary(self.plan) + ".\n\nTell each new person their username and the "
            "first password. Save the report for the list of what was set.")

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save report", "import_report.csv", "CSV (*.csv)")
        if path:
            user_import.write_report_csv(self.plan, path)

    def reject(self):
        if self._worker is not None:
            return          # not while people are being created
        super().reject()
