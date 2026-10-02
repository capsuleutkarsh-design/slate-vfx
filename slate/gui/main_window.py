"""
Slate - MAIN WINDOW
=========================
The primary entry point for the GUI.
Manages tabs, workflow modes, global settings, and styling.

FEATURES:
- Dual Workflow Mode (Standard vs Auto-Scan)
- Professional Dark Theme Loading
- Integrated Reporting and Backup tools
- NEW: Stock Asset Viewer (Proxy Workflow)
- PowerRename Utility
- Offline Update System
"""

import sys
import re
from pathlib import Path
from typing import Optional, Any, Callable, Dict, List, Tuple
import logging

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStatusBar, QMessageBox, QStackedWidget,
    QApplication, QDialog, QListWidget, QPushButton
)
from PySide6.QtCore import Qt, QTimer, Signal, QRect, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QFileSystemWatcher
from PySide6.QtGui import QAction, QFont, QPalette, QIcon, QKeySequence

# Internal Module Imports
from .. import __version__ as APP_VERSION
from ..core.infra.global_config import GlobalConfig
from ..core.infra.theme_manager import ThemeManager
from ..core.infra.app_context import AppContext
from ..core.infra.database_manager import database_manager
from ..core.workers.db_monitor import DatabaseMonitor
from .components.qt_safety import safe_single_shot

from ..utils.resource_manager import ResourcePathManager
from ..utils.error_handler import error_handler
from ..utils.security import SecurityValidator
from ..utils.reporting import report_generator
from ..utils.backup_recovery import BackupManager, RecoveryManager
from ..core.updater.update_checker import UpdateChecker

from ..core.domain.central_attendance import CentralAttendance
from ..core.infra.network_manager import NetworkManager
from ..gui.notification_overlay import NotificationOverlay
from ..core.infra.telemetry import telemetry
from ..core.system.adaptation_engine import system_engine # [NEW] Adaptation Engine
from ..core.services.sweeper_engine import SweeperEngine
from ..core.services.sweepers.temp_sweeper import TempFileSweeper
from ..gui.help_dialog import show_help  # Re-enabled with JSON-based content

# The screens are imported when they are first opened (main_window_builder),
# not here: importing every one of them made start-up over a second slower.
from .login_dialog import LoginDialog


# Component Imports
from .components.header_builder import HeaderBuilder
from .components.tab_coordinator import TabCoordinator
from .components.session_manager import SessionManagerMixin
from .components.sidebar_controller import SidebarControllerMixin
from .components.quick_search_controller import QuickSearchControllerMixin
from .components.main_window_builder import MainWindowBuilderMixin
from slate.core.infra.gate import Gate

# The smallest the main window may be made (width, height).
MIN_WINDOW_SIZE = (960, 600)


# Every keyboard shortcut, in one table. setup_shortcuts binds them, and the
# Keyboard shortcuts sheet (Account menu, Help) lists them - so the list can no
# longer miss a key that works, which is how Help came to know only four.
# (keys, what it does, method on the window, the key it is an alias of)
SHORTCUTS = (
    ("Ctrl+K", "Search or jump to a screen, a command or a shot", "show_quick_search", ""),
    ("Ctrl+P", "Search or jump to (same as Ctrl+K)", "show_quick_search", "Ctrl+K"),
    ("Ctrl+1 \u2026 Ctrl+9", "Open the 1st to 9th screen in the sidebar", "switch_to_nth_tab", ""),
    ("F5", "Refresh the screen you are on", "refresh_current_tab", ""),
    ("Ctrl+R", "Refresh (same as F5)", "refresh_current_tab", "F5"),
    ("F1", "Help for the screen you are on", "show_help_dialog", ""),
    ("F11", "Full screen on or off", "toggle_fullscreen", ""),
    ("Ctrl+Shift+S", "Open Settings", "show_settings_tab", ""),
    ("Ctrl+Shift+D", "Diagnostics: version, database and shared folder", "show_runtime_diagnostics", ""),
)


