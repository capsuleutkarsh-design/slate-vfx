import sys
import ctypes
import time
import subprocess
from pathlib import Path
import asyncio
import logging

current_file = Path(__file__).resolve()
package_dir = current_file.parent
root_dir = package_dir.parent
if str(root_dir) not in sys.path: sys.path.insert(0, str(root_dir))

from slate.core.infra.logging_utils import setup_logging
setup_logging("Gatekeeper")

from slate.core.infra.crash_handler import setup_global_crash_handler
setup_global_crash_handler()

try:
    import qasync
except ImportError as e:
    logging.critical("Missing required dependency 'qasync'. Please install it using 'pip install qasync'.")
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv)
    QMessageBox.critical(None, "Startup Error", "Missing required background software 'qasync'.\nPlease install it or contact support.")
    sys.exit(1)


from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
    QLabel,
    QFrame,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QLineEdit,
    QHBoxLayout,
    QFormLayout,
    QDialogButtonBox,
    QSpinBox,
    QPushButton,
    QFileDialog,
)
from PySide6.QtGui import QIcon
from slate.core.infra.qt_compat import Qt, QThread, Signal

from slate.gui.login_dialog import LoginDialog
from slate.gui.components.qt_safety import safe_single_shot
from slate.utils.startup_manager import StartupManager
from slate.utils.resource_manager import ResourcePathManager
from slate.core.infra.server_hub import ServerHub
from slate.core.domain.central_attendance import CentralAttendance  
from slate.core.domain.live_reporter import LiveReporter 
from slate.core.domain.backup_service import AutoBackupThread
from slate.core.infra.database_manager import database_manager 
from slate.core.infra.app_context import AppContext
from slate.core.infra.global_config import GlobalConfig

class BroadcastWindow(QDialog):
    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        
        # Frame
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: rgba(20, 20, 20, 240);
                border: 2px solid #D9635F;
                border-radius: 10px;
            }
        """)
        frame_layout = QVBoxLayout(frame)
        
        # Icon
        title = QLabel("[WARN] ADMIN MESSAGE")
        title.setStyleSheet("color: #D9635F; font-weight: bold; font-size: 16px;")
        title.setAlignment(Qt.AlignCenter)
        frame_layout.addWidget(title)
        
        # Message
        msg_label = QLabel(message)
        msg_label.setStyleSheet("color: white; font-size: 14px;")
        msg_label.setAlignment(Qt.AlignCenter)
        msg_label.setWordWrap(True)
        msg_label.setTextFormat(Qt.PlainText) # SECURITY: Prevent HTML Injection
        frame_layout.addWidget(msg_label)
        
        # Close Button
        btn_close = QLabel("Running command...")
        btn_close.setAlignment(Qt.AlignCenter)
        btn_close.setStyleSheet("color: #87857F; font-size: 10px; margin-top: 10px;")
        frame_layout.addWidget(btn_close) # Actually just informational
        
        layout.addWidget(frame)
        
        # Auto close
        safe_single_shot(10000, self, self.close)
        self.resize(400, 200)
        
        # Center on screen
        if QApplication.primaryScreen():
            self.move(QApplication.primaryScreen().availableGeometry().center() - self.rect().center())


class StartupLoadingDialog(QDialog):
    """Lightweight loading dialog shown while MainWindow initializes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Launching Slate")
        self.setModal(False)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)

        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background-color: rgba(18, 24, 36, 235);
                border: 1px solid #2C2C34;
                border-radius: 10px;
            }
            QLabel {
                color: #E8E6E1;
                font-size: 13px;
                background: transparent;
                border: none;
            }
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("Starting Slate...")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        progress = QProgressBar()
        progress.setRange(0, 0)  # Indeterminate
        progress.setTextVisible(False)
        progress.setFixedHeight(10)
        progress.setStyleSheet(
            """
            QProgressBar {
                background: #16323A;
                border: 1px solid #16323A;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: #3EA8BF;
                border-radius: 5px;
            }
            """
        )
        layout.addWidget(progress)

        hint = QLabel("Loading workspace and services")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #6BA4C9; font-size: 11px;")
        layout.addWidget(hint)

        root.addWidget(frame)
        self.resize(360, 130)

        if QApplication.primaryScreen():
            self.move(QApplication.primaryScreen().availableGeometry().center() - self.rect().center())


