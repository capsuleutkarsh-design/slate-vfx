from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                             QPushButton, QComboBox, QMessageBox)
from PySide6.QtCore import Qt
from slate.core.infra.app_context import AppContext

class LoginDialog(QDialog):
    def __init__(self, user_manager=None, app_context=None):
        super().__init__()
        self.setWindowTitle("Slate - Login")
        self.setMinimumSize(400, 300)
        self.app_context = app_context or AppContext()
        self.user_manager = user_manager or self.app_context.user_manager()
        self.user_role = None
        
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Title
        title = QLabel("Slate Dashboard")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18pt; font-weight: bold; margin-bottom: 20px;")
        layout.addWidget(title)
        
        # User Selection
        self.user_combo = QComboBox()
        self.user_combo.addItems(self.user_manager.get_all_users().keys())
        self.user_combo.currentTextChanged.connect(self.on_user_changed)
        layout.addWidget(QLabel("Select User:"))
        layout.addWidget(self.user_combo)
        
        # Password
        self.pass_input = QLineEdit()
        self.pass_input.setPlaceholderText("Password")
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.pass_input)
        
        # Login Button
        self.login_btn = QPushButton("Login")
        self.login_btn.clicked.connect(self.handle_login)
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.login_btn)
        
        self.setLayout(layout)
        
        # Initial state
        self.on_user_changed(self.user_combo.currentText())
        
    def on_user_changed(self, username):
        # Disable password for artist if configured that way
        user_data = self.user_manager.get_all_users().get(username, {})
        # Use new 'roles' array format with fallback to legacy 'role'
        roles = user_data.get('roles', [user_data.get('role', 'Artist')])
        if isinstance(roles, str):
            roles = [roles]
        
        # Check if user has ONLY artist role (case-insensitive)
        is_artist_only = all(r.lower() == 'artist' for r in roles)
        
        if is_artist_only:
            self.pass_input.setEnabled(False)
            self.pass_input.setPlaceholderText("No password required")
            self.pass_input.clear()
        else:
            self.pass_input.setEnabled(True)

            self.pass_input.setPlaceholderText("Password")
            
    def handle_login(self):
        username = self.user_combo.currentText()
        password = self.pass_input.text()
        
        user_data = self.user_manager.authenticate(username, password)
        
        if user_data:
            # MULTI-ROLE FIX: Get from 'roles' array (new) or fallback to 'role' (legacy)
            roles_data = user_data.get('roles', user_data.get('role', ['Artist']))
            if isinstance(roles_data, list):
                self.user_role = roles_data[0] if roles_data else 'Artist'
            else:
                self.user_role = roles_data
            self.accept()
        else:
            QMessageBox.warning(self, "Login Failed", "Invalid password")

    def get_role(self):
        return self.user_role
