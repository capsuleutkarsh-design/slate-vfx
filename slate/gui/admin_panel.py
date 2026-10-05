import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QMessageBox, QLineEdit, QFrame, QInputDialog,
    QStackedWidget, QTabBar, QLabel
)

from PySide6.QtCore import Qt


from ..core.infra.app_context import AppContext
from ..core.domain import access
from ..core.domain.user_manager import UserManager
from ..core.infra.config_manager import ConfigManager
from ..core.infra.database_manager import DatabaseManager
from ..core.infra.performance_monitor import performance_monitor


from .database_explorer import DatabaseExplorer
from .advanced_log_viewer import UnifiedLogViewer
from .admin_widgets import LiveDashboard
from .admin_fleet_report_service import run_fleet_report_export
from .components.queued_worker_controller import QueuedWorkerController
from .components.feedback import toast

# Import design tokens for theming
from ..core.infra.design_tokens import ColorTokens as C
from ..core.infra.style_builder import StyleBuilder
from .core.icons import icon as draw_icon
from .core.controls import make_button
from slate.core.infra.gate import Gate

STYLE_INPUT = StyleBuilder.input_field()

PAGES = (
    # (label, glyph, needs manage_system)
    ("Live Ops", "monitor", False),
    ("Audit Logs", "info", True),
    ("Data Center", "database", True),
)


def roles_of_user(roles=None, user_role=None, app_context=None):
    """The signed-in person's roles: given, legacy single role, or from the context."""
    if roles:
        return [roles] if isinstance(roles, str) else list(roles)
    if user_role:
        return [user_role] if isinstance(user_role, str) else list(user_role)
    try:
        return list(app_context.current_roles()) if app_context is not None else []
    except Exception:
        return []


