from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QTextEdit, QProgressBar, QLabel, QMessageBox, QGroupBox
)
from PySide6.QtCore import Qt
import os
import sys
from worker.process_runner import ProcessRunner


def _python():
    """
    The interpreter to build with, quoted for the shell.

    Every command here used to start with a bare "python". On a machine with
    only the portable runtime - which is every machine this ships to - that name
    resolves to the Windows Store alias stub, which prints "Python was not found"
    and exits 9009 without running anything. The console is itself running under
    the right interpreter, so sys.executable is the answer and needs no lookup.
    """
    return '"%s"' % sys.executable

class BuildTab(QWidget):
    """
    'The Forge' - Build Automation Interface.
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
        header = QLabel("DEPLOYMENT FORGE")
        header.setStyleSheet("color: #00B4D8; font-size: 18px; font-weight: bold; letter-spacing: 2px;")
        layout.addWidget(header)

        # --- VERSION INPUT ---
        # --- VERSION & TARGET INPUT ---
        ver_group = QGroupBox("Release Config")
        ver_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        v_layout = QHBoxLayout(ver_group)
        
        v_layout.addWidget(QLabel("Version Tag:"))
        from PySide6.QtWidgets import QLineEdit, QComboBox
        self.ver_input = QLineEdit(self._read_current_version())
        self.ver_input.setStyleSheet("background: #111; color: #00B4D8; border: 1px solid #444; padding: 5px; font-weight: bold;")
        v_layout.addWidget(self.ver_input)

        v_layout.addWidget(QLabel("Target Component:"))
        self.target_combo = QComboBox()
        self.target_combo.addItem("🌐 All Components (Full Suite)", "all")
        self.target_combo.addItem("🎬 Slate Studio", "vfx")
        self.target_combo.addItem("🏢 Slate Operations", "ops")
        self.target_combo.addItem("🖥️ Slate Central Server", "server")
        self.target_combo.setStyleSheet("background: #111; color: #00B4D8; border: 1px solid #444; padding: 5px; font-weight: bold;")
        v_layout.addWidget(self.target_combo)
        layout.addWidget(ver_group)

        # --- CONFIGURATION EDITOR ---
        config_group = QGroupBox("Runtime Configuration (Bundled)")
        config_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        config_layout = QVBoxLayout(config_group)
        
        self.config_editor = QTextEdit()
        self.config_editor.setStyleSheet("""
            QTextEdit {
                background-color: #111; color: #00ff00; 
                font-family: Consolas, monospace; font-size: 12px;
                border: 1px solid #333;
            }
        """)
        self.load_config() # Load existing config
        config_layout.addWidget(self.config_editor)
        layout.addWidget(config_group)

        # --- CONTROLS ---
        controls_group = QGroupBox("Operations")
        controls_group.setStyleSheet("QGroupBox { border: 1px solid #444; margin-top: 10px; } QGroupBox::title { color: #888; }")
        c_layout = QVBoxLayout(controls_group)
        
        # Style for Big Buttons
        btn_style = """
            QPushButton {
                background-color: #2b2b2b; color: #ddd; border: 1px solid #444;
                border-radius: 6px; padding: 12px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background-color: #3d3d3d; border: 1px solid #00B4D8; color: white; }
            QPushButton:pressed { background-color: #1a1a1a; }
            QPushButton:disabled { background-color: #1a1a1a; color: #555; border: 1px solid #222; }
        """
        
        self.btn_full = QPushButton("🚀 FULL BUILD PIPELINE")
        self.btn_full.setToolTip("Builds Exe + Inno Setup Installer for selected target")
        self.btn_full.setStyleSheet(btn_style + "QPushButton { border: 2px solid #00B4D8; background-color: #1a2a3a; }")
        self.btn_full.clicked.connect(self.run_full_build)
        
        self.btn_update = QPushButton("📦 BUILD UPDATE PACKAGE")
        self.btn_update.setToolTip("Builds and packages an update zip with SHA-256 validation.")
        self.btn_update.setStyleSheet(btn_style + "QPushButton { border: 2px solid #00D8B4; background-color: #1a3a2a; }")
        self.btn_update.clicked.connect(self.run_build_update)
        
        self.btn_vfx = QPushButton("🎬 BUILD VFX STUDIO")
        self.btn_vfx.setToolTip("Direct full build for Slate Studio (Exe + Setup)")
        self.btn_vfx.setStyleSheet(btn_style)
        self.btn_vfx.clicked.connect(lambda: self.run_component_build("vfx"))

        self.btn_ops = QPushButton("🏢 BUILD STUDIO OPS")
        self.btn_ops.setToolTip("Direct full build for Slate Operations (Exe + Setup)")
        self.btn_ops.setStyleSheet(btn_style)
        self.btn_ops.clicked.connect(lambda: self.run_component_build("ops"))

        self.btn_server = QPushButton("🖥️ BUILD SERVER")
        self.btn_server.setToolTip("Direct full build for Slate Central Server (Exe + Setup)")
        self.btn_server.setStyleSheet(btn_style)
        self.btn_server.clicked.connect(lambda: self.run_component_build("server"))

        self.btn_clean = QPushButton("🧹 CLEAN ARTIFACTS")
        self.btn_clean.setStyleSheet(btn_style)
        self.btn_clean.clicked.connect(self.run_clean)
        
        self.btn_exe = QPushButton("⚡ ONEDIR / EXE ONLY")
        self.btn_exe.setStyleSheet(btn_style)
        self.btn_exe.clicked.connect(self.run_build_exe)

        # Row 1: Pipeline & Update
        row1 = QHBoxLayout()
        row1.addWidget(self.btn_full, 1)
        row1.addWidget(self.btn_update, 1)
        c_layout.addLayout(row1)
        
        # Row 2: Specialized Components
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_vfx, 1)
        row2.addWidget(self.btn_ops, 1)
        row2.addWidget(self.btn_server, 1)
        c_layout.addLayout(row2)

        # Row 3: Dev Tools
        row3 = QHBoxLayout()
        row3.addWidget(self.btn_exe, 1)
        row3.addWidget(self.btn_clean, 1)
        c_layout.addLayout(row3)
        
        layout.addWidget(controls_group)
        
        # --- PROGRESS ---
        self.progress = QProgressBar()
        self.progress.setStyleSheet("""
            QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; background: #111; height: 10px;}
            QProgressBar::chunk { background-color: #00B4D8; }
        """)
        self.progress.setRange(0, 0) # Indeterminate initially
        self.progress.hide()
        layout.addWidget(self.progress)
        
        # --- TERMINAL ---
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QTextEdit {
                background-color: #0d0d0d; color: #00ff00; 
                font-family: Consolas, monospace; font-size: 12px;
                border: 1px solid #333;
            }
        """)
        layout.addWidget(self.log_view)
        
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet("color: #666;")
        layout.addWidget(self.status_lbl)

    def _read_current_version(self):
        """Read the current application version from __init__.py"""
        import re
        init_file = os.path.join(self.root_dir, 'slate', '__init__.py')
        if os.path.exists(init_file):
            try:
                with open(init_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                    if match:
                        return match.group(1)
            except Exception:
                pass
        return "0.0.0"

    def load_config(self):
        """Load from slate/default_config.json"""
        json_path = os.path.join(self.root_dir, 'slate', 'default_config.json')
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    self.config_editor.setText(f.read())
            except Exception as e:
                self.config_editor.setText(f"Error loading config: {e}")
        else:
            self.config_editor.setText("{}")

    def save_config(self):
        """Save to slate/default_config.json"""
        json_path = os.path.join(self.root_dir, 'slate', 'default_config.json')
        try:
            content = self.config_editor.toPlainText()
            # Basic validation
            import json
            json.loads(content) # Check valid JSON
            
            with open(json_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.log("[CONFIG] Saved updated configuration.", "#00ff00")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Invalid JSON", f"Configuration is not valid JSON:\n{e}")
            return False

    def log(self, text, color=None):
        if color:
            self.log_view.append(f'<span style="color:{color}">{text}</span>')
        else:
            self.log_view.append(text)
        # Auto scroll
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        
    def start_process(self, cmd, desc):
        if self.runner and self.runner.isRunning():
            QMessageBox.warning(self, "Busy", "A process is already running.")
            return

        self.log(f"\n--- STARTING: {desc} ---\n", "#00B4D8")
        self.status_lbl.setText(f"Running: {desc}")
        self.progress.show()
        self.set_buttons_enabled(False)
        
        self.runner = ProcessRunner(cmd, cwd=self.root_dir)
        self.runner.log_output.connect(self.handle_output)
        self.runner.finished_code.connect(self.handle_finished)
        self.runner.error_occurred.connect(self.handle_error)
        self.runner.start()

    def handle_output(self, text):
        # Basic highlighting
        if "error" in text.lower() or "failed" in text.lower():
            self.log(text, "#ff5555")
        elif "success" in text.lower() or "completed" in text.lower():
            self.log(text, "#55ff55")
        else:
            self.log(text)

    def handle_finished(self, code):
        self.progress.hide()
        self.set_buttons_enabled(True)
        if code == 0:
            self.log(f"\n[SUCCESS] Process finished with code {code}", "#00ff00")
            self.status_lbl.setText("Operation Successful")
        else:
            self.log(f"\n[FAILURE] Process failed with code {code}", "#ff0000")
            self.status_lbl.setText("Operation Failed")

    def handle_error(self, err):
        self.handle_finished(-1)
        self.log(f"Process Error: {err}", "#ff0000")

    def set_buttons_enabled(self, enabled):
        self.btn_clean.setEnabled(enabled)
        self.btn_full.setEnabled(enabled)
        self.btn_exe.setEnabled(enabled)
        self.btn_update.setEnabled(enabled)
        if hasattr(self, "btn_vfx"):
            self.btn_vfx.setEnabled(enabled)
        if hasattr(self, "btn_ops"):
            self.btn_ops.setEnabled(enabled)
        if hasattr(self, "btn_server"):
            self.btn_server.setEnabled(enabled)
        if hasattr(self, "target_combo"):
            self.target_combo.setEnabled(enabled)
        self.config_editor.setEnabled(enabled)

    def stop_running_process(self):
        """Stop active build command when console is closing."""
        if self.runner and self.runner.isRunning():
            self.log("\n[STOP] Closing console: terminating active build process...", "#ffaa00")
            self.runner.stop()
            self.runner.wait(5000)

    # --- ACTIONS ---
    def run_clean(self):
        # "rd" on a folder that is not there prints "The system cannot find the
        # file specified" and exits 2, so cleaning an already-clean tree was
        # reported as a failed operation. Nothing to delete is the success case.
        # A spec at the project root is a stray from running PyInstaller against
        # a script by hand; the pipeline refuses to build while one is there,
        # so cleaning takes it away too. The maintained specs are in deployment/.
        cmd = ('(if exist build rd /s /q build) & '
               '(if exist dist rd /s /q dist) & '
               '(if exist Slate_Server.spec del /q Slate_Server.spec) & '
               '(if exist Slate.spec del /q Slate.spec) & '
               'echo Build artifacts cleared.')
        self.start_process(cmd, "Clean Artifacts")

    def run_full_build(self):
        # Save Config First!
        if not self.save_config(): return
        
        ver = self.ver_input.text().strip()
        if not ver:
            QMessageBox.warning(self, "Version Required", "Please enter a version number.")
            return
        target = self.target_combo.currentData() if hasattr(self, "target_combo") else "all"
        cmd = f'{_python()} tools/build_pipeline.py --mode full --version "{ver}" --target {target}'
        self.start_process(cmd, f"Full Build Pipeline ({ver} - {target.upper()})")

    def run_component_build(self, target):
        # Save Config First!
        if not self.save_config(): return

        ver = self.ver_input.text().strip()
        if not ver:
            QMessageBox.warning(self, "Version Required", "Please enter a version number.")
            return
        cmd = f'{_python()} tools/build_pipeline.py --mode full --version "{ver}" --target {target}'
        self.start_process(cmd, f"Build {target.upper()} Suite Component ({ver})")

    def run_build_exe(self):
        # Save Config First!
        if not self.save_config(): return

        # Use onedir to build unified dist/Slate with all executables
        cmd = f'{_python()} tools/build_pipeline.py --mode onedir'
        self.start_process(cmd, "Build Executables (Onedir)")

    def run_build_update(self):
        # Save Config First!
        if not self.save_config(): return
        
        target = self.target_combo.currentData() if hasattr(self, "target_combo") else "all"
        cmd = f'{_python()} tools/build_update_package.py --target {target}'
        self.start_process(cmd, f"Build Update Package ({target.upper()})")
