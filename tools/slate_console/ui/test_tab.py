from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QListWidget, QListWidgetItem, QTextEdit, QMessageBox
)
import os
import sys
import subprocess
from worker.process_runner import ProcessRunner


def _python():
    """
    The interpreter to run things with.

    A bare "python" hits the Windows Store alias stub on a machine that only has
    the portable runtime, which is every machine this ships to. The console is
    already running under the right one.
    """
    return sys.executable


# What the launch buttons start. There is no slate/main.py and never was: the
# products are the two entry scripts below, plus the server.
LAUNCHERS = (
    ("Slate Studio", os.path.join("slate", "vfx_studio_main.py")),
    ("Slate Operations", os.path.join("slate", "studio_ops_main.py")),
    ("Slate Server", os.path.join("slate_server", "main.py")),
)


class TestTab(QWidget):
    """
    'The Lab' - launching the products from source, and running the tests.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.runner = None
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # --- HEADER ---
        header = QLabel("TESTING LAB")
        header.setStyleSheet("color: #00B4D8; font-size: 18px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(header)

        # --- LAUNCHERS ---
        launch_group = QGroupBox("Run from source")
        launch_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        l_layout = QHBoxLayout(launch_group)

        btn_style = """
            QPushButton {
                background-color: #2b2b2b; color: #ddd; border: 1px solid #444;
                border-radius: 6px; padding: 10px; font-weight: bold;
            }
            QPushButton:hover { background-color: #3d3d3d; border: 1px solid #E9C46A; color: white; }
            QPushButton:pressed { background-color: #1a1a1a; }
        """

        for title, script in LAUNCHERS:
            btn = QPushButton(f"▶ {title}")
            btn.setToolTip(f"Run {script} under the console's Python")
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda _checked=False, s=script: self.launch(s))
            l_layout.addWidget(btn)

        self.btn_run_bat = QPushButton("\U0001F680 Batch launcher")
        self.btn_run_bat.setToolTip("Run launchers/launch_app.bat (the legacy all-in-one gatekeeper)")
        self.btn_run_bat.setStyleSheet(btn_style)
        self.btn_run_bat.clicked.connect(self.run_batch_mode)
        l_layout.addWidget(self.btn_run_bat)
        layout.addWidget(launch_group)

        # --- TESTS ---
        test_group = QGroupBox("Tests (pytest)")
        test_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        t_layout = QVBoxLayout(test_group)

        self.test_list = QListWidget()
        self.test_list.setStyleSheet("background: #111; color: #ddd; border: 1px solid #333;")
        self.test_list.itemDoubleClicked.connect(lambda _item: self.run_selected_test())
        t_layout.addWidget(self.test_list)

        # Populate tests
        self.populate_tests()

        row = QHBoxLayout()
        self.btn_run_test = QPushButton("RUN SELECTED TEST FILE")
        self.btn_run_test.setStyleSheet(btn_style)
        self.btn_run_test.clicked.connect(self.run_selected_test)
        row.addWidget(self.btn_run_test)

        self.btn_run_all = QPushButton("RUN WHOLE SUITE")
        self.btn_run_all.setToolTip("python -m pytest tests - the PostgreSQL tests skip themselves when no server is running")
        self.btn_run_all.setStyleSheet(btn_style)
        self.btn_run_all.clicked.connect(self.run_all_tests)
        row.addWidget(self.btn_run_all)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet(btn_style)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_running_process)
        row.addWidget(self.btn_stop)
        t_layout.addLayout(row)

        layout.addWidget(test_group)

        # --- OUTPUT ---
        # Test runs used to be fire-and-forget: the file was run with plain
        # Python, which does not collect tests, in a window that closed as soon
        # as it finished. The output lands here now.
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QTextEdit {
                background-color: #0d0d0d; color: #00ff00;
                font-family: Consolas, monospace; font-size: 12px;
                border: 1px solid #333;
            }
        """)
        layout.addWidget(self.log_view, 1)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #666;")
        layout.addWidget(self.status_lbl)

    def populate_tests(self):
        tests_dir = os.path.join(self.root_dir, "tests")
        if os.path.exists(tests_dir):
            for f in sorted(os.listdir(tests_dir)):
                if f.startswith("test_") and f.endswith(".py"):
                    self.test_list.addItem(QListWidgetItem(f, self.test_list))

    # --- LAUNCHING ---
    def launch(self, script):
        """Start a product from source, detached from the console."""
        path = os.path.join(self.root_dir, script)
        if not os.path.exists(path):
            QMessageBox.warning(self, "Not found", f"{script} is not in this checkout.")
            return
        self.log(f"--- LAUNCH: {script} ---", "#00B4D8")
        env = dict(os.environ)
        env.setdefault("SLATE_ENABLE_EXR_LOADING", "1")
        env.setdefault("SLATE_ENABLE_OIIO", "1")
        subprocess.Popen([_python(), path], cwd=self.root_dir, env=env)

    def run_batch_mode(self):
        cmd = os.path.join(self.root_dir, "launchers", "launch_app.bat")
        self.log("--- LAUNCH: launchers/launch_app.bat ---", "#00B4D8")
        subprocess.Popen(cmd, cwd=self.root_dir, shell=True)

    # --- TESTS ---
    def run_selected_test(self):
        item = self.test_list.currentItem()
        if not item:
            return
        path = os.path.join("tests", item.text())
        self.start_process(
            f'"{_python()}" -m pytest "{path}" -q --no-cov -p no:cacheprovider',
            f"pytest {item.text()}")

    def run_all_tests(self):
        self.start_process(
            f'"{_python()}" -m pytest tests -q --no-cov -p no:cacheprovider',
            "pytest tests")

    def start_process(self, cmd, desc):
        if self.runner and self.runner.isRunning():
            QMessageBox.warning(self, "Busy", "A test run is already going.")
            return
        self.log(f"\n--- STARTING: {desc} ---\n", "#00B4D8")
        self.status_lbl.setText(f"Running: {desc}")
        self.btn_run_test.setEnabled(False)
        self.btn_run_all.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self.runner = ProcessRunner(cmd, cwd=self.root_dir)
        self.runner.log_output.connect(self.handle_output)
        self.runner.finished_code.connect(self.handle_finished)
        self.runner.error_occurred.connect(lambda err: self.handle_finished(-1, err))
        self.runner.start()

    def handle_output(self, text):
        lowered = text.lower()
        if "failed" in lowered or "error" in lowered:
            self.log(text, "#ff5555")
        elif "passed" in lowered:
            self.log(text, "#55ff55")
        else:
            self.log(text)

    def handle_finished(self, code, error=""):
        self.btn_run_test.setEnabled(True)
        self.btn_run_all.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if error:
            self.log(f"Process error: {error}", "#ff0000")
        if code == 0:
            self.log(f"\n[PASSED] exit code {code}", "#00ff00")
            self.status_lbl.setText("Tests passed")
        else:
            self.log(f"\n[FAILED] exit code {code}", "#ff0000")
            self.status_lbl.setText("Tests failed")

    def stop_running_process(self):
        if self.runner and self.runner.isRunning():
            self.log("\n[STOP] Terminating the test run...", "#ffaa00")
            self.runner.stop()
            self.runner.wait(5000)

    def log(self, text, color=None):
        if color:
            self.log_view.append(f'<span style="color:{color}">{text}</span>')
        else:
            self.log_view.append(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