# --- MAIN ADMIN PANEL ---
class AdminPanelTab(QWidget):
    def __init__(self, current_username=None, user_manager=None, hub=None, attendance=None,
                 db_manager=None, app_context=None, roles=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self._is_closing = False
        self.current_username = current_username
        self.user_manager = user_manager or self.app_context.user_manager()
        self.hub = hub or self.app_context.server_hub()
        self.attendance = attendance or self.app_context.attendance()
        self.db = db_manager or self.app_context.db_manager()

        # Who may do what here. A supervisor keeps this tab but sees only Live
        # Ops, read-only: the logs, the database and every remote action are
        # for admins and developers (manage_system). Before, anybody who could
        # open the tab had a SQL console and fleet restart.
        self.roles = roles_of_user(roles, getattr(self, "user_role", None), self.app_context)
        self.can_manage_system = access.can(self.roles, "manage_system")

        self.log_file = self.hub.get_attendance_dir().parent / "Config" / "audit.log"
        try: self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logging.warning(f"Could not create audit log directory: {e}")

        # Scoped to the panel itself. A selector-less sheet was inherited by
        # every child, so fields and lists took the panel's background.
        self.setObjectName("AdminPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#AdminPanel {{ background-color: {C.BG_MAIN}; color: {C.TEXT_PRIMARY}; }}")
        self.setup_ui()
        self.destroyed.connect(self.cleanup_resources)

    def setup_ui(self):
        """
        Page switcher across the top, then the page.

        The switcher is a tab bar like every other tab bar in Slate. It was a
        horizontal list with ~10 px text whose selected page was only an
        outline.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.pages = [(label, glyph) for label, glyph, restricted in PAGES
                      if self.can_manage_system or not restricted]

        # --- PAGE SWITCHER ---
        self.sidebar = QTabBar()
        self.sidebar.setDrawBase(False)
        self.sidebar.setExpanding(False)
        for label, glyph in self.pages:
            self.sidebar.addTab(draw_icon(glyph), label)
        self.sidebar.currentChanged.connect(self.change_page)
        switcher = QWidget()
        switcher.setObjectName("AdminSwitcher")
        switcher.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        switcher.setStyleSheet(f"QWidget#AdminSwitcher {{ background-color: {C.BG_SURFACE}; "
                               f"border-bottom: 1px solid {C.BORDER_DEFAULT}; }}")
        sw = QHBoxLayout(switcher)
        sw.setContentsMargins(8, 4, 8, 0)
        sw.addWidget(self.sidebar)
        sw.addStretch(1)
        layout.addWidget(switcher)
        # One page needs no switcher.
        switcher.setVisible(len(self.pages) > 1)

        if not self.can_manage_system:
            note = QLabel("Live Ops, read-only. Remote actions, the logs and the database "
                          "are for admins and developers.")
            note.setObjectName("AdminReadOnlyNote")
            note.setWordWrap(True)
            note.setStyleSheet(f"QLabel#AdminReadOnlyNote {{ color: {Gate.TEXT_DIM}; padding: 8px 15px; }}")
            layout.addWidget(note)

        # --- CONTENT STACK ---
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # 1. LIVE OPS
        self.live_dashboard = LiveDashboard(
            self.hub,
            verify_callback=self.verify_admin_action,
            read_only=not self.can_manage_system,
            log_action=self.log_action,
        )
        self.live_dashboard_worker_controller = QueuedWorkerController(
            self.live_dashboard.worker,
            self.live_dashboard,
        )
        self.live_dashboard.bind_worker_controller(self.live_dashboard_worker_controller)
        self.restore_mission_control(self.live_dashboard)
        self.stack.addWidget(self.live_dashboard)

        # 2. AUDIT LOGS and 3. DATA CENTER - built only for the people who may
        # use them, so nothing of either exists for anybody else.
        if self.can_manage_system:
            self.audit_widget = QWidget()
            self.setup_audit_ui()
            self.stack.addWidget(self.audit_widget)

            self.data_center = DatabaseExplorer(self.db, app_context=self.app_context)
            self.stack.addWidget(self.data_center)

        self.sidebar.setCurrentIndex(0)

    def page_labels(self):
        return [label for label, _glyph in self.pages]

    def change_page(self, row):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        if self.pages[row][0] == "Audit Logs":
            self.load_audit_log()

    def show_page(self, label) -> bool:
        """Switch to a page by name; False when this person has no such page."""
        labels = self.page_labels()
        if label not in labels:
            return False
        self.sidebar.setCurrentIndex(labels.index(label))
        return True

    def load_audit_log(self):
        """Refreshes the data in the Unified Log Viewer."""
        if hasattr(self, 'unified_log_viewer'):
            self.unified_log_viewer.refresh_all()

    def restore_mission_control(self, dashboard):
        """The admin actions above the Live Ops grid."""
        # Reading the fleet is allowed to everybody who sees Live Ops.
        btn_export = make_button("Fleet report", "secondary",
                                 tooltip="Save every machine's latest report as Excel, CSV or JSON",
                                 on_click=self.export_fleet_report)
        btn_export.setIcon(draw_icon("download"))
        dashboard.add_tool(btn_export)

        if not self.can_manage_system:
            return

        control_frame = QFrame()
        control_frame.setObjectName("MissionControl")
        control_frame.setStyleSheet(
            f"QFrame#MissionControl {{ background-color: {C.BG_ELEVATED}; "
            f"border-bottom: 1px solid {C.BORDER_LIGHT}; }}")
        control_frame.setFixedHeight(60)

        h = QHBoxLayout(control_frame)
        h.setContentsMargins(15, 5, 15, 5)

        self.inp_broadcast = QLineEdit()
        self.inp_broadcast.setPlaceholderText("Broadcast a message to every workstation...")
        self.inp_broadcast.setStyleSheet(STYLE_INPUT)
        h.addWidget(self.inp_broadcast)

        btn_alert = make_button("Send alert", "primary", on_click=self.send_broadcast)
        h.addWidget(btn_alert)

        dashboard.layout().insertWidget(0, control_frame)

    # ------------------------------------------------------- remote actions
    def send_broadcast(self):
        msg = self.inp_broadcast.text().strip()
        if not msg: return
        # "message" is what the workstations show (gatekeeper_main); they
        # dropped "alert" as an unknown command, so no broadcast ever arrived.
        self.hub.post_command("message", "all", msg)
        self.inp_broadcast.clear()
        QMessageBox.information(self, "Sent", "Broadcast alert sent to all active stations.")
        self.log_action(f"Broadcast Alert: {msg}")

    def export_fleet_report(self):
        run_fleet_report_export(self, self.hub, self.log_action)


    def setup_audit_ui(self):
        """Initialize the Advanced Audit Log Viewer."""
        layout = QVBoxLayout(self.audit_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.unified_log_viewer = UnifiedLogViewer(
            db_manager=self.db,
            app_context=self.app_context,
            audit_file=self.log_file,
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

    def verify_admin_action(self):
        """Re-authentication callback for destructive operations (restart, shut down)."""
        password, ok = QInputDialog.getText(
            self, "Admin Verification",
            "Enter your password to confirm this action:",
            QLineEdit.EchoMode.Password
        )
        if not ok or not password:
            return False

        # Only the signed-in person's own password. A shared admin_password from
        # config.json (admin123 by default) used to work too (SYS-001).
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
        if hasattr(self, 'unified_log_viewer'):
            try:
                self.unified_log_viewer.cleanup_resources()
            except Exception:
                pass

    def closeEvent(self, event):
        """Ensure all background workers are stopped when the panel closes."""
        self.cleanup_resources()
        super().closeEvent(event)

class AdminPanel(AdminPanelTab):
    """Backward-compatible wrapper for legacy callers/tests."""
    def __init__(self, user_role=None, current_username=None, **kwargs):
        # The legacy single role still decides what the panel shows.
        self.user_role = user_role
        super().__init__(current_username=current_username, **kwargs)
