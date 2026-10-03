import sys
import ctypes
import time
import subprocess
from pathlib import Path
import asyncio
import logging
from slate.core.infra.gate import Gate

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
    QMessageBox.critical(
        None, "Starting Slate",
        "Slate could not start: a part it needs (qasync) is missing from this "
        "installation.\n\nReinstall Slate, or ask IT to.")
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
# The first-run window lives in its own module; these names stay importable here.
from slate.gui.dialogs.first_run_dialog import (  # noqa: F401
    ConnectionTestWorker, FirstRunSetupDialog, test_database_connection,
)
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
    """
    A message from an administrator. It stays until it is read and closed:
    it used to vanish after ten seconds, with every label boxed in red, a
    '[WARN] ADMIN MESSAGE' title and 'Running command...' under a plain note.
    """

    def __init__(self, message, parent=None, sender=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        layout = QVBoxLayout(self)

        frame = QFrame()
        frame.setObjectName("BroadcastFrame")
        # Scoped to the frame: a bare QFrame rule also boxed every QLabel in it.
        frame.setStyleSheet(f"""
            QFrame#BroadcastFrame {{
                background-color: {Gate.RAISED};
                border: 1px solid {Gate.LINE};
                border-radius: 10px;
            }}
            QLabel {{ background: transparent; border: none; }}
        """)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(20, 16, 20, 16)
        frame_layout.setSpacing(10)

        title = QLabel(f"Message from {sender}" if sender else "Message from your administrator")
        title.setStyleSheet(f"color: {Gate.TEXT}; font-weight: bold; font-size: 15px;")
        frame_layout.addWidget(title)

        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"color: {Gate.TEXT_2}; font-size: 14px;")
        msg_label.setWordWrap(True)
        msg_label.setTextFormat(Qt.PlainText)  # SECURITY: Prevent HTML Injection
        frame_layout.addWidget(msg_label)

        from slate.gui.core.controls import make_button
        row = QHBoxLayout()
        row.addStretch(1)
        self.ok_button = make_button("OK", "primary", on_click=self.close)
        row.addWidget(self.ok_button)
        frame_layout.addLayout(row)

        layout.addWidget(frame)
        self.resize(420, 200)

        # Center on screen
        if QApplication.primaryScreen():
            self.move(QApplication.primaryScreen().availableGeometry().center() - self.rect().center())


