from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QLabel, QGroupBox, QListWidget, QListWidgetItem
)
import os
from worker.process_runner import ProcessRunner

class TestTab(QWidget):
    """
    'The Lab' - Testing Interface.
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
        launch_group = QGroupBox("Runtime Environment")
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
        
        self.btn_run_py = QPushButton("▶ QUICK LAUNCH (Python Mode)")
        self.btn_run_py.setToolTip("Run python -m ut_vfx.main")
        self.btn_run_py.setStyleSheet(btn_style)
        self.btn_run_py.clicked.connect(self.run_python_mode)
        
        self.btn_run_bat = QPushButton("🚀 LAUNCHER (Batch Mode)")
        self.btn_run_bat.setToolTip("Run launch_app.bat")
        self.btn_run_bat.setStyleSheet(btn_style)
        self.btn_run_bat.clicked.connect(self.run_batch_mode)
        
        l_layout.addWidget(self.btn_run_py)
        l_layout.addWidget(self.btn_run_bat)
        layout.addWidget(launch_group)
        
        # --- UNIT TESTS ---
        test_group = QGroupBox("Verification Scripts")
        test_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        t_layout = QVBoxLayout(test_group)
        
        self.test_list = QListWidget()
        self.test_list.setStyleSheet("background: #111; color: #ddd; border: 1px solid #333;")
        t_layout.addWidget(self.test_list)
        
        # Populate tests
        self.populate_tests()
        
        run_test_btn = QPushButton("RUN SELECTED TEST")
        run_test_btn.setStyleSheet(btn_style)
        run_test_btn.clicked.connect(self.run_selected_test)
        t_layout.addWidget(run_test_btn)
        
        layout.addWidget(test_group)
        
        # Output is shared in Build Tab for now, or we can add small output here.
        # For simplicity, we just leverage `print` which goes to console or spawn new window.
        
    def populate_tests(self):
        # Scan tests folder
        tests_dir = os.path.join(self.root_dir, "tests")
        if os.path.exists(tests_dir):
            for f in os.listdir(tests_dir):
                if f.startswith("test_") and f.endswith(".py"):
                    self.test_list.addItem(QListWidgetItem(f, self.test_list))

        # Scan experiments
        exp_dir = os.path.join(self.root_dir, "tools", "experiments")
        if os.path.exists(exp_dir):
            for f in os.listdir(exp_dir):
                if f.endswith(".py"):
                    self.test_list.addItem(QListWidgetItem(f"EXP: {f}", self.test_list))

    def run_python_mode(self):
        # python -m ut_vfx.main
        cmd = "python -m ut_vfx.main"
        self.start_process(cmd)

    def run_batch_mode(self):
        cmd = "launch_app.bat"
        self.start_process(cmd)

    def run_selected_test(self):
        item = self.test_list.currentItem()
        if not item: return
        
        txt = item.text()
        if txt.startswith("EXP: "):
            # Run experiment
            fname = txt.replace("EXP: ", "")
            path = os.path.join("tools", "experiments", fname)
            cmd = f"python {path}"
        else:
            # Run Test (basic)
            path = os.path.join("tests", txt)
            cmd = f"python {path}"
            
        self.start_process(cmd)
        
    def start_process(self, cmd):
        # Simple fire and forget for UI tests, or launch in new console
        import subprocess
        # For GUI tests, we want them to pop up separate from this tool
        subprocess.Popen(cmd, cwd=self.root_dir, shell=True)
