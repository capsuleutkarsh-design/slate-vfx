"""
Users & Roles: the one place people are added and given access.

Adding somebody creates a new account and nothing else - a username already
in use is refused, never quietly updated. Editing changes the profile and the
employment record; a field can be emptied as well as changed. Reset Password
is its own action. Every change is checked against what the signed-in person
may grant (UserManager.set_acting_user) and written to the audit log.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QComboBox, QMessageBox,
    QDialog, QLineEdit, QDateEdit, QCheckBox,
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QColor
import json
import logging
from slate.core.domain.user_manager import UserManager
from slate.core.domain.onboarding_service import EMPLOYMENT_TYPES
from slate.core.domain import people
from slate.core.domain.dates import format_date
from slate.core.infra.gate import Gate
from slate.gui.core.table_style import style_table
from slate.gui.core.controls import make_button, page_title, form_layout
from slate.gui.core.data_display import setup_date_edit
from slate.gui.components.table_tools import KeepSelection, make_item, selected_keys, setup_table

logger = logging.getLogger(__name__)

# Accounts that must survive a mis-click. EMP0012 is here because it used to be
# re-created on every start instead: deleting it appeared to work and then it
# came back. It is no longer re-created, so the studios that already have one
# need it protected rather than resurrected.
PROTECTED_USERNAMES = {"admin", "developer", "emp0012"}


def _locations():
    """The places the studio's people are in (what holidays are matched by), plus free text."""
    try:
        from slate.core.infra.leave_repository import LeaveRepository
        return LeaveRepository().locations()
    except Exception as exc:
        logger.debug("Locations not read: %s", exc)
        return []


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
    # has recorded this yet" and is shown as such.
    NOT_SET = QDate(1900, 1, 1)

    def __init__(self, user_manager, username=None, record=None,
                 all_usernames=(), parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.editing = username is not None
        self.username = username or ""
        self.record = record = record or {}

        self.setWindowTitle("Edit %s" % (record.get("display_name") or username)
                            if self.editing else "Add user")
        self.setObjectName("userDialog")
        self.setStyleSheet(f"QDialog#userDialog {{ background-color: {Gate.RAISED}; }}")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        form = form_layout()
        root.addLayout(form)

        # Editing: the username is shown as text - it cannot be changed, and a
        # read-only box looked editable.
        if self.editing:
            self.username_input = QLineEdit(self.username)
            self.username_input.hide()
            shown = QLabel(self.username)
            shown.setStyleSheet(f"color: {Gate.TEXT_2};")
            form.addRow("Username", shown)
        else:
            self.username_input = QLineEdit()
            self.username_input.setPlaceholderText("e.g. priya.sharma")
            self.username_input.textChanged.connect(self._check_username)
            form.addRow("Username", self.username_input)

        self.display_input = QLineEdit(str(record.get("display_name") or ""))
        form.addRow("Display name", self.display_input)

        from slate.core.domain.departments import staff_department_names
        self.dept_input = QComboBox()
        self.dept_input.setEditable(True)
        self.dept_input.addItems(staff_department_names())
        current_dept = str(record.get("job_title") or "")
        # A new person starts with no department chosen - it pre-selected the
        # first entry, so everybody added in a hurry became Matte Painting.
        self.dept_input.setCurrentIndex(-1)
        self.dept_input.lineEdit().setPlaceholderText("Choose department")
        if current_dept:
            self.dept_input.setCurrentText(current_dept)
        form.addRow("Department", self.dept_input)

        # Every role, not just the first: a dropdown of tick boxes, because a
        # person really can be both a Team Lead and a Compositor. A new person
        # starts with nothing ticked.
        from slate.gui.components.check_combo import CheckComboBox
        # Only roles the signed-in person may give (UserManager.assignable_roles),
        # plus whatever this person already has.
        if hasattr(self.user_manager, "assignable_roles"):
            available = list(self.user_manager.assignable_roles())
        else:
            available = list(self.user_manager.get_available_roles() or [])
        for held in record.get("roles") or []:
            if str(held).lower() not in {str(r).lower() for r in available}:
                available.append(str(held))
        if not available:
            available = ["Artist"]
        self.roles_input = CheckComboBox(placeholder="Choose one or more roles…")
        self.roles_input.add_items(sorted((str(r) for r in available), key=str.lower))
        self.roles_input.set_checked(record.get("roles") or [])
        form.addRow("Roles", self.roles_input)

        self.pass_input = None
        if not self.editing:
            self.pass_input = QLineEdit()
            self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("Password", self.pass_input)

        # ------------------------------------------------- employment record
        self.joined_input = setup_date_edit(QDateEdit())
        self.joined_input.setMinimumDate(self.NOT_SET)
        self.joined_input.setSpecialValueText("Not recorded")
        self.joined_input.setDate(self._as_qdate(record.get("joined_on")) or (
            QDate.currentDate() if not self.editing else self.NOT_SET))
        form.addRow("Joined on", self.joined_input)

        # Clearable, so somebody who left can be brought back: there was no
        # last-day field at all, and Reactivate pointed here.
        self.last_day_input = setup_date_edit(QDateEdit())
        self.last_day_input.setMinimumDate(self.NOT_SET)
        self.last_day_input.setSpecialValueText("Not set")
        self.last_day_input.setDate(self._as_qdate(record.get("last_day")) or self.NOT_SET)
        self.last_day_input.setToolTip("Their last working day. Once it has passed the account "
                                       "is switched off. Clear it (Not set) for somebody coming back.")
        form.addRow("Last working day", self.last_day_input)

        self.employment_input = QComboBox()
        self.employment_input.addItem("Not recorded", "")
        for kind in EMPLOYMENT_TYPES:
            self.employment_input.addItem(kind, kind)
        current_employment = str(record.get("employment") or "")
        if current_employment:
            index = self.employment_input.findData(current_employment)
            if index < 0:
                index = self.employment_input.findData(current_employment.title())
            if index < 0:
                self.employment_input.addItem(current_employment, current_employment)
                index = self.employment_input.count() - 1
            self.employment_input.setCurrentIndex(index)
        form.addRow("Employment", self.employment_input)

        # Who approves this person's leave at the first stage. Only people who
        # can approve leave are offered, by name: an artist named here left
        # the person's leave waiting for ever.
        self.reports_input = QComboBox()
        self.reports_input.addItem("Nobody (their leave goes to HR)", "")
        try:
            approvers = self.user_manager.approvers()
        except Exception:
            approvers = list(all_usernames)
        for name in approvers:
            if name.strip().lower() == self.username.strip().lower():
                continue
            self.reports_input.addItem(people.label(name), name)
        current_manager = str(record.get("reports_to") or "")
        if current_manager:
            index = self.reports_input.findData(current_manager)
            if index < 0:
                self.reports_input.addItem("%s - cannot approve leave" % people.label(current_manager),
                                           current_manager)
                index = self.reports_input.count() - 1
            self.reports_input.setCurrentIndex(index)
        form.addRow("Reports to", self.reports_input)

        self.location_input = QComboBox()
        self.location_input.setEditable(True)
        self.location_input.addItem("")
        self.location_input.addItems(_locations())
        self.location_input.setCurrentText(str(record.get("location") or ""))
        self.location_input.lineEdit().setPlaceholderText("Mumbai, Chennai, Remote…")
        form.addRow("Location", self.location_input)

        note = QLabel(
            "Joining date drives leave accrual. Location decides which public "
            "holidays their leave is charged against. Reports to decides whose "
            "queue their leave request lands in first."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px;")
        root.addWidget(note)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {Gate.BAD}; font-size: 12.5px;")
        self.error.hide()
        root.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.save_btn = make_button("Save" if self.editing else "Add user", "primary",
                                    on_click=self._accept_if_valid)
        buttons.addWidget(self.save_btn)
        root.addLayout(buttons)

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

    def _fail(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def _check_username(self, *_):
        if self.editing:
            return ""
        text = self.username_input.text().strip()
        problem = self.user_manager.username_problem(text) if text and hasattr(
            self.user_manager, "username_problem") else ""
        self._fail(problem)
        return problem

    def _accept_if_valid(self):
        if not self.username_input.text().strip():
            self._fail("Enter a username.")
            return
        if self._check_username():
            return
        if not self.selected_roles():
            self._fail("Pick at least one role. A person with no role can sign in and "
                       "do nothing, which reads as a broken account.")
            return
        if self.pass_input is not None and not self.pass_input.text():
            self._fail("Enter a first password for the new account.")
            return
        manager = self.reports_input.currentData() or ""
        # Only a new choice is checked: the person's current manager may have
        # left since, and that must not stop a change to their name.
        if manager and manager.lower() != str(self.record.get("reports_to") or "").lower() \
                and hasattr(self.user_manager, "reports_to_problem"):
            why = self.user_manager.reports_to_problem(self.username_input.text().strip(), manager)
            if why:
                self._fail(why)
                return
        joined, last = self.joined_input.date(), self.last_day_input.date()
        if last != self.NOT_SET and joined != self.NOT_SET and last < joined:
            self._fail("The last working day is before the joining date.")
            return
        was = self._as_qdate(self.record.get("last_day"))
        if last != self.NOT_SET and last < QDate.currentDate() and last != was:
            if QMessageBox.question(
                    self, self.windowTitle(),
                    "%s is in the past, so this switches the account off now. Save?"
                    % format_date(last.toPython()),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.accept()

    def payload(self):
        """
        What to save. For a new person anything not filled in is None. When
        editing, a field emptied on screen comes back as UserManager.CLEAR so
        it really is emptied - 'Nobody' and 'Not recorded' used to mean
        'leave whatever is there'.
        """
        clear = getattr(UserManager, "CLEAR", None)

        def value(new, field):
            if new:
                return new
            if self.editing and self.record.get(field):
                return clear
            return None

        joined = self.joined_input.date()
        joined_text = None if joined == self.NOT_SET else joined.toString("yyyy-MM-dd")
        last = self.last_day_input.date()
        last_text = None if last == self.NOT_SET else last.toString("yyyy-MM-dd")
        return {
            "username": self.username_input.text().strip(),
            "password": self.pass_input.text() if self.pass_input is not None else "KEEP_OLD",
            "roles": self.selected_roles(),
            "display_name": self.display_input.text().strip(),
            "job_title": self.dept_input.currentText().strip(),
            "joined_on": value(joined_text, "joined_on"),
            "employment": value(self.employment_input.currentData() or "", "employment"),
            "reports_to": value(self.reports_input.currentData() or "", "reports_to"),
            "location": value(self.location_input.currentText().strip(), "location"),
            "last_day": value(last_text, "last_day"),
        }


class AdminUsersTab(QWidget):
    """
    Users & Roles: the one place people are added and given access.

    Two tabs. Users is for whoever may manage users (HR, Admin, ...); Roles &
    Permissions is for whoever may edit permissions (Admin, IT, HR, ...). Each
    person sees the tabs their roles allow.
    """

    def __init__(self, user_role="Admin", user_data=None, parent=None):
        super().__init__(parent)
        self.user_role = user_role
        self.user_data = user_data or {}
        self.user_manager = UserManager()
        # Every change made here is checked against what this person may
        # grant - in UserManager, not only by what the screen offers.
        self.user_manager.set_acting_user(
            self.user_data.get("user_id") or self.user_data.get("username"))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        main_layout.addWidget(page_title("Users & Roles", "Who can sign in, and what each role may do"))

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
            lbl.setStyleSheet(f"color: {Gate.BAD}; font-size: 14px;")
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
        self.tabs.currentChanged.connect(self._refresh_current)
        main_layout.addWidget(self.tabs)

    def heightForWidth(self, width):
        """
        No height-for-width: fill the page frame, and let the users table and
        the permissions list scroll inside it.

        The wrapped subtitle and status lines made the layout report a
        height-for-width, and the page frame (PageScroll) then sized this page
        to its *preferred* height - 658 px in a 591 px frame at 1280x720 - so the
        whole page scrolled as well, and the role editor's Save and Delete
        buttons sat below the fold.
        """
        return -1

    def _refresh_current(self, _index):
        page = self.tabs.currentWidget()
        if page is self.role_editor and self.role_editor is not None:
            self.role_editor.refresh_roles(select=self.role_editor.current_role)
        elif page is self.users_panel and self.users_panel is not None:
            self.users_panel.load_data()


class UsersPanel(QWidget):
    COLUMNS = ["Username", "Display name", "Department", "Roles",
               "Joined", "Employment", "Reports to", "Location", "Status"]

    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.users = {}
        self._approvers = set()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 8, 0, 0)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(make_button("Add user", "primary", on_click=self.add_user))

        # Nothing selected: these are off (they answered with 'Selection
        # Error' pop-ups).
        self.edit_btn = make_button("Edit", "secondary", on_click=self.edit_user)
        self.reset_btn = make_button("Reset password", "secondary", on_click=self.reset_password)
        # Deactivate, not delete: a person who leaves keeps their attendance
        # and leave history. Delete is only for an account with no history.
        self._deactivate_tip = ("Switch the account off. Their history is kept and they disappear "
                                "from lists and pickers. Can be undone with Reactivate.")
        self._delete_tip = "Only for an account made by mistake, with no history at all."
        self.deactivate_btn = make_button(
            "Deactivate", "danger", on_click=self.deactivate_user, tooltip=self._deactivate_tip)
        self.reactivate_btn = make_button("Reactivate", "secondary", on_click=self.reactivate_user)
        self.delete_btn = make_button("Delete…", "ghost", on_click=self.delete_user,
                                      tooltip=self._delete_tip)
        self._needs_selection = [self.edit_btn, self.reset_btn, self.deactivate_btn,
                                 self.reactivate_btn, self.delete_btn]
        for button in self._needs_selection:
            controls.addWidget(button)

        controls.addStretch()
        controls.addWidget(make_button("Import…", "secondary", on_click=self.import_users,
                                       tooltip="Add many people at once from Excel or CSV"))
        controls.addWidget(make_button("Export CSV", "secondary", on_click=self.export_csv))
        main_layout.addLayout(controls)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search any column: name, department, role, manager, location…")
        self.search.textChanged.connect(self.apply_filter)
        search_row.addWidget(self.search, 1)
        self.show_inactive = QCheckBox("Show deactivated")
        self.show_inactive.setToolTip("Include people who were deactivated or whose last day has passed")
        self.show_inactive.toggled.connect(self.apply_filter)
        search_row.addWidget(self.show_inactive)
        main_layout.addLayout(search_row)

        self.grid = QTableWidget(0, len(self.COLUMNS))
        self.grid.setHorizontalHeaderLabels(self.COLUMNS)
        self.style_table(self.grid)
        self.grid.doubleClicked.connect(lambda _index: self.edit_user())
        self.grid.itemSelectionChanged.connect(self._sync_buttons)
        main_layout.addWidget(self.grid)
        self.load_data()

    def apply_filter(self, *_args):
        text = self.search.text().strip().lower()
        show_inactive = bool(self.show_inactive.isChecked())
        for row in range(self.grid.rowCount()):
            cells = [self.grid.item(row, c) for c in range(self.grid.columnCount())]
            haystack = " ".join((c.text() + " " + (c.toolTip() or "")).lower() for c in cells if c)
            first = self.grid.item(row, 0)
            active = first is None or first.data(Qt.ItemDataRole.UserRole + 1) is not False
            hidden = (bool(text) and text not in haystack) or (not active and not show_inactive)
            self.grid.setRowHidden(row, hidden)
        self._sync_buttons()

    def _selected_active(self):
        """Whether the selected account is active (None when nothing is selected)."""
        username = self._selected_username()
        if not username:
            return None
        return bool((self.users.get(username) or {}).get("active", True))

    def _sync_buttons(self, *_):
        """
        Each action only where it can work: Reactivate on a deactivated account,
        Deactivate on an active one, and neither Deactivate nor Delete on the
        protected system accounts. Every button used to light up for any row.
        """
        username = self._selected_username()
        active = self._selected_active()
        protected = username.lower() in PROTECTED_USERNAMES if username else False
        me = str(getattr(self.user_manager, "acting_user", "") or "").lower()
        yours = bool(username) and username.lower() == me
        self.edit_btn.setEnabled(bool(username))
        self.reset_btn.setEnabled(bool(username))
        self.deactivate_btn.setEnabled(bool(username) and bool(active) and not protected and not yours)
        self.reactivate_btn.setEnabled(bool(username) and active is False)
        self.delete_btn.setEnabled(bool(username) and not protected and not yours)
        why = ("%s is a system account." % username if protected else
               "This is your own account." if yours else "")
        self.deactivate_btn.setToolTip(why or self._deactivate_tip)
        self.delete_btn.setToolTip(why or self._delete_tip)

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
            QMessageBox.warning(self, "Export users", f"The list could not be saved: {e}")
            return
        from slate.gui.components.feedback import toast
        toast(self, "Saved %d people to %s" % (len(self.users), path), "success")

    # ------------------------------------------------------------------ data
    def load_data(self):
        try:
            self.users = self.user_manager.get_all_users() or {}
        except Exception as e:
            logger.exception("Failed to load users: %s", e)
            self.users = {}
        try:
            self._approvers = {u.lower() for u in self.user_manager.approvers()}
        except Exception:
            self._approvers = set()

        with KeepSelection(self.grid):
            self.grid.setRowCount(len(self.users))
            for r, (username, data) in enumerate(sorted(self.users.items(), key=lambda x: x[0])):
                roles = data.get('roles', [])
                role_str = ", ".join(str(i) for i in roles if i) if isinstance(roles, list) else str(roles or "")
                joined = data.get('joined_on')
                active = bool(data.get('active', True))
                status, tone = self.user_manager.account_status(dict(data, username=username))
                manager = str(data.get('reports_to') or "")
                # System accounts are not people: no joining date or manager to chase.
                person = active and not data.get('is_service')
                no_approver = person and (not manager or manager.lower() not in self._approvers)
                dim = Gate.TEXT_DIM if not active else None
                cells = [
                    make_item(username, key=username, foreground=dim),
                    make_item(str(data.get('display_name', '')), foreground=dim),
                    make_item(str(data.get('job_title', '')), foreground=dim),
                    make_item(role_str, tooltip=role_str or None, foreground=dim),
                    make_item(format_date(joined) if joined else "-",
                              sort_value=str(joined)[:10] if joined else None,
                              # A missing joining date is not cosmetic -
                              # accrual counts from it.
                              foreground=dim or (None if joined or not person else Gate.WARN),
                              tooltip=None if joined or not person else
                              "No joining date, so leave accrues from 1 January"),
                    make_item(str(data.get('employment') or "-"), foreground=dim),
                    make_item(people.display_name(manager) if manager else "-",
                              tooltip=(manager or "") + (" - cannot approve leave; their leave goes to HR"
                                                         if manager and no_approver else
                                                         ("No manager; their leave goes to HR"
                                                          if not manager and person else "")),
                              foreground=dim or (Gate.WARN if no_approver else None)),
                    make_item(str(data.get('location') or "-"), foreground=dim),
                    make_item(status, foreground=dim or (Gate.WARN if tone == "warn" else None)),
                ]
                cells[0].setData(Qt.ItemDataRole.UserRole + 1, active)
                for c, item in enumerate(cells):
                    self.grid.setItem(r, c, item)
        self.apply_filter()

    def _selected_username(self):
        keys = selected_keys(self.grid)
        return str(keys[0]) if keys else ""

    # --------------------------------------------------------------- actions
    def add_user(self):
        dialog = UserDialog(self.user_manager, all_usernames=list(self.users.keys()), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.payload()
        try:
            ok, message = self.user_manager.create_user(
                values["username"], values["password"], values["roles"],
                values["display_name"] or values["username"], values["job_title"],
                joined_on=values["joined_on"], employment=values["employment"],
                reports_to=values["reports_to"], location=values["location"],
                last_day=values["last_day"],
            )
        except PermissionError as refused:
            QMessageBox.warning(self, "Add user", str(refused))
            return
        if not ok:
            QMessageBox.warning(self, "Add user", message)
            return
        self.load_data()
        from slate.gui.components.feedback import toast
        toast(self, message + ".", "success")

    def edit_user(self):
        username = self._selected_username()
        if not username:
            return
        # Read the record, not the table: the cell holds a comma-joined string.
        record = self.users.get(username) or {}
        dialog = UserDialog(self.user_manager, username=username, record=record,
                            all_usernames=list(self.users.keys()), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.payload()
        # Your own Admin / HR role, unticked by accident, is the way out of
        # this screen. The role editor already asked; this dialog did not.
        warning = self.user_manager.lockout_warning(username, values["roles"]) \
            if hasattr(self.user_manager, "lockout_warning") else ""
        if warning and QMessageBox.warning(
                self, "You will lose access", warning,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            success = self.user_manager.update_user(
                username, roles=values["roles"],
                display_name=values["display_name"] or None, job_title=values["job_title"],
                joined_on=values["joined_on"], employment=values["employment"],
                reports_to=values["reports_to"], location=values["location"],
                last_day=values["last_day"],
            )
        except PermissionError as refused:
            QMessageBox.warning(self, "Edit user", str(refused))
            return
        except Exception as e:
            QMessageBox.warning(self, "Edit user", f"The changes could not be saved: {e}")
            return
        if success:
            self.load_data()
            from slate.gui.components.feedback import toast
            toast(self, "Saved %s." % people.label(username), "success")
        else:
            QMessageBox.warning(self, "Edit user", "%s could not be saved. Try again." % username)

    def reset_password(self):
        username = self._selected_username()
        if not username:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Reset password")
        dialog.setObjectName("resetDialog")
        dialog.setStyleSheet(f"QDialog#resetDialog {{ background-color: {Gate.RAISED}; }}")
        dialog.setMinimumWidth(380)
        root = QVBoxLayout(dialog)
        form = form_layout()
        root.addLayout(form)
        form.addRow("Person", QLabel(people.label(username)))
        pass_input = QLineEdit()
        pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", pass_input)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=dialog.reject))
        buttons.addWidget(make_button("Reset password", "primary", on_click=dialog.accept))
        root.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # Only the password: it went through add_user, which blanked the
        # picture and was audited as 'Updated roles'.
        try:
            ok, message = self.user_manager.set_password(username, pass_input.text())
        except PermissionError as refused:
            ok, message = False, str(refused)
        except Exception as e:
            ok, message = False, f"The password could not be reset: {e}"
        if ok:
            QMessageBox.information(self, "Reset password", message)
        else:
            QMessageBox.warning(self, "Reset password", message)

    def deactivate_user(self):
        username = self._selected_username()
        if not username:
            return
        kept = ("Their attendance, leave and other history are kept. They disappear from "
                "lists and pickers. You can reactivate them later.")
        blocking = self.user_manager.open_items(username, include_reports=False)
        if blocking:
            QMessageBox.warning(self, "Deactivate", "%s still has %s. Handle those first, then "
                                "deactivate the account." % (people.label(username), "; ".join(blocking)))
            return
        team = self.user_manager.reports_of(username)
        if team:
            # A supervisor's team and the leave waiting on them, moved in the
            # same step - deactivating left 101 people with nobody to approve.
            new_manager = self._ask_new_manager(username, kept)
            if new_manager is None:
                return
            moved, problems = self.user_manager.move_reports(username, new_manager)
            if problems:
                self.load_data()
                QMessageBox.warning(self, "Deactivate", "%s moved, but not:\n\n%s\n\n%s is still "
                                    "active." % (people.plural(moved, "person", "people"),
                                                 "\n".join(problems[:10]), people.label(username)))
                return
        else:
            reply = QMessageBox.question(
                self, "Deactivate", f"Deactivate {people.label(username)}?\n\n{kept}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        ok, message = self.user_manager.deactivate_user(username)
        if ok:
            self.load_data()
            QMessageBox.information(self, "Deactivate", message)
        else:
            QMessageBox.warning(self, "Deactivate", message)

    def _ask_new_manager(self, username, kept):
        """Who a leaving supervisor's team reports to from now on: a username, "" (HR) or None (cancel)."""
        team = self.user_manager.reports_of(username)
        items = self.user_manager.open_items(username)
        dialog = QDialog(self)
        dialog.setWindowTitle("Deactivate")
        dialog.setObjectName("moveDialog")
        dialog.setStyleSheet(f"QDialog#moveDialog {{ background-color: {Gate.RAISED}; }}")
        dialog.setMinimumWidth(460)
        root = QVBoxLayout(dialog)
        text = QLabel("%s has %s.\n\nMove their team to somebody else before the account is "
                      "switched off. Leave waiting on them follows the team.\n\n%s"
                      % (people.label(username), " and ".join(items), kept))
        text.setWordWrap(True)
        root.addWidget(text)
        form = form_layout()
        choice = QComboBox()
        choice.addItem("Nobody (their leave goes to HR)", "")
        for name in self.user_manager.approvers():
            if name.lower() != username.lower():
                choice.addItem(people.label(name), name)
        form.addRow("New manager", choice)
        root.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("Cancel", "ghost", on_click=dialog.reject))
        buttons.addWidget(make_button("Move %s and deactivate" % people.plural(len(team), "person", "people"),
                                      "danger", on_click=dialog.accept))
        root.addLayout(buttons)
        self._move_choice = choice                 # for tests
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return choice.currentData() or ""

    def reactivate_user(self):
        username = self._selected_username()
        if not username:
            return
        # Somebody who left (last day passed) only comes back with that day
        # cleared - asked here, in the same step.
        past = self.user_manager.past_last_day(username)
        if past:
            if QMessageBox.question(
                    self, "Reactivate",
                    "%s's last working day was %s. Reactivate them and clear that last day?"
                    % (people.label(username), format_date(past)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        ok, message = self.user_manager.reactivate_user(username, clear_last_day=bool(past))
        if ok:
            self.load_data()
            QMessageBox.information(self, "Reactivate", message)
        else:
            QMessageBox.warning(self, "Reactivate", message)

    def delete_user(self):
        """
        Permanent removal, for an account made by mistake. Anybody with any
        history is refused (UserManager.delete_user) and pointed at Deactivate.
        """
        username = self._selected_username()
        if not username:
            return
        if username.lower() in PROTECTED_USERNAMES:
            QMessageBox.warning(self, "Delete account",
                                f"{username} is a system account and cannot be deleted.")
            return
        reply = QMessageBox.question(
            self, "Delete account",
            f"Delete {people.label(username)} for good?\n\nOnly an account with no history can be "
            "deleted. Anybody who has worked here should be deactivated instead.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            success = self.user_manager.delete_user(username)
        except Exception as e:
            QMessageBox.warning(self, "Delete account", f"{username} could not be deleted: {e}")
            return
        if success:
            self.load_data()
            QMessageBox.information(self, "Delete account", f"{username} was deleted.")
        else:
            QMessageBox.warning(self, "Delete account",
                                getattr(self.user_manager, "last_error", "")
                                or f"{username} could not be deleted.")

    def style_table(self, table: QTableWidget):
        """The shared table style; short columns sized to their contents, the name stretches."""
        style_table(table, {
            "Username": "contents", "Display name": "stretch",
            "Department": ("interactive", 150), "Roles": ("interactive", 200),
            "Joined": "contents", "Employment": "contents", "Reports to": ("interactive", 150),
            "Location": "contents", "Status": "contents",
        }, multi_select=False)
        # Sortable: 150 people were only ever in username order.
        setup_table(table, multi_select=False)
