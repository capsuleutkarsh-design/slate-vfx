from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QPushButton, QLabel, QMessageBox, QApplication
from PySide6.QtCore import Qt, QThread, Signal, QTimer
import os
import psycopg2
import subprocess
import webbrowser
import psutil

from .design_system import C, GLOBAL_STYLESHEET, T
from .views.dashboard_view import DashboardView
from .views.settings_view import SettingsView
from .views.analytics_view import AnalyticsView
from slate_server.core.db_engine import DatabaseEngine
from slate_server.core.pgbouncer_engine import PgBouncerEngine
from slate_server.core.db_credentials import connect_kwargs


def _client_setting(key, default=""):
    """
    Read one value from the settings the Slate clients ship with.

    PgBouncer has to reach the database itself, so it needs the same account
    the clients use. Reading it here rather than keeping a second copy means
    the two can never drift apart.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "client_config.json",
                      root / "slate" / "default_config.json"):
        try:
            if candidate.exists():
                value = json.loads(candidate.read_text(encoding="utf-8")).get(key)
                if value:
                    return value
        except Exception:
            continue
    return default
from slate_server.core.network_broadcaster import NetworkBroadcaster

class DBWorker(QThread):
    progress = Signal(str)
    finished = Signal(bool, str) # success, error_msg
    
    def __init__(self, engine, action="start"):
        super().__init__()
        self.engine = engine
        self.action = action
        
    def run(self):
        try:
            if self.action == "start":
                self.engine.start(progress_callback=self.progress.emit)
                # The pool goes up after the database it points at. If it is
                # not installed this reports so and changes nothing.
                pooler = getattr(self.engine, "pooler", None)
                if pooler is not None:
                    pooler.start(progress_callback=self.progress.emit,
                                 psql_exe=self.engine.bin_dir / "psql.exe")
            else:
                pooler = getattr(self.engine, "pooler", None)
                if pooler is not None:
                    pooler.stop(progress_callback=self.progress.emit)
                self.engine.stop(progress_callback=self.progress.emit)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))

class UTServerWindow(QMainWindow):
    """
    Main Application Window for Slate Central Server.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Slate Server")
        self.setMinimumSize(900, 650)
        self.setStyleSheet(GLOBAL_STYLESHEET)
        
        # Main Widget
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        
        # Layout
        self.main_layout = QHBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        

        
        # Stacked Widget
        self.stacked_widget = QStackedWidget()
        self.main_layout.addWidget(self.stacked_widget, 1)
        
        # Initialize Database Engine
        # Store database using config file in a persistent location
        import json
        appdata_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), "Slate_Central")
        os.makedirs(appdata_dir, exist_ok=True)
        self.config_path = os.path.join(appdata_dir, "slate_server_config.json")
        default_path = r"X:\Extra\Slate_Central\Database"
        default_port = 5440
        
        db_path = default_path
        port = default_port
        # Where clients connect once PgBouncer is in front of the database.
        pooler_port = 6432
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    cfg = json.load(f)
                    db_path = cfg.get("db_path", db_path)
                    port = cfg.get("port", port)
                    pooler_port = cfg.get("pooler_port", pooler_port)
            except Exception:
                pass
                
        # Fallback for local testing if drive doesn't exist
        drive_letter = os.path.splitdrive(db_path)[0]
        if drive_letter and not os.path.exists(drive_letter + "\\"):
            db_path = os.path.join(appdata_dir, "LocalDatabase")
            print(f"Warning: Drive {drive_letter} not found. Falling back to {db_path}")
            
        try:
            self.db_engine = DatabaseEngine(db_path, port=int(port))

            # Connection pooling. Attached to the database engine so whatever
            # starts or stops the database also starts or stops the pool.
            self.db_engine.pooler = PgBouncerEngine(
                db_path,
                db_port=int(port),
                listen_port=int(pooler_port),
                db_user=_client_setting("db_user", "ut_vfx_app"),
                db_password=_client_setting("db_password", ""),
            )
            engine_ready = True
        except Exception as e:
            self.db_engine = None
            engine_ready = False
            self.startup_error = str(e)
        
        # Views
        self.dashboard = DashboardView()
        self.settings_view = SettingsView()
        self.analytics_view = AnalyticsView()
        
        self.settings_view.input_db_path.setText(db_path)
        self.settings_view.input_port.setText(str(port))
        self.dashboard.card_port.set_value(str(port))
        
        self.stacked_widget.addWidget(self.dashboard)
        self.stacked_widget.addWidget(self.settings_view)
        self.stacked_widget.addWidget(self.analytics_view)
        
        if not engine_ready:
            self._log(f"CRITICAL ERROR: Failed to initialize database paths: {self.startup_error}")
            self.dashboard.toggle_power.setEnabled(False)
        
        import socket
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = "127.0.0.1"
        self.dashboard.card_ip.set_value(local_ip)
        
        # Connect Signals
        self.dashboard.toggle_power.stateChanged.connect(self._on_power_toggled)
        self.dashboard.btn_force_kill.clicked.connect(self._on_force_kill)
        self.settings_view.btn_save.clicked.connect(self._on_save_settings)
        self.settings_view.btn_firewall.clicked.connect(self._on_allow_firewall)
        self.dashboard.btn_api_dashboard.clicked.connect(self._on_open_dashboard)
        self.settings_view.btn_check_update.clicked.connect(self._on_check_update)
        
        self.worker = None
        self.broadcaster = None
        self.fastapi_process = None
        
        self.update_checker = None
        self.sidecar_engine = None
        
        self.analytics_view.card_projects.clicked.connect(self._on_stat_card_clicked)
        self.analytics_view.card_assets.clicked.connect(self._on_stat_card_clicked)
        self.analytics_view.card_connections.clicked.connect(self._on_stat_card_clicked)
        
        # Setup Analytics Polling
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_database_stats)
        
        # Setup Sidebar after views are ready
        self.setup_sidebar()
        
    def setup_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(f"background-color: {C.BG_SURFACE}; border-right: 1px solid {C.BORDER_DEFAULT};")
        
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 40, 0, 40)
        sidebar_layout.setSpacing(10)
        
        lbl_logo = QLabel("Slate")
        lbl_logo.setStyleSheet(f"font-size: 20px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY}; padding-left: 24px; border: none;")
        sidebar_layout.addWidget(lbl_logo)
        sidebar_layout.addSpacing(30)
        
        # Nav Buttons
        self.btn_nav_dash = QPushButton("Dashboard")
        self.btn_nav_analytics = QPushButton("Analytics")
        self.btn_nav_settings = QPushButton("Settings")
        
        for btn in (self.btn_nav_dash, self.btn_nav_analytics, self.btn_nav_settings):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(40)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    padding-left: 24px;
                    background-color: transparent;
                    color: {C.TEXT_SECONDARY};
                    border: none;
                    font-size: 14px;
                    font-weight: {T.WEIGHT_SEMI};
                }}
                QPushButton:hover {{
                    color: {C.TEXT_PRIMARY};
                    background-color: {C.BG_SURFACE_HOVER};
                }}
            """)
            sidebar_layout.addWidget(btn)
            
        sidebar_layout.addStretch()
        self.main_layout.insertWidget(0, sidebar)
        
        self.btn_nav_dash.clicked.connect(lambda: self.switch_view(0))
        self.btn_nav_settings.clicked.connect(lambda: self.switch_view(1))
        self.btn_nav_analytics.clicked.connect(lambda: self.switch_view(2))
        
        # Initial State
        self.switch_view(0)

    def switch_view(self, index):
        self.stacked_widget.setCurrentIndex(index)
        
        # Update Nav Styles
        active_style = f"text-align: left; padding-left: 20px; background-color: {C.BG_ROOT}; color: {C.ACCENT_PRIMARY}; border-left: 4px solid {C.ACCENT_PRIMARY}; font-size: 14px; font-weight: {T.WEIGHT_BOLD};"
        inactive_style = f"text-align: left; padding-left: 24px; background-color: transparent; color: {C.TEXT_SECONDARY}; border: none; font-size: 14px; font-weight: {T.WEIGHT_SEMI};"
        
        self.btn_nav_dash.setStyleSheet(active_style if index == 0 else inactive_style)
        self.btn_nav_settings.setStyleSheet(active_style if index == 1 else inactive_style)
        self.btn_nav_analytics.setStyleSheet(active_style if index == 2 else inactive_style)
        
    def _on_save_settings(self):
        new_path = self.settings_view.input_db_path.text()
        new_port = self.settings_view.input_port.text()
        try:
            import json
            with open(self.config_path, "w") as f:
                json.dump({"db_path": new_path, "port": int(new_port)}, f, indent=4)
            
            try:
                self.db_engine = DatabaseEngine(new_path, port=int(new_port))
                self.dashboard.status_badge.set_status("Settings Saved", "ok")
                self.switch_view(0)
                self._log("> Configuration saved. If server is running, restart to apply.")
            except Exception as e:
                self._log(f"CRITICAL ERROR initializing engine: {e}")
        except Exception as e:
            self._log(f"Failed to save settings: {e}")
        
    def _on_power_toggled(self, state):
        self.dashboard.toggle_power.setEnabled(False)
        
        if state:
            self.dashboard.status_badge.set_status("Starting...", "warning")
            self._log("> Initializing Database Engine...")
            self.worker = DBWorker(self.db_engine, "start")
        else:
            self.dashboard.status_badge.set_status("Stopping...", "warning")
            self._log("> Stopping Database Engine...")
            self.worker = DBWorker(self.db_engine, "stop")
            
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._handle_worker_finished)
        self.worker.start()
        
    def _on_worker_progress(self, msg):
        self._log(f"  {msg}")
        
    def _handle_worker_finished(self, success, error_msg):
        state = self.worker.action == "start"
        self._on_worker_finished(success, error_msg, state)
        
    def _on_worker_finished(self, success, error_msg, state):
        self.dashboard.toggle_power.setEnabled(True)
        if success:
            if state:
                self.dashboard.status_badge.set_status("Server Online", "ok")
                self._log(f"> Server successfully started on port {self.settings_view.input_port.text()}.")
                
                # Start UDP Broadcaster
                self.broadcaster = NetworkBroadcaster(db_port=int(self.settings_view.input_port.text()))
                self.broadcaster.start()
                self._log("> Network Discovery Broadcaster is ONLINE (Port 54400).")
                
                # Start FastAPI Server
                self._log("> Starting FastAPI Server on port 8000...")
                try:
                    python_exe = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "python_portable", "Scripts", "python.exe")
                    if not os.path.exists(python_exe):
                        python_exe = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "python_portable", "python.exe")
                    if not os.path.exists(python_exe):
                        python_exe = "python"
                    api_cmd = [python_exe, "-m", "uvicorn", "slate.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
                    # Provide cwd so slate is accessible
                    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    env = os.environ.copy()
                    env["PYTHONPATH"] = cwd + os.pathsep + env.get("PYTHONPATH", "")
                    self.fastapi_process = subprocess.Popen(
                        api_cmd, cwd=cwd, env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, creationflags=subprocess.CREATE_NO_WINDOW)

                    # Wait for it to actually answer before saying it is up.
                    # This used to report success the instant the process was
                    # launched, so a web interface that died on a missing
                    # dependency still read as "running" in the log.
                    import socket
                    import time as _time

                    started = False
                    for _ in range(20):
                        if self.fastapi_process.poll() is not None:
                            break
                        try:
                            with socket.create_connection(("127.0.0.1", 8000), timeout=0.5):
                                started = True
                                break
                        except OSError:
                            _time.sleep(0.5)

                    if started:
                        self._log("> FastAPI Server is running.")
                        self.dashboard.btn_api_dashboard.setEnabled(True)
                    else:
                        detail = ""
                        try:
                            if self.fastapi_process.poll() is not None:
                                detail = (self.fastapi_process.stdout.read() or "").strip()
                                detail = detail.splitlines()[-1] if detail else ""
                        except Exception:
                            detail = ""
                        self._log("> FastAPI Server did NOT start."
                                  + (f" {detail}" if detail else ""))
                        self.dashboard.btn_api_dashboard.setEnabled(False)
                except Exception as e:
                    self._log(f"> Failed to start FastAPI: {e}")
                
                self.poll_timer.start(3000)
            else:
                self.dashboard.status_badge.set_status("Server Offline", "error")
                self._log("> Server stopped gracefully.")
                
                if self.broadcaster:
                    self.broadcaster.stop()
                    self.broadcaster = None
                    self._log("> Network Discovery Broadcaster stopped.")
                    
                if self.fastapi_process:
                    self.fastapi_process.terminate()
                    self.fastapi_process = None
                    self._log("> FastAPI Server stopped.")
                self.dashboard.btn_api_dashboard.setEnabled(False)
                    
                self.poll_timer.stop()
        else:
            self.dashboard.status_badge.set_status("Error", "error")
            self._log(f"> ERROR: {error_msg}")
            # Revert toggle visually without emitting signal
            self.dashboard.toggle_power.blockSignals(True)
            self.dashboard.toggle_power.setChecked(not state)
            self.dashboard.toggle_power.blockSignals(False)

    def _on_open_dashboard(self):
        webbrowser.open("http://localhost:8000/admin")

    def _on_allow_firewall(self):
        self._log("> Prompting for Administrator privileges to open Firewall...")
        import subprocess
        try:
            port = self.settings_view.input_port.text()
            cmd = f"Start-Process cmd -ArgumentList '/c netsh advfirewall firewall add rule name=\"Slate Central Server (Database)\" dir=in action=allow protocol=TCP localport={port} & netsh advfirewall firewall add rule name=\"Slate Central Server (Discovery)\" dir=in action=allow protocol=UDP localport=54320' -Verb RunAs -WindowStyle Hidden"
            subprocess.run(["powershell", "-Command", cmd], creationflags=subprocess.CREATE_NO_WINDOW)
            self._log("> Firewall exception requested. If accepted, connections are allowed.")
            self.dashboard.status_badge.set_status("Firewall Allowed", "ok")
        except Exception as e:
            self._log(f"Failed to open firewall: {e}")

    def _on_force_kill(self):
        self._log("Attempting to force kill PostgreSQL processes...")
        import subprocess
        try:
            subprocess.run(["taskkill", "/F", "/IM", "postgres.exe"], capture_output=True)
            self._log("Force kill command executed.")
            # Set toggle switch to off visually
            self.dashboard.toggle_power.blockSignals(True)
            self.dashboard.toggle_power.setChecked(False)
            self.dashboard.toggle_power.blockSignals(False)
            self.dashboard.status_badge.set_status("Server Offline", "error")
        except Exception as e:
            self._log(f"Force kill failed: {e}")

    def _poll_database_stats(self):
        """Poll the database for live stats and update Analytics View."""
        # Same beat as the dashboard refresh: if the pooler has died, bring it
        # back before 150 machines start connecting to the database directly.
        try:
            pooler = getattr(self.db_engine, "pooler", None)
            if pooler is not None:
                pooler.ensure_running(psql_exe=self.db_engine.bin_dir / "psql.exe")
        except Exception as exc:
            logging.debug("Pooler health check skipped: %s", exc)

        try:
            # Connect directly to our embedded local DB
            # The database now asks for a password, so these queries have to
            # carry one. Without it this panel silently showed nothing.
            conn = psycopg2.connect(
                **connect_kwargs(self.settings_view.input_port.text())
            )
            
            with conn.cursor() as cur:
                # 1. Active Connections
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active' OR state = 'idle'")
                res1 = cur.fetchone()
                active_conns = res1[0] if res1 else 0
                
                # 2. Max Connections
                cur.execute("SHOW max_connections")
                res2 = cur.fetchone()
                max_conns = max(1, int(res2[0]) if res2 else 100)
                
                load_pct = min(100, int((active_conns / max_conns) * 100))
                
                # 3. Connected IPs List
                cur.execute("SELECT client_addr, application_name, state, query FROM pg_stat_activity WHERE client_addr IS NOT NULL ORDER BY state ASC LIMIT 50")
                clients = cur.fetchall()
                
                # 4. Total Projects
                cur.execute("SELECT count(*) FROM tracking_projects")
                res4 = cur.fetchone()
                total_projects = res4[0] if res4 else 0
                
                # 5. Total Assets
                cur.execute("SELECT count(*) FROM stock_library")
                res5 = cur.fetchone()
                total_assets = res5[0] if res5 else 0
                
                # 6. Database Size
                cur.execute("SELECT pg_size_pretty(pg_database_size('slate'))")
                res6 = cur.fetchone()
                db_size = res6[0] if res6 else "Unknown"
                
            conn.close()
            
            # 7. System Stats
            cpu_usage = psutil.cpu_percent(interval=None)
            ram_usage = psutil.virtual_memory().percent
            
            # Update UI Cards
            self.analytics_view.card_connections.set_value(f"{active_conns}")
            self.analytics_view.card_load.set_value(f"{load_pct}%")
            self.analytics_view.card_projects.set_value(f"{total_projects}")
            self.analytics_view.card_assets.set_value(f"{total_assets}")
            self.analytics_view.card_db_size.set_value(f"{db_size}")
            self.analytics_view.card_cpu.set_value(f"{cpu_usage}%")
            self.analytics_view.card_ram.set_value(f"{ram_usage}%")
            
            # Update Data Grid
            self.analytics_view.update_table(clients)
            
        except Exception as e:
            import logging
            logging.debug(f"Poll database stats error: {e}")

    def _on_stat_card_clicked(self, title):
        if not self.dashboard.toggle_power.isChecked():
            return
            
        port = int(self.settings_view.input_port.text())
        from .views.analytics_view import DataViewerDialog
        
        query = ""
        if "Projects" in title:
            query = "SELECT id, name, status, created_at FROM tracking_projects ORDER BY created_at DESC LIMIT 100"
        elif "Assets" in title:
            query = "SELECT id, type, name, tags FROM stock_library LIMIT 100"
        elif "Active" in title or "Connections" in title:
            query = "SELECT client_addr, application_name, state, query_start FROM pg_stat_activity WHERE client_addr IS NOT NULL"
            
        if query:
            dlg = DataViewerDialog(f"{title} Data", query, port, self)
            dlg.exec()

    def _log(self, message):
        print(f"[Slate Server] {message}")
        current = self.dashboard.lbl_logs.text()
        self.dashboard.lbl_logs.setText(f"{current}\n{message}")
        
    def closeEvent(self, event):
        """Ensure database is gracefully stopped when the application closes."""
        try:
            self._log("> Shutting down database engine...")
            self.db_engine.stop()
        except Exception as e:
            logging.debug(f"Database stop on close error: {e}")
                
        if self.broadcaster:
            try:
                self.broadcaster.stop()
            except Exception:
                pass
            
        if self.fastapi_process:
            try:
                self.fastapi_process.terminate()
                self.fastapi_process.wait(timeout=1.0)
            except Exception:
                try:
                    self.fastapi_process.kill()
                except Exception:
                    pass
            self.fastapi_process = None
            
        event.accept()

    # --- UPDATE FLOW ---
    def _on_check_update(self):
        if self.sidecar_engine and hasattr(self.sidecar_engine, 'temp_updater'):
            self._apply_staged_update()
            return
            
        self.settings_view.btn_check_update.setText("Checking...")
        self.settings_view.btn_check_update.setEnabled(False)
        self.settings_view.lbl_update_status.setText("Looking for updates...")
        
        if self.update_checker:
            self.update_checker.stop()
            self.update_checker.deleteLater()
            
        from slate.core.updater.update_checker import UpdateChecker
        self.update_checker = UpdateChecker(self, manual_mode=True, target="server")
        self.update_checker.update_available.connect(self.on_update_found)
        self.update_checker.update_not_found.connect(self.on_no_update)
        
        def cleanup():
            self.settings_view.btn_check_update.setEnabled(True)
            if self.settings_view.btn_check_update.text() == "Checking...":
                self.settings_view.btn_check_update.setText("Check for Updates")
        self.update_checker.finished.connect(cleanup)
        self.update_checker.start()

    def on_update_found(self, manifest):
        self.settings_view.btn_check_update.setText("Check for Updates")
        self.settings_view.lbl_update_status.setText(f"Update Found: v{manifest.get('version')}")
        from slate.gui.dialogs.update_available_dialog import UpdateAvailableDialog
        dlg = UpdateAvailableDialog(manifest, self)
        if dlg.exec():
            self._stage_update(manifest)

    def _stage_update(self, manifest):
        from slate.core.updater.sidecar_engine import SidecarEngine
        
        self.settings_view.btn_check_update.setEnabled(False)
        self.settings_view.btn_check_update.setText("Downloading...")
        self.settings_view.lbl_update_status.setText("Staging update in background...")
        self.sidecar_engine = SidecarEngine(manifest)
        
        QApplication.processEvents()
        success = self.sidecar_engine.stage_update()
        self.settings_view.btn_check_update.setEnabled(True)
        
        if success:
            self.settings_view.btn_check_update.setText("Restart to Apply")
            self.settings_view.btn_check_update.setStyleSheet("background-color: #1B3A2C; color: white;")
            self.settings_view.lbl_update_status.setText("Update staged. Click to restart.")
            QMessageBox.information(self, "Update Ready", "Update downloaded and verified. Click 'Restart to Apply'.")
        else:
            self.settings_view.btn_check_update.setText("Check for Updates")
            self.settings_view.lbl_update_status.setText("Failed to stage the update.")
            QMessageBox.warning(self, "Update Failed", "Failed to stage the update. Check the logs.")

    def _apply_staged_update(self):
        if not self.sidecar_engine:
            return
            
        reply = QMessageBox.question(self, "Apply Update", "The server will now restart to apply the update. Continue?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            if self.dashboard.toggle_power.isChecked():
                self.db_engine.stop()
            self.sidecar_engine.apply_update()
            QApplication.quit()

    def on_no_update(self, current_ver):
        self.settings_view.btn_check_update.setText("Check for Updates")
        reason = ""
        if self.update_checker:
            reason = str(getattr(self.update_checker, "last_result_reason", "") or "")
            
        if reason in ["missing_latest_pointer", "manifest_missing"]:
            QMessageBox.information(self, "Update Feed Missing", "No update manifest was found for the server.")
        elif reason == "invalid_manifest":
            QMessageBox.information(self, "Update Feed Invalid", "The update manifest exists but is invalid.")
        else:
            QMessageBox.information(self, "Up to Date", f"Server is running the latest version: {current_ver}")
        self.settings_view.lbl_update_status.setText(f"Up to date: v{current_ver}")
