from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QCheckBox, QPushButton, QLabel, QMessageBox,
    QInputDialog, QGridLayout, QFrame, QScrollArea
)
from PySide6.QtCore import Qt

from slate.core.domain import permissions_catalog as catalog
from slate.core.domain.user_manager import UserManager
from slate.core.infra.gate import Gate

ROLE_NAME_ROLE = Qt.ItemDataRole.UserRole


class RoleEditor(QWidget):
    """
    Admin UI for editing roles: the tabs each role opens and what it may do.

    The tabs and abilities come from permissions_catalog, the same list the
    sidebar is built from, so a tab added to Slate can be granted here. What a
    role has stored but this screen does not know is kept untouched on save -
    ticking one box used to rewrite the whole list and drop the rest.
    """
    def __init__(self, user_manager: UserManager, parent=None, editor_username=None):
        super().__init__(parent)
        self.user_manager = user_manager
        self.editor_username = editor_username
        self.current_role = None
        self._stored = []          # the selected role's permissions exactly as stored
        self._role_refusal = ""    # why the selected role is read-only for this editor
        self.can_edit = self._editor_may_edit()
        # Every save is checked in UserManager against what this editor may
        # grant, not only by the boxes on this screen.
        if editor_username and hasattr(user_manager, "set_acting_user") \
                and not getattr(user_manager, "acting_user", None):
            user_manager.set_acting_user(editor_username)

        self.setup_ui()
        self.refresh_roles()

    # ------------------------------------------------------------ who may edit
    def _editor_roles(self):
        if not self.editor_username:
            return []
        wanted = str(self.editor_username).strip().lower()
        for username, data in (self.user_manager.get_all_users() or {}).items():
            if str(username).strip().lower() == wanted:
                return data.get("roles") or []
        return []

    def _editor_may_edit(self):
        """Admin, IT, HR and Developer - or any role given "Edit roles and permissions"."""
        if not self.editor_username:
            return True        # built without a signed-in user (tests, tools)
        from slate.core.domain.access import can
        return can(self._editor_roles(), "manage_permissions")

    # ------------------------------------------------------------------- UI
    def setup_ui(self):
        # Main Layout (No Margins to fit nicely in parent tab)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # CARD CONTAINER
        # Wraps everything in a distinct background to avoid the "void" look
        self.card = QFrame()
        self.card.setObjectName("RoleEditorCard")
        self.card.setStyleSheet(f"""
            QFrame#RoleEditorCard {{
                background-color: {Gate.RAISED};
                border-radius: 12px;
                border: 1px solid {Gate.RAISED_HI};
            }}
            QLabel {{
                border: none;
                color: {Gate.TEXT};
            }}
            QListWidget {{
                background-color: {Gate.PANEL};
                border: 1px solid {Gate.RAISED_HI};
                border-radius: 6px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 10px;
                color: {Gate.TEXT_2};
            }}
            QListWidget::item:selected {{
                background-color: {Gate.ACCENT};
                color: {Gate.TEXT_ON_ACCENT};
                border-radius: 4px;
            }}
            QCheckBox {{
                color: {Gate.TEXT};
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid {Gate.LINE};
                background: {Gate.RAISED};
            }}
            QCheckBox::indicator:checked {{
                background: {Gate.ACCENT};
                border: 1px solid {Gate.ACCENT};
            }}
            QScrollArea {{ background: transparent; border: none; }}
        """)

        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(20)

        # --- LEFT PANEL: ROLE LIST ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        lbl_roles = QLabel("ROLES")
        lbl_roles.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {Gate.TEXT_DIM}; letter-spacing: 1px;")
        left_layout.addWidget(lbl_roles)

        self.role_list = QListWidget()
        self.role_list.setFixedWidth(240)  # Fixed width for sidebar feel
        self.role_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.role_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.role_list.itemClicked.connect(self.on_role_selected)
        left_layout.addWidget(self.role_list)

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("New Role")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setStyleSheet(f"background-color: {Gate.RAISED}; color: {Gate.TEXT}; border: 1px solid {Gate.LINE}; border-radius: 4px; padding: 6px;")
        self.btn_add.clicked.connect(self.add_role)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setStyleSheet(f"background-color: {Gate.BAD_SURFACE}; color: {Gate.BAD}; border: 1px solid {Gate.BAD}; border-radius: 4px; padding: 6px;")
        self.btn_delete.clicked.connect(self.delete_role)

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_delete)
        left_layout.addLayout(btn_layout)
        self.btn_add.setEnabled(self.can_edit)
        self.btn_delete.setEnabled(self.can_edit)

        # --- RIGHT PANEL: PERMISSIONS ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Header
        self.lbl_editing = QLabel("Select a role to edit permissions")
        self.lbl_editing.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {Gate.TEXT}; margin-bottom: 4px;")
        right_layout.addWidget(self.lbl_editing)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"color: {Gate.TEXT_DIM}; margin-bottom: 8px;")
        right_layout.addWidget(self.lbl_status)

        if not self.can_edit:
            read_only = QLabel("You can look, but only Admin, IT, HR and Developer "
                               "(or a role with \"Edit roles and permissions\") can change roles.")
            read_only.setWordWrap(True)
            read_only.setStyleSheet("color: #D9A55F; margin-bottom: 8px;")
            right_layout.addWidget(read_only)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        perm_container = QFrame()
        perm_container.setObjectName("PermBox")      # by name: a bare QFrame rule also hits every QLabel
        perm_container.setStyleSheet(f"QFrame#PermBox {{ background-color: {Gate.PANEL}; border-radius: 8px; border: 1px solid {Gate.RAISED}; }}")
        perm_layout = QVBoxLayout(perm_container)
        perm_layout.setContentsMargins(20, 20, 20, 20)
        perm_layout.setSpacing(10)

        # Full access
        self.cb_all = QCheckBox("Full access - every tab and every ability, including ones added later")
        self.cb_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb_all.stateChanged.connect(self.on_all_changed)
        perm_layout.addWidget(self.cb_all)

        # Tabs, by group
        perm_layout.addWidget(self._section("TABS THIS ROLE CAN OPEN"))
        always = QLabel("Always open to everyone: " + ", ".join(catalog.ALWAYS_OPEN))
        always.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-style: italic;")
        perm_layout.addWidget(always)

        self.tab_boxes = {}
        for group in catalog.TAB_GROUPS:
            perm_layout.addWidget(self._subsection(group))
            grid = QGridLayout()
            grid.setHorizontalSpacing(15)
            grid.setVerticalSpacing(8)
            tabs = [t for t in catalog.TABS if t.group == group]
            for i, tab in enumerate(tabs):
                cb = QCheckBox(tab.label.replace("&", "&&"))   # a single & is a shortcut marker
                cb.setToolTip(f"Opens: {tab.opens}")
                cb.setCursor(Qt.CursorShape.PointingHandCursor)
                cb.stateChanged.connect(self.on_perm_changed)
                self.tab_boxes[tab.key] = cb
                grid.addWidget(cb, i // 3, i % 3)
            perm_layout.addLayout(grid)

        # Abilities
        perm_layout.addWidget(self._section("WHAT THIS ROLE CAN DO"))
        ability_grid = QGridLayout()
        ability_grid.setHorizontalSpacing(15)
        ability_grid.setVerticalSpacing(8)
        self.ability_boxes = {}
        for i, ability in enumerate(catalog.ABILITIES):
            cb = QCheckBox(ability.label)
            cb.setToolTip(ability.help)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.stateChanged.connect(self.on_perm_changed)
            self.ability_boxes[ability.key] = cb
            ability_grid.addWidget(cb, i // 2, i % 2)
        perm_layout.addLayout(ability_grid)
        perm_layout.addStretch()

        scroll.setWidget(perm_container)
        right_layout.addWidget(scroll, 1)

        # Add panels to Card Layout
        card_layout.addWidget(left_panel)
        card_layout.addWidget(right_panel, stretch=1)  # Right side expands

        # Add Card to Main Layout
        main_layout.addWidget(self.card)

    @staticmethod
    def _section(text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {Gate.ACCENT}; letter-spacing: 1px; margin-top: 10px;")
        return lbl

    @staticmethod
    def _subsection(text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {Gate.TEXT_DIM}; margin-top: 4px;")
        return lbl

    def _all_boxes(self):
        return [self.cb_all, *self.tab_boxes.values(), *self.ability_boxes.values()]

    def block_signals_checkboxes(self, block):
        for cb in self._all_boxes():
            cb.blockSignals(block)

    # ----------------------------------------------------------------- roles
    def refresh_roles(self, select=None):
        self.role_list.clear()
        users = self.user_manager.get_all_users() or {}
        counts = {}
        for data in users.values():
            roles = data.get("roles") or []
            for r in ([roles] if isinstance(roles, str) else roles):
                counts[str(r).strip().lower()] = counts.get(str(r).strip().lower(), 0) + 1
        for role in sorted(self.user_manager.get_available_roles(), key=lambda r: str(r).lower()):
            n = counts.get(str(role).strip().lower(), 0)
            item = QListWidgetItem(f"{role}   ({n})" if n else role)
            item.setData(ROLE_NAME_ROLE, role)
            item.setToolTip(f"{n} user(s) have this role" if n else "Nobody has this role yet")
            self.role_list.addItem(item)

        # Reset Interaction State
        self.current_role = None
        self._stored = []
        self.lbl_editing.setText("Select a role to edit")
        self.lbl_status.setText("")
        self.block_signals_checkboxes(True)
        for cb in self._all_boxes():
            cb.setChecked(False)
            cb.setEnabled(False)
            cb.setStyleSheet(f"color: {Gate.LINE};")  # Dim disabled
        self.block_signals_checkboxes(False)

        if select:
            for row in range(self.role_list.count()):
                item = self.role_list.item(row)
                if str(item.data(ROLE_NAME_ROLE)).lower() == str(select).lower():
                    self.role_list.setCurrentItem(item)
                    self.on_role_selected(item)
                    break

    def _is_locked(self, role):
        # Developer is the way back in if everything else is misconfigured.
        return str(role or "").strip().lower() == "developer"

    def _is_superuser(self):
        from slate.core.domain.access import is_superuser
        return not self.editor_username or is_superuser(self._editor_roles())

    def _grant_refusal_for_role(self, role, stored):
        """Why this editor may not change the role at all ("" if they may)."""
        if not self.editor_username:
            return ""
        from slate.core.domain.access import role_change_refusal
        try:
            return role_change_refusal(self._editor_roles(), role, stored, stored,
                                       self.user_manager.roles_config)
        except Exception:
            return ""

    def _may_grant(self, permission):
        if self._is_superuser():
            return True
        from slate.core.domain.access import can_grant
        return can_grant(self._editor_roles(), [permission], self.user_manager.roles_config)

    def on_role_selected(self, item):
        self.current_role = item.data(ROLE_NAME_ROLE) or item.text()
        self.lbl_editing.setText(f"Permissions: <span style='color:{Gate.ACCENT};'>{self.current_role.upper()}</span>")
        self._stored = self.user_manager.role_permissions(self.current_role)
        holders = self.user_manager.users_with_role(self.current_role)
        status = f"{len(holders)} user(s) have this role." if holders else "Nobody has this role yet."
        self._role_refusal = ""
        if self._is_locked(self.current_role):
            status += " Developer always has full access and cannot be changed."
        elif self.can_edit:
            self._role_refusal = self._grant_refusal_for_role(self.current_role, self._stored)
            if self._role_refusal:
                status += " " + self._role_refusal
        if self.can_edit and not self._role_refusal and not self._is_locked(self.current_role):
            status += " Changes save immediately."
            if not self._is_superuser():
                status += (" You can give only what you hold yourself; Full access and "
                           "the sensitive abilities are for Admin and Developer.")
        self.lbl_status.setText(status)
        self._show_stored()

    def _show_stored(self):
        stored = self._stored
        full = catalog.has_all(stored)
        abilities = catalog.abilities_in(stored)
        editable = (self.can_edit and not self._is_locked(self.current_role)
                     and not self._role_refusal)
        superuser = self._is_superuser()
        not_yours = "Only what you hold yourself can be given - ask an Admin or Developer."

        self.block_signals_checkboxes(True)
        self.cb_all.setChecked(full)
        # Full access is Admin and Developer's to give, and nobody else's.
        self.cb_all.setEnabled(editable and superuser)
        if not superuser:
            self.cb_all.setToolTip("Only an Admin or Developer can give Full access.")
        for key, cb in self.tab_boxes.items():
            checked = full or key in stored
            cb.setChecked(checked)
            grantable = checked or self._may_grant(key)
            cb.setEnabled(editable and not full and grantable)
            if not grantable:
                cb.setToolTip(not_yours)
        for key, cb in self.ability_boxes.items():
            granted_by_all = full and key not in catalog.NOT_IMPLIED_BY_ALL
            checked = granted_by_all or key in abilities
            cb.setChecked(checked)
            grantable = checked or self._may_grant(catalog.ability_key(key))
            cb.setEnabled(editable and not granted_by_all and grantable)
            if not grantable:
                cb.setToolTip(
                    "Only an Admin or Developer can give this." if key in catalog.SENSITIVE_ABILITIES
                    else not_yours)
        for cb in self._all_boxes():
            cb.setStyleSheet(f"color: {Gate.TEXT}; font-weight: bold;" if cb.isChecked() else f"color: {Gate.TEXT_2};")
        self.block_signals_checkboxes(False)

    # ----------------------------------------------------------------- saving
    def _permissions_from_boxes(self):
        """The boxes on screen, plus everything stored that this screen does not show."""
        known = set(catalog.TAB_KEYS) | {catalog.ALL.lower()}
        kept = []
        for p in self._stored:
            text = str(p).strip()
            if text.upper() == catalog.ALL or text in known:
                continue
            if text.lower().startswith(catalog.ABILITY_PREFIX) and \
                    text[len(catalog.ABILITY_PREFIX):].lower() in catalog.ABILITY_KEYS:
                continue
            kept.append(p)                      # unknown to this version: leave it alone

        perms = []
        if self.cb_all.isChecked():
            perms.append(catalog.ALL)
        for key, cb in self.tab_boxes.items():
            if cb.isChecked():
                perms.append(key)
        for key, cb in self.ability_boxes.items():
            if cb.isChecked():
                perms.append(catalog.ability_key(key))
        return perms + kept

    def _warn_if_locking_self_out(self, new_perms):
        """Stop an editor removing their own way back to this screen."""
        held = {str(r).strip().lower() for r in self._editor_roles()}
        if str(self.current_role).strip().lower() not in held:
            return True
        full = catalog.has_all(new_perms)
        from slate.core.domain.access import roles_for
        by_name = str(self.current_role).strip().lower() in roles_for("manage_permissions")
        keeps_edit = full or by_name or "manage_permissions" in catalog.abilities_in(new_perms)
        if keeps_edit:
            return True
        answer = QMessageBox.warning(
            self, "You will lose access",
            f"You have the {self.current_role} role. Without \"Edit roles and permissions\" "
            "you will not be able to open or change this screen again (unless another of "
            "your roles allows it).\n\nMake this change?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _save(self):
        if not self.current_role or not self.can_edit or self._is_locked(self.current_role):
            return
        new_perms = self._permissions_from_boxes()
        if not self._warn_if_locking_self_out(new_perms):
            self._show_stored()                 # put the boxes back as they were
            return
        from slate.core.domain.access import GrantRefused
        try:
            if self.user_manager.update_role_permissions(self.current_role, new_perms):
                self._stored = self.user_manager.role_permissions(self.current_role)
        except GrantRefused as refused:
            QMessageBox.warning(self, "Change role", str(refused))
        self._show_stored()

    def on_perm_changed(self):
        self._save()

    def on_all_changed(self):
        if not self.current_role:
            return
        if not self.cb_all.isChecked():
            answer = QMessageBox.question(
                self, "Remove full access",
                f"{self.current_role} will keep only the boxes ticked below, and will not get "
                "tabs added to Slate later. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self._show_stored()
                return
        self._save()

    # ------------------------------------------------------------ add / delete
    def add_role(self):
        if not self.can_edit:
            return
        name, ok = QInputDialog.getText(self, "New Role", "Role Name:")
        if not (ok and name):
            return
        name = " ".join(name.split())
        if not name or len(name) > 40:
            QMessageBox.warning(self, "Error", "Give the role a name of up to 40 characters.")
            return
        if self.user_manager.role_exists(name):   # case-insensitive: "hr" is "HR"
            QMessageBox.warning(self, "Error", "Role already exists!")
            return

        # Create with the basics everybody needs; tick the rest.
        from slate.core.domain.access import GrantRefused
        try:
            self.user_manager.create_role(name, ["Settings"])
        except GrantRefused as refused:
            QMessageBox.warning(self, "New role", str(refused))
            return
        self.refresh_roles(select=name)

    def delete_role(self):
        if not (self.current_role and self.can_edit):
            return
        if self._is_locked(self.current_role):
            QMessageBox.critical(self, "Error", "Cannot delete Developer role!")
            return
        holders = self.user_manager.users_with_role(self.current_role)
        if holders:
            shown = ", ".join(holders[:10]) + (f" and {len(holders) - 10} more" if len(holders) > 10 else "")
            QMessageBox.warning(
                self, "Role in use",
                f"{len(holders)} user(s) still have the {self.current_role} role:\n\n{shown}\n\n"
                "Give them another role in User Mgmt first, then delete it.")
            return

        confirm = QMessageBox.question(self, "Confirm", f"Delete role '{self.current_role}'?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            from slate.core.domain.access import GrantRefused
            try:
                self.user_manager.delete_role(self.current_role)
            except GrantRefused as refused:
                QMessageBox.warning(self, "Delete role", str(refused))
                return
            self.refresh_roles()
