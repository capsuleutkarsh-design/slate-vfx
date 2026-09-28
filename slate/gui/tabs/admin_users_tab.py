from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QDateEdit
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont, QColor
import json
import logging
from slate.core.domain.user_manager import UserManager

logger = logging.getLogger(__name__)

# Accounts that must survive a mis-click. EMP0012 is here because it used to be
# re-created on every start instead: deleting it appeared to work and then it
# came back. It is no longer re-created, so the studios that already have one
# need it protected rather than resurrected.
PROTECTED_USERNAMES = {"admin", "developer", "emp0012"}

EMPLOYMENT_TYPES = ("Staff", "Freelance", "Contract")

# Matches the locations the holiday calendar offers, because a person's
# location is what decides which holidays their leave is charged against.
LOCATIONS = ("", "Mumbai", "Chennai", "Remote")


class UserDialog(QDialog):
    """
    One form for adding a person and for editing them, because both ask for
    the same things.

    The two inline dialogs this replaces had drifted apart. The edit one read
    its starting values out of the table rather than out of the user record,
    and the table only ever showed one role - so editing somebody with three
    roles silently left them with one. It also had nowhere to put a joining
    date, which is why leave accrual had nothing to count from.
    """

    # QDateEdit has no empty state, so the minimum date stands in for "nobody
    # has recorded this yet" and is shown as such. Saving it means "leave
    # whatever is already there alone" rather than writing 1900.
    NOT_SET = QDate(1900, 1, 1)

    def __init__(self, user_manager, username=None, record=None,
                 all_usernames=(), parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.editing = username is not None
        self.username = username or ""
        record = record or {}

        self.setWindowTitle("Edit %s" % username if self.editing else "Add New User")
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        self.setMinimumWidth(420)

        form = QFormLayout(self)

        self.username_input = QLineEdit(self.username)
        if self.editing:
            self.username_input.setReadOnly(True)
        form.addRow("Username:", self.username_input)

        self.display_input = QLineEdit(str(record.get("display_name") or ""))
        form.addRow("Display Name:", self.display_input)

        from slate.core.domain.departments import staff_department_names
        self.dept_input = QComboBox()
        self.dept_input.setEditable(True)
        self.dept_input.addItems(staff_department_names())
        self.dept_input.setStyleSheet("background: #26262D; padding: 4px;")
        current_dept = str(record.get("job_title") or "")
        if current_dept:
            self.dept_input.setCurrentText(current_dept)
        form.addRow("Department:", self.dept_input)

        # Every role, not just the first: a dropdown of tick boxes, because a
        # person really can be both a Team Lead and a Compositor, and dropping
        # the second one quietly takes away whatever it granted. A new person
        # starts with nothing ticked - it used to tick the first role in the
        # list, which is Developer, the one with full access.
        from slate.gui.components.check_combo import CheckComboBox
        available = self.user_manager.get_available_roles() or [
            "Artist", "Coordinator", "Lead", "Supervisor", "HR", "IT",
            "Developer", "Tester",
        ]
        self.roles_input = CheckComboBox(placeholder="Choose one or more roles…")
        self.roles_input.setStyleSheet("background: #26262D; padding: 4px;")
        self.roles_input.add_items(sorted((str(r) for r in available), key=str.lower))
        self.roles_input.set_checked(record.get("roles") or [])
        form.addRow("Roles:", self.roles_input)

        self.pass_input = None
        if not self.editing:
            self.pass_input = QLineEdit()
            self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password:", self.pass_input)

        # ------------------------------------------------- employment record
        self.joined_input = QDateEdit()
        self.joined_input.setCalendarPopup(True)
        self.joined_input.setMinimumDate(self.NOT_SET)
        self.joined_input.setSpecialValueText("Not recorded")
        self.joined_input.setDate(self._as_qdate(record.get("joined_on")) or (
            QDate.currentDate() if not self.editing else self.NOT_SET))
        self.joined_input.setStyleSheet("background: #26262D; padding: 4px;")
        form.addRow("Joined on:", self.joined_input)

        self.employment_input = QComboBox()
        self.employment_input.addItem("Not recorded", "")
        for kind in EMPLOYMENT_TYPES:
            self.employment_input.addItem(kind, kind)
        current_employment = str(record.get("employment") or "")
        if current_employment:
            index = self.employment_input.findData(current_employment)
            if index < 0:
                self.employment_input.addItem(current_employment, current_employment)
                index = self.employment_input.count() - 1
            self.employment_input.setCurrentIndex(index)
        self.employment_input.setStyleSheet("background: #26262D; padding: 4px;")
        form.addRow("Employment:", self.employment_input)

        # Who approves this person's leave at the first stage. Without it a
        # supervisor is shown every request in the studio.
        self.reports_input = QComboBox()
        self.reports_input.addItem("Nobody", "")
        for name in sorted(all_usernames):
            if name.strip().lower() == self.username.strip().lower():
                continue
            self.reports_input.addItem(name, name)
        current_manager = str(record.get("reports_to") or "")
        if current_manager:
            index = self.reports_input.findData(current_manager)
            if index < 0:
                self.reports_input.addItem(current_manager, current_manager)
                index = self.reports_input.count() - 1
            self.reports_input.setCurrentIndex(index)
        self.reports_input.setStyleSheet("background: #26262D; padding: 4px;")
        form.addRow("Reports to:", self.reports_input)

        self.location_input = QComboBox()
        self.location_input.setEditable(True)
        self.location_input.addItems(LOCATIONS)
        self.location_input.setCurrentText(str(record.get("location") or ""))
        self.location_input.setStyleSheet("background: #26262D; padding: 4px;")
        form.addRow("Location:", self.location_input)

        note = QLabel(
            "Joining date drives leave accrual. Location decides which public "
            "holidays their leave is charged against. Reports to decides whose "
            "queue their leave request lands in first."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #87857F; font-size: 11px;")
        form.addRow(note)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Update" if self.editing else "Save")
        save_btn.setStyleSheet("background-color: #5FBF8F; font-weight: bold; padding: 4px;")
        save_btn.clicked.connect(self._accept_if_valid)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        form.addRow(buttons)

    @staticmethod
    def _as_qdate(value):
        """A stored date as a QDate, whatever shape the driver returned it in."""
        if not value:
            return None
        if isinstance(value, QDate):
            return value
        text = str(value)[:10]
        parsed = QDate.fromString(text, "yyyy-MM-dd")
        return parsed if parsed.isValid() else None

    def selected_roles(self):
        return self.roles_input.checked()

    def _accept_if_valid(self):
        if not self.username_input.text().strip():
            QMessageBox.warning(self, "Error", "Username is required.")
            return
        if not self.selected_roles():
            QMessageBox.warning(
                self, "Error",
                "Pick at least one role. A person with no role can sign in and "
                "do nothing, which reads as a broken account.")
            return
        if self.pass_input is not None and not self.pass_input.text():
            QMessageBox.warning(self, "Error", "Password is required for a new user.")
            return
        self.accept()

    def payload(self):
        """
        What to save. Anything nobody filled in comes back as None, which
        add_user reads as "leave it alone" rather than blanking it.
        """
        joined = self.joined_input.date()
        return {
            "username": self.username_input.text().strip(),
            "password": self.pass_input.text() if self.pass_input is not None else "KEEP_OLD",
            "roles": self.selected_roles(),
            "display_name": self.display_input.text().strip(),
            "job_title": self.dept_input.currentText().strip(),
            "joined_on": None if joined == self.NOT_SET else joined.toString("yyyy-MM-dd"),
            "employment": self.employment_input.currentData() or None,
            "reports_to": self.reports_input.currentData() or None,
            "location": self.location_input.currentText().strip() or None,
        }


class AdminUsersTab(QWidget):
    """
    Users & Roles: the one place people are added and given access.

    Two tabs. Users is for whoever may manage users (HR, Admin, ...); Roles &
    Permissions is for whoever may edit permissions (Admin, IT, HR, ...). Each
    person sees the tabs their roles allow. Admin Panel used to carry a second,
    unguarded copy of both; it has been removed.
    """

    def __init__(self, user_role="Admin", user_data=None, parent=None):
        super().__init__(parent)
        self.user_role = user_role
        self.user_data = user_data or {}
        self.user_manager = UserManager()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        header_title = QLabel("Users & Roles")
        header_title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        header_title.setStyleSheet("color: white;")
        main_layout.addWidget(header_title)

        # Access control. Every role the person holds is considered, answered
        # by access.json and by the abilities ticked on each role.
        from slate.core.domain.access import can
        roles = list(self.user_data.get("roles") or [])
        if self.user_role:
            roles.append(self.user_role)
        may_manage_users = can(roles, "manage_users")
        may_edit_roles = can(roles, "manage_permissions")

        self.users_panel = None
        self.role_editor = None
        if not (may_manage_users or may_edit_roles):
            lbl = QLabel("You do not have permission to manage users or roles.")
            lbl.setStyleSheet("color: #D9635F; font-size: 14px;")
            main_layout.addWidget(lbl)
            main_layout.addStretch()
            return

        from PySide6.QtWidgets import QTabWidget
        self.tabs = QTabWidget()
        if may_manage_users:
            self.users_panel = UsersPanel(self.user_manager, self)
            self.tabs.addTab(self.users_panel, "Users")
        if may_edit_roles:
            from slate.gui.role_editor import RoleEditor
            username = self.user_data.get("user_id") or self.user_data.get("username")
            self.role_editor = RoleEditor(self.user_manager, editor_username=username)
            self.tabs.addTab(self.role_editor, "Roles && Permissions")   # a single & is a shortcut marker
        # Each side shows what the other changed: user counts per role, and
        # the roles on offer when adding somebody.
        self.tabs.currentChanged.connect(self._refresh_current)
        main_layout.addWidget(self.tabs)

    def _refresh_current(self, _index):
        page = self.tabs.currentWidget()
        if page is self.role_editor and self.role_editor is not None:
            self.role_editor.refresh_roles(select=self.role_editor.current_role)
        elif page is self.users_panel and self.users_panel is not None:
            self.users_panel.load_data()


class UsersPanel(QWidget):
    COLUMNS = ["Username", "Display Name", "Department (Job Title)", "Roles",
               "Joined", "Employment", "Reports To"]

    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.users = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 8, 0, 0)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        add_btn = QPushButton("+ Add New User")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_user)
        controls.addWidget(add_btn)

        edit_btn = QPushButton("Edit User")
        edit_btn.setObjectName("secondaryButton")
        edit_btn.clicked.connect(self.edit_user)
        controls.addWidget(edit_btn)

        reset_btn = QPushButton("Reset Password")
        reset_btn.setObjectName("secondaryButton")
        reset_btn.clicked.connect(self.reset_password)
        controls.addWidget(reset_btn)

        del_btn = QPushButton("Delete User")
        del_btn.setObjectName("dangerButton")
        del_btn.clicked.connect(self.delete_user)
        controls.addWidget(del_btn)

        controls.addSpacing(20)
        import_btn = QPushButton("Import from Excel / CSV…")
        import_btn.setObjectName("secondaryButton")
        import_btn.setToolTip("Add many people at once from a list of usernames and names")
        import_btn.clicked.connect(self.import_users)
        controls.addWidget(import_btn)

        export_btn = QPushButton("Export CSV")
        export_btn.setObjectName("secondaryButton")
        export_btn.clicked.connect(self.export_csv)
        controls.addWidget(export_btn)

        controls.addStretch()
        main_layout.addLayout(controls)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by username, name, department or role…")
        self.search.setStyleSheet("background: #26262D; padding: 6px; border-radius: 4px;")
        self.search.textChanged.connect(self.apply_filter)
        main_layout.addWidget(self.search)

        self.grid = QTableWidget(0, len(self.COLUMNS))
        self.grid.setHorizontalHeaderLabels(self.COLUMNS)
        self.style_table(self.grid)
        self.grid.doubleClicked.connect(lambda _index: self.edit_user())
        self.load_data()

        main_layout.addWidget(self.grid)

    def apply_filter(self, *_args):
        text = self.search.text().strip().lower()
        for row in range(self.grid.rowCount()):
            cells = [self.grid.item(row, c) for c in range(4)]
            haystack = " ".join(c.text().lower() for c in cells if c)
            self.grid.setRowHidden(row, bool(text) and text not in haystack)

    def import_users(self):
        from slate.gui.dialogs.import_users_dialog import ImportUsersDialog
        dialog = ImportUsersDialog(self.user_manager, parent=self)
        dialog.exec()
        if dialog.imported:
            self.load_data()

    def export_csv(self):
        from PySide6.QtWidgets import QFileDialog
        from slate.core.domain.user_import import export_users_csv
        path, _ = QFileDialog.getSaveFileName(self, "Export users", "slate_users.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            export_users_csv(self.user_manager, path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not export: {e}")
            return
        QMessageBox.information(self, "Exported", f"Saved {len(self.users)} users to\n{path}")

    # ------------------------------------------------------------------ data
    def load_data(self):
        try:
            self.users = self.user_manager.get_all_users() or {}
        except Exception as e:
            logger.exception("Failed to load users: %s", e)
            self.users = {}

        self.grid.setRowCount(len(self.users))
        for r, (username, data) in enumerate(sorted(self.users.items(), key=lambda x: x[0])):
            roles = data.get('roles', [])
            if isinstance(roles, list):
                role_str = ", ".join(str(item) for item in roles if item)
            else:
                role_str = str(roles or "")

            joined = data.get('joined_on')
            joined_str = str(joined)[:10] if joined else "-"

            cells = [
                str(username),
                str(data.get('display_name', '')),
                str(data.get('job_title', '')),
                role_str,
                joined_str,
                str(data.get('employment') or "-"),
                str(data.get('reports_to') or "-"),
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                # A missing joining date is not cosmetic - accrual counts from
                # it, so say so rather than showing a tidy dash.
                if c == 4 and text == "-":
                    item.setForeground(QColor("#D9A441"))
                    item.setToolTip("No joining date, so leave accrues from 1 January")
                self.grid.setItem(r, c, item)
        if hasattr(self, "search"):
            self.apply_filter()

    def _selected_username(self):
        rows = sorted({item.row() for item in self.grid.selectedItems()})
        if not rows:
            return ""
        item = self.grid.item(rows[0], 0)
        return item.text() if item else ""

    # --------------------------------------------------------------- actions
    def add_user(self):
        dialog = UserDialog(self.user_manager, all_usernames=list(self.users.keys()),
                            parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.payload()
        try:
            success = self.user_manager.add_user(
                values["username"].lower(), values["password"], values["roles"],
                values["display_name"] or values["username"], values["job_title"],
                joined_on=values["joined_on"], employment=values["employment"],
                reports_to=values["reports_to"], location=values["location"],
            )
            if success:
                self.load_data()
                QMessageBox.information(
                    self, "Success",
                    "User '%s' created successfully in database." % values["username"])
            else:
                QMessageBox.warning(
                    self, "Error",
                    "Failed to add user '%s' to database." % values["username"])
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to add user: {e}")

    def edit_user(self):
        username = self._selected_username()
        if not username:
            QMessageBox.warning(self, "Selection Error", "Please select a user to edit.")
            return

        # Read the record, not the table. Reading the table is how every role
        # after the first was lost: the cell holds a comma-joined string and
        # only the first entry was ever put back.
        record = self.users.get(username) or {}
        dialog = UserDialog(self.user_manager, username=username, record=record,
                            all_usernames=list(self.users.keys()), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.payload()
        try:
            success = self.user_manager.add_user(
                username, "KEEP_OLD", values["roles"],
                values["display_name"] or username, values["job_title"],
                joined_on=values["joined_on"], employment=values["employment"],
                reports_to=values["reports_to"], location=values["location"],
            )
            if success:
                self.load_data()
                QMessageBox.information(self, "Success",
                                        "User '%s' updated successfully." % username)
            else:
                QMessageBox.warning(self, "Error",
                                    "Failed to update user '%s'." % username)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to update user: {e}")

    def reset_password(self):
        username = self._selected_username()
        if not username:
            QMessageBox.warning(self, "Selection Error",
                                "Please select a user to reset password.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Reset Password for {username}")
        dialog.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(dialog)

        pass_input = QLineEdit()
        pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("New Password:", pass_input)

        btn_box = QHBoxLayout()
        save_btn = QPushButton("Reset")
        save_btn.setStyleSheet("background-color: #D9635F; font-weight: bold; padding: 4px;")
        save_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addRow(btn_box)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            p = pass_input.text()
            if not p:
                QMessageBox.warning(self, "Error", "Password cannot be empty.")
                return
            try:
                curr_user = self.users.get(username, {})
                success = self.user_manager.add_user(
                    username, p,
                    curr_user.get("roles", ["Artist"]),
                    curr_user.get("display_name", username),
                    curr_user.get("job_title", ""),
                )
                if success:
                    # A password an admin set is not forced to change, even for
                    # somebody imported who never signed in with the first one.
                    self.user_manager.set_must_change_password(username, False)
                    QMessageBox.information(self, "Success",
                                            f"Password reset for '{username}'.")
                else:
                    QMessageBox.warning(self, "Error",
                                        f"Failed to reset password for '{username}'.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to reset password: {e}")

    def delete_user(self):
        username = self._selected_username()
        if not username:
            QMessageBox.warning(self, "Selection Error", "Please select a user to delete.")
            return

        if username.lower() in PROTECTED_USERNAMES:
            QMessageBox.warning(self, "Protected Account",
                                f"Cannot delete core system user '{username}'.")
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to permanently delete user '{username}' from the database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.user_manager.delete_user(username)
                if success:
                    self.load_data()
                    QMessageBox.information(self, "Deleted", f"User '{username}' was deleted.")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to delete user '{username}'.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete user: {e}")

    def style_table(self, table: QTableWidget):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setStyleSheet("""
            QTableWidget {
                background-color: #0D0D0F;
                color: #E8E6E1;
                gridline-color: #1D1D22;
                border: 1px solid #1D1D22;
                border-radius: 6px;
                font-size: 12px;
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: rgba(62, 168, 191, 0.18); color: white; }
            QHeaderView::section {
                background-color: #16161A;
                color: #87857F;
                border: none;
                border-bottom: 2px solid #1D1D22;
                border-right: 1px solid rgba(255, 255, 255, 0.04);
                padding: 8px 10px;
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
            }
        """)
