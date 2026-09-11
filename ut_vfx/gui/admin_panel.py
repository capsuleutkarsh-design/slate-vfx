import csv
from datetime import datetime
from ..core.infra.global_config import GlobalConfig # For initial load reading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QLineEdit, QFrame, QAbstractItemView, QFileDialog, QInputDialog,
    QStackedWidget, QListWidget, QListWidgetItem
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QSize, QUrl
import subprocess
import os
import sys


from ..core.domain.workers.admin_workers import UserDataWorker
from ..core.infra.app_context import AppContext
from ..core.domain.user_manager import UserManager
from ..core.infra.config_manager import ConfigManager
from ..core.infra.database_manager import DatabaseManager
from ..core.infra.network_manager import NetworkManager
from ..core.infra.performance_monitor import performance_monitor


# Import Role Editor
from .role_editor import RoleEditor
from .database_explorer import DatabaseExplorer
from .advanced_log_viewer import UnifiedLogViewer
from .admin_widgets import LiveDashboard
from .admin_user_dialogs import AddUserDialog
from .admin_fleet_report_service import run_fleet_report_export
from .components.queued_worker_controller import QueuedWorkerController
from .components.qt_safety import safe_single_shot

# Import design tokens for theming
from ..core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R
from ..core.infra.style_builder import StyleBuilder
from .core.icons import icon as draw_icon
from .core.controls import make_button

# Import shared PyToggle widget (no more duplication!)

# --- STYLESHEETS (Using Design Tokens) ---
STYLE_SWITCHER = f"""
    QListWidget {{
        background-color: {C.BG_SURFACE};
        border: none;
        border-bottom: 1px solid {C.BORDER_DEFAULT};
        outline: none;
        padding: 0px {S.SM}px;
    }}
    QListWidget::item {{
        color: {C.TEXT_SECONDARY};
        padding: 0px {S.MD}px;
        margin: {S.XS}px {S.XS}px;
        border-radius: {R.SM}px;
        font-size: {T.SIZE_BASE}px;
        font-weight: {T.WEIGHT_MEDIUM};
        border: 1px solid transparent;
    }}
    QListWidget::item:hover {{
        background-color: {C.BG_HOVER};
        color: {C.TEXT_PRIMARY};
    }}
    QListWidget::item:selected {{
        background-color: {C.BG_ELEVATED};
        color: {C.ACCENT_PRIMARY};
        border: 1px solid {C.BORDER_DEFAULT};
    }}
"""

STYLE_SIDEBAR = f"""
    QListWidget {{ 
        background-color: {C.BG_SIDEBAR}; 
        border: none; 
        outline: none; 
        padding-top: {S.MD}px; 
    }} 
    QListWidget::item {{ 
        color: {C.TEXT_GRAY_LIGHT}; 
        padding: {S.LG}px {S.XL}px; 
        margin: {S.XS}px {S.LG}px; 
        border-radius: {R.MD}px; 
        font-size: {T.SIZE_MD}px; 
        font-weight: {T.WEIGHT_SEMIBOLD}; 
        border: 1px solid transparent;
    }} 
    QListWidget::item:hover {{ 
        background-color: #16161A; 
        color: white; 
        border: 1px solid {C.BORDER_DEFAULT};
    }} 
    QListWidget::item:selected {{ 
        background-color: #16323A; 
        color: {C.BORDER_HOVER}; 
        border: 1px solid {C.BORDER_HOVER}; 
        font-weight: {T.WEIGHT_STYLE_BOLD};
    }}
"""
STYLE_CARD = f"QFrame#Card {{ background-color: {C.BG_SURFACE}; border-radius: {S.LG}px; border: 1px solid {C.BORDER_DEFAULT}; }} QLabel {{ border: none; }}"
STYLE_BTN_PRIMARY = StyleBuilder.primary_button()
STYLE_BTN_DANGER = StyleBuilder.danger_button()
STYLE_INPUT = StyleBuilder.input_field()

