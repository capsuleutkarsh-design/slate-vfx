"""
The first-run window: where the studio's shared folder and database are.

Its own module, with nothing run at import time. It lived in gatekeeper_main,
which sets up logging and the crash handler when imported, so opening
Reconfigure from the sign-in window renamed the log to "Gatekeeper".
"""
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.core.infra.global_config import GlobalConfig


class ConnectionTestWorker(QThread):
    """Try the typed database details off the UI thread (5 s at most)."""
    done = Signal(bool, str)

    def __init__(self, values, connect=None):
        super().__init__()
        self.values = dict(values)
        self._connect = connect

    def run(self):
        ok, message = test_database_connection(self.values, connect=self._connect)
        self.done.emit(ok, message)


def test_database_connection(values, connect=None):
    """(ok, plain sentence) for the database details in the first-run window."""
    try:
        if connect is None:
            import psycopg2
            connect = psycopg2.connect
        conn = connect(host=values.get("db_host"), port=int(values.get("db_port") or 5440),
                       dbname=values.get("db_name"), user=values.get("db_user"),
                       password=values.get("db_password") or None, connect_timeout=5)
        try:
            conn.close()
        except Exception:
            pass
        return True, "Connected. These details work."
    except Exception as exc:
        text = str(exc).lower()
        if "password" in text or "authentication" in text:
            reason = "the server refused the user name or password"
        elif "does not exist" in text:
            reason = "there is no database with that name on the server"
        elif "timeout" in text or "timed out" in text:
            reason = "the server did not answer within 5 seconds"
        else:
            reason = "the server could not be reached at that address and port"
        logging.info("First-run connection test failed: %s", exc)
        return False, f"Not connected: {reason}."


class FirstRunSetupDialog(QDialog):
    """First-run configuration dialog for required runtime paths and DB connection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up Slate on this computer")
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # The names here are the ones Settings uses, so the advice can be followed.
        intro = QLabel(
            "Tell Slate where the studio's shared folder (the server root) and its "
            "server are. Find server fills in the server for you when it is on this "
            "network. You can change these later in Settings, under "
            "Server, database and branding."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        from slate.gui.core.controls import form_layout, make_button
        form = form_layout()

        server_wrap = QWidget()
        server_row = QHBoxLayout(server_wrap)
        server_row.setContentsMargins(0, 0, 0, 0)
        self.server_root_input = QLineEdit(str(GlobalConfig.get("SERVER_ROOT", "")))
        self.server_root_input.setPlaceholderText(
            "The studio's shared folder - a mapped drive or a \\\\server\\share path")
        browse_server_btn = QPushButton("Browse…")
        browse_server_btn.clicked.connect(self._browse_server_root)
        server_row.addWidget(self.server_root_input, 1)
        server_row.addWidget(browse_server_btn)
        form.addRow("Server root", server_wrap)

        host_wrap = QWidget()
        host_row = QHBoxLayout(host_wrap)
        host_row.setContentsMargins(0, 0, 0, 0)
        self.db_host_input = QLineEdit(str(GlobalConfig.get("db_host", "")))
        self.db_host_input.setPlaceholderText("e.g. 10.0.0.15 or slate-server")
        self.find_button = make_button("Find server", "secondary", on_click=self._find_server,
                                       tooltip="Ask the network where Slate Server is")
        host_row.addWidget(self.db_host_input, 1)
        host_row.addWidget(self.find_button)
        form.addRow("Server address", host_wrap)

        self.db_port_input = QSpinBox()
        self.db_port_input.setRange(1, 65535)
        self.db_port_input.setValue(int(GlobalConfig.get("db_port", 5440) or 5440))
        form.addRow("Port", self.db_port_input)

        self.db_name_input = QLineEdit(str(GlobalConfig.get("db_name", "ut_vfx")))
        form.addRow("Database name", self.db_name_input)

        self.db_user_input = QLineEdit(str(GlobalConfig.get("db_user", "ut_vfx_app")))
        form.addRow("Database user", self.db_user_input)

        self.db_password_input = QLineEdit(str(GlobalConfig.get("db_password", "")))
        self.db_password_input.setEchoMode(QLineEdit.Password)
        self.db_password_input.setPlaceholderText("Leave empty if IT gave you no database password")
        form.addRow("Database password", self.db_password_input)

        root.addLayout(form)

        test_row = QHBoxLayout()
        self.test_button = make_button("Test connection", "secondary", on_click=self.test_connection)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        root.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._test_worker = None
        self._find_worker = None

    def _set_result(self, ok, message):
        colour = Gate.OK if ok else Gate.BAD
        self.test_result.setStyleSheet(f"color: {colour};")
        self.test_result.setText(message)
        self.test_button.setEnabled(True)
        self.test_button.setText("Test connection")

    def test_connection(self, connect=None):
        """Check the details on a worker thread; the result goes on the line beside."""
        if self._test_worker is not None and self._test_worker.isRunning():
            return self._test_worker
        self.test_button.setEnabled(False)
        self.test_button.setText("Testing…")
        self.test_result.setStyleSheet("")
        self.test_result.setText("Trying the server (up to 5 seconds)…")
        self._test_worker = ConnectionTestWorker(self.values(), connect=connect)
        self._test_worker.done.connect(self._set_result)
        self._test_worker.start()
        return self._test_worker

    def _find_server(self):
        """Ask the network on a worker (2 s); the window stays usable and says so."""
        if self._find_worker is not None:
            return self._find_worker
        from slate.core.infra.db_worker import run_db_async
        from slate.core.infra import network_discovery
        discover = network_discovery.discover_server_details
        self.find_button.setEnabled(False)
        self.find_button.setText("Looking…")
        self.test_result.setStyleSheet("")
        self.test_result.setText("Looking for Slate Server on this network…")
        self._find_worker = run_db_async(lambda: discover(timeout=2.0),
                                         on_success=self._on_found,
                                         on_error=lambda _text: self._on_found(None), owner=self)
        return self._find_worker

    def _on_found(self, found):
        self._find_worker = None
        self.find_button.setEnabled(True)
        self.find_button.setText("Find server")
        if not found:
            self._set_result(False, "No Slate Server answered on this network. Type its address.")
            return
        self.db_host_input.setText(found["host"])
        self.db_port_input.setValue(int(found.get("pooler_port") or found.get("db_port") or 5440))
        self._set_result(True, f"Found Slate Server at {found['host']}.")

    def _browse_server_root(self):
        start_dir = self.server_root_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose the studio's shared folder", start_dir)
        if selected:
            self.server_root_input.setText(selected)

    def _validate_and_accept(self):
        missing = [label for label, field in (
            ("the server root", self.server_root_input),
            ("the server address", self.db_host_input),
            ("the database name", self.db_name_input),
            ("the database user", self.db_user_input)) if not field.text().strip()]
        if missing:
            QMessageBox.warning(self, "Set up Slate", "Fill in " + ", ".join(missing) + ".")
            return
        root = self.server_root_input.text().strip()
        # A warning, not a refusal (as in Settings): a share can be down for a
        # moment. Saved silently, a typo only showed up later as missing thumbnails.
        if not Path(root).is_dir() and QMessageBox.question(
                self, "Set up Slate",
                f"{root} cannot be reached from this computer. Save it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            self.server_root_input.setFocus()
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
