from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QMessageBox,
    QDialog, QFormLayout, QLineEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
import json
import logging
from slate.core.domain.user_manager import UserManager

logger = logging.getLogger(__name__)

class AdminUsersTab(QWidget):
    def __init__(self, user_role="Admin", user_data=None, parent=None):
        super().__init__(parent)
        self.user_role = user_role
        self.user_data = user_data or {}
        self.user_manager = UserManager()
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        header_title = QLabel("User Management")
        header_title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        header_title.setStyleSheet("color: white;")
        main_layout.addWidget(header_title)
        
        # Access control
        if self.user_role.lower() not in ["hr", "admin", "developer", "supervisor"]:
            lbl = QLabel("You do not have permission to access User Management.")
            lbl.setStyleSheet("color: #D9635F; font-size: 14px;")
            main_layout.addWidget(lbl)
            main_layout.addStretch()
            return
            
        controls = QHBoxLayout()
        controls.setSpacing(10)
        add_btn = QPushButton("+ Add New User")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_user)
        controls.addWidget(add_btn)
        
        edit_btn = QPushButton("Edit Role / Dept")
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
        
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 4)
        self.grid.setHorizontalHeaderLabels(["Username", "Display Name", "Department (Job Title)", "Roles"])
        self.style_table(self.grid)
        self.load_data()
        
        main_layout.addWidget(self.grid)

    def load_data(self):
        try:
            users_dict = self.user_manager.get_all_users() or {}
        except Exception as e:
            logger.exception("Failed to load users: %s", e)
            users_dict = {}
            
        self.grid.setRowCount(len(users_dict))
        for r, (username, data) in enumerate(sorted(users_dict.items(), key=lambda x: x[0])):
            self.grid.setItem(r, 0, QTableWidgetItem(str(username)))
            self.grid.setItem(r, 1, QTableWidgetItem(str(data.get('display_name', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(data.get('job_title', ''))))
            
            roles = data.get('roles', [])
            if isinstance(roles, list):
                role_str = ", ".join(str(item) for item in roles if item)
            else:
                role_str = str(roles or "")
            self.grid.setItem(r, 3, QTableWidgetItem(role_str))

    def add_user(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Add New User")
        dialog.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(dialog)
        
        username_input = QLineEdit()
        display_input = QLineEdit()
        dept_input = QComboBox()
        from slate.core.domain.departments import staff_department_names
        dept_input.addItems(staff_department_names())
        dept_input.setStyleSheet("background: #26262D; padding: 4px;")
        
        role_input = QComboBox()
        available_roles = self.user_manager.get_available_roles() or [
            "Artist", "Coordinator", "Lead", "Supervisor", "Developer", "Tester"
        ]
        role_input.addItems(available_roles)
        role_input.setStyleSheet("background: #26262D; padding: 4px;")
        
        pass_input = QLineEdit()
        pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        layout.addRow("Username:", username_input)
        layout.addRow("Display Name:", display_input)
        layout.addRow("Department:", dept_input)
        layout.addRow("Role:", role_input)
        layout.addRow("Password:", pass_input)
        
        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("background-color: #5FBF8F; font-weight: bold; padding: 4px;")
        save_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addRow(btn_box)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            u = username_input.text().strip().lower()
            d = display_input.text().strip() or u
            jt = dept_input.currentText()
            r = role_input.currentText()
            p = pass_input.text()
            
            if not u or not p:
                QMessageBox.warning(self, "Error", "Username and Password are required.")
                return
                
            try:
                success = self.user_manager.add_user(u, p, [r], d, jt)
                if success:
                    self.load_data()
                    QMessageBox.information(self, "Success", f"User '{u}' created successfully in database.")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to add user '{u}' to database.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to add user: {e}")

    def edit_user(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Error", "Please select a user to edit.")
            return
            
        row = list(selected_rows)[0]
        item0 = self.grid.item(row, 0)
        item1 = self.grid.item(row, 1)
        item2 = self.grid.item(row, 2)
        item3 = self.grid.item(row, 3)
        username = item0.text() if item0 else ""
        current_display = item1.text() if item1 else username
        current_dept = item2.text() if item2 else ""
        current_role_str = item3.text() if item3 else "Artist"
        current_role = current_role_str.split(",")[0].strip() if current_role_str else "Artist"
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {username}")
        dialog.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(dialog)
        
        display_input = QLineEdit(current_display)
        dept_input = QComboBox()
        from slate.core.domain.departments import staff_department_names
        dept_input.addItems(staff_department_names())
        dept_input.setCurrentText(current_dept)
        dept_input.setStyleSheet("background: #26262D; padding: 4px;")
        
        role_input = QComboBox()
        available_roles = self.user_manager.get_available_roles() or [
            "Artist", "Coordinator", "Lead", "Supervisor", "Developer", "Tester"
        ]
        role_input.addItems(available_roles)
        role_input.setCurrentText(current_role)
        role_input.setStyleSheet("background: #26262D; padding: 4px;")
        
        layout.addRow("Display Name:", display_input)
        layout.addRow("Department:", dept_input)
        layout.addRow("Role:", role_input)
        
        btn_box = QHBoxLayout()
        save_btn = QPushButton("Update")
        save_btn.setStyleSheet("background-color: #5FBF8F; font-weight: bold; padding: 4px;")
        save_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addRow(btn_box)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_disp = display_input.text().strip() or current_display
            jt = dept_input.currentText()
            r = role_input.currentText()
            try:
                success = self.user_manager.add_user(username, "KEEP_OLD", [r], new_disp, jt)
                if success:
                    self.load_data()
                    QMessageBox.information(self, "Success", f"User '{username}' updated successfully.")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to update user '{username}'.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update user: {e}")

    def reset_password(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Error", "Please select a user to reset password.")
            return
            
        row = list(selected_rows)[0]
        item0 = self.grid.item(row, 0)
        username = item0.text() if item0 else ""
        
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
                users = self.user_manager.get_all_users()
                curr_user = users.get(username, {})
                curr_roles = curr_user.get("roles", ["Artist"])
                curr_disp = curr_user.get("display_name", username)
                curr_job = curr_user.get("job_title", "")
                
                success = self.user_manager.add_user(username, p, curr_roles, curr_disp, curr_job)
                if success:
                    QMessageBox.information(self, "Success", f"Password reset for '{username}'.")
                else:
                    QMessageBox.warning(self, "Error", f"Failed to reset password for '{username}'.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to reset password: {e}")

    def delete_user(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Error", "Please select a user to delete.")
            return
            
        row = list(selected_rows)[0]
        item0 = self.grid.item(row, 0)
        username = item0.text() if item0 else ""
        if not username:
            return
            
        if username.lower() in ["admin", "developer"]:
            QMessageBox.warning(self, "Protected Account", f"Cannot delete core system user '{username}'.")
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