# --- MAIN ADMIN PANEL ---
class AdminPanelTab(QWidget):
    def __init__(self, current_username=None, user_manager=None, hub=None, attendance=None, db_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self._is_closing = False
        self.current_username = current_username
        self.user_manager = user_manager or self.app_context.user_manager()
        self.hub = hub or self.app_context.server_hub()
        self.attendance = attendance or self.app_context.attendance()
        self.db = db_manager or self.app_context.db_manager()
        self.log_file = self.hub.get_attendance_dir().parent / "Config" / "audit.log"
        try: self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            import logging
            logging.warning(f"Could not create audit log directory: {e}")
            
        # Worker for User Data
        self.user_worker = UserDataWorker(self.user_manager)
        self.user_worker.users_loaded.connect(self.on_users_loaded)
        self.user_refresh_controller = QueuedWorkerController(self.user_worker, self)

        self.setStyleSheet(f"background-color: {C.BG_MAIN}; color: {C.TEXT_PRIMARY};")
        self.setup_ui()
        self.destroyed.connect(self.cleanup_resources)
        # Trigger initial data load
        safe_single_shot(100, self, self.refresh_table)

    def setup_ui(self):
        """
        Panel switcher across the top, then the panel.

        This used to be a second vertical sidebar 220px wide, sitting directly
        beside the application's own. Two stacked navigations ate 470px before
        any content appeared, and the five entries were spread down 900px of
        mostly empty column. Across the top costs 40px and matches how Tester
        Panel already works.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- PANEL SWITCHER ---
        self.sidebar = QListWidget()
        self.sidebar.setFlow(QListWidget.Flow.LeftToRight)
        self.sidebar.setFixedHeight(42)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.sidebar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar.setStyleSheet(STYLE_SWITCHER)
        self.sidebar.currentRowChanged.connect(self.change_page)

        items = [
            ("Live Ops", "monitor"),
            ("User Mgmt", "users"),
            ("Permissions", "key"),
            ("Audit Logs", "info"),
            ("Data Center", "database"),
        ]

        for label, glyph in items:
            item = QListWidgetItem(label)
            item.setIcon(draw_icon(glyph))
            # Width from the text itself, so no label is ever clipped.
            width = self.sidebar.fontMetrics().horizontalAdvance(label) + 62
            item.setSizeHint(QSize(width, 34))
            self.sidebar.addItem(item)

        layout.addWidget(self.sidebar)

        # --- RIGHT CONTENT STACK ---
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {C.BG_MAIN};")
        layout.addWidget(self.stack)
        
        # 1. LIVE DASHBOARD (Restored Mission Control)
        self.live_dashboard = LiveDashboard(self.hub, verify_callback=self.verify_admin_action)
        self.live_dashboard_worker_controller = QueuedWorkerController(
            self.live_dashboard.worker,
            self.live_dashboard,
        )
        self.live_dashboard.bind_worker_controller(self.live_dashboard_worker_controller)
        self.restore_mission_control(self.live_dashboard)
        self.stack.addWidget(self.live_dashboard)
        
        # 2. USER MANAGEMENT
        self.user_mgmt_widget = QWidget()
        self.setup_user_mgmt_ui() # Helper to keep init clean
        self.stack.addWidget(self.user_mgmt_widget)

        # 3. ROLE MANAGEMENT
        self.role_editor = RoleEditor(self.user_manager)
        self.stack.addWidget(self.role_editor)
        
        # 4. AUDIT LOG
        self.audit_widget = QWidget()
        self.setup_audit_ui()
        self.stack.addWidget(self.audit_widget)
        
        # 5. DATA CENTER
        self.data_center = DatabaseExplorer(self.db, app_context=self.app_context)
        self.stack.addWidget(self.data_center)
        
        # Select first item
        self.sidebar.setCurrentRow(0)

    def change_page(self, row):
        self.stack.setCurrentIndex(row)
        
        # Refresh logic based on page
        if row == 4: # Data Center
            pass 
        elif row == 3: # Audit
            self.load_audit_log()

    def load_audit_log(self):
        """Refreshes the data in the Unified Log Viewer."""
        if hasattr(self, 'unified_log_viewer'):
            # Refresh both system logs and database audit
            if hasattr(self.unified_log_viewer, 'sys_logs'):
                self.unified_log_viewer.sys_logs.refresh_list()
            if hasattr(self.unified_log_viewer, 'db_audit'):
                self.unified_log_viewer.db_audit.refresh_data()

    def restore_mission_control(self, dashboard):
        """Injects the 'Mission Control' bar into the LiveDashboard layout."""
        # Find the existing layout or create a wrapper
        # The Dashboard has a VBoxLayout. We insert at index 0.
        
        control_frame = QFrame()
        control_frame.setStyleSheet(f"background-color: {C.BG_ELEVATED}; border-bottom: 1px solid {C.BORDER_LIGHT};")
        control_frame.setFixedHeight(60)
        
        h = QHBoxLayout(control_frame)
        h.setContentsMargins(15, 5, 15, 5)
        
        # Broadcast Input
        self.inp_broadcast = QLineEdit()
        self.inp_broadcast.setPlaceholderText("Broadcast a message to every workstation...")
        self.inp_broadcast.setStyleSheet(STYLE_INPUT)
        h.addWidget(self.inp_broadcast)

        # These four were cyan, orange, dark and green, which told you nothing
        # about them - the destructive one was styled more quietly than the
        # harmless one. Sending the broadcast is what this row is for, so it is
        # the primary; wiping every machine's cache is the only destructive one.
        btn_alert = make_button("Send alert", "primary", on_click=self.send_broadcast)
        h.addWidget(btn_alert)

        btn_export = make_button("Fleet report", "secondary",
                                 tooltip="Export the current fleet status",
                                 on_click=self.export_fleet_report)
        btn_export.setIcon(draw_icon("download"))
        h.addWidget(btn_export)

        self.btn_api = make_button("Start API gateway", "secondary",
                                   on_click=self.start_api_server)
        h.addWidget(self.btn_api)

        btn_wipe = make_button("Wipe caches", "danger",
                               tooltip="Clear the local cache on every connected workstation",
                               on_click=self.wipe_remote_caches)
        btn_wipe.setIcon(draw_icon("trash"))
        h.addWidget(btn_wipe)
        
        dashboard.layout().insertWidget(0, control_frame)
        self.api_process = None

    def start_api_server(self):
        import urllib.request
        server_running = False
        host = GlobalConfig.get("db_host", "127.0.0.1")
        try:
            # Check if server is already running (maybe started by ut_server.py)
            urllib.request.urlopen(f"http://{host}:8000/docs", timeout=1)
            server_running = True
        except Exception:
            pass

        if server_running or (self.api_process and self.api_process.poll() is None):
            # Already running, just open the dashboard
            QDesktopServices.openUrl(QUrl(f"http://{host}:8000/admin"))
            self.btn_api.setText("Open API Dashboard")
            self.btn_api.setStyleSheet("background-color: #3EA8BF; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold;")
            return
            
        try:
            # Find the path to api/main.py
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            api_main_path = os.path.join(base_dir, "api", "main.py")
            
            # Start process
            cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env = os.environ.copy()
            proc = subprocess.Popen([sys.executable, api_main_path], cwd=cwd, env=env, shell=False)
            try:
                from ..utils.process_manager import subprocess_tracker
                self.api_process = subprocess_tracker.register(proc)
            except Exception:
                self.api_process = proc
            self.log_action("Started API Gateway (FastAPI) on port 8000")
            
            self.btn_api.setText("Open API Dashboard")
            self.btn_api.setStyleSheet("background-color: #3EA8BF; color: white; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold;")
            QMessageBox.information(self, "API Started", "The Waiter (FastAPI) is now booting up on port 8000!\n\nClick the button again to view the Admin Dashboard.")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start API Server:\n{e}")
            self.log_action(f"Failed to start API Server: {e}")

    def send_broadcast(self):
        msg = self.inp_broadcast.text().strip()
        if not msg: return
        self.hub.post_command("alert", "all", msg)
        self.inp_broadcast.clear()
        QMessageBox.information(self, "Sent", "Broadcast alert sent to all active stations.")
        self.log_action(f"Broadcast Alert: {msg}")

    def wipe_remote_caches(self):
        if QMessageBox.question(self, "Confirm", "Wipe thumbnails/cache on ALL connected PCs?") == QMessageBox.StandardButton.Yes:
            self.hub.post_command("wipe_cache", "all")
            self.log_action("Triggered Remote Cache Wipe")

    def export_fleet_report(self):
        run_fleet_report_export(self, self.hub, self.log_action)


    def setup_user_mgmt_ui(self):
        user_layout = QVBoxLayout(self.user_mgmt_widget)
        
        # Top Bar (Search + Actions)
        top_bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Users...")
        self.search.setStyleSheet(STYLE_INPUT)
        self.search.textChanged.connect(self.apply_filters)
        top_bar.addWidget(self.search)
        
        btn_add = QPushButton("Add User")
        btn_add.setStyleSheet(STYLE_BTN_PRIMARY)
        btn_add.clicked.connect(self.add_user)
        top_bar.addWidget(btn_add)
        
        btn_export = QPushButton("Export CSV")
        btn_export.setStyleSheet(f"background-color: {C.BG_SIDEBAR}; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: {S.SM}px {S.MD}px; border-radius: {R.SM}px;")
        btn_export.clicked.connect(self.export_to_csv)
        top_bar.addWidget(btn_export)
        user_layout.addLayout(top_bar)
        
        # User Table
        self.user_table = QTableWidget()
        self.user_table.setColumnCount(4)
        self.user_table.setHorizontalHeaderLabels(["ID", "Name", "Roles", "Job Title"])
        self.user_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.user_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.user_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.user_table.setStyleSheet(f"""
            QTableWidget {{ background-color: {C.BG_SIDEBAR}; border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px; gridline-color: {C.BORDER_LIGHT}; }}
            QHeaderView::section {{ background-color: {C.BG_ELEVATED}; padding: 4px; border: none; font-weight: bold; }}
            QTableWidget::item {{ padding: 5px; }}
            QTableWidget::item:selected {{ background-color: {C.ACCENT_PRIMARY}; color: black; }}
        """)
        self.user_table.doubleClicked.connect(self.edit_user)
        user_layout.addWidget(self.user_table)
        
        # Bottom Actions
        action_layout = QHBoxLayout()
        btn_edit = QPushButton("Edit Selected")
        btn_edit.clicked.connect(self.edit_user)
        btn_edit.setStyleSheet(f"background-color: {C.BG_ELEVATED}; color: white; border: 1px solid {C.BORDER_LIGHT}; padding: 6px 12px; border-radius: 4px;")
        btn_del = QPushButton("Delete Selected")
        btn_del.setStyleSheet(STYLE_BTN_DANGER)
        btn_del.clicked.connect(self.delete_user)
        action_layout.addWidget(btn_edit)
        action_layout.addWidget(btn_del)
        action_layout.addStretch()
        user_layout.addLayout(action_layout)

    def setup_audit_ui(self):
        """Initialize the Advanced Audit Log Viewer."""
        layout = QVBoxLayout(self.audit_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.unified_log_viewer = UnifiedLogViewer(
            db_manager=self.db,
            app_context=self.app_context,
        )
        layout.addWidget(self.unified_log_viewer)

    def log_action(self, message):
        """Append action to audit log."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user = self.current_username or "Unknown"
        entry = f"[{timestamp}] {user}: {message}\n"
        try:
            with open(self.log_file, "a", encoding='utf-8') as f:
                f.write(entry)
        except Exception as e:
            logging.exception("Failed to write audit log: %s", e)

    def data_refresh_sequence(self):
        # Legacy stub or re-route
        self.refresh_table() 
        self.load_permissions()
    
    def on_users_loaded(self, users):
        """Update the table with loaded user data."""
        # Keep in-memory manager state in sync with what we just displayed.
        self.user_manager_users = dict(users or {})
        self.user_table.setRowCount(0)

        for r, (u, d) in enumerate((users or {}).items()):
            self.user_table.insertRow(r)
            self.user_table.setItem(r,0,QTableWidgetItem(u))
            self.user_table.setItem(r,1,QTableWidgetItem(d.get('display_name','')))
            
            # Formatting Roles List
            roles = d.get('roles', [])
            if not isinstance(roles, list): roles = [roles] if roles is not None else []
            # Safety: Ensure all items are strings
            role_str = ", ".join(str(r) for r in roles if r is not None)
            
            self.user_table.setItem(r,2,QTableWidgetItem(role_str))
            self.user_table.setItem(r,3,QTableWidgetItem(d.get('job_title','')))
        self.apply_filters()

    def _on_user_worker_finished(self):
        """Legacy no-op retained for backward compatibility."""
        return

    def refresh_table(self):
        """Start the background worker."""
        self.user_refresh_controller.request_refresh()

    def apply_filters(self, *args):
        txt = self.search.text().lower()
        for i in range(self.user_table.rowCount()):
            item0 = self.user_table.item(i, 0)
            item1 = self.user_table.item(i, 1)
            text0 = item0.text().lower() if item0 else ""
            text1 = item1.text().lower() if item1 else ""
            self.user_table.setRowHidden(i, not (txt in text0 or txt in text1))
    def add_user(self):
        d = AddUserDialog(self)
        if d.exec(): 
            u,p,r_list,n,j,pic = d.get_data() 
            self.user_manager.add_user(u,p,r_list,n,j, pic)
            # Immediate UI feedback, then async refresh from disk.
            self.on_users_loaded(self.user_manager.get_all_users())
            self.refresh_table()
            self.log_action(f"Added user: {u} with roles {r_list}")
            
    def edit_user(self):
        r = self.user_table.currentRow()
        if r < 0:
            QMessageBox.warning(self, "Select User", "Please select a user to edit first.")
            return

        uid = self.user_table.item(r,0).text()
        ud = self.user_manager.get_all_users().get(uid)
        
        if not ud:
            QMessageBox.critical(self, "Error", "User data not found in database.")
            return

        # Prepare initial data for dialog
        # Handle 'roles' list vs legacy 'role' string
        current_roles = ud.get('roles', [])
        if not current_roles and 'role' in ud: 
            current_roles = [ud['role']]

        initial_data = (
            uid,
            ud.get('display_name', ''),
            current_roles,
            ud.get('job_title', ''),
            ud.get('profile_pic_path', '')
        )
        
        d = AddUserDialog(self, edit_mode=True, user_data=initial_data)
        if d.exec(): 
            login_id, password, role_list, name, job, pic = d.get_data()
            
            # If password is empty, pass a flag or None to tell UserManager to NOT update it
            final_pass = password if password else "KEEP_OLD"
            
            self.user_manager.add_user(uid, final_pass, role_list, name, job, pic)
            # Immediate UI feedback, then async refresh from disk.
            self.on_users_loaded(self.user_manager.get_all_users())
            self.refresh_table()
            self.log_action(f"Edited user: {uid} updated roles: {role_list}")

    def delete_user(self):
        r = self.user_table.currentRow()
        if r>=0:
            uid = self.user_table.item(r,0).text()
            if QMessageBox.question(self, "Delete", f"Delete {uid}?") == QMessageBox.StandardButton.Yes:
                self.user_manager.delete_user(uid)
                # Immediate UI feedback, then async refresh from disk.
                self.on_users_loaded(self.user_manager.get_all_users())
                self.refresh_table()
                self.log_action(f"Deleted user: {uid}")
    def load_permissions(self):
        # Permissions now handled by RoleEditor internally
        if hasattr(self, 'role_editor'):
            self.role_editor.refresh_roles()

    def save_permissions(self):
        # Legacy method stub
        pass

    def verify_admin_action(self):
        """Re-authentication callback for destructive operations (restart, shutdown, wipe)."""
        password, ok = QInputDialog.getText(
            self, "Admin Verification",
            "Enter your password to confirm this action:",
            QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            return False
            
        # 1. Check Master Password (from config.json / default_config.json)
        # This allows the 'admin_password' key in the JSON to actually work as an override
        master_pass = GlobalConfig.get("admin_password")
        if master_pass and password == master_pass:
             self.log_action("Admin verified via MASTER PASSWORD override")
             return True

        # 2. Verify against current user's password (Standard)
        if self.current_username and self.user_manager.authenticate(self.current_username, password):
            self.log_action(f"Admin verified for destructive action by {self.current_username}")
            return True
            
        QMessageBox.warning(self, "Denied", "Invalid password.")
        return False

    def cleanup_resources(self):
        """Gracefully stop all background workers when tab is closed."""
        self._is_closing = True
        if hasattr(self, 'live_dashboard') and self.live_dashboard:
            try:
                self.live_dashboard.cleanup()
            except Exception:
                pass
        if hasattr(self, 'user_refresh_controller') and self.user_refresh_controller:
            try:
                self.user_refresh_controller.shutdown(timeout_ms=1000)
            except Exception:
                pass
        if hasattr(self, 'user_worker') and self.user_worker:
            try:
                if self.user_worker.isRunning():
                    self.user_worker.requestInterruption()
                    self.user_worker.wait(500)
            except Exception:
                pass
        
        # Shutdown API Server
        if hasattr(self, 'api_process') and self.api_process:
            try:
                from ..utils.process_manager import subprocess_tracker
                subprocess_tracker.unregister(self.api_process)
                if self.api_process.poll() is None:
                    self.api_process.terminate()
                    self.api_process.wait(timeout=1.0)
            except Exception:
                try:
                    self.api_process.kill()
                except Exception:
                    pass
            self.api_process = None

    def closeEvent(self, event):
        """Ensure all background workers are stopped when the panel closes."""
        self.cleanup_resources()
        super().closeEvent(event)

    def export_to_csv(self):
        path,_=QFileDialog.getSaveFileName(self,"Export","users.csv","CSV (*.csv)")
        if path:
            try:
                with open(path,'w',newline='') as f:
                    w=csv.writer(f); w.writerow(["ID","Name","Roles","Job"])
                    for u,d in self.user_manager.get_all_users().items(): 
                        roles = d.get('roles', [])
                        if not isinstance(roles, list): roles = [roles] if roles is not None else []
                        # Safety: Ensure all items are strings
                        role_str = "|".join(str(r) for r in roles if r is not None)
                        w.writerow([u,d.get('display_name'), role_str, d.get('job_title')])
                QMessageBox.information(self,"Success","Exported.")
            except Exception as e: QMessageBox.critical(self,"Error",str(e))

class AdminPanel(AdminPanelTab):
    """Backward-compatible wrapper for legacy callers/tests."""
    def __init__(self, user_role=None, current_username=None, **kwargs):
        # user_role is kept for compatibility with older API.
        self.user_role = user_role
        super().__init__(current_username=current_username, **kwargs)
