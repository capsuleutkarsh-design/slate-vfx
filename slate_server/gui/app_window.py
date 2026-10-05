from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QStackedWidget, QPushButton, QLabel, QMessageBox,
                               QApplication, QScrollArea, QFrame, QFileDialog)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
import logging
import os
import sys
import psycopg2
import subprocess
import webbrowser
import psutil

from .design_system import C, GLOBAL_STYLESHEET, T
from .views.dashboard_view import DashboardView
from .views.settings_view import SettingsView
from .views.analytics_view import AnalyticsView
from .views.operations_view import OperationsView
from .views.recovery_view import RecoveryView
from slate_server.core.db_engine import DatabaseEngine
from slate_server.core.pgbouncer_engine import PgBouncerEngine
from slate_server.core.db_credentials import connect_kwargs


def _scrollable(view):
    """
    Let a screen be taller than the window instead of being crushed into it.

    A QStackedWidget gives every page exactly the space it has, and Qt honours
    that by squeezing each widget below the height it asked for. The result is
    not a scrollbar - it is every field on the screen losing a few pixels off
    the top and bottom of its text, which reads as a broken theme rather than
    as a window that is too short. Settings asks for 758 pixels and a 762-pixel
    window has about 720 to give it.

    So each page goes in a scroll area. Nothing else about the page changes,
    and a window big enough to hold it looks exactly as it did.
    """
    area = QScrollArea()
    area.setWidget(view)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    area.viewport().setStyleSheet("background: transparent;")
    return area


def _client_setting(key, default=""):
    """
    Read one value from the settings the Slate clients ship with.

    PgBouncer has to reach the database itself, so it needs the same account
    the clients use. This was a second lookup of its own, over two paths that
    do not exist in an installed build - so the pool was handed an empty
    password on every frozen server, which is precisely the drift the comment
    here claimed to prevent. It asks the one module that knows where the
    settings are now, which is also the one a password typed into Settings
    reaches.
    """
    try:
        from slate_server.core.db_credentials import setting
        return setting(key, default)
    except Exception:
        return default
from slate_server.core.network_broadcaster import NetworkBroadcaster
def _server_home():
    """
    Where this server keeps its settings, its log and, by default, its data.

    Running from a checkout: the checkout. Installed: the per-user folder.
    They used to be the same folder for both, which is how uninstalling the
    installed build - with "delete its data" - deleted the settings file the
    development server was using. The answer lives in slate_server.core.
    server_home now, so the recovery tool reaches the same one without Qt.
    """
    from slate_server.core.server_home import server_home
    return server_home()


def _default_data_dir(appdata_dir):
    """
    Where the database is when the settings do not say: SLATE_DB_PATH, or
    <server home>\LocalDatabase. One answer, fixed, derived from nothing that
    changes - every time it used to change, a new empty cluster was built.
    """
    from slate_server.core.server_home import default_data_dir
    return default_data_dir(appdata_dir)


def _settings_path(appdata_dir):
    """
    The server's settings file, carrying forward an older install's so an
    upgrade is not mistaken for a first run (see server_home.settings_path).
    """
    from slate_server.core.server_home import settings_path
    return settings_path(appdata_dir)


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
            elif self.action == "pool":
                # Rewrite the pool's configuration and restart it (Restart pool).
                pooler = self.engine.pooler
                try:
                    pooler.stop(progress_callback=self.progress.emit)
                except Exception as exc:
                    logging.debug("Pool stop reported: %s", exc)
                # start() rewrites the configuration itself, and needs psql to
                # read the verifiers: without it, it wrote an empty user list
                # over the one just written and the pool never came back.
                psql = self.engine.bin_dir / "psql.exe"
                if not pooler.start(progress_callback=self.progress.emit,
                                    psql_exe=psql if psql.exists() else None):
                    raise RuntimeError("The pool did not come up.")
            else:
                pooler = getattr(self.engine, "pooler", None)
                if pooler is not None:
                    pooler.stop(progress_callback=self.progress.emit)
                self.engine.stop(progress_callback=self.progress.emit)
            self.finished.emit(True, "")
        except Exception as e:
            self.finished.emit(False, str(e))

# What a click on an Analytics card shows - on the columns these tables really
# have (they asked for id/status/created_at and type/name, which do not exist,
# so both cards always failed).
STAT_QUERIES = {
    "projects": "SELECT code, name, active, last_updated FROM tracking_projects "
                "ORDER BY last_updated DESC LIMIT 100",
    "assets": "SELECT file_name, file_type, tags FROM stock_library ORDER BY file_name LIMIT 100",
}


class StageUpdateWorker(QThread):
    """SidecarEngine.stage_update() off the UI thread."""
    done = Signal(bool)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            ok = bool(self.engine.stage_update())
        except Exception as exc:
            logging.warning("Staging the update failed: %s", exc)
            ok = False
        self.done.emit(ok)


def poll_facts(port: int) -> dict:
    """
    One look at the database for the Analytics screen. Runs on PollWorker.

    The session rows come from server_facts.sessions() alone: the table used to
    be drawn from it and then overwritten by a second query in another order,
    so "Disconnect session" could end a different connection from the one shown.
    """
    from slate.core.security.dbapi import ConnectionDB
    from slate_server.core.recovery.hardening import logged_refusals
    from slate_server.core.server_facts import sessions
    facts = {"sessions": sessions(int(port)), "stats": None, "refusals": []}
    try:
        conn = psycopg2.connect(**connect_kwargs(port))
    except Exception as exc:
        logging.debug("Poll database stats error: %s", exc)
        return facts
    try:
        db = ConnectionDB(conn)                          # autocommit: no idle transaction
        facts["refusals"] = logged_refusals(db, facts["sessions"])
        one = lambda sql: (db.execute_query(sql, fetch="one") or {}).get("n")  # noqa: E731
        active = one("SELECT count(*) AS n FROM pg_stat_activity "
                     "WHERE state = 'active' OR state = 'idle'") or 0
        most = max(1, int(one("SELECT setting AS n FROM pg_settings "
                              "WHERE name = 'max_connections'") or 100))
        facts["stats"] = {
            "connections": active, "load": min(100, int(active / most * 100)),
            "projects": one("SELECT count(*) AS n FROM tracking_projects") or 0,
            "assets": one("SELECT count(*) AS n FROM stock_library") or 0,
            "db_size": one("SELECT pg_size_pretty(pg_database_size(current_database())) "
                           "AS n") or "Unknown",
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
        }
    except Exception as exc:
        logging.debug("Poll database stats error: %s", exc)
    finally:
        conn.close()
    return facts


class PollWorker(QThread):
    """poll_facts() off the UI thread: a slow database must not freeze the window."""
    done = Signal(object)

    def __init__(self, port):
        super().__init__()
        self.port = int(port)

    def run(self):
        self.done.emit(poll_facts(self.port))


class BackupWorker(QThread):
    """The scheduled backup: pg_dump can take minutes, never on the UI thread."""
    done = Signal(object)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            self.done.emit(self.engine.back_up())
        except Exception as exc:
            self.done.emit({"ok": False, "message": "Backup failed: %s" % exc})