class FirstRunSetupDialog(QDialog):
    """First-run configuration dialog for required runtime paths and DB connection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Slate - First Run Setup")
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "Configure required paths and database connection.\n"
            "You can update these later in Settings > Paths & Connections."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        server_wrap = QWidget()
        server_row = QHBoxLayout(server_wrap)
        server_row.setContentsMargins(0, 0, 0, 0)
        self.server_root_input = QLineEdit(str(GlobalConfig.get("SERVER_ROOT", "")))
        self.server_root_input.setPlaceholderText("e.g. X:/Extra/Slate_Central")
        browse_server_btn = QPushButton("Browse")
        browse_server_btn.clicked.connect(self._browse_server_root)
        server_row.addWidget(self.server_root_input, 1)
        server_row.addWidget(browse_server_btn)
        form.addRow("Server Root", server_wrap)

        self.db_host_input = QLineEdit(str(GlobalConfig.get("db_host", "")))
        form.addRow("DB Host", self.db_host_input)

        self.db_port_input = QSpinBox()
        self.db_port_input.setRange(1, 65535)
        self.db_port_input.setValue(int(GlobalConfig.get("db_port", 5440) or 5440))
        form.addRow("DB Port", self.db_port_input)

        self.db_name_input = QLineEdit(str(GlobalConfig.get("db_name", "ut_vfx")))
        form.addRow("DB Name", self.db_name_input)

        self.db_user_input = QLineEdit(str(GlobalConfig.get("db_user", "postgres")))
        form.addRow("DB User", self.db_user_input)

        self.db_password_input = QLineEdit(str(GlobalConfig.get("db_password", "")))
        self.db_password_input.setEchoMode(QLineEdit.Password)
        self.db_password_input.setPlaceholderText("Optional if configured via credential setup")
        form.addRow("DB Password", self.db_password_input)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _browse_server_root(self):
        start_dir = self.server_root_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Slate_Central Root", start_dir)
        if selected:
            self.server_root_input.setText(selected)

    def _validate_and_accept(self):
        if not self.server_root_input.text().strip():
            QMessageBox.warning(self, "Missing Field", "Server Root is required.")
            return
        if not self.db_host_input.text().strip():
            QMessageBox.warning(self, "Missing Field", "DB Host is required.")
            return
        if not self.db_name_input.text().strip():
            QMessageBox.warning(self, "Missing Field", "DB Name is required.")
            return
        if not self.db_user_input.text().strip():
            QMessageBox.warning(self, "Missing Field", "DB User is required.")
            return
        self.accept()

    def values(self):
        return {
            "SERVER_ROOT": self.server_root_input.text().strip(),
            "db_host": self.db_host_input.text().strip(),
            "db_port": int(self.db_port_input.value()),
            "db_name": self.db_name_input.text().strip(),
            "db_user": self.db_user_input.text().strip(),
            "db_password": self.db_password_input.text().strip(),
        }


        
# --- WORKER CLASS TO FIX UI FREEZE ---
class CommandCheckWorker(QThread):
    command_received = Signal(dict)
    
    def __init__(self, hub, processed_cmds):
        super().__init__()
        self.hub = hub
        self.processed_cmds = processed_cmds
        self.running = True
        self.ALLOWED_COMMANDS = {'message', 'shutdown', 'restart', 'update_notify'}

    def run(self):
        while self.running:
            try:
                # Runs on background thread - DB/File I/O safe here
                commands = self.hub.get_active_commands()
                for cmd in commands:
                    cmd_id = f"{cmd['command']}_{cmd['timestamp']}"
                    if cmd_id in self.processed_cmds: 
                        continue
                        
                    # Validate
                    if cmd.get('command') not in self.ALLOWED_COMMANDS:
                        continue
                    
                    self.processed_cmds.append(cmd_id)
                    self.command_received.emit(cmd)
                    
            except Exception as e:
                logging.exception(f"Command check failed: {e}")
            
            # Sleep 5 seconds
            for _ in range(10): 
                if not self.running: break
                time.sleep(0.5)

    def stop(self):
        self.running = False
        self.wait()

class ApplicationEntry:
    def __init__(self, app_mode: str = "all"):
        # --- DPI SCALING FIX ---
        # Let Qt 6 handle its own DPI context natively. Mixing ctypes SetProcessDpiAwareness
        # with Qt 6 breaks the Windows display boundaries, causing maximize/minimize bugs.
        self.app_mode = app_mode or "all"
        
        from slate.core.infra.qt_compat import Qt
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )

        # ... (Same init logic) ...
        self.app = QApplication(sys.argv)
        
        # --- APPLY GLOBAL THEME ---
        try:
            from slate.gui.core.theme_manager import ThemeManager
            ThemeManager.apply_theme(self.app, str(package_dir))
        except Exception as e:
            logging.exception(f"Failed to apply global theme: {e}")
            
        self.app.setQuitOnLastWindowClosed(False)
        self._cleanup_done = False
        self._is_closing = False
        self.loading_dialog = None
        self._startup_cancelled = False
        self.app.aboutToQuit.connect(self._cleanup_background_services)
        self._db_runtime_status = {}
        
        self.hub = ServerHub(); self.attendance = CentralAttendance()
        self.app_context = AppContext(
            db_manager=database_manager,
            server_hub=self.hub,
            attendance=self.attendance,
        )

        if not self._ensure_first_run_setup():
            self._startup_cancelled = True
            self.cleanup_and_exit()
            return

        # Rehydrate DB backend after first-run config writes.
        try:
            database_manager.reload_from_config()
        except Exception as exc:
            logging.warning("Database manager reload after setup failed: %s", exc)
        
        # FATAL CRASH FIX: Force Database Password Prompt on Main Thread
        # If we wait until IngestWorker (bg thread) needs it, PySide crashes because QInputDialog 
        # cannot run in a thread. We must ensure the pool is initialized here.
        try:
             with database_manager.get_connection():
                 pass
        except Exception as e:
             logging.warning(f"Startup DB Init / Password Check: {e}")
        self._db_runtime_status = self._get_db_runtime_status()
        self._show_gatekeeper_db_warning()

        
        self.startup_mgr = StartupManager()
        self._maybe_cleanup_startup_entry()
        
        self.reporter = None
        self.backup_thread = None
        if not self._is_sqlite_fallback_mode():
            self.reporter = LiveReporter(user_name="Locked")
            self.reporter.start()
            
            # Start automatic background backups
            self.backup_thread = AutoBackupThread(interval_hours=12)
            self.backup_thread.start()
            logging.info("Auto backup thread started.")
        else:
            logging.warning("Live reporter and auto backup disabled in SQLite fallback mode.")
        
        self.processed_cmds = []
        self.cmd_worker = None

        # Remote command polling is central-sync only. Disable in SQLite fallback mode.
        if not self._is_sqlite_fallback_mode():
            self.cmd_worker = CommandCheckWorker(self.hub, self.processed_cmds)
            self.cmd_worker.command_received.connect(self._process_command_main_thread)
            self.cmd_worker.start()
        else:
            logging.warning("Command polling disabled in SQLite fallback mode.")
        
        self.icon_path = ResourcePathManager.get_icons_dir() / "app_icon_128.ico"
        if self.icon_path.exists(): self.app.setWindowIcon(QIcon(str(self.icon_path)))

        self.show_software_login()

    def _get_db_runtime_status(self) -> dict:
        try:
            return database_manager.get_runtime_status() or {}
        except Exception as exc:
            logging.debug("DB runtime status unavailable in gatekeeper: %s", exc)
            return {}

    def _is_sqlite_fallback_mode(self) -> bool:
        status = self._db_runtime_status or self._get_db_runtime_status()
        active_mode = str(status.get("active_mode", "")).lower()
        fallback_used = bool(status.get("fallback_used", False))
        return active_mode == "sqlite" and fallback_used

    def _show_gatekeeper_db_warning(self):
        status = self._db_runtime_status or {}
        if not status:
            return
        if not self._is_sqlite_fallback_mode():
            return
        requested_mode = str(status.get("requested_mode", "postgres"))
        message = (
            "Database fallback is active before login.\n\n"
            f"Requested: {requested_mode}\n"
            "Active: sqlite (fallback)\n\n"
            "Slate is running in LOCAL MODE (standalone).\n"
            "Central sync features are limited."
        )
        QMessageBox.warning(None, "Database Fallback Active", message)

    def _get_first_run_flag_path(self) -> Path:
        config_instance = GlobalConfig._instance or GlobalConfig()
        return config_instance.local_app_data / ".setup_complete"

    def _ensure_first_run_setup(self) -> bool:
        """
        Run first-run setup when required config is missing.

        Returns:
            bool: True to continue startup, False to exit.
        """
        try:
            required_keys = ("SERVER_ROOT", "db_host", "db_port", "db_name", "db_user")
            missing = [k for k in required_keys if not str(GlobalConfig.get(k, "")).strip()]
            flag_path = self._get_first_run_flag_path()
            setup_marked_complete = flag_path.exists()

            # Only force setup dialog on true first-run when required keys are missing.
            needs_setup = (not setup_marked_complete) and bool(missing)

            if missing:
                logging.info(f"FirstRunSetup: Missing keys: {missing}")
                for k in required_keys:
                    logging.info(f"  {k} = {repr(GlobalConfig.get(k, ''))}")
                if setup_marked_complete:
                    logging.warning(
                        "FirstRunSetup: setup flag exists but keys are missing; "
                        "skipping forced dialog. Use Login > Reconfigure to fix values."
                    )

            if not needs_setup:
                if (not setup_marked_complete) and (not missing):
                    try:
                        flag_path.parent.mkdir(parents=True, exist_ok=True)
                        flag_path.write_text("configured=true\n", encoding="utf-8")
                    except OSError as flag_exc:
                        logging.debug("FirstRunSetup: could not write setup flag (%s)", flag_exc)
                return True

            dlg = FirstRunSetupDialog()
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False

            values = dlg.values()
            for key in ("SERVER_ROOT", "db_host", "db_port", "db_name", "db_user"):
                GlobalConfig.set(key, values[key])
            if values.get("db_password"):
                GlobalConfig.set("db_password", values["db_password"])

            flag_path.parent.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("configured=true\n", encoding="utf-8")

            users_file = Path(values["SERVER_ROOT"]) / "Config" / "users.json"
            if not users_file.exists():
                reply = QMessageBox.question(
                    None,
                    "Setup Notice",
                    f"users.json was not found at:\n{users_file}\n\n"
                    "Create default admin user EMP0001 now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        self.app_context.user_manager().add_user(
                            "EMP0001",
                            "admin123",
                            ["Developer"],
                            "Administrator",
                            "Admin",
                        )
                    except Exception as create_exc:
                        logging.warning("Could not auto-create default user: %s", create_exc)
            return True
        except Exception as exc:
            logging.exception("First-run setup failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                None,
                "Setup Error",
                f"Could not complete first-run setup:\n{exc}",
            )
            return False

    def _maybe_cleanup_startup_entry(self):
        """
        Cleanup legacy startup registry entry only when explicitly enabled in config.

        Keeps OPS behavior predictable and avoids silently removing IT-managed startup entries.
        """
        should_cleanup = bool(GlobalConfig.get("cleanup_legacy_startup_entry", False))
        if not should_cleanup:
            return
        try:
            if self.startup_mgr.remove_from_startup():
                logging.info("Removed legacy startup entry (cleanup_legacy_startup_entry=true).")
        except Exception as exc:
            logging.warning("Startup entry cleanup failed: %s", exc)

    def _show_startup_loading(self):
        if self.loading_dialog is not None:
            return
        self.loading_dialog = StartupLoadingDialog()
        self.loading_dialog.show()
        QApplication.processEvents()

    def _hide_startup_loading(self):
        if self.loading_dialog is None:
            return
        try:
            self.loading_dialog.close()
            self.loading_dialog.deleteLater()
        except Exception as exc:
            logging.debug("Loading dialog cleanup skipped: %s", exc)
        finally:
            self.loading_dialog = None

    def _process_command_main_thread(self, cmd):
        """Handle command on Main Thread (UI Safe)"""
        try:
            if cmd['command'] == "message":
                self.alert = BroadcastWindow(cmd['message'])
                self.alert.show()
                logging.info("Admin message displayed")
                
            elif cmd['command'] in ("shutdown", "restart"):
                self._handle_system_command(cmd)
        except Exception as e:
            logging.exception(f"Error processing command: {e}")

    # check_remote_commands REMOVED (Replaced by Worker)
    
    def _handle_system_command(self, cmd: dict):
        """
        Handle shutdown/restart commands with user confirmation.
        
        Args:
            cmd: Command dictionary with 'command', 'admin_user', 'reason', etc.
        """
        action = "shutdown" if cmd['command'] == "shutdown" else "restart"
        admin = cmd.get('admin_user', 'Administrator')
        reason = cmd.get('reason', 'No reason provided')
        
        # Show confirmation dialog
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle(f"Admin Remote {action.title()}")
        msg.setText(f"⚠️ {admin} is requesting to {action} this PC in 60 seconds.")
        msg.setInformativeText(
            f"Reason: {reason}\n\n"
            f"Click OK to proceed or Cancel to abort.\n\n"
            f"Save your work before clicking OK!"
        )
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        msg.setDefaultButton(QMessageBox.Cancel)
        
        # Show dialog and wait for user response
        reply = msg.exec()
        
        if reply == QMessageBox.Ok:
            # User accepted - proceed with system command
            logging.critical(f"Remote {action} accepted by user. Admin: {admin}, Reason: {reason}")
            
            shutdown_flag = "/s" if cmd['command'] == "shutdown" else "/r"
            
            try:
                # Use subprocess.run instead of os.system for safety
                subprocess.run(
                    ["shutdown", shutdown_flag, "/t", "60", "/c", reason[:100]],
                    capture_output=True,
                    text=True,
                    check=True
                )
                logging.info(f"{action.title()} command executed successfully")
                
                # Cleanup and exit
                safe_single_shot(1000, self.app, self.cleanup_and_exit)
                
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to execute {action} command: {e}")
                QMessageBox.critical(
                    None,
                    "Error",
                    f"Failed to {action} system: {e}\n\nPlease contact IT support."
                )
        else:
            # User cancelled
            logging.info(f"User cancelled remote {action} request from {admin}")
            QMessageBox.information(
                None,
                "Cancelled",
                f"{action.title()} request cancelled.\n\nYour PC will not {action}."
            )

    def show_software_login(self):
        # Ensure the app doesn't quit when we switch windows
        self.app.setQuitOnLastWindowClosed(False)
        
        self.login_dialog = LoginDialog(app_context=self.app_context) # Keep reference
        result = self.login_dialog.exec()
        
        # Blocking call finished here
        if result == QDialog.DialogCode.Accepted:
            user_data = self.login_dialog.user_data

            if not user_data:
                logging.error("Login accepted but no user_data returned. Cannot launch.")
                self.cleanup_and_exit()
                return

            if self.reporter:
                self.reporter.update_user(user_data.get('display_name', 'Unknown'))
            logging.info("Login accepted. Starting main-window handoff.")

            # Show deterministic loading state while heavy UI initializes.
            self._show_startup_loading()

            # Launch immediately to avoid a no-window gap that can trigger app quit.
            self.launch_main_tool(user_data)

            # Drop dialog reference after handoff.
            if self.login_dialog:
                self.login_dialog.deleteLater()
                self.login_dialog = None
        else: 
            # Only exit if truly rejected (User clicked Close/Cancel)
            self.cleanup_and_exit()

    def launch_main_tool(self, user_data):
        logging.info(f"Input: Launching Main Window for mode='{self.app_mode}'...")
        try:
            if self.app_mode == "vfx":
                from slate.gui.vfx_studio_window import VFXStudioWindow
                logging.info("Initializing VFXStudioWindow...")
                self.main_window = VFXStudioWindow(user_data, app_context=self.app_context)
            elif self.app_mode == "ops":
                from slate.gui.studio_ops_window import StudioOpsWindow
                logging.info("Initializing StudioOpsWindow...")
                self.main_window = StudioOpsWindow(user_data, app_context=self.app_context)
            else:
                from slate.gui.main_window import VFXFolderCreatorApp
                logging.info("Initializing VFXFolderCreatorApp (all)...")
                self.main_window = VFXFolderCreatorApp(user_data, app_context=self.app_context, app_mode="all")
            
            logging.info("Showing Main Window...")
            self.main_window.showMaximized()
            # Main window is now the primary lifecycle owner.
            self.app.setQuitOnLastWindowClosed(True)
            logging.info("Main Window Launched Successfully.")
            
        except Exception as e:
            msg = f"CRITICAL: Error launching Main Window:\n{e}"
            logging.critical(msg)
            import traceback
            traceback.print_exc()
            
            # Ensure we see the error
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setText("Startup Error")
            box.setInformativeText(str(e))
            box.setDetailedText(traceback.format_exc())
            box.exec()
            
            self.cleanup_and_exit()
        finally:
            self._hide_startup_loading()

    def _cleanup_background_services(self):
        """Stop background threads and optionally force-shutdown DB pool."""

        # Always stop background threads to prevent
        # 'QThread: Destroyed while thread is still running' crash.

        # Stop command worker thread
        try:
            if hasattr(self, "cmd_worker") and self.cmd_worker:
                self.cmd_worker.stop()
        except Exception as e:
            logging.exception(f"Error stopping command worker: {e}")

        # Stop live reporter thread
        try:
            if hasattr(self, "reporter") and self.reporter:
                self.reporter.stop()
        except Exception as e:
            logging.exception(f"Error stopping live reporter: {e}")
            
        # Stop backup thread
        try:
            if hasattr(self, "backup_thread") and self.backup_thread:
                self.backup_thread.stop()
        except Exception as e:
            logging.exception(f"Error stopping backup thread: {e}")

        # Always force-shutdown DB and terminate subprocesses
        try:
            database_manager.force_shutdown()
        except Exception as e:
            logging.debug(f"Error forcing DB shutdown: {e}")
        try:
            from slate.utils.process_manager import subprocess_tracker
            subprocess_tracker.terminate_all(timeout=1.0)
        except Exception as e:
            logging.debug(f"Error terminating subprocesses: {e}")
        try:
            from slate.core.infra.telemetry import telemetry
            telemetry.shutdown()
        except Exception as e:
            logging.debug(f"Telemetry shutdown skipped in gatekeeper: {e}")
        try:
            from slate.utils.error_handler import error_handler
            error_handler.cleanup()
        except Exception as e:
            logging.debug(f"Error handler cleanup skipped in gatekeeper: {e}")

    def cleanup_and_exit(self):
        self._is_closing = True
        self._hide_startup_loading()
        self._cleanup_background_services()
        app = QApplication.instance()
        if app:
            app.quit()
        import os
        os._exit(0)

    def run(self):
        if self._startup_cancelled:
            import os
            os._exit(0)

        # Async I/O Integration
        loop = qasync.QEventLoop(self.app)
        asyncio.set_event_loop(loop)
        
        with loop:
            loop.run_forever()

        self._cleanup_background_services()
        import os
        os._exit(0)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UT Suite Gatekeeper")
    parser.add_argument("--mode", choices=["all", "vfx", "ops"], default="all", help="Suite mode to launch (vfx, ops, all)")
    cli_args, _ = parser.parse_known_args()

    from slate.utils.single_instance import SingleInstance
    lock_name = f"Slate_Process_{cli_args.mode}" if cli_args.mode != "all" else "Slate_Process"
    if not SingleInstance(lock_name).check():
        sys.exit(0)
        
    entry = ApplicationEntry(app_mode=cli_args.mode)
    entry.run()