class StartupLoadingDialog(QDialog):
    """Lightweight loading dialog shown while MainWindow initializes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Launching Slate")
        self.setModal(False)
        # Not always on top: it covered every other window for the whole
        # start-up.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)

        frame = QFrame()
        frame.setStyleSheet(
            f"""
            QFrame {{
                background-color: {Gate.RAISED};
                border: 1px solid {Gate.LINE};
                border-radius: 10px;
            }}
            QLabel {{
                color: {Gate.TEXT};
                font-size: 13px;
                background: transparent;
                border: none;
            }}
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("Starting Slate\u2026")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        progress = QProgressBar()
        progress.setRange(0, 0)  # Indeterminate
        progress.setTextVisible(False)
        progress.setFixedHeight(10)
        progress.setStyleSheet(
            f"""
            QProgressBar {{
                background: {Gate.ACCENT_SURFACE};
                border: 1px solid {Gate.ACCENT_SURFACE};
                border-radius: 5px;
            }}
            QProgressBar::chunk {{
                background: {Gate.ACCENT};
                border-radius: 5px;
            }}
            """
        )
        layout.addWidget(progress)

        hint = QLabel("Opening your workspace")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {Gate.INFO}; font-size: 11px;")
        layout.addWidget(hint)

        root.addWidget(frame)
        self.resize(360, 130)

        if QApplication.primaryScreen():
            self.move(QApplication.primaryScreen().availableGeometry().center() - self.rect().center())


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

        # Licence section 5: the licence files and credits must be intact
        from slate import licence
        if not licence.check_startup():
            sys.exit(3)

        self.app.setQuitOnLastWindowClosed(False)
        # Signing out and in again goes through launch_main_tool too
        # (MainWindow._open_window_for), so it can find this entry.
        self.app._slate_entry = self
        # Starting Slate again brings this one forward instead of a box that
        # named an internal lock.
        from slate.utils.single_instance import listen, lock_name_for
        self._instance_server = listen(lock_name_for(self.app_mode), self._bring_to_front)
        self._cleanup_done = False
        self._is_closing = False
        self.loading_dialog = None
        self._startup_cancelled = False
        self.app.aboutToQuit.connect(self._cleanup_background_services)
        self._db_runtime_status = {}
        
        # The shared database manager (built when first used) - not a second
        # one of its own, which connected (and waited) before any window.
        self.hub = ServerHub(); self.attendance = CentralAttendance(db=database_manager)
        self.app_context = AppContext(
            db_manager=database_manager,
            server_hub=self.hub,
            attendance=self.attendance,
        )

        if not self._ensure_first_run_setup():
            self._startup_cancelled = True
            # Cancelled setup is not a crash; a setup that failed is.
            self.cleanup_and_exit(getattr(self, "_setup_failed", False) and 1 or 0)
            return

        # Use the settings as they are now (first-run may have just written
        # them). Nothing connects here: the sign-in window opens at once and
        # connects behind it. Connecting here first (with retries) showed
        # nothing at all for about 30 s whenever the database was down.
        try:
            database_manager.reload_from_config()
        except Exception as exc:
            logging.warning("Database manager reload after setup failed: %s", exc)

        self.startup_mgr = StartupManager()
        self._maybe_cleanup_startup_entry()

        self.reporter = None
        self.backup_thread = None
        self.processed_cmds = []
        self.cmd_worker = None

        self.icon_path = ResourcePathManager.get_icons_dir() / "app_icon_128.ico"
        if self.icon_path.exists(): self.app.setWindowIcon(QIcon(str(self.icon_path)))

        self.show_software_login()

    def _start_background_services(self):
        """Once the database is known: live status, backups and admin commands."""
        if self._db_runtime_status:
            return                                  # already started
        self._db_runtime_status = self._get_db_runtime_status() or {"started": True}
        if not self._is_sqlite_fallback_mode():
            self.reporter = LiveReporter(user_name="Locked")
            self.reporter.start()
            
            # Start automatic background backups
            self.backup_thread = AutoBackupThread(interval_hours=12)
            self.backup_thread.start()
            logging.info("Auto backup thread started.")
        else:
            logging.warning("Live reporter and auto backup disabled in SQLite fallback mode.")

        # Remote command polling is central-sync only. Disable in SQLite fallback mode.
        if not self._is_sqlite_fallback_mode():
            self.cmd_worker = CommandCheckWorker(self.hub, self.processed_cmds)
            self.cmd_worker.command_received.connect(self._process_command_main_thread)
            self.cmd_worker.start()
        else:
            logging.warning("Command polling disabled in SQLite fallback mode.")

    def _bring_to_front(self):
        """Another start of this application asked for us: show the open window."""
        window = getattr(self, "main_window", None) or getattr(self, "login_dialog", None)
        if window is None:
            return
        try:
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
        except RuntimeError:
            pass

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
            self._setup_failed = True
            from slate.gui.components.feedback import show_error
            show_error(None, "Slate could not be set up on this computer.", exc=exc,
                       title="Set up Slate",
                       hint=("Check that the shared folder can be written to from here, "
                             "then start Slate again. If it keeps happening, copy the "
                             "details and send them to IT."))
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
                self.alert = BroadcastWindow(cmd['message'], sender=cmd.get('admin_user', ''))
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
        verb = "shut down" if action == "shutdown" else "restart"
        button_text = "Shut down" if action == "shutdown" else "Restart"

        # Show confirmation dialog
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle(f"{button_text} this PC")
        msg.setText(f"{admin} wants to {verb} this PC.")
        msg.setInformativeText(
            f"Reason: {reason}\n\n"
            f"Save your work, then choose {button_text} - Windows will {verb} "
            f"60 seconds later. Choose Cancel to keep working."
        )
        go = msg.addButton(button_text, QMessageBox.ButtonRole.AcceptRole)
        cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(cancel)
        msg.setEscapeButton(cancel)

        # Show dialog and wait for user response
        msg.exec()

        if msg.clickedButton() is go:
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
                    f"{button_text} this PC",
                    f"Windows did not {verb}: {e}\n\nAsk IT for help."
                )
        else:
            # User cancelled
            logging.info(f"User cancelled remote {action} request from {admin}")
            QMessageBox.information(
                None,
                f"{button_text} this PC",
                f"Cancelled. This PC will not {verb}."
            )

    def show_software_login(self):
        # Ensure the app doesn't quit when we switch windows
        self.app.setQuitOnLastWindowClosed(False)
        
        self.login_dialog = LoginDialog(app_context=self.app_context, app_mode=self.app_mode)  # Keep reference
        self.login_dialog.database_ready.connect(self._start_background_services)
        result = self.login_dialog.exec()
        
        # Blocking call finished here
        if result == QDialog.DialogCode.Accepted:
            user_data = self.login_dialog.user_data

            if not user_data:
                logging.error("Login accepted but no user_data returned. Cannot launch.")
                self.cleanup_and_exit(1)
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
        started = time.perf_counter()
        try:
            # Import first, then let the loading window paint, then build:
            # the screens themselves load when they are first opened.
            import slate.gui.main_window  # noqa: F401
            QApplication.processEvents()
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
            
            from slate import licence
            if not licence.check_window(self.main_window):
                import os
                os._exit(3)             # licence section 5: the window lacks the credit line
            logging.info("Showing Main Window...")
            # As it was left (maximised the first time) - see show_restored.
            if hasattr(self.main_window, "show_restored"):
                self.main_window.show_restored()
            else:
                self.main_window.showMaximized()
            # Main window is now the primary lifecycle owner.
            self.app.setQuitOnLastWindowClosed(True)
            logging.info("Main Window Launched Successfully in %.2f s.", time.perf_counter() - started)
            
        except Exception as e:
            msg = f"CRITICAL: Error launching Main Window:\n{e}"
            logging.critical(msg)
            import traceback
            traceback.print_exc()
            
            # Ensure we see the error - in words, with the traceback only
            # behind "Show Details..." and "Copy details for IT". The box used
            # to say "Startup Error" and then the raw exception.
            from slate.gui.components.feedback import show_error
            show_error(
                None,
                "Slate could not open its main window.",
                exc=e,
                title="Starting Slate",
                hint=("Close Slate and start it again. If it keeps happening, "
                      "copy the details and send them to IT."),
            )

            # A non-zero code, so the launcher shows its crash message.
            self.cleanup_and_exit(1)
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

    def cleanup_and_exit(self, code: int = 0):
        """Stop everything and leave with `code` (non-zero for a failed start)."""
        self._is_closing = True
        self._hide_startup_loading()
        self._cleanup_background_services()
        app = QApplication.instance()
        if app:
            app.quit()
        import os
        os._exit(int(code))

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
        os._exit(int(getattr(self, "exit_code", 0) or 0))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UT Suite Gatekeeper")
    parser.add_argument("--mode", choices=["all", "vfx", "ops"], default="all", help="Suite mode to launch (vfx, ops, all)")
    cli_args, _ = parser.parse_known_args()

    from slate.utils.single_instance import SingleInstance, lock_name_for
    lock_name = lock_name_for(cli_args.mode)
    if not SingleInstance(lock_name).check():
        sys.exit(0)
        
    entry = ApplicationEntry(app_mode=cli_args.mode)
    entry.run()