class UTServerWindow(QMainWindow):
    """
    Main Application Window for Slate Central Server.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Slate Server")
        # Analytics' three cards and Operations' backup row need this much;
        # at 900 px they ran off the right edge behind a scroll bar.
        self.setMinimumSize(1120, 650)
        self.setStyleSheet(GLOBAL_STYLESHEET)
        # Opened at the minimum, 220 of those 900 pixels went to the sidebar
        # and the dashboard was cut off at the right-hand edge. Open at a size
        # the screens were designed for, or the size it was last closed at.
        self._restore_window_geometry()

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

        # Credit line at the bottom of the window (licence section 5: must stay)
        from slate.licence import credit_label
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(credit_label())

        # Initialize Database Engine
        # Store database using config file in a persistent location
        import json
        appdata_dir = _server_home()
        os.makedirs(appdata_dir, exist_ok=True)
        self.config_path = _settings_path(appdata_dir)
        default_path = _default_data_dir(appdata_dir)
        default_port = 5440

        db_path = default_path
        port = default_port
        # Where clients connect once PgBouncer is in front of the database.
        pooler_port = 6432
        # PostgreSQL's own limit. With the pool in front of it this rarely needs
        # raising, but it was not visible or editable anywhere at all.
        cfg_max_conn = 100

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8-sig") as f:
                    cfg = json.load(f)
                    db_path = cfg.get("db_path", db_path)
                    port = cfg.get("port", port)
                    pooler_port = cfg.get("pooler_port", pooler_port)
                    cfg_max_conn = cfg.get("max_connections", cfg_max_conn)
            except Exception:
                pass

        # No fallback when the configured folder is unreachable. Falling back
        # meant a server whose drive was not mapped yet started cleanly onto a
        # brand new empty database, and every figure on its dashboard was
        # correct. An unreachable path is reported as one, on the dashboard,
        # and the engine refuses to build anything at it.

        # Remembered so every panel that needs to reach the database - backups,
        # maintenance, diagnostics, the session list - asks the same port the
        # engine was actually started on rather than a default.
        self._db_port = int(port)
        self._db_pooler_port = int(pooler_port)
        self._db_path = db_path

        try:
            self.db_engine = self._build_engine(db_path, int(port), int(pooler_port))
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
        self.settings_view.input_pooler_port.setText(str(pooler_port))
        try:
            from slate_server.core.db_credentials import database_name
            self.settings_view.input_db_name.setText(database_name())
        except Exception:
            self.settings_view.input_db_name.setText("ut_vfx")
        self.settings_view.input_max_conn.setText(str(cfg_max_conn))
        try:
            # The workstations' password - not the superuser's, once that
            # has one of its own (db_admin_password).
            from slate_server.core.db_credentials import app_password
            self.settings_view.input_db_password.setText(app_password())
        except Exception:
            pass
        # Which file these came from. It did not exist on the machine where all
        # of this went wrong, and nothing on screen said so.
        source = "%s%s" % (self.config_path,
                           "" if os.path.exists(self.config_path)
                           else "   (does not exist yet - defaults are in use)")
        try:
            # Which files the password and the database name came from. An
            # installed build used to read none of them, which is invisible
            # unless the list is on the screen.
            from slate_server.core.db_credentials import settings_sources
            read = settings_sources()
            source += "\nCredentials read from: %s" % (
                ", ".join(read) if read else "nothing - no settings file was found")
        except Exception:
            pass
        self.settings_view.lbl_config_source.setText(source)
        self.dashboard.card_port.set_value(str(port))

        self.stacked_widget.addWidget(_scrollable(self.dashboard))
        self.stacked_widget.addWidget(_scrollable(self.settings_view))
        self.stacked_widget.addWidget(_scrollable(self.analytics_view))

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
        self.dashboard.btn_restart_pool.clicked.connect(self._on_restart_pool)
        self.settings_view.btn_save.clicked.connect(self._on_save_settings)
        self.settings_view.btn_firewall.clicked.connect(self._on_allow_firewall)
        self.settings_view.btn_check_update.clicked.connect(self._on_check_update)

        self.worker = None
        self.broadcaster = None

        self.update_checker = None
        self.sidecar_engine = None

        self.analytics_view.card_projects.clicked.connect(self._on_stat_card_clicked)
        self.analytics_view.card_assets.clicked.connect(self._on_stat_card_clicked)
        self.analytics_view.disconnect_requested.connect(self._on_disconnect_session)
        self.analytics_view.card_connections.clicked.connect(self._on_stat_card_clicked)

        # Operations: backups, maintenance, the log and the diagnostics report.
        # None of these had a screen, so every one of them needed a terminal.
        self.operations_view = OperationsView()
        self.stacked_widget.addWidget(_scrollable(self.operations_view))
        self.operations_view.backup_requested.connect(self._on_back_up)
        self.operations_view.prune_requested.connect(self._on_prune_backups)
        self.operations_view.restore_requested.connect(self._on_restore)
        self.operations_view.job_requested.connect(self._on_run_job)
        self.operations_view.diagnostics_requested.connect(self._on_diagnostics)
        self.operations_view.open_log_requested.connect(self._on_open_log)
        self.operations_view.clear_log_requested.connect(self._on_trim_log)
        # Force kill lives in the Operations danger zone, not beside routine
        # buttons on the Dashboard.
        self.operations_view.force_kill_requested.connect(self._on_force_kill)
        self._set_running_controls(False)
        self._show_configured_data_dir()

        # Recovery: for when nobody can sign in. The same page as the
        # standalone Recover Slate window (slate_server/core/recovery).
        self.recovery_view = RecoveryView(self._recovery_layout)
        self.stacked_widget.addWidget(_scrollable(self.recovery_view))

        # Setup Analytics Polling
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_database_stats)

        # Comp off is earned from the attendance record, and the service that
        # works that out was never called by anything - so it was credited to
        # nobody and the balance on every artist's screen was permanently zero.
        # Once a day is often enough: it reads yesterday's punches.
        self.comp_off_timer = QTimer(self)
        self.comp_off_timer.timeout.connect(self._credit_comp_off)
        # The daily backup the Operations screen promised was only ever taken
        # by hand (Maintenance.due() had no caller). Same beat, plus once soon
        # after start so a server left off overnight catches up.
        self.comp_off_timer.timeout.connect(self._scheduled_backup)
        self.comp_off_timer.start(6 * 60 * 60 * 1000)   # every six hours
        self._backup_worker = None
        if not os.environ.get("HEADLESS_TESTING"):
            QTimer.singleShot(5 * 60 * 1000, self._scheduled_backup)

        # Setup Sidebar after views are ready
        self.setup_sidebar()

        # The safety net: a Recovery Key for this server (made once, shown
        # once) and a first snapshot, as soon as there is a database here.
        if not os.environ.get("HEADLESS_TESTING"):
            QTimer.singleShot(1500, self._prepare_recovery)

    # ------------------------------------------------------------ geometry
    DEFAULT_SIZE = (1200, 820)

    @staticmethod
    def _window_settings():
        from PySide6.QtCore import QSettings
        return QSettings("UT Studio", "Slate Server")

    def _restore_window_geometry(self):
        """Last closed size and position, or a size the screens fit in."""
        try:
            saved = self._window_settings().value("window/geometry")
            if saved and self.restoreGeometry(saved):
                screen = QApplication.screenAt(self.frameGeometry().center())
                if screen is not None:
                    return
        except Exception as exc:
            logging.debug("Could not restore the window geometry: %s", exc)

        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(*self.DEFAULT_SIZE)
            return
        available = screen.availableGeometry()
        width = min(self.DEFAULT_SIZE[0], available.width() - 60)
        height = min(self.DEFAULT_SIZE[1], available.height() - 60)
        self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))
        self.move(available.center() - self.rect().center())

    def _save_window_geometry(self):
        try:
            self._window_settings().setValue("window/geometry", self.saveGeometry())
        except Exception as exc:
            logging.debug("Could not save the window geometry: %s", exc)

    def _pg_bin(self):
        """Where the bundled PostgreSQL tools are."""
        from pathlib import Path
        base = Path(__file__).parent.parent
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", sys.executable)) / "slate_server"
        return base / "bin" / "pgsql" / "bin"

    def _data_dir(self):
        engine = getattr(self, "db_engine", None)
        return getattr(engine, "data_dir", "") if engine else ""

    def _backup_engine(self):
        from pathlib import Path
        from slate_server.core.backup_engine import BackupEngine
        data_dir = Path(str(self._data_dir() or "."))
        return BackupEngine(self._pg_bin(), data_dir.parent / "Backups",
                            port=int(getattr(self, "_db_port", 5440) or 5440))

    def _maintenance(self):
        from pathlib import Path
        from slate_server.core.maintenance import Maintenance
        return Maintenance(self._pg_bin(), Path(str(self._data_dir() or ".")),
                           port=int(getattr(self, "_db_port", 5440) or 5440))

    def _refresh_operations(self):
        """Redraw the Operations screen from what is on disk right now."""
        try:
            from slate_server.core.server_facts import human_size

            backups = self._backup_engine().backups()
            if backups:
                newest = backups[0]
                note = ("Last backup %s ago"
                        % ("less than a day" if newest["age_days"] == 0
                           else "1 day" if newest["age_days"] == 1 else "%d days" % newest["age_days"]))
                overdue = newest["age_days"] > 1
            else:
                note = "No backup has ever been taken"
                overdue = True
            self.operations_view.set_backups(backups, note, overdue)

            self.operations_view.set_jobs(self._maintenance().log.summary())

            log = self._log_path()
            if log and log.exists():
                size = log.stat().st_size
                self.operations_view.set_log_state(
                    "%s  -  %s" % (human_size(size), log.name), size > 50 * 1024 * 1024)
            else:
                self.operations_view.set_log_state("no log yet", False)
        except Exception as exc:
            logging.warning("Could not refresh Operations: %s", exc)

    def _log_path(self):
        from pathlib import Path
        data_dir = self._data_dir()
        if not data_dir:
            return None
        return Path(str(data_dir)).parent / "pg_server.log"

    # ------------------------------------------------------------ operations
    def _on_back_up(self):
        engine = self._backup_engine()
        if not engine.is_available():
            QMessageBox.warning(self, "Cannot back up",
                                "pg_dump is not in %s." % engine.bin_dir)
            return
        self._log("> Backing up the database...")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            result = engine.back_up(progress=self._log)
        finally:
            QApplication.restoreOverrideCursor()

        self._maintenance().record_backup(result["ok"], result["message"])
        self._log("> " + result["message"])
        if result["ok"]:
            QMessageBox.information(self, "Backed up", result["message"])
        else:
            QMessageBox.warning(self, "Backup failed", result["message"])
        self._refresh_operations()

    def _on_prune_backups(self, keep_days: int, keep_at_least: int):
        engine = self._backup_engine()
        doomed = engine.prune(keep_days, keep_at_least, apply=False)
        if not doomed:
            QMessageBox.information(
                self, "Nothing to tidy",
                "No backup is older than %d days once the newest %d are kept."
                % (keep_days, keep_at_least))
            return

        if QMessageBox.question(
            self, "Delete old backups",
            "Delete %d backup(s) older than %d days?\n\nThe newest %d are kept "
            "whatever their age. This cannot be undone.\n\n%s"
            % (len(doomed), keep_days, keep_at_least,
               "\n".join(row["name"] for row in doomed[:10])),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        removed = engine.prune(keep_days, keep_at_least, apply=True)
        self._log("> Removed %d old backup(s)." % len(removed))
        self._refresh_operations()

    def _on_restore(self, path: str):
        engine = self._backup_engine()
        facts = self._cluster_facts()
        target = facts.get("database") or "the database"

        from PySide6.QtWidgets import QInputDialog
        typed, ok = QInputDialog.getText(
            self, "Restore over the live database",
            "This replaces everything in '%s' with the contents of:\n%s\n\n"
            "The data directory is:\n%s\n\nEvery workstation must be closed "
            "first, and this cannot be undone. Type RESTORE to go on."
            % (target, path, facts.get("running_data_dir") or facts.get("data_dir") or "?"))
        if not ok or typed.strip().upper() != "RESTORE":
            return

        self._log("> Restoring from %s" % path)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            result = engine.restore(path, confirm_overwrite=True, progress=self._log)
        finally:
            QApplication.restoreOverrideCursor()

        self._log("> " + result["message"])
        if result["ok"]:
            QMessageBox.information(self, "Restored", result["message"])
        else:
            QMessageBox.warning(self, "Restore failed", result["message"])
        self._refresh_operations()

    def _on_run_job(self, job: str):
        jobs = self._maintenance()
        runner = {"vacuum": jobs.vacuum, "reindex": jobs.reindex,
                  "comp_off": jobs.credit_comp_off}.get(job)
        if runner is None:
            return

        self._log("> Running %s..." % job)
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            result = runner(progress=self._log)
        finally:
            QApplication.restoreOverrideCursor()

        self._log("> " + result["message"])
        if result["ok"]:
            QMessageBox.information(self, "Finished", result["message"])
        else:
            QMessageBox.warning(self, "Did not finish", result["message"])
        self._refresh_operations()

    def _on_diagnostics(self):
        """
        The same checks the command-line doctor runs, in the window.

        It found the cause of a whole morning's confusion in two seconds, and it
        was only reachable from a terminal.
        """
        import contextlib
        import io

        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        try:
            from slate import doctor

            # The checks print as they go, which is what makes them readable on a
            # command line. Captured here rather than rewritten, so the window
            # and the terminal always report exactly the same thing.
            buffer = io.StringIO()
            report = doctor.Report()
            with contextlib.redirect_stdout(buffer):
                cfg = doctor.check_configuration(report)
                doctor.check_database(report, cfg)
                doctor.check_server(report, cfg)
                doctor.check_pool(report, cfg)
                doctor.check_updates(report, cfg)
            body = buffer.getvalue().strip()
            problems = len(report.failed)
        except Exception as exc:
            body = "The diagnostics could not run: %s" % exc
            problems = 1
        finally:
            QApplication.restoreOverrideCursor()

        self.operations_view.show_report("Diagnostics", body, problems)

    def _on_open_log(self):
        log = self._log_path()
        if not log or not log.exists():
            QMessageBox.information(self, "No log yet",
                                    "The server has not written a log here yet.")
            return
        webbrowser.open(str(log))

    def _on_trim_log(self):
        """
        Trim the log, keeping the end of it.

        Deleting it outright while PostgreSQL holds it open does not free the
        space and can confuse the writer, so the tail is kept and the rest
        dropped - and the tail is the part anybody reads.
        """
        log = self._log_path()
        if not log or not log.exists():
            QMessageBox.information(self, "No log yet", "There is no log to trim.")
            return

        from slate_server.core.server_facts import human_size
        size = log.stat().st_size
        if QMessageBox.question(
            self, "Trim the server log",
            "The log is %s. Keep the last 2 MB and discard the rest?\n\n"
            "What is discarded is gone." % human_size(size),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            keep = 2 * 1024 * 1024
            with open(log, "rb") as handle:
                if size > keep:
                    handle.seek(size - keep)
                tail = handle.read()
            with open(log, "wb") as handle:
                handle.write(tail)
            self._log("> Trimmed the server log to %s." % human_size(len(tail)))
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not trim it",
                "PostgreSQL holds this file open while it runs, and Windows may "
                "refuse to truncate it:\n\n%s" % exc)
        self._refresh_operations()

    def _cluster_facts(self) -> dict:
        from slate_server.core.server_facts import cluster, other_clusters
        facts = cluster(self._data_dir(),
                        int(getattr(self, "_db_port", 5440) or 5440))
        try:
            facts["others"] = other_clusters(
                facts.get("running_data_dir") or facts.get("data_dir"))
        except Exception as exc:
            logging.debug("Could not look for other clusters: %s", exc)
            facts["others"] = []
        return facts

    def _credit_comp_off(self):
        """
        Credit whatever the attendance record has earned, at most once a day.

        Guarded by the date of the last run rather than by the timer, so
        restarting the server does not credit twice and leaving it off for a
        week does not miss the days in between - the service itself skips a day
        it has already credited.
        """
        from datetime import date

        # Recorded in the server's own maintenance log, not in GlobalConfig.
        # GlobalConfig.set() saves the whole client configuration back to the
        # per-machine file - the server was rewriting the workstation settings
        # file once a day to store one date.
        try:
            jobs = self._maintenance()
            if jobs.log.ran_today("comp_off"):
                return
            result = jobs.credit_comp_off()
            if result.get("ok") and not result.get("skipped"):
                self._log("> Comp off: %s" % result.get("message", ""))
        except Exception as exc:
            logging.warning("Comp off crediting did not run: %s", exc)

    def _scheduled_backup(self):
        """Back up when the last one is over a day old and the database is up."""
        if self._backup_worker is not None and self._backup_worker.isRunning():
            return None
        try:
            if not self.server_running() or "backup" not in self._maintenance().due():
                return None
            engine = self._backup_engine()
            if not engine.is_available():
                return None
        except Exception as exc:
            logging.warning("Scheduled backup check failed: %s", exc)
            return None
        self._log("> Scheduled backup started.")
        self._backup_worker = BackupWorker(engine)
        self._backup_worker.done.connect(self._on_scheduled_backup_done)
        self._backup_worker.start()
        return self._backup_worker

    def _on_scheduled_backup_done(self, result):
        self._maintenance().record_backup(result["ok"], "Scheduled: " + result["message"])
        self._log("> Scheduled backup: " + result["message"])
        if result["ok"]:
            # Keep to the retention shown on the Operations screen; the newest
            # ones are always kept whatever their age.
            try:
                removed = self._backup_engine().prune(
                    self.operations_view.spin_keep_days.value(),
                    self.operations_view.spin_keep_least.value(), apply=True)
                if removed:
                    self._log("> Removed %d old backup(s)." % len(removed))
            except Exception as exc:
                logging.warning("Pruning after the scheduled backup failed: %s", exc)
        else:
            logging.warning("Scheduled backup failed: %s", result["message"])
        self._refresh_operations()

    def setup_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(f"background-color: {C.BG_SURFACE}; border-right: 1px solid {C.BORDER_DEFAULT};")

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 40, 0, 40)
        sidebar_layout.setSpacing(10)

        # The product's own name, lined up with the menu text (24 px).
        lbl_logo = QLabel("Slate Server")
        lbl_logo.setStyleSheet(f"font-size: 20px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY}; "
                               f"padding-left: 24px; margin: 0px; border: none;")
        lbl_logo.setContentsMargins(0, 0, 0, 0)
        lbl_logo.setIndent(0)
        sidebar_layout.addWidget(lbl_logo)
        sidebar_layout.addSpacing(30)

        # Nav Buttons
        self.btn_nav_dash = QPushButton("Dashboard")
        self.btn_nav_analytics = QPushButton("Analytics")
        self.btn_nav_operations = QPushButton("Operations")
        self.btn_nav_settings = QPushButton("Settings")
        self.btn_nav_recovery = QPushButton("Recovery")

        for btn in (self.btn_nav_dash, self.btn_nav_analytics,
                    self.btn_nav_operations, self.btn_nav_settings,
                    self.btn_nav_recovery):
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
        self.btn_nav_operations.clicked.connect(lambda: self.switch_view(3))
        self.btn_nav_recovery.clicked.connect(lambda: self.switch_view(4))

        # Initial State
        self.switch_view(0)

    def switch_view(self, index):
        self.stacked_widget.setCurrentIndex(index)
        if index == 3:
            self._refresh_operations()

        # Update Nav Styles
        active_style = f"text-align: left; padding-left: 20px; background-color: {C.BG_ROOT}; color: {C.ACCENT_PRIMARY}; border-left: 4px solid {C.ACCENT_PRIMARY}; font-size: 14px; font-weight: {T.WEIGHT_BOLD};"
        inactive_style = f"text-align: left; padding-left: 24px; background-color: transparent; color: {C.TEXT_SECONDARY}; border: none; font-size: 14px; font-weight: {T.WEIGHT_SEMI};"

        self.btn_nav_dash.setStyleSheet(active_style if index == 0 else inactive_style)
        self.btn_nav_settings.setStyleSheet(active_style if index == 1 else inactive_style)
        self.btn_nav_analytics.setStyleSheet(active_style if index == 2 else inactive_style)
        self.btn_nav_operations.setStyleSheet(active_style if index == 3 else inactive_style)
        recovery = getattr(self, "btn_nav_recovery", None)
        if recovery is not None:
            recovery.setStyleSheet(active_style if index == 4 else inactive_style)

    def _on_save_settings(self):
        """
        Write the server's settings, all of them.

        Only the path and the port were saved; the pool port, the database name
        and the connection limit were read from this file and could not be
        written to it. And the whole file could be absent, in which case the
        server silently used defaults - which is how it came to serve an empty
        cluster in a fallback location while the studio's real database sat
        untouched. Saving here is what makes that stop happening.
        """
        import json

        view = self.settings_view
        new_path = view.input_db_path.text().strip()
        try:
            new_port = int(view.input_port.text().strip() or 5440)
            new_pooler = int(view.input_pooler_port.text().strip() or 6432)
            new_max_conn = int(view.input_max_conn.text().strip() or 100)
        except ValueError:
            QMessageBox.warning(self, "Not a number",
                                "The ports and the connection limit have to be numbers.")
            return

        new_name = view.input_db_name.text().strip() or "ut_vfx"

        if not new_path:
            QMessageBox.warning(self, "No data directory",
                                "Give the database a path, or the server will build "
                                "a new empty one somewhere of its own choosing.")
            return
        if new_port == new_pooler:
            QMessageBox.warning(self, "Ports clash",
                                "PostgreSQL and the pool cannot share port %d."
                                % new_port)
            return

        # Say so when the path has no cluster in it. The server will happily
        # create one, and a new empty cluster is indistinguishable from the real
        # database by anything else on screen.
        from pathlib import Path
        target = Path(new_path)
        if target.exists() and not (target / "PG_VERSION").exists():
            if QMessageBox.question(
                self, "No database there yet",
                "%s has no PostgreSQL cluster in it. Starting the server will "
                "build a new, empty one.\n\nIf the studio's data is somewhere "
                "else, cancel and point this at that folder instead. Carry on?"
                % new_path,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            ) != QMessageBox.StandardButton.Yes:
                return

        existing = self._server_config()
        if existing is None:
            self._settings_saved_note("NOT saved - the settings file cannot be read")
            QMessageBox.warning(
                self, "Not saved",
                "%s exists but cannot be read (it may be damaged), so it was not "
                "overwritten and nothing was saved.\n\nOpen it in Notepad and fix it, or "
                "move it aside (the server then starts from its defaults), and press "
                "Save again." % self.config_path)
            return
        try:
            existing.update({
                "db_path": new_path,
                "port": new_port,
                "pooler_port": new_pooler,
                "max_connections": new_max_conn,
            })
            with open(self.config_path, "w") as f:
                json.dump(existing, f, indent=4)

            # The database name is what clients ask for, so it lives with the
            # client settings rather than the server's own.
            new_password = view.input_db_password.text()
            client_error = ""
            try:
                from slate.core.infra.local_secrets import write_local_config
                written = {"db_name": new_name,
                           "db_port": new_port,
                           "db_pooler_port": new_pooler}
                # Blank means "leave it alone". Writing an empty password is how
                # a server loses the one setting it cannot work without, and it
                # would look like a successful save.
                if new_password:
                    written["db_password"] = new_password
                write_local_config(written)

                # These are read once and cached, so without this the server
                # goes on using the password it started with and the save looks
                # like it did nothing.
                from slate_server.core import db_credentials
                db_credentials.reload()
            except Exception as exc:
                client_error = str(exc) or exc.__class__.__name__
                self._log("> NOT saved: the database settings (name, ports, password) could "
                          "not be written: %s" % client_error)

            running = self.server_running()
            self.settings_view.lbl_config_source.setText(str(self.config_path))
            self._log("> Configuration saved to %s" % self.config_path)
            if running:
                # The running engine is kept: replacing it while it ran left
                # the old one serving with nothing able to stop it. The new
                # path and ports apply when the server is next started.
                self._pending_settings = (new_path, new_port, new_pooler)
                self._log("> Saved. Restart the server (power off and on) to use the new path and ports.")
                self._settings_saved_note("Saved - restart the server to apply")
            else:
                self._db_port = new_port
                self._db_pooler_port = new_pooler
                try:
                    self.db_engine = self._build_engine(new_path, new_port, new_pooler)
                    self._settings_saved_note("Saved")
                except Exception as e:
                    self._log(f"CRITICAL ERROR initializing engine: {e}")
            if client_error:
                self._settings_saved_note("NOT saved - the database settings could not be "
                                          "written")
                QMessageBox.warning(
                    self, "Not saved",
                    "The server's own settings (folder and ports) were saved, but the "
                    "database settings - its name, ports and password - were NOT:\n\n%s\n\n"
                    "The database password has not changed. Fix the problem and press Save "
                    "again." % client_error)
        except Exception as e:
            self._log(f"Failed to save settings: {e}")
            self._settings_saved_note("NOT saved")
            QMessageBox.warning(self, "Not saved", "The settings were not saved:\n\n%s" % e)

    def _server_config(self):
        """
        slate_server_config.json as a dict ({} when there is none). None when it
        exists but cannot be read: that file is never overwritten, because
        writing {} plus the fields on screen over it throws away everything else
        it held.
        """
        import json
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
        except (OSError, ValueError) as exc:
            logging.error("%s cannot be read, so it is not overwritten: %s",
                          self.config_path, exc)
            return None
        return loaded if isinstance(loaded, dict) else None

    # ------------------------------------------------------------ recovery
    def _recovery_layout(self):
        """Where this server's database and recovery folder are, as the window knows them."""
        from pathlib import Path
        from slate_server.core.recovery.layout import ServerLayout, credentials_file
        try:
            credentials = credentials_file()
        except Exception:
            credentials = None
        return ServerLayout(
            data_dir=Path(str(self._db_path)), port=int(self._db_port),
            pooler_port=int(self._db_pooler_port), server_home=Path(_server_home()),
            settings_path=Path(self.config_path), credentials_path=credentials)

    def _prepare_recovery(self, show=True):
        """
        Once there is a database here: tell the switches where this PC's
        override file is, make the Recovery Key if there is none (and show it,
        once), and take a first snapshot so there is always one to go back to.
        """
        try:
            layout = self._recovery_layout()
            if not (layout.data_dir / "PG_VERSION").exists():
                return None
            from slate.core.security import switches
            switches.set_override_file(layout.switches_file)
            from slate_server.core.recovery import key as recovery_key
            made = recovery_key.create_first_key(layout)
            if made and show:
                from .views.recovery_view import RecoveryKeyDialog
                dialog = RecoveryKeyDialog(made, self, first_time=True)
                dialog.open()
                self._recovery_key_dialog = dialog
                self._log("> A Recovery Key was made for this server. Print it and keep it "
                          "safe (see the Recovery page).")
            from slate_server.core.recovery import snapshots
            if snapshots.latest_snapshot(layout) is None:
                import threading
                threading.Thread(target=lambda: snapshots.before_security_change(
                    "first snapshot", layout), daemon=True).start()
            return made
        except Exception as exc:
            logging.warning("The recovery safety net was not prepared: %s", exc)
            return None

    def _after_start_safety_net(self):
        """After every successful start: what the recovery tool reads later."""
        try:
            layout = self._recovery_layout()
            from slate_server.core import server_home
            from slate_server.core.recovery import health
            server_home.remember_last_good(_server_home(), layout.data_dir, layout.port,
                                           layout.pooler_port)
            health.record_server_state(layout)
            # Switches forced off on this PC reach the workstations through the
            # database.
            from slate.core.security import switches
            from slate.core.security.dbapi import ConnectionDB
            switches.set_override_file(layout.switches_file)
            if switches.local_overrides(layout.switches_file):
                conn = psycopg2.connect(**connect_kwargs(layout.port, connect_timeout=4))
                try:
                    changed = switches.apply_local_overrides(ConnectionDB(conn),
                                                             layout.switches_file)
                    if changed:
                        self._log("> %d security switch(es) turned off, as set on this PC."
                                  % changed)
                finally:
                    conn.close()
        except Exception as exc:
            logging.warning("Recovery bookkeeping after start failed: %s", exc)
        self._prepare_recovery()

    def server_running(self) -> bool:
        """The database is up (the power switch is on)."""
        try:
            return bool(self.dashboard.toggle_power.isChecked())
        except (AttributeError, RuntimeError):
            return False

    def _settings_saved_note(self, text):
        """Save feedback on the Settings screen - never in the Online/Offline badge."""
        label = getattr(self.settings_view, "lbl_update_status", None)
        note = getattr(self.settings_view, "lbl_save_note", None) or label
        try:
            if note is not None:
                note.setText(text)
        except RuntimeError:
            pass

    def _build_engine(self, db_path, port, pooler_port, allow_create=False):
        """The engine and its pool, built the one way, for the one path."""
        engine = DatabaseEngine(db_path, port=int(port), allow_create=allow_create)
        # Connection pooling. Attached to the database engine so whatever
        # starts or stops the database also starts or stops the pool.
        engine.pooler = PgBouncerEngine(
            db_path,
            db_port=int(port),
            listen_port=int(pooler_port),
            db_user=_client_setting("db_user", "ut_vfx_app"),
            db_password=_client_setting("db_password", ""),
        )
        return engine

    def _remember_data_dir(self, path):
        """Write the chosen folder down, so the next start does not ask again."""
        import json

        existing = self._server_config()
        if existing is None:
            self._log("> The database path was NOT saved: %s cannot be read, so it was not "
                      "overwritten. Fix or move it aside." % self.config_path)
            return
        existing.update({"db_path": path,
                         "port": int(self._db_port),
                         "pooler_port": int(self._db_pooler_port)})
        try:
            with open(self.config_path, "w") as f:
                json.dump(existing, f, indent=4)
            self.settings_view.lbl_config_source.setText(str(self.config_path))
        except OSError as exc:
            self._log("> Could not save the database path: %s" % exc)

    def _database_chosen(self) -> bool:
        """
        Make sure the folder about to be started holds a database, or that a
        person has said to build one there. Nothing is built by default.

        A new empty database is indistinguishable from the studio's real one on
        every screen. So when the folder is empty the server stops and asks -
        and "create a new one" is a button somebody has to press, on a dialog
        that says what it means.
        """
        engine = getattr(self, "db_engine", None)
        if engine is None:
            return False
        if engine.is_initialized():
            return True

        data_dir = str(engine.data_dir)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Where is the studio's database?")
        box.setText("There is no database in\n%s" % data_dir)
        box.setInformativeText(
            "The server will not build one on its own. A new empty database "
            "looks exactly like the studio's real one on every screen, and "
            "nothing about it says which is which.\n\n"
            "If this studio already has a database, choose its folder. Only "
            "create a new one if this is genuinely a new studio.")
        use_existing = box.addButton("Use an existing database folder...",
                                     QMessageBox.ButtonRole.AcceptRole)
        create_new = box.addButton("Create a new empty database here",
                                   QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(use_existing)
        # Three buttons, two of them sentences: the box has to be wide enough
        # to show them whole, or the choice reads as "existing" and "new".
        box.setStyleSheet("QLabel#qt_msgbox_label { min-width: 600px; }")
        box.exec()
        clicked = box.clickedButton()

        if clicked is create_new:
            engine.allow_create = True
            self._remember_data_dir(data_dir)
            self._log("> Creating a new empty database in %s, as chosen." % data_dir)
            return True

        if clicked is use_existing:
            chosen = QFileDialog.getExistingDirectory(
                self, "The folder holding the studio's database",
                os.path.dirname(data_dir) or data_dir)
            if not chosen:
                return False
            if not os.path.exists(os.path.join(chosen, "PG_VERSION")):
                QMessageBox.warning(
                    self, "Not a database",
                    "%s has no PostgreSQL database in it (no PG_VERSION file). "
                    "Nothing has been changed." % chosen)
                return False
            self.db_engine = self._build_engine(chosen, self._db_port,
                                                self._db_pooler_port)
            self._db_path = chosen
            self.settings_view.input_db_path.setText(chosen)
            self._remember_data_dir(chosen)
            self._log("> Using the existing database in %s." % chosen)
            return True

        return False

    def _on_power_toggled(self, state):
        if state and not self._database_chosen():
            # Put the switch back without coming through here again.
            self.dashboard.toggle_power.blockSignals(True)
            self.dashboard.toggle_power.setChecked(False)
            self.dashboard.toggle_power.blockSignals(False)
            return

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

    def _set_running_controls(self, running: bool):
        """
        Restart pool only while the server runs: with it off, the pool was
        started in front of nothing.
        """
        button = self.dashboard.btn_restart_pool
        button.setEnabled(bool(running))
        button.setToolTip("Rewrites the pool configuration and restarts it." if running
                          else "Start the server first")

    def _show_configured_data_dir(self):
        """The data directory is known before the server starts: show it."""
        try:
            path = str(self._data_dir())
        except Exception:
            return
        exists = os.path.exists(os.path.join(path, "PG_VERSION"))
        note = "" if exists else (" (not created yet)" if os.path.isdir(path) else " (not reachable)")
        self.dashboard.card_data_dir.set_value(path + note)

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
                self.broadcaster = NetworkBroadcaster(
                    db_port=int(self.settings_view.input_port.text()),
                    pooler_port=int(self.settings_view.input_pooler_port.text().strip() or 0))
                self.broadcaster.start()
                self._log(f"> Network Discovery Broadcaster is ONLINE (UDP port {self.broadcaster.listen_port}).")

                # The web API is no longer started here: nothing in Slate used
                # it, and on 0.0.0.0 it let anyone on the network take over the
                # admin account (2.2.0).
                self._set_running_controls(True)

                self.poll_timer.start(3000)
                self._after_start_safety_net()
            else:
                self.dashboard.status_badge.set_status("Server Offline", "error")
                self._log("> Server stopped gracefully.")

                if self.broadcaster:
                    self.broadcaster.stop()
                    self.broadcaster = None
                    self._log("> Network Discovery Broadcaster stopped.")

                self._set_running_controls(False)
                self._show_configured_data_dir()

                self.poll_timer.stop()
        else:
            self.dashboard.status_badge.set_status("Error", "error")
            self._log(f"> ERROR: {error_msg}")
            if state:
                # A start that failed is worth a window of its own. The log
                # line above is one line; a data folder from another
                # PostgreSQL version, or a locked-out cluster, comes with
                # instructions that do not fit in it.
                QMessageBox.warning(self, "The database did not start", str(error_msg))
            # Revert toggle visually without emitting signal
            self.dashboard.toggle_power.blockSignals(True)
            self.dashboard.toggle_power.setChecked(not state)
            self.dashboard.toggle_power.blockSignals(False)

    def _on_restart_pool(self):
        """
        Rewrite the pool's configuration and restart it.

        The config is regenerated from the current settings, which is the fix for
        a pool that publishes a different database name from the one clients ask
        for. That state refuses every connection and nothing about the pool being
        "up" reveals it - a pooler left running from an older install kept its
        stale config for a day here.
        """
        engine = getattr(self, "db_engine", None)
        pooler = getattr(engine, "pooler", None) if engine else None
        if pooler is None:
            QMessageBox.information(self, "No pool",
                                    "This server is not running a connection pool.")
            return

        if not self.server_running():
            QMessageBox.information(self, "Restart pool", "Start the server first: the pool "
                                    "sits in front of the database.")
            return
        self._log("> Restarting the connection pool\u2026")
        self.dashboard.btn_restart_pool.setEnabled(False)
        self.dashboard.btn_restart_pool.setText("Restarting the pool\u2026")
        self._pool_worker = DBWorker(engine, "pool")
        self._pool_worker.progress.connect(self._on_worker_progress)
        self._pool_worker.finished.connect(self._on_pool_restarted)
        self._pool_worker.start()
        return self._pool_worker

    def _on_pool_restarted(self, started, error_msg):
        self.dashboard.btn_restart_pool.setText("Restart pool")
        self._set_running_controls(self.server_running())
        if error_msg:
            self._log("> Pool restart failed: %s" % error_msg)
        pooler = getattr(getattr(self, "db_engine", None), "pooler", None)
        from slate_server.core.server_facts import pool as pool_facts
        facts = pool_facts(pooler)
        if started and facts.get("agrees") is not False:
            QMessageBox.information(
                self, "Pool restarted",
                "The pool is on port %s and publishes '%s', which is what clients "
                "ask for." % (facts.get("listen_port"), facts.get("publishes")))
        elif facts.get("agrees") is False:
            QMessageBox.warning(
                self, "Pool still disagrees",
                "The pool publishes '%s' but clients ask for '%s'. Check the "
                "database name in Settings, then restart the pool again."
                % (facts.get("publishes"), facts.get("expects")))
        else:
            QMessageBox.warning(self, "Pool did not start",
                                "The pool did not come up. The log above says why.")
        self._refresh_cluster_cards()

    def _refresh_cluster_cards(self):
        """Put the cluster and pool facts on the dashboard."""
        try:
            from slate_server.core.server_facts import pool as pool_facts
            engine = getattr(self, "db_engine", None)
            pooler = getattr(engine, "pooler", None) if engine else None
            self.dashboard.set_cluster_facts(self._cluster_facts(), pool_facts(pooler))
        except Exception as exc:
            logging.debug("Could not refresh the cluster cards: %s", exc)

    FIREWALL_RULE = "Slate Central Server (Database)"

    @classmethod
    def firewall_rule_exists(cls, run=None) -> bool:
        """Whether Windows has the database rule (netsh), so we never claim it blindly."""
        import subprocess
        run = run or subprocess.run
        try:
            result = run(["netsh", "advfirewall", "firewall", "show", "rule",
                          "name=%s" % cls.FIREWALL_RULE],
                         capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            logging.debug("Firewall check failed: %s", exc)
            return False
        return result.returncode == 0 and cls.FIREWALL_RULE in (result.stdout or "")

    def _on_allow_firewall(self, run=None):
        self._log("> Asking Windows for administrator rights to open the firewall...")
        import subprocess
        run = run or subprocess.run
        try:
            port = self.settings_view.input_port.text()
            # The pool's port as well. It was left out, so every workstation
            # tried PgBouncer, waited five seconds for a port the firewall was
            # silently dropping, and then connected to the database directly -
            # four times per start-up, twenty seconds before the login screen.
            pooler_port = self.settings_view.input_pooler_port.text() or "6432"
            # -Wait: the check below must run after the rules are added, not
            # while the administrator prompt is still open.
            cmd = (f"Start-Process cmd -Wait -ArgumentList '/c "
                   f"netsh advfirewall firewall add rule name=\"{self.FIREWALL_RULE}\" dir=in action=allow protocol=TCP localport={port} & "
                   f"netsh advfirewall firewall add rule name=\"Slate Central Server (Pool)\" dir=in action=allow protocol=TCP localport={pooler_port} & "
                   f"netsh advfirewall firewall add rule name=\"Slate Central Server (Discovery)\" dir=in action=allow protocol=UDP localport=54320' "
                   f"-Verb RunAs -WindowStyle Hidden")
            run(["powershell", "-Command", cmd], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._log(f"Could not ask Windows to open the firewall: {e}")
        # Said only when Windows has the rule: refusing the prompt used to
        # show "Firewall Allowed" all the same.
        if self.firewall_rule_exists(run):
            self._log("> Firewall rules are in place; workstations can connect.")
            self._settings_saved_note("Firewall allowed")
            return True
        self._log("> The firewall was not changed (the request was refused or failed).")
        self._settings_saved_note("Firewall not changed")
        return False

    def _on_force_kill(self):
        """
        Kill PostgreSQL outright.

        This used to run the moment it was clicked. A hard kill of a live
        database can leave the cluster needing recovery on next start, and
        because it matches on the process name it takes down every PostgreSQL on
        the machine rather than only this one. Both of those are worth a sentence
        before it happens.
        """
        import subprocess

        facts = self._cluster_facts()
        where = facts.get("running_data_dir") or facts.get("data_dir") or "unknown"

        if QMessageBox.question(
            self, "Force kill the database",
            "This kills PostgreSQL immediately, without letting it finish what "
            "it is doing.\n\nData directory:\n%s\n\nAnything a workstation is "
            "part way through saving is lost, and the cluster may need recovery "
            "when it next starts. It also stops every PostgreSQL on this machine, "
            "not only this one.\n\nTry the power switch first - it shuts down "
            "cleanly. Force kill anyway?" % where,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            self._log("Force kill cancelled.")
            return

        self._log("Attempting to force kill PostgreSQL processes...")
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
        # The cluster facts and the session list come from one place each, so the
        # dashboard and the analytics table cannot disagree about them.
        self._refresh_cluster_cards()
        # Same beat as the dashboard refresh: if the pooler has died, bring it
        # back before 150 machines start connecting to the database directly.
        try:
            pooler = getattr(self.db_engine, "pooler", None)
            if pooler is not None:
                pooler.ensure_running(psql_exe=self.db_engine.bin_dir / "psql.exe")
        except Exception as exc:
            logging.debug("Pooler health check skipped: %s", exc)

        # The queries run on a thread; a poll still running is not doubled up.
        worker = getattr(self, "_poll_worker", None)
        if worker is not None and worker.isRunning():
            return worker
        self._poll_worker = PollWorker(int(getattr(self, "_db_port", 5440) or 5440))
        self._poll_worker.done.connect(self._show_poll)
        self._poll_worker.start()
        return self._poll_worker

    def _show_poll(self, facts):
        """poll_facts() on screen. The table's rows are exactly server_facts.sessions()."""
        rows = facts.get("sessions") or []
        self.analytics_view.set_sessions(rows)
        self._client_count = len({r["client"] for r in rows if r.get("client")})
        stats = facts.get("stats")
        if stats:
            view = self.analytics_view
            view.card_connections.set_value("%s" % stats["connections"])
            view.card_load.set_value("%s%%" % stats["load"])
            view.card_projects.set_value("%s" % stats["projects"])
            view.card_assets.set_value("%s" % stats["assets"])
            view.card_db_size.set_value("%s" % stats["db_size"])
            view.card_cpu.set_value("%s%%" % stats["cpu"])
            view.card_ram.set_value("%s%%" % stats["ram"])
        # Server switches in log_only: what they WOULD refuse, once per connection.
        seen = self.__dict__.setdefault("_logged_refusals", set())
        for line in facts.get("refusals") or []:
            if line not in seen:
                seen.add(line)
                logging.warning("Security switch (log only): %s", line)
                self._log("> Security switch: %s" % line)

    def _on_disconnect_session(self):
        """
        Disconnect one workstation's session.

        The list was read-only, so when a single client wedged the database the
        only lever was to stop the whole server and put every other artist off
        it too.
        """
        from slate_server.core.server_facts import terminate

        row = self.analytics_view.selected_pid()
        if not row:
            QMessageBox.information(self, "Nothing selected",
                                    "Pick a session in the list first.")
            return

        who = row.get("client") or "?"
        if QMessageBox.question(
            self, "Disconnect this session",
            "Disconnect session %d from %s (%s)?\n\nIt is asked to stop its "
            "query first; only a session that ignores that is cut off. Anything "
            "it was part way through saving is lost."
            % (row["pid"], who, row.get("application") or "unknown application"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return

        ok, message = terminate(int(getattr(self, "_db_port", 5440) or 5440), row["pid"])
        self._log("> " + message)
        if not ok:
            QMessageBox.warning(self, "Not disconnected", message)
        self._poll_database_stats()

    def _on_stat_card_clicked(self, title):
        if not self.dashboard.toggle_power.isChecked():
            return

        port = int(self.settings_view.input_port.text())
        from .views.analytics_view import DataViewerDialog

        query = ""
        if "Projects" in title:
            query = STAT_QUERIES["projects"]
        elif "Assets" in title:
            query = STAT_QUERIES["assets"]
        elif "Active" in title or "Connections" in title:
            query = "SELECT client_addr, application_name, state, query_start FROM pg_stat_activity WHERE client_addr IS NOT NULL"

        if query:
            dlg = DataViewerDialog(f"{title} Data", query, port, self)
            dlg.exec()

    def _log(self, message):
        print(f"[Slate Server] {message}")
        dashboard = getattr(self, "dashboard", None)
        if dashboard is not None and hasattr(dashboard, "append_log"):
            dashboard.append_log(message)

    def _who_is_connected(self) -> str:
        count = int(getattr(self, "_client_count", 0) or 0)
        if count == 1:
            return "1 workstation is connected"
        if count:
            return "%d workstations are connected" % count
        return "Workstations may be connected"

    def ask_before_closing(self) -> str:
        """
        'stop', 'tray' or 'cancel'. One click on X used to stop the studio
        database with no question, taking every workstation offline.
        """
        who = self._who_is_connected()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Close Slate Server")
        box.setText("Stop the studio database?")
        box.setInformativeText(
            "%s. Stopping the server takes every workstation offline.\n\n"
            "Keep it running in the tray to close this window only." % who)
        tray = box.addButton("Keep running in the tray", QMessageBox.ButtonRole.AcceptRole)
        stop = box.addButton("Stop server", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(tray)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is stop:
            return "stop"
        if clicked is tray:
            return "tray"
        return "cancel"

    def _tray(self):
        """The tray icon that keeps a running server reachable with its window closed."""
        from PySide6.QtWidgets import QSystemTrayIcon, QMenu
        tray = getattr(self, "tray_icon", None)
        if tray is None:
            tray = QSystemTrayIcon(self.windowIcon(), self)
            tray.setToolTip("Slate Server - the studio database is running")
            menu = QMenu(self)
            menu.addAction("Show Slate Server", self._show_from_tray)
            menu.addSeparator()
            menu.addAction("Stop server and quit", self._quit_from_tray)
            tray.setContextMenu(menu)
            tray.activated.connect(lambda _reason: self._show_from_tray())
            self.tray_icon = tray
        return tray

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self._closing_confirmed = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
        """Stop the database only when the person means it (or keep it in the tray)."""
        if self.server_running() and not getattr(self, "_closing_confirmed", False):
            answer = self.ask_before_closing()
            if answer == "cancel":
                event.ignore()
                return
            if answer == "tray":
                tray = self._tray()
                tray.show()
                app = QApplication.instance()
                if app is not None:
                    app.setQuitOnLastWindowClosed(False)
                self._save_window_geometry()
                self.hide()
                event.ignore()
                self._log("> Window closed; the server keeps running in the tray.")
                return
        self._save_window_geometry()
        backup = getattr(self, "_backup_worker", None)
        if backup is not None and backup.isRunning():
            self._log("> Waiting for the scheduled backup to finish…")
            backup.wait()              # stopping the database under pg_dump spoils the backup
        if self.server_running() and not getattr(self, "_stopped_for_close", False):
            # pg_ctl stop can take seconds: on the worker, and the window
            # closes when it reports back (it froze, its log unpainted).
            self.dashboard.status_badge.set_status("Stopping the database\u2026", "warning")
            self._log("> Stopping the database\u2026")
            self.setEnabled(False)
            self.worker = DBWorker(self.db_engine, "stop")
            self.worker.progress.connect(self._on_worker_progress)
            self.worker.finished.connect(self._close_after_stop)
            self.worker.start()
            event.ignore()
            return
        # The pool goes down with the database. It used to be left running:
        # the next start found it "already running", kept it, and every
        # workstation was refused until somebody ended pgbouncer.exe by hand.
        pooler = getattr(getattr(self, "db_engine", None), "pooler", None)
        if pooler is not None:
            try:
                pooler.stop(progress_callback=self._log)
            except Exception as e:
                logging.debug(f"Pool stop on close error: {e}")
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

        if getattr(self, "poll_timer", None) is not None:
            self.poll_timer.stop()
        worker = getattr(self, "_poll_worker", None)
        if worker is not None:
            worker.wait(5000)          # a QThread destroyed while running takes the app down

        event.accept()

    def _close_after_stop(self, _ok, error_msg):
        if error_msg:
            logging.warning("Stopping on close reported: %s", error_msg)
        self._stopped_for_close = True
        self._closing_confirmed = True
        self.dashboard.toggle_power.blockSignals(True)
        self.dashboard.toggle_power.setChecked(False)
        self.dashboard.toggle_power.blockSignals(False)
        self.close()

    # --- UPDATE FLOW ---
    def _on_check_update(self):
        if self.sidecar_engine and hasattr(self.sidecar_engine, 'temp_updater'):
            self._apply_staged_update()
            return

        self.settings_view.btn_check_update.setText("Checking\u2026")
        self.settings_view.btn_check_update.setEnabled(False)
        self.settings_view.lbl_update_status.setText("Looking for updates\u2026")

        if self.update_checker:
            self.update_checker.stop()
            self.update_checker.deleteLater()

        from slate.core.updater.update_checker import UpdateChecker
        self.update_checker = UpdateChecker(self, manual_mode=True, target="server")
        self.update_checker.update_available.connect(self.on_update_found)
        self.update_checker.update_not_found.connect(self.on_no_update)

        def cleanup():
            self.settings_view.btn_check_update.setEnabled(True)
            if self.settings_view.btn_check_update.text() == "Checking\u2026":
                self.settings_view.btn_check_update.setText("Check for updates")
        self.update_checker.finished.connect(cleanup)
        self.update_checker.start()

    def on_update_found(self, manifest):
        self.settings_view.btn_check_update.setText("Check for updates")
        self.settings_view.lbl_update_status.setText(f"Update found: {manifest.get('version')}")
        from slate.gui.dialogs.update_available_dialog import UpdateAvailableDialog
        dlg = UpdateAvailableDialog(manifest, self)
        if dlg.exec():
            self._stage_update(manifest)

    def _stage_update(self, manifest):
        """
        Download and verify the update on a worker thread - it ran on the UI
        thread, and the window froze for the whole download.
        """
        from slate.core.updater.sidecar_engine import SidecarEngine

        self.settings_view.btn_check_update.setEnabled(False)
        self.settings_view.btn_check_update.setText("Downloading\u2026")
        self.settings_view.lbl_update_status.setText("Downloading the update in the background\u2026")
        self.sidecar_engine = SidecarEngine(manifest)
        self._stage_worker = StageUpdateWorker(self.sidecar_engine)
        self._stage_worker.done.connect(self._on_update_staged)
        self._stage_worker.start()
        return self._stage_worker

    def _on_update_staged(self, success):
        self.settings_view.btn_check_update.setEnabled(True)
        if success:
            self.settings_view.btn_check_update.setText("Restart to apply")
            self.settings_view.lbl_update_status.setText("Update downloaded and checked. Restart to apply it.")
            QMessageBox.information(self, "Update ready", "The update is downloaded and checked. Choose 'Restart to apply'.")
        else:
            self.settings_view.btn_check_update.setText("Check for updates")
            self.settings_view.lbl_update_status.setText("The update could not be downloaded.")
            QMessageBox.warning(self, "Update not downloaded",
                                "The update could not be downloaded or checked. The server log says why.")

    def _apply_staged_update(self):
        if not self.sidecar_engine:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Apply the update")
        box.setText("Restart Slate Server to apply the update?")
        box.setInformativeText(
            "%s. Restarting stops the studio database, so every workstation "
            "goes offline until it is back." % self._who_is_connected())
        now = box.addButton("Restart now", QMessageBox.ButtonRole.DestructiveRole)
        later = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later)
        box.setEscapeButton(later)
        box.exec()
        if box.clickedButton() is now:
            if self.dashboard.toggle_power.isChecked():
                pooler = getattr(self.db_engine, "pooler", None)
                if pooler is not None:
                    pooler.stop()
                self.db_engine.stop()
            self.sidecar_engine.apply_update()
            QApplication.quit()

    def on_no_update(self, current_ver):
        self.settings_view.btn_check_update.setText("Check for updates")
        reason = ""
        if self.update_checker:
            reason = str(getattr(self.update_checker, "last_result_reason", "") or "")

        message, status = self.update_result_text(reason, current_ver)
        QMessageBox.information(self, "Check for updates", message)
        self.settings_view.lbl_update_status.setText(status)

    @staticmethod
    def update_result_text(reason: str, current_ver) -> tuple:
        """(message, status line) for a check that found no update - or failed."""
        reason = str(reason or "")
        if reason in ("missing_latest_pointer", "manifest_missing"):
            return ("No update information was found for the server.", "Could not check for updates")
        if reason == "invalid_manifest":
            return ("The update information is damaged, so no update can be offered.",
                    "Could not check for updates")
        if reason in ("", "up_to_date", "no_update", "current", "latest"):
            return (f"The server is up to date (version {current_ver}).", f"Up to date: v{current_ver}")
        # 'error' and anything else is a failed check, not "latest version".
        return ("Could not check for updates. Check the network and the update "
                "folder, then try again.", "Could not check for updates")