# The mixins come before QMainWindow deliberately.
#
# With QMainWindow first, Python resolved resizeEvent to QWidget's and the
# mixin's was never called - so the responsive sidebar code in
# SidebarControllerMixin has never once run on a resize. resizeEvent is the only
# Qt method any of these mixins override, and closeEvent is defined on this
# class directly, so this ordering changes nothing else.
class VFXFolderCreatorApp(SessionManagerMixin, SidebarControllerMixin, QuickSearchControllerMixin, MainWindowBuilderMixin, QMainWindow):
    """
    Main application class with responsive UI and enhanced workflow switching.
    """
    attendance_status_signal = Signal(str, str, int)
    
    def __init__(self, user_data=None, app_context=None, app_mode: str = "all"):
        super().__init__()
        self._is_closing = False
        self._init_complete = False  # Set True at end of init; guards closeEvent
        self._startup_complete = False
        self._logout_requested = False
        self._db_init_worker = None
        self._db_fallback_warned = False
        self._db_runtime_status_cache = {}
        self.app_mode = getattr(self, "app_mode", None) or app_mode or "all"
        self.status_bar = None
        self.attendance_status_signal.connect(self.show_status)
        self.app_context = app_context or AppContext()
        # Every screen built from the context can ask what this person may do.
        self.app_context.set_current_user(user_data)
        
        # --- SECURITY CONTEXT ---
        self.user_data = user_data
        self.user_role = "Artist"  # Default (kept as string for backward compatibility)
        self.user_display_name = "User"
        
        if self.app_mode == "vfx":
            suite_title = "Slate VFX"
        elif self.app_mode == "ops":
            suite_title = "Slate Operations"
        else:
            suite_title = "Slate"
        self.suite_title = suite_title

        if self.user_data:
            # Handle both 'roles' array (new) and 'role' string (legacy)
            roles_data = self.user_data.get('roles', self.user_data.get('role', ['Artist']))
            
            # Convert to list if needed
            if isinstance(roles_data, str):
                roles_list = [roles_data]
            elif isinstance(roles_data, list):
                roles_list = roles_data
            else:
                roles_list = ['Artist']
            
            # Keep user_role as STRING (first role for backward compatibility)
            self.user_role = roles_list[0] if roles_list else "Artist"
            
            # Store the full roles list for permission checking
            self.user_roles = roles_list
                
            self.user_display_name = self.user_data.get('display_name', self.user_data.get('user_id', self.user_data.get('username', 'User')))
            self.current_user = self.user_data.get('user_id', self.user_data.get('username', 'debug_user'))
            subtitle = self.user_subtitle()
            self.setWindowTitle(f"{suite_title} | {self.user_display_name}"
                                + (f" ({subtitle})" if subtitle else ""))
            
            # --- AUTO ATTENDANCE LOGGING (ASYNC) ---
            safe_single_shot(3000, self, self.perform_async_login)
                
        else:
            self.current_user = "debug_user"
            self.user_roles = ["Artist"]
            self.setWindowTitle(f"{suite_title} - {self.user_role} Mode")
        
        # Apply Saved Theme
        ThemeManager.apply_saved_theme()
        # Load Permissions
        self.user_manager = self.app_context.user_manager()
        # Use roles list (not single role string) for permission checking
        self.allowed_tabs = self.user_manager.get_allowed_tabs(self.user_roles)
        
        # One line at INFO; the full role table only when debugging. It used to
        # write a 2 KB line into every log on every start.
        logging.info("User: %s | Role: %s | %d tab permissions",
                     self.current_user, self.user_role, len(self.allowed_tabs or []))
        logging.debug(f"[ACCESS] Allowed Tabs: {self.allowed_tabs}")
        logging.debug(f"[ROLES] All Roles Config: {self.user_manager.roles_config}")
        # ------------------------
        
        self.config_manager = self.app_context.config_manager()
        self.settings = self.config_manager.settings
        self.global_settings = self.settings.get("global_settings", self.config_manager.default_global_settings)
        self.security_validator = SecurityValidator()
        self.report_generator = report_generator
        
        # Initialize Library Manager (DI)
        self.library_manager = self.app_context.library_manager()

        # Initialize Header Builder (EXTRACTED COMPONENT)
        self.header_builder = HeaderBuilder(self, self.user_data)

        # Initialize Sweeper Service
        self.sweeper_engine = SweeperEngine(self)
        self.sweeper_engine.register_sweeper(TempFileSweeper(max_age_days=1))  # Daily cleanup of temps > 24h

        # --- NETWORK & NOTIFICATION (SAFE INIT) ---
        if self._is_sqlite_fallback_mode():
            # Fallback mode should avoid central-sync listeners to prevent misleading behavior.
            self.network_manager = None
            self.overlay = None
            logging.warning("Network manager disabled in SQLite fallback mode.")
        else:
            try:
                self.network_manager = NetworkManager(username=self.user_display_name)
                self.network_manager.message_received.connect(self.on_network_message)
                self.network_manager.start()
                self.overlay = NotificationOverlay(self)
                logging.info("Network Manager & Overlay initialized successfully.")
            except Exception as e:
                logging.exception(f"Failed to init Network/Notification systems: {e}", exc_info=True)
                self.network_manager = None
                self.overlay = None

        self.backup_manager = BackupManager()
        self.recovery_manager = RecoveryManager(self.backup_manager)
        
        # 2. Setup Window Properties
        # SMART RESIZE: Responsive to screen resolution
        screen_geo = QApplication.primaryScreen().availableGeometry()
        
        # Use 85% of screen size, but enforce minimums
        target_w = max(1280, int(screen_geo.width() * 0.85))
        target_h = max(800, int(screen_geo.height() * 0.85))
        
        # Ensure we don't exceed screen size
        target_w = min(target_w, screen_geo.width())
        target_h = min(target_h, screen_geo.height())
        
        # [NEW] Apply user scale override, then calculate UI scale based on screen.
        system_engine.set_user_scale_override(self.global_settings.get("ui_scale_override", 0.0))
        system_engine.calculate_ui_scale(screen_geo, self.screen().logicalDotsPerInch())
        
        self.resize(target_w, target_h)
        self.center_window()
        # Small enough for a 1280x720 or 1366x768 laptop with a taskbar. It
        # used to be 1024x768, which is taller than the usable area of those
        # screens: the footer and the bottom of the window sat off-screen.
        # Pages scroll inside their frame (tab_coordinator.PageScroll) when
        # they need more room than this.
        self.setMinimumSize(*MIN_WINDOW_SIZE)

        # Set Window Icon
        icon_path = ResourcePathManager.get_icons_dir() / "app_icon_128.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        self.fix_selection_colors()
        
        # 3. Build UI
        self.init_ui()
        self.init_variables()
        self.apply_stylesheet()
        
        # 4. Restore State
        self.restore_last_paths()
        self.restore_window_geometry()

        # The entry start-up selected. perform_async_login runs a few seconds
        # later and used to switch to Home unconditionally - so anybody quick
        # enough to open a tab first was yanked back to Home. It now only
        # does that when the person is still where start-up left them.
        self._startup_row = self.sidebar_nav.currentRow() if getattr(self, "sidebar_nav", None) else -1
        
        # 5. Setup Timers & Shortcuts
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.timeout.connect(self.periodic_cleanup)
        self.cleanup_timer.start(300000)  # Run cleanup every 5 minutes
        self.setup_help_shortcuts()
        self.setup_shortcuts()  # NEW: Global keyboard shortcuts (Improvement #9)

        # The update check: only when the studio has not switched it off
        # (check_updates_on_startup), and its answer is now heard - it was
        # started, and what it found was thrown away.
        self.update_checker = None
        if self.update_check_enabled():
            self.update_checker = UpdateChecker(self)
            self.update_checker.update_available.connect(self._on_update_available)
            self.update_checker.start()
        
        # Reflect DB runtime mode in UI immediately.
        self._refresh_db_runtime_indicator(show_fallback_warning=True)
        
        logging.info("Application initialized successfully")
        
        # Telemetry: App Start
        telemetry.track_event("app_started", {
            "version": APP_VERSION,
            "role": self.user_role,
            "os": sys.platform
        })
        
        self._init_complete = True
        
        # --- RV Integration Listener ---
        self._setup_rv_listener()

    @staticmethod
    def update_check_enabled() -> bool:
        value = GlobalConfig.get("check_updates_on_startup", True)
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "off")
        return bool(value)

    def _on_update_available(self, manifest):
        """A newer version: a toast that does not interrupt, unless put off today."""
        from .dialogs.update_available_dialog import is_snoozed
        version = str((manifest or {}).get("version", ""))
        if is_snoozed(version):
            logging.info("Update %s was put off; not offering it again yet.", version)
            return False
        self._pending_update = manifest
        self.show_feedback(f"Slate {version} is available.", "info", duration=15000,
                           action=("See what's new", self.show_update_dialog))
        return True

    def show_update_dialog(self):
        """The update dialog; Download and install hands over to Settings' installer."""
        from .dialogs.update_available_dialog import UpdateAvailableDialog
        manifest = getattr(self, "_pending_update", None)
        if not manifest:
            return False
        dialog = UpdateAvailableDialog(manifest, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        settings = self._get_tab_instance("Settings", create=True)
        if settings is not None and hasattr(settings, "_stage_update"):
            self._switch_to_tab_label("Settings")
            settings._stage_update(manifest)
            return True
        self.show_feedback("Open Settings to install the update.", "warning")
        return False

    def _setup_rv_listener(self):
        import os, json
        from pathlib import Path
        
        self.rv_watcher = QFileSystemWatcher(self)
        rv_feedback_dir = Path(os.path.expanduser("~")) / ".slate"
        rv_feedback_dir.mkdir(parents=True, exist_ok=True)
        self.rv_feedback_file = str(rv_feedback_dir / "rv_feedback.json")
        
        if not os.path.exists(self.rv_feedback_file):
            with open(self.rv_feedback_file, 'w') as f:
                f.write('{}')
                
        self.rv_watcher.addPath(self.rv_feedback_file)
        self.rv_watcher.fileChanged.connect(self.on_rv_feedback)
        
    def on_rv_feedback(self, path):
        """
        A verdict came back from RV: put it on the shot and save it.

        The watcher used to look for a tab named "VFX Dashboard PRO" and match
        the media against shot.render_path / shot.scan_path. Neither exists, so
        no verdict ever reached a shot. Matching now happens on the shot's own
        folders, which is the thing that is actually true.
        """
        import os

        from slate.core.domain.rv_feedback import (
            apply_feedback, match_shot, read_feedback,
        )

        try:
            feedback = read_feedback(path)
            if feedback is None:
                return

            # Qt drops a watch when the file is replaced rather than appended,
            # which is exactly what a JSON rewrite does.
            if hasattr(self, "rv_watcher") and path not in self.rv_watcher.files():
                self.rv_watcher.addPath(path)

            filename = os.path.basename(feedback.media_path)
            status = feedback.dashboard_status
            level = "success" if status == "Approved" else "error"

            tab = self._get_tab_instance("VFX Dashboard", create=False)
            shots = getattr(tab, "all_shots", None) if tab else None
            shot = match_shot(shots or [], feedback.media_path)

            if shot is None:
                self.show_status(
                    f"RV: {filename} marked {status}, but no matching shot is "
                    "open in the dashboard.", "warning", 8000
                )
                return

            project = getattr(tab, "current_project", None)
            apply_feedback(
                shot, feedback,
                project_root=getattr(project, "folder_base", "") or None,
                folder_resolver=getattr(tab, "_shot_folder_resolver", None),
            )

            saved = False
            if hasattr(tab, "on_shot_save"):
                try:
                    tab.on_shot_save(shot)
                    saved = True
                except Exception as exc:
                    logging.exception("Could not save RV verdict: %s", exc)

            if saved:
                self.show_status(
                    f"RV: {shot.shot_name} marked {status} and saved.",
                    level, 8000
                )
            else:
                self.show_status(
                    f"RV: {shot.shot_name} marked {status}, but it could not "
                    "be saved - check your permissions.", "warning", 8000
                )

        except Exception as e:
            logging.error(f"Error handling RV feedback: {e}")

    def statusBar(self):
        """
        The status line in the footer. Asking QMainWindow for its status bar
        would create a second, empty one under the footer again.
        """
        bar = getattr(self, "status_bar", None)
        return bar if bar is not None else super().statusBar()

    def user_subtitle(self) -> str:
        """
        What is shown after the name: the job title, or the role when there is
        none. The header and the window title both use it - they used to show
        different things, and "(None)" for a person without a job title.
        """
        data = self.user_data or {}
        job = str(data.get("job_title") or "").strip()
        if job and job.lower() != "none":
            return job
        role = str(getattr(self, "user_role", "") or "").strip()
        return role if role.lower() != "none" else ""

    def fix_selection_colors(self):
        """
        Nothing to do any more: the selection colour comes from the theme's
        palette (ThemeManager.build_palette). This used to set dark blue on the
        window over the application's own highlight, so a selection was a
        different colour depending on which widget drew it.
        """
    
    def show_status(self, message: str, level: str = "info", duration: int = 3000):
        """
        Show a color-coded, timed status bar message.

        Args:
            message:  The text to display.
            level:    "info" (white), "success" (green), "warning" (amber), "error" (red)
            duration: Display duration in milliseconds (0 = permanent).
        """
        status_bar = getattr(self, "status_bar", None)
        if not status_bar:
            logging.info(f"[{level.upper()}] {message}")
            return
        colors = {
            "info":    Gate.TEXT,   # near-white
            "success": Gate.OK,   # green
            "warning": Gate.WARN,   # amber
            "error":   Gate.BAD,   # red
        }
        color = colors.get(level, colors["info"])
        status_bar.setStyleSheet(
            f"QStatusBar {{ color: {color}; font-weight: {'bold' if level != 'info' else 'normal'}; "
            "background: transparent; border: none; }"
        )
        status_bar.showMessage(message, duration)
        if duration > 0:
            # Reset style after message clears
            safe_single_shot(
                duration + 50,
                self,
                lambda: getattr(self, "status_bar", None) and self.status_bar.setStyleSheet(
                    "QStatusBar { background: transparent; border: none; }"),
            )

    def show_feedback(self, message: str, level: str = "info", duration: int = None,
                      details: str = "", action=None):
        """
        Unified feedback entry point for tabs/widgets.

        The status bar keeps a copy of every line. Anything that matters -
        success, warning, error, or a message with an action - is also shown
        as a toast in the corner, which stays long enough to read and carries
        its action button ("Undo", "Open folder") and a "Details" button for
        the reason behind an error. Errors no longer throw a modal box at the
        person: the details wait behind that button.

        Tabs call it through slate.gui.components.feedback.toast().
        """
        from .components.feedback import raw_toast
        status_ms = duration if duration is not None else (4000 if level == "info" else 6000)
        self.show_status(message, level=level, duration=status_ms)
        if level != "info" or action or details:
            return raw_toast(self, message, level, action=action, duration=duration,
                             details=details)
        return None

    def _on_tab_switched(self, tab_text):
        """Callback when tab is switched (triggered by TabCoordinator)."""
        # Telemetry tracking
        try:
            from ..core.infra.telemetry import telemetry
            telemetry.track_event("tab_switched", {"tab": tab_text})
        except Exception as e:
            logging.debug(f"Telemetry tracking failed: {e}")

    def set_cinematic_mode(self, enabled: bool):
        """
        Disabled: Hiding the sidebar permanently traps the user on the Home tab.
        """
        pass

    def logout_user(self):
        """Sign out and show the sign-in window again (same application)."""
        from .components.feedback import confirm
        if not confirm(self, "Sign out", "Sign out of Slate?",
                       yes_label="Sign out", no_label="Cancel",
                       informative="Your attendance is not affected: signing out "
                                   "does not punch you out."):
            return
        # Ask the open screens first (unsaved edits, an ingest still copying).
        # closeEvent would ask again; the flag says it has been asked.
        if not self.confirm_open_work("sign out"):
            return
        self._work_confirmed = True
        self._logout_requested = True
        self.show_status("Signing out\u2026", "info", 1200)
        self.close()

    def confirm_open_work(self, action: str) -> bool:
        """
        Ask every open tab about unsaved edits and running work before
        `action` ("close Slate", "sign out", "sync"). True when it is safe.
        See components/work_guard.py for what a tab implements.
        """
        from .components.work_guard import confirm_leave
        tc = getattr(self, "tab_coordinator", None)
        tabs = dict(getattr(tc, "tab_instances", {}) or {}) if tc else {}
        return confirm_leave(self, tabs, action)

    def _reopen_login_after_logout(self):
        """
        Present login dialog again in the same process and relaunch main window on success.
        """
        app = QApplication.instance()
        if app:
            app.setQuitOnLastWindowClosed(False)

        login = LoginDialog(app_context=self.app_context, app_mode=self.app_mode)
        if login.exec() != QDialog.DialogCode.Accepted or not login.user_data:
            if app:
                app.quit()
            return
        self._open_window_for(login.user_data, app)

    def _open_window_for(self, user_data, app=None):
        """
        The window for the person who just signed in: the same application as
        this one. Signing out of Slate VFX or Slate Operations used to bring
        back the all-in-one window, with every group in it.

        Goes the way the first start goes: the loading window, the licence
        check, and the fleet report told the new name.
        """
        app = app or QApplication.instance()
        entry = getattr(app, "_slate_entry", None) if app else None
        if entry is not None and hasattr(entry, "launch_main_tool"):
            if getattr(entry, "reporter", None):
                try:
                    entry.reporter.update_user(user_data.get("display_name", "Unknown"))
                except Exception as exc:
                    logging.debug("Live reporter user update skipped: %s", exc)
            entry._show_startup_loading()
            entry.launch_main_tool(user_data)
            new_window = getattr(entry, "main_window", None)
        else:
            new_window = self.window_for_mode(user_data)
            from slate import licence
            if not licence.check_window(new_window):
                import os
                os._exit(3)             # licence section 5: the window lacks the credit line
            new_window.show_restored()
        if app and new_window is not None:
            # Keep strong ref to avoid GC closing the new main window.
            setattr(app, "_ut_active_window", new_window)
            app.setQuitOnLastWindowClosed(True)
        return new_window

    def window_for_mode(self, user_data):
        """A new window of this one's kind (VFX, Operations or the full suite)."""
        cls = type(self)
        if cls is VFXFolderCreatorApp:
            return cls(user_data, app_context=self.app_context, app_mode=self.app_mode)
        return cls(user_data, app_context=self.app_context)

    # --- ACTION HANDLERS ---

    def _get_tab_instance(self, label: str, create: bool = False):
        """Resolve a tab instance by label from TabCoordinator (lazy-safe)."""
        tc = getattr(self, "tab_coordinator", None)
        if not tc:
            return None

        if label in tc.tab_instances:
            return tc.tab_instances[label]

        if not create:
            return None

        try:
            idx = tc.tab_labels.index(label)
        except ValueError:
            return None
        return tc.get_or_create_tab(idx)

    def new_project(self):
        reply = QMessageBox.question(
            self, 
            "New Project", 
            "Start a new project?\nThis will clear current input fields.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        if reply == QMessageBox.StandardButton.Yes:
            cleared = 0
            for label in ("Build & Ingest",):
                tab = self._get_tab_instance(label, create=False)
                if tab and hasattr(tab, "clear_all"):
                    try:
                        tab.clear_all()
                        cleared += 1
                    except Exception as e:
                        logging.debug(f"New project clear failed for {label}: {e}")

            if cleared:
                self.status_bar.showMessage("New project started", 3000)
            else:
                self.status_bar.showMessage("No loaded workflow tabs to clear", 3000)

    def on_templates_refreshed(self):
        """Called when Settings Tab requests a template refresh."""
        try:
            # Reload merged templates into the shared ConfigManager instance.
            self.config_manager.user_templates = self.config_manager._load_user_templates()
            self.config_manager.templates = self.config_manager._secure_combine_templates()
        except Exception as e:
            logging.warning(f"Template reload failed in ConfigManager: {e}")

        refreshed_labels = []
        refresh_map = (
            ("Build & Ingest", "load_templates_to_ui"),
        )
        for label, method_name in refresh_map:
            tab = self._get_tab_instance(label, create=False)
            if tab and hasattr(tab, method_name):
                try:
                    getattr(tab, method_name)()
                    refreshed_labels.append(label)
                except Exception as e:
                    logging.warning(f"Template refresh failed for {label}: {e}")

        if refreshed_labels:
            joined = ", ".join(refreshed_labels)
            self.status_bar.showMessage(f"Templates refreshed in: {joined}", 3000)
            logging.info(f"Main Window: Refreshed templates in tabs: {joined}")
        else:
            self.status_bar.showMessage("Templates refreshed (new tabs will use latest config)", 3000)

    def on_global_settings_updated(self, updated_settings):
        """Propagate global settings to loaded operation tabs."""
        if not isinstance(updated_settings, dict):
            return

        try:
            self.global_settings = dict(updated_settings)
            self.config_manager.settings["global_settings"] = dict(updated_settings)
            system_engine.set_user_scale_override(updated_settings.get("ui_scale_override", 0.0))
            screen_geo = QApplication.primaryScreen().availableGeometry()
            system_engine.calculate_ui_scale(screen_geo, self.screen().logicalDotsPerInch())
            self.apply_stylesheet()
        except Exception as e:
            logging.debug(f"Failed to cache updated global settings in MainWindow: {e}")

        applied_labels = []
        for label in ("Build & Ingest",):
            tab = self._get_tab_instance(label, create=False)
            if tab and hasattr(tab, "apply_global_settings"):
                try:
                    tab.apply_global_settings(updated_settings)
                    applied_labels.append(label)
                except Exception as e:
                    logging.warning(f"Applying global settings failed for {label}: {e}")

        if hasattr(self, "header_builder") and hasattr(self.header_builder, "reload_branding"):
            try:
                self.header_builder.reload_branding()
            except Exception as e:
                logging.warning(f"Header branding refresh failed: {e}")

        if applied_labels:
            self.status_bar.showMessage(
                f"Settings applied to: {', '.join(applied_labels)}",
                3000,
            )
        else:
            self.status_bar.showMessage(
                "Settings saved (will apply when tabs are opened)",
                3000,
            )

    def apply_stylesheet(self):
        """
        Bring the theme up to date with the screen's UI scale.

        The look is the application's (main.qss through ThemeManager); the
        window no longer lays resources/styles.qss over it. That second sheet
        was an older dark design - charcoal, blue gradients - so the Light
        theme could never reach the window, and its "QWidget { font-size }"
        rule overruled every font a screen set for itself.
        """
        try:
            ThemeManager.refresh_scale()
        except Exception as e:
            logging.warning(f"Could not apply the theme: {e}")

    def _log_attendance_async(self):
        """Attempts to log attendance in background to avoid freezing UI."""
        import os
        if not self.user_data or getattr(self, '_is_closing', False) or os.environ.get("HEADLESS_TESTING") == "1":
            return
            
        if self._is_sqlite_fallback_mode():
            self.attendance_status_signal.emit(
                "Central attendance auto-log is unavailable in LOCAL MODE.",
                "warning",
                5000,
            )
            return

        username = self.user_data.get('user_id', self.user_data.get('username'))
        if not username:
            self.attendance_status_signal.emit(
                "Attendance skipped: user ID not available.",
                "warning",
                4500,
            )
            return

        from PySide6.QtCore import QRunnable, QThreadPool
        
        class AttendanceWorker(QRunnable):
            def __init__(self, username, is_closing_func, signal):
                super().__init__()
                self.username = username
                self.is_closing = is_closing_func
                self.signal = signal
                
            def run(self):
                import time
                import logging
                from slate.core.domain.central_attendance import CentralAttendance
                
                last_error = None
                for attempt in range(3):
                    if self.is_closing(): return
                    try:
                        att = CentralAttendance()
                        att.log_action(self.username, 'in')
                        logging.info(f"Attendance Success: {self.username}")
                        if not self.is_closing():
                            self.signal.emit(f"Attendance logged for {self.username}.", "success", 3000)
                        return
                    except Exception as e:
                        last_error = e
                        logging.warning(f"Attendance Attempt {attempt+1} failed: {e}")
                        time.sleep(2)
                        
                if not self.is_closing():
                    self.signal.emit("Attendance auto-log failed. Please use Attendance tab.", "warning", 6000)
                if last_error:
                    logging.error(f"Attendance failed after retries: {last_error}")

        worker = AttendanceWorker(
            username=username,
            is_closing_func=lambda: self._is_closing,
            signal=self.attendance_status_signal
        )
        worker.setAutoDelete(True)
        QThreadPool.globalInstance().start(worker)

    def user_has_navigated(self) -> bool:
        """
        Whether the sidebar selection has moved since start-up chose a tab.

        Keyboard shortcuts, the command palette and clicks all move the
        sidebar's current row, so comparing rows catches every route.
        """
        nav = getattr(self, "sidebar_nav", None)
        start = getattr(self, "_startup_row", -1)
        if nav is None or start is None or start < 0:
            return False
        return nav.currentRow() != start

    def perform_async_login(self):
        """Log attendance and finish the main window boot sequence."""
        # Only log attendance automatically in Ops or All mode (VFX has attendance removed)
        if getattr(self, "app_mode", "all") != "vfx":
            self._log_attendance_async()
        
        # E. Start Background Workers
        if hasattr(self, 'worker_manager'):
            self.worker_manager.start_workers()
        
        # Initiate cinematic mode by default for VFX, Ops, and All (both VFX and Ops have cinematic Home!)
        self.set_cinematic_mode(True)
        if self.user_has_navigated():
            # They have already opened something: leave them there.
            logging.info("Start-up: staying on %s - opened before start-up finished",
                         self.tab_coordinator.get_current_tab_name())
        elif not self._switch_to_tab_label("Home"):
            self.set_cinematic_mode(False)
            if getattr(self, "app_mode", "all") == "ops":
                if not self._switch_to_tab_label("Attendance"):
                    if hasattr(self, "tab_coordinator") and self.tab_coordinator.nav_items:
                        self.switch_to_tab_index(0)
            else:
                if hasattr(self, "tab_coordinator") and self.tab_coordinator.nav_items:
                    self.switch_to_tab_index(0)
        
        self._startup_complete = True
        
        # Safety Check: If Home failed to load, disable cinematic mode fallback to prevent UI trap
        if "Home" not in self.tab_coordinator.tab_instances:
            logging.warning("Home tab failed to load. Disabling cinematic mode fallback to prevent UI trap.")
            self.set_cinematic_mode(False)
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self.check_system_idle)
        self.idle_timer.start(60000) # Check every minute

        # Start Database Monitor
        self.db_monitor = DatabaseMonitor(interval_sec=5)
        self.db_monitor.connection_status.connect(self.on_db_status_changed)
        self.db_monitor.start()

    def on_db_status_changed(self, is_connected, latency):
        """Update header indicator."""
        if hasattr(self, 'header_builder'):
            self.header_builder.update_db_status(is_connected, latency)
        # Keep runtime mode indicator in sync (handles fallback visibility).
        self._refresh_db_runtime_indicator(show_fallback_warning=False)

    def _refresh_db_runtime_indicator(self, show_fallback_warning: bool = False):
        """Refresh header/status representation of DB runtime mode."""
        try:
            status = database_manager.get_runtime_status()
        except Exception as exc:
            logging.debug("DB runtime status unavailable: %s", exc)
            return
        self._db_runtime_status_cache = status or {}

        active_mode = str(status.get("active_mode", "unknown"))
        fallback_used = bool(status.get("fallback_used", False))

        if hasattr(self, "header_builder") and hasattr(self.header_builder, "set_db_runtime_status"):
            try:
                self.header_builder.set_db_runtime_status(active_mode, fallback_used)
            except Exception as exc:
                logging.debug("DB header mode update skipped: %s", exc)

        # Sync only means something while working offline on the local copy.
        if hasattr(self, "header_builder") and hasattr(self.header_builder, "set_sync_available"):
            self.header_builder.set_sync_available(active_mode == "sqlite" and fallback_used)

        self._refresh_system_health_strip(status)

        if show_fallback_warning and fallback_used and not self._db_fallback_warned:
            self._db_fallback_warned = True
            requested_mode = str(status.get("requested_mode", "postgres"))
            msg = (
                f"Database fallback active: requested {requested_mode}, using {active_mode}. "
                "Running in LOCAL MODE (central sync features limited)."
            )
            self.show_status(msg, "warning", 9000)

    def _refresh_system_health_strip(self, status: Optional[dict] = None):
        """Refresh compact runtime health strip in header."""
        if not hasattr(self, "header_builder") or not hasattr(self.header_builder, "set_system_health_status"):
            return
        status = status or self._get_db_runtime_status_cached()

        server_root_value = str(GlobalConfig.get("SERVER_ROOT", "")).strip()
        server_root = Path(server_root_value) if server_root_value else None
        server_root_ok = bool(server_root and server_root.exists() and server_root.is_dir())
        exr_enabled = bool(GlobalConfig.exr_loading_enabled())
        active_mode = str(status.get("active_mode", "")).lower()
        fallback_used = bool(status.get("fallback_used", False))
        sync_enabled = active_mode == "postgres" and not fallback_used

        try:
            self.header_builder.set_system_health_status(
                server_root_ok=server_root_ok,
                exr_enabled=exr_enabled,
                sync_enabled=sync_enabled,
            )
        except Exception as exc:
            logging.debug("Health strip update skipped: %s", exc)

    def _get_db_runtime_status_cached(self) -> dict:
        if self._db_runtime_status_cache:
            return self._db_runtime_status_cache
        try:
            self._db_runtime_status_cache = database_manager.get_runtime_status() or {}
        except Exception as exc:
            logging.debug("DB runtime status cache refresh skipped: %s", exc)
            self._db_runtime_status_cache = {}
        return self._db_runtime_status_cache

    def _is_sqlite_fallback_mode(self) -> bool:
        status = self._get_db_runtime_status_cached()
        active_mode = str(status.get("active_mode", "")).lower()
        fallback_used = bool(status.get("fallback_used", False))
        return active_mode == "sqlite" and fallback_used

    def diagnostics_text(self) -> str:
        """
        What IT asks for, in plain words: version, database, shared folder.
        No passwords or other credentials, ever.
        """
        status = self._get_db_runtime_status_cached()
        active_mode = str(status.get("active_mode", "unknown")).lower()
        fallback_used = bool(status.get("fallback_used", False))
        bootstrap_error = status.get("bootstrap_error")

        server_root_value = str(GlobalConfig.get("SERVER_ROOT", "")).strip()
        server_root = Path(server_root_value) if server_root_value else None
        server_root_ok = bool(server_root and server_root.exists() and server_root.is_dir())
        exr_enabled = bool(GlobalConfig.exr_loading_enabled())

        def yes(flag):
            return "Yes" if flag else "No"

        if active_mode == "postgres" and not fallback_used:
            database = "Studio database (PostgreSQL)"
        elif fallback_used:
            database = "This machine's local copy (SQLite) - the studio database could not be reached"
        else:
            database = active_mode.title() or "Unknown"
        host = str(GlobalConfig.get("db_host", "") or "").strip()
        port = str(GlobalConfig.get("db_port", "") or "").strip()
        name = str(GlobalConfig.get("db_name", "") or "").strip()
        where = ":".join(p for p in (host, port) if p)
        if name:
            where = f"{where} / {name}" if where else name

        lines = [
            f"Slate version: {APP_VERSION} ({getattr(self, 'suite_title', 'Slate')})",
            f"Signed in as: {getattr(self, 'user_display_name', '')} ({getattr(self, 'current_user', '')})",
            f"Role: {getattr(self, 'user_role', '')}",
            "",
            f"Database: {database}",
            f"Database server: {where or 'not set'}",
            f"Working offline (local mode): {yes(fallback_used)}",
            "",
            f"Shared folder: {server_root_value or 'not set'}",
            f"Shared folder reachable: {yes(server_root_ok)}",
            f"EXR previews: {'On' if exr_enabled else 'Off'}",
        ]
        if bootstrap_error:
            lines += ["", f"Database problem at start-up: {bootstrap_error}"]
        return "\n".join(lines)

    def show_runtime_diagnostics(self):
        """Diagnostics (Ctrl+Shift+D): the facts above, with a Copy button for a ticket."""
        from PySide6.QtWidgets import QPlainTextEdit
        from .core.controls import make_button
        text = self.diagnostics_text()
        dialog = QDialog(self)
        dialog.setWindowTitle("Diagnostics")
        dialog.setMinimumSize(520, 320)
        layout = QVBoxLayout(dialog)
        intro = QLabel("Copy this into an IT ticket if something is not working.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        layout.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy_button = make_button("Copy", "secondary")

        def copy():
            QApplication.clipboard().setText(text)
            copy_button.setText("Copied")

        copy_button.clicked.connect(copy)
        close_button = make_button("Close", "primary", on_click=dialog.accept)
        row.addWidget(copy_button)
        row.addWidget(close_button)
        layout.addLayout(row)
        self._diagnostics_dialog = dialog
        dialog.exec()

    def _build_sync_disabled_tab(self, title: str, message: str) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {Gate.INFO};")
        body_label = QLabel(message)
        body_label.setWordWrap(True)
        body_label.setStyleSheet(
            f"font-size: 13px; color: {Gate.WARN}; background: {Gate.ACCENT_SURFACE}; "
            f"border: 1px solid {Gate.LINE}; border-radius: 8px; padding: 12px;"
        )
        hint_label = QLabel("The LOCAL MODE badge in the header shows while this lasts. "
                            "Workspace Info and Diagnostics (Ctrl+Shift+D) say more.")
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f"font-size: 12px; color: {Gate.INFO};")

        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addWidget(hint_label)
        layout.addStretch(1)
        return panel

    def check_system_idle(self):
        try:
            from ..core.infra.idle_monitor import IdleMonitor
            monitor = IdleMonitor()
            idle_sec = monitor.get_idle_duration()
            if idle_sec > 600: 
                logging.debug(f"System Idle: {idle_sec:.1f}s")
        except Exception as e:
            logging.debug(f"Idle monitor check failed: {e}")




    # --- NETWORK LISTENERS ---
    def on_network_message(self, msg: dict):
        """Delegated to NetworkHandler."""
        if not hasattr(self, 'network_handler'):
            from .components.network_handler import NetworkHandler
            self.network_handler = NetworkHandler(self)
            
        self.network_handler.on_network_message(msg)

    # perform_remote_wipe moved to NetworkHandler


    def center_window(self):
        """Centers the window on the screen."""
        frame_geo = self.frameGeometry()
        screen = self.screen().availableGeometry().center()
        frame_geo.moveCenter(screen)
        self.move(frame_geo.topLeft())

    def _update_sidebar_responsive_width(self):
        """Reduce sidebar pressure on narrow windows to prevent tab overlap and animate transition smoothly."""
        if not hasattr(self, "sidebar_container") or self.sidebar_container is None:
            return

        if getattr(self, "sidebar_collapsed", False):
            target = 64
        else:
            width = self.width()
            if width < 1280:
                target = 180
            elif width < 1500:
                target = 205
            else:
                target = 240
                
        if self.sidebar_container.width() != target:
            # Stop existing animation if running
            if hasattr(self, "_sidebar_anim") and self._sidebar_anim.state() == QParallelAnimationGroup.Running:
                self._sidebar_anim.stop()
                
            self._sidebar_anim = QParallelAnimationGroup(self)
            
            anim_min = QPropertyAnimation(self.sidebar_container, b"minimumWidth")
            anim_min.setDuration(250)
            anim_min.setEasingCurve(QEasingCurve.InOutQuad)
            anim_min.setStartValue(self.sidebar_container.minimumWidth())
            anim_min.setEndValue(target)
            
            anim_max = QPropertyAnimation(self.sidebar_container, b"maximumWidth")
            anim_max.setDuration(250)
            anim_max.setEasingCurve(QEasingCurve.InOutQuad)
            anim_max.setStartValue(self.sidebar_container.maximumWidth())
            anim_max.setEndValue(target)
            
            self._sidebar_anim.addAnimation(anim_min)
            self._sidebar_anim.addAnimation(anim_max)
            self._sidebar_anim.start()

    def toggle_fullscreen(self):
        """Toggles between Fullscreen and Normal mode."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def setup_help_shortcuts(self):
        """Setup keyboard shortcuts including help."""
        # Fullscreen Toggle (F11)
        self.fs_shortcut = QAction("Toggle Fullscreen", self)
        self.fs_shortcut.setShortcut(QKeySequence("F11"))
        self.fs_shortcut.triggered.connect(self.toggle_fullscreen)
        self.addAction(self.fs_shortcut)
        
        # Help Dialog (F1) - Re-enabled with JSON content
        self.help_shortcut = QAction("Show Help", self)
        self.help_shortcut.setShortcut(QKeySequence("F1"))
        self.help_shortcut.triggered.connect(self.show_help_dialog)
        self.addAction(self.help_shortcut)
        
        logging.info("Shortcuts registered: F11 (fullscreen), F1 (help)")
    
    def show_help_dialog(self):
        """Show help dialog with documentation."""
        try:
            # Get current tab name from TabCoordinator
            current_tab_name = self.tab_coordinator.get_current_tab_name()
            tab_id = "getting_started"  # Default
            
            # Map current tab name to help tab ID
            # Every screen in the sidebar, so F1 always lands somewhere useful.
            # Half of these were missing, and "Timeline Viewer" pointed at a
            # section that did not exist - both fell through to the front page.
            tab_mapping = {
                "Home": "home",
                "Build & Ingest": "folder_creator",
                "CAP Rename": "rename_tool",
                "Stock Viewer": "stock_browser",
                "Timeline Viewer": "shot_review",
                "VFX Dashboard": "dashboard",
                "Scheduling": "scheduling",
                "Bidding": "bidding",
                "Attendance": "attendance",
                "Leave": "leave",
                "Joining & Leaving": "joining_leaving",
                "Hardware": "hardware",
                "Licences": "licences",
                "IT Support": "it_support",
                "Deployment": "deployment",
                "Users & Roles": "users_roles",
                "Admin Panel": "admin_panel",
                "Tester Panel": "tester",
                "Settings": "settings",
                "Workspace Info": "workspace_info",
            }
            
            # Get the help tab ID based on current tab
            tab_id = tab_mapping.get(current_tab_name, "getting_started")
            
            logging.info(f"Opening help for tab: {current_tab_name} (help_id: {tab_id})")
            
            # Show help dialog
            # The two shells do not have the same sidebar, so they do not
            # get the same help.
            show_help(self, tab_id, mode=getattr(self, "app_mode", None))
            
        except Exception as e:
            logging.exception(f"Error opening help dialog: {e}")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Help Error", 
                f"Could not open help dialog.\n\nError: {str(e)}") 

    def periodic_cleanup(self):
        """Run background maintenance tasks."""
        if hasattr(self, 'sweeper_engine'):
             self.sweeper_engine.start_sweep()

    # ===== KEYBOARD SHORTCUTS =====
    def setup_shortcuts(self):
        """Bind the SHORTCUTS table (F1 and F11 are window actions, set up above)."""
        from PySide6.QtGui import QShortcut, QKeySequence

        self._shortcuts = []
        for keys, _label, method, _alias in SHORTCUTS:
            if keys in ("F1", "F11"):
                continue
            if method == "switch_to_nth_tab":
                # Ctrl+1 opens the first screen in the sidebar - the first
                # real one, not a category heading.
                for n in range(1, 10):
                    shortcut = QShortcut(QKeySequence(f"Ctrl+{n}"), self)
                    shortcut.activated.connect(lambda n=n: self.switch_to_nth_tab(n))
                    self._shortcuts.append(shortcut)
                continue
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(getattr(self, method))
            self._shortcuts.append(shortcut)
        logging.debug("[SHORTCUTS] %s", ", ".join(k for k, *_ in SHORTCUTS))

    def show_shortcuts(self):
        """The Keyboard shortcuts sheet (Account menu)."""
        from PySide6.QtWidgets import QGridLayout
        from .core.controls import make_button
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard shortcuts")
        layout = QVBoxLayout(dialog)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        for row, (keys, label, _method, alias) in enumerate(SHORTCUTS):
            key_label = QLabel(keys)
            key_label.setStyleSheet(
                f"font-family: Consolas, monospace; color: {Gate.TEXT}; "
                f"background: {Gate.RAISED}; border: 1px solid {Gate.LINE}; "
                "border-radius: 4px; padding: 2px 8px;")
            what = QLabel(label)
            what.setStyleSheet(f"color: {Gate.TEXT_DIM if alias else Gate.TEXT_2};")
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(what, row, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Close", "primary", on_click=dialog.accept))
        layout.addLayout(row)
        self._shortcuts_dialog = dialog
        dialog.exec()

    def switch_to_tab_index(self, index):
        """Switch to the sidebar row `index` (0-based; headers count)."""
        if 0 <= index < self.tab_coordinator.get_tab_count():
            self.sidebar_nav.setCurrentRow(index)
            logging.debug(f"[SHORTCUT] Switched to row {index}")

    def switch_to_nth_tab(self, n: int) -> bool:
        """Ctrl+N: the N-th screen this person has, counting from 1 and skipping headings."""
        rows = self.tab_coordinator.tab_rows()
        if 1 <= n <= len(rows):
            self.tab_coordinator._reveal_row(rows[n - 1])
            self.sidebar_nav.setCurrentRow(rows[n - 1])
            return True
        return False

    def _switch_to_tab_label(self, label: str) -> bool:
        """Switch to tab by exact label."""
        for i, tab_label in enumerate(self.tab_coordinator.tab_labels):
            if tab_label == label:
                self.switch_to_tab_index(i)
                return True
        return False

    def _run_quick_temp_cleanup(self):
        """Run the registered maintenance sweepers immediately."""
        if hasattr(self, "sweeper_engine") and self.sweeper_engine:
            self.sweeper_engine.start_sweep()
            self.show_feedback("Maintenance sweep started.", level="info", duration=2500)
        else:
            self.show_feedback("Sweeper engine is unavailable.", level="warning", duration=3000)

    def _rebuild_timeline_from_dashboard(self):
        """Rebuild the Olive lineup from the shots the dashboard is tracking."""
        if not self._switch_to_tab_label("Timeline Viewer"):
            self.show_feedback("Timeline Viewer is not available for this user.",
                               level="warning", duration=3000)
            return

        tab = self._get_tab_instance("Timeline Viewer", create=True)
        if not tab or not hasattr(tab, "refresh_from_dashboard"):
            self.show_feedback("Timeline Viewer could not be opened.",
                               level="error", duration=3000)
            return

        # Say what really happened: this used to report success whatever the
        # rebuild did.
        from .components.feedback import Result
        result = Result.from_value(
            tab.refresh_from_dashboard(),
            failure_message="The timeline could not be rebuilt.")
        if result.ok:
            self.show_feedback(result.message or "Timeline rebuilt from the dashboard.",
                               level="success")
        else:
            self.show_feedback(result.message, level="warning", details=result.detail)

    def _open_named_tab(self, label: str):
        """Open a named tab through coordinator and show status feedback."""
        if not self._switch_to_tab_label(label):
            self.show_feedback(f"Tab '{label}' is not available for this user.", level="warning", duration=3000)
            return
        self.show_feedback(f"Opened {label}.", level="info", duration=1800)

    def _refresh_stock_viewer(self):
        """Refresh the stock browser tab if available."""
        if not self._switch_to_tab_label("Stock Viewer"):
            self.show_feedback("Stock Viewer tab is not available for this user.", level="warning", duration=3000)
            return
        stock_tab = self._get_tab_instance("Stock Viewer", create=True)
        if not stock_tab:
            self.show_feedback("Stock Viewer tab could not be created.", level="error", duration=3000)
            return
        if hasattr(stock_tab, "load_library_from_server"):
            stock_tab.load_library_from_server()
            self.show_feedback("Stock Viewer refresh started.", level="info", duration=2200)
            return
        self.show_feedback("Stock Viewer refresh action is unavailable.", level="warning", duration=3000)

    def trigger_sync_database(self):
        """
        The header's Sync: push the changes made here while Slate was working
        offline (on its local copy) up to the studio database, then pull the
        studio's changes back down.

        Only offered while Slate is in local mode - with the studio database
        connected every change is already shared the moment it is saved, and
        there is nothing to sync. The button used to be shown to everybody and
        was connected to nothing.
        """
        from ..core.domain.sync_worker import SyncWorker
        worker = getattr(self, "_sync_worker", None)
        if worker is not None and worker.sync_manager.is_syncing:
            self.show_feedback("Sync is already running.", level="info", duration=2500)
            return

        # Edits still sitting unsaved on a screen are not part of the push.
        if not self.confirm_open_work("sync"):
            return

        self._sync_worker = SyncWorker(database_manager)
        # A bound method, so the result is delivered on the UI thread: the
        # worker finishes on its own thread.
        self._sync_worker.finished.connect(self._on_sync_finished)
        self._set_sync_busy(True)
        self._sync_worker.start_sync()
        self.show_feedback("Syncing your offline changes with the studio database…",
                           level="info", duration=3000)

    def _on_sync_finished(self, success: bool):
        self._set_sync_busy(False)
        worker = getattr(self, "_sync_worker", None)
        task = getattr(worker, "task_info", None)
        reason = str(getattr(task, "error_message", "") or "")
        if success:
            self.show_feedback("Offline changes synced with the studio database.",
                               level="success")
        elif "unreachable" in reason.lower() or getattr(task, "status", "") == "Offline":
            self.show_feedback(
                "Could not reach the studio database, so nothing was synced. "
                "Your offline changes are kept - try again when the connection is back.",
                level="warning")
        else:
            self.show_feedback(
                "Sync did not finish. Your offline changes are kept on this machine.",
                level="error", details=reason or "The sync reported a failure without a reason.")

    def _set_sync_busy(self, busy: bool):
        button = getattr(getattr(self, "header_builder", None), "sync_button", None)
        if button is None:
            return
        try:
            button.setEnabled(not busy)
            button.setText("Syncing…" if busy else "Sync")
        except RuntimeError:
            pass

    def refresh_current_tab(self):
        """Refresh/reload the current tab if it supports it"""
        current_widget = self.tab_coordinator.get_current_tab()
        if current_widget:
            called_method = None
            for method_name in (
                "refresh",
                "refresh_data",
                "refresh_project",
                "load_library_from_server",
                "reload",
                # Screens built on the shared table tools (Hardware,
                # Deployment, Scheduling, Bidding) reload with load_data;
                # their Refresh button promises F5.
                "load_data",
            ):
                if hasattr(current_widget, method_name):
                    getattr(current_widget, method_name)()
                    called_method = method_name
                    break

            if called_method:
                self.show_status("Refreshed", "info", 2000)
            else:
                self.show_status("This screen keeps itself up to date", "info", 2000)
            logging.debug(f"[SHORTCUT] Refresh triggered on {self.tab_coordinator.get_current_tab_name()}")
    
    def closeEvent(self, event):
        """Handle application shutdown cleanly."""
        # Before anything is stopped: does an open screen have unsaved edits,
        # or work that stopping would cut short? Signing out has already asked.
        if getattr(self, "_init_complete", False) and not getattr(self, "_is_closing", False):
            already_asked = getattr(self, "_work_confirmed", False)
            self._work_confirmed = False
            if not already_asked and not self.confirm_open_work("close Slate"):
                self._logout_requested = False
                event.ignore()
                return
        self._is_closing = True
        logging.info("VFXFolderCreatorApp: Starting application shutdown sequence...")

        # 1. Stop recurring UI timers
        if hasattr(self, 'cleanup_timer') and self.cleanup_timer and self.cleanup_timer.isActive():
            self.cleanup_timer.stop()
        if hasattr(self, 'idle_timer') and self.idle_timer and self.idle_timer.isActive():
            self.idle_timer.stop()
        centre = getattr(self, "notification_center", None)
        if centre is not None:
            centre.stop()

        # 2. Cleanup all registered tabs and their workers/threads
        if hasattr(self, 'tab_coordinator') and self.tab_coordinator:
            for item in getattr(self.tab_coordinator, 'nav_items', []):
                page = item.get('page')
                if page:
                    for cleanup_method in ('cleanup_resources', 'cleanup', 'closeEvent', 'close', 'stop'):
                        if hasattr(page, cleanup_method) and callable(getattr(page, cleanup_method)):
                            try:
                                if cleanup_method == 'closeEvent':
                                    pass  # don't simulate event
                                else:
                                    getattr(page, cleanup_method)()
                                    break
                            except Exception as exc:
                                logging.debug("Tab cleanup error on %s: %s", getattr(page, '__class__', {}).__name__, exc)

        # 3. Cancel all background tasks in task registry
        try:
            from ..core.infra.task_registry import task_registry
            task_registry.cancel_all_tasks()
        except Exception as exc:
            logging.debug(f"Task registry cancel skipped: {exc}")

        # 4. Stop DB monitor
        try:
            if hasattr(self, 'db_monitor') and self.db_monitor:
                self.db_monitor.stop(timeout_ms=1000)
        except Exception as e:
            logging.debug(f"Error stopping db monitor: {e}")

        # 5. Stop network manager
        try:
            from ..core.infra.network_manager import network_manager
            if network_manager:
                network_manager.stop()
        except Exception:
            pass

        # 6. Stop sweeper engine
        try:
            if hasattr(self, 'sweeper_engine') and self.sweeper_engine:
                self.sweeper_engine.stop()
        except Exception:
            pass

        # 7. Terminate any tracked child subprocesses (FFmpeg, uvicorn, etc.)
        try:
            from ..utils.process_manager import subprocess_tracker
            subprocess_tracker.terminate_all(timeout=1.0)
        except Exception as exc:
            logging.debug("Subprocess tracker cleanup: %s", exc)

        # 8. Drain global thread pool
        try:
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().waitForDone(500)
        except Exception:
            pass

        # 9. Force database connection pool shutdown
        try:
            from ..core.infra.database_manager import database_manager
            database_manager.force_shutdown()
        except Exception as exc:
            logging.debug("Database force shutdown skipped: %s", exc)

        # 10. Process teardown
        super().closeEvent(event)

    def show_settings_tab(self):
        """Jump directly to Settings tab"""
        for i, label in enumerate(self.tab_coordinator.tab_labels):
            if label == "Settings":
                self.switch_to_tab_index(i)
                break
    
    def load_plugins(self):
        """Dynamically find and load tabs from 'gui/plugins' directory."""
        try:
            import importlib
            import pkgutil
            import inspect
            from .plugins.plugin_interface import SlatePlugin
            
            # Locate the plugins package
            try:
                from . import plugins
                path = plugins.__path__
                prefix = plugins.__name__ + "."
            except ImportError:
                 logging.warning("Plugin package not found.")
                 return

            for _, name, _ in pkgutil.iter_modules(path, prefix):
                try:
                    module = importlib.import_module(name)
                    # Find classes that inherit from SlatePlugin
                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)
                        if (inspect.isclass(attribute) and 
                            issubclass(attribute, SlatePlugin) and 
                            attribute is not SlatePlugin):
                            
                            # Instantiate and Add
                            try:
                                plugin_instance = attribute()
                                
                                # Inject Context
                                context = {
                                    "user_data": self.user_data,
                                    "user_role": self.user_role,
                                    "config_manager": self.config_manager,
                                    "main_window": self
                                }
                                if hasattr(plugin_instance, 'initialize'):
                                    plugin_instance.initialize(context)

                                tab_name = getattr(plugin_instance, 'plugin_name', "Plugin")
                                tab_icon = getattr(plugin_instance, 'plugin_icon', "\U0001F9E9")
                                
                                # Register as eager tab so coordinator handles navigation consistently.
                                self.tab_coordinator.register_tab(
                                    plugin_instance,
                                    tab_name,
                                    icon=tab_icon,
                                )
                                
                                if not hasattr(self, "loaded_plugins"):
                                    self.loaded_plugins = []
                                self.loaded_plugins.append(tab_name)
                                logging.info(f"Loaded Plugin: {name}.{attribute_name}")
                            except Exception as e:
                                logging.exception(f"Failed to instantiate plugin {attribute_name}: {e}")
                                
                except Exception as e:
                    logging.exception(f"Failed to load plugin module {name}: {e}")
                    
        except Exception as e:
            logging.exception(f"Plugin Loader Error: {e}")

