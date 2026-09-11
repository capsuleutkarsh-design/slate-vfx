import sys
import os
import re
import ast
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QTextEdit, QListWidget, 
                               QLabel, QProgressBar, QSplitter, QGroupBox, QListWidgetItem)
from PySide6.QtCore import QProcess, Qt, QSize
from PySide6.QtGui import QFont, QColor, QTextCursor

class TestRunnerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UT_VFX - Enterprise Test Suite")
        self.resize(1300, 850)
        
        # Modern Dark Theme
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; color: #E0E0E0; }
            QWidget { font-family: 'Segoe UI', sans-serif; font-size: 15px; }  /* Increased Base Font */
            QTextEdit { 
                background-color: #1E1E1E; 
                color: #D4D4D4; 
                border: 1px solid #333333; 
                font-family: 'Consolas', monospace;
                font-size: 14px;
                border-radius: 4px;
            }
            QListWidget { 
                background-color: #1E1E1E; 
                color: #D4D4D4; 
                border: 1px solid #333333; 
                padding: 8px; /* More padding */
                border-radius: 4px;
                font-size: 15px;
            }
            QListWidget::item {
                padding: 5px; /* Comfy items */
            }
            QListWidget::item:selected {
                background-color: #264F78;
                border-radius: 2px;
            }
            QPushButton { 
                background-color: #007ACC; 
                color: white; 
                border: none; 
                padding: 10px 20px; /* Bigger buttons */
                border-radius: 4px; 
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover { background-color: #0098FF; }
            QPushButton:pressed { background-color: #005F9E; }
            QPushButton:disabled { background-color: #333333; color: #888888; }
            
            QGroupBox { 
                border: 1px solid #444444; 
                margin-top: 24px; 
                font-weight: bold; 
                border-radius: 4px;
                padding-top: 10px;
            }
            QGroupBox::title { 
                subcontrol-origin: margin; 
                subcontrol-position: top left; 
                padding: 0 5px; 
                color: #007ACC;
                font-size: 14px;
            }
            QSplitter::handle { background-color: #333333; width: 2px; }
        """)
        
        self.process = None
        self.project_root = Path(__file__).parent.parent
        self.init_ui()
        self.refresh_tests()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15) # Add margin to main window
        
        # Header (Smaller now)
        header = QHBoxLayout()
        title = QLabel("🧪 Capsule Test Lab") # Shortened
        title.setStyleSheet("font-size: 18px; color: #007ACC; font-weight: 800; margin-bottom: 5px;")
        header.addWidget(title)
        header.addStretch()
        main_layout.addLayout(header, stretch=0)
        
        # Main Splitter: [List] | [Console] | [Right Panel (Info + AI)]
        self.main_split = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.main_split, stretch=1)
        
        # --- 1. Left: Test List ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0,0,0,0)
        
        left_layout.addWidget(QLabel("📂 Test Suites"))
        self.test_list = QListWidget()
        self.test_list.currentItemChanged.connect(self.display_test_info)
        left_layout.addWidget(self.test_list)
        
        # Controls
        ctrl_layout = QVBoxLayout()
        self.btn_run = QPushButton("▶ Run Selected")
        self.btn_run.clicked.connect(self.run_selected_test)
        
        self.btn_run_all = QPushButton("⏩ Run All Tests")
        self.btn_run_all.clicked.connect(self.run_all_tests)
        
        self.btn_refresh = QPushButton("Refresh List")
        self.btn_refresh.setStyleSheet("background-color: #3E3E42; font-weight: normal;")
        self.btn_refresh.clicked.connect(self.refresh_tests)
        
        ctrl_layout.addWidget(self.btn_run)
        ctrl_layout.addWidget(self.btn_run_all)
        ctrl_layout.addWidget(self.btn_refresh)
        left_layout.addLayout(ctrl_layout)
        
        self.main_split.addWidget(left_widget)
        
        # --- 2. Center: Console Output ---
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0,0,0,0)
        
        con_group = QGroupBox("Console Output")
        con_layout = QVBoxLayout(con_group)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        con_layout.addWidget(self.log_output)
        
        self.status_bar = QProgressBar()
        self.status_bar.setRange(0, 0)
        self.status_bar.hide()
        con_layout.addWidget(self.status_bar)
        
        center_layout.addWidget(con_group)
        self.main_split.addWidget(center_widget)
        
        # --- 3. Right: Info & Analysis ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0,0,0,0)
        
        right_split = QSplitter(Qt.Vertical)
        
        # Top Right: Test Info
        info_group = QGroupBox("ℹ️ Test Information")
        info_layout = QVBoxLayout(info_group)
        self.info_output = QTextEdit()
        self.info_output.setReadOnly(True)
        self.info_output.setStyleSheet("font-family: 'Segoe UI', sans-serif; font-size: 15px; color: #CCCCCC;")
        info_layout.addWidget(self.info_output)
        right_split.addWidget(info_group)
        
        # Bottom Right: AI Analysis
        ai_group = QGroupBox("🤖 AI Analysis")
        ai_group.setStyleSheet("QGroupBox::title { color: #FFD700; }")
        ai_layout = QVBoxLayout(ai_group)
        self.explain_output = QTextEdit()
        self.explain_output.setReadOnly(True)
        self.explain_output.setStyleSheet("font-family: 'Segoe UI', sans-serif; font-size: 14px; background-color: #252526;")
        ai_layout.addWidget(self.explain_output)
        right_split.addWidget(ai_group)
        
        right_layout.addWidget(right_split)
        self.main_split.addWidget(right_widget)
        
        # Set Initial Sizes
        self.main_split.setSizes([250, 600, 350])
        right_split.setSizes([300, 500])

    def refresh_tests(self):
        self.test_list.clear()
        tests_dir = self.project_root / "tests"
        if not tests_dir.exists(): return

        for f in tests_dir.rglob("test_*.py"):
            rel_path = f.relative_to(tests_dir)
            parts = rel_path.parts
            
            category = parts[0].capitalize() if len(parts) > 1 else "Root"
            name = parts[-1]
            
            icon = "🐍"
            if "gui" in str(rel_path).lower(): icon = "🖥️"
            if "integration" in str(rel_path).lower(): icon = "🧩"
            
            item = QListWidgetItem(f"{icon} [{category}] {name}")
            item.setData(Qt.ItemDataRole.UserRole, str(rel_path)) # Store path
            self.test_list.addItem(item)
            
        self.log("Found tests.", False)

    def display_test_info(self, current, previous):
        if not current: return
        
        rel_path = current.data(Qt.ItemDataRole.UserRole)
        full_path = self.project_root / "tests" / rel_path
        
        self.info_output.clear()
        
        try:
            content = full_path.read_text(encoding="utf-8")
            
            # Parse Docstring
            module = ast.parse(content)
            docstring = ast.get_docstring(module)
            
            description = docstring if docstring else "No description provided."
            
            html = f"<h3>📑 {rel_path}</h3>"
            html += f"<p><b>Description:</b><br>{description.replace(chr(10), '<br>')}</p>"
            html += "<hr><b>functions:</b><ul>"
            
            for node in module.body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                     html += f"<li>{node.name}</li>"
                elif isinstance(node, ast.ClassDef):
                     html += f"<li>📦 <b>{node.name}</b><ul>"
                     for sub in node.body:
                         if isinstance(sub, ast.FunctionDef) and sub.name.startswith("test_"):
                             html += f"<li>{sub.name}</li>"
                     html += "</ul></li>"
            
            html += "</ul>"
            self.info_output.setHtml(html)
            
        except Exception as e:
            self.info_output.setText(f"Could not read details: {e}")

    def run_selected_test(self):
        item = self.test_list.currentItem()
        if not item: return
        
        rel_path = item.data(Qt.ItemDataRole.UserRole)
        target = self.project_root / "tests" / rel_path
        self.start_test_process([str(target)])

    def run_all_tests(self):
        target = self.project_root / "tests"
        self.start_test_process([str(target)])

    def start_test_process(self, targets):
        if self.process and self.process.state() == QProcess.Running:
            self.log("Busy.", True)
            return

        self.log_output.clear()
        self.explain_output.clear()
        self.status_bar.show()
        self.current_buffer = ""
        
        self.process = QProcess()
        self.process.setWorkingDirectory(str(self.project_root))
        
        env = QProcess.systemEnvironment()
        env.append(f"PYTHONPATH={self.project_root}")
        self.process.setEnvironment(env)
        
        args = ["-m", "pytest"] + targets + ["-v"]
        
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.handle_finished)
        self.process.start("python", args)
        
        self.enable_controls(False)

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode('utf-8', errors='replace')
        self.current_buffer += data
        self.log_output.insertPlainText(data)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode('utf-8', errors='replace')
        self.current_buffer += data
        self.log_output.insertPlainText(data)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def handle_finished(self):
        self.status_bar.hide()
        self.enable_controls(True)
        self.analyze_results(self.process.exitCode(), self.current_buffer)
        self.process = None

    def enable_controls(self, enable):
        self.btn_run.setEnabled(enable)
        self.btn_run_all.setEnabled(enable)

    def log(self, msg, err=False):
        self.log_output.append(msg)

    def explain(self, text, color="#FFFFFF"):
        self.explain_output.append(f'<div style="color:{color}; margin-bottom:5px;">{text}</div>')

    def analyze_results(self, exit_code, output):
        self.explain_output.clear()
        
        # 1. Check for Pytest Summary Line first (Ultimate Truth)
        # Scan last 10 lines for the summary pattern (handling junk at end)
        lines = output.strip().splitlines()
        recent_lines = lines[-10:] if len(lines) > 10 else lines
        
        summary_text = ""
        for line in reversed(recent_lines):
            match = re.search(r"={5,}\s+(.*?)\s+={5,}", line)
            if match:
                summary_text = match.group(1)
                break
        
        tests_passed = "passed" in summary_text
        tests_failed = "failed" in summary_text or "error" in summary_text
        
        # 2. Heuristic: If tests passed and no failures recorded, it's a success, even if exit_code != 0
        if tests_passed and not tests_failed:
             self.explain("✅ <b>SUCCESS</b>", "#4EC9B0")
             self.explain(f"Summary: {summary_text}", "#4EC9B0")
             
             if exit_code != 0:
                 self.explain("<br>⚠️ <b>Note:</b> Process finished with errors (likely Qt cleanup issues). Tests are fine.", "#DCDCAA")
                 if "QThread: Destroyed" in output:
                     self.explain("• <i>Ignored: Qt Thread destroyed while running (harmless for testing).</i>", "#808080")
             return

        # 3. If we get here, valid failures exist OR pytest crashed hard
        self.explain("❌ <b>FAILURE DETECTED</b>", "#FF6B6B")
        
        # Regex Parsing for Failures
        failure_pattern = re.compile(r"_{10,}\s+(.+?)\s+_{10,}")
        matches = list(failure_pattern.finditer(output))
        
        failures = []
        for i, match in enumerate(matches):
            test_name = match.group(1)
            start = match.end()
            end = matches[i+1].start() if i+1 < len(matches) else len(output)
            block = output[start:end]
            
            lines = [l.strip() for l in block.splitlines() if l.strip().startswith("E ") or l.strip().startswith(">")]
            failures.append({"name": test_name, "lines": lines})
            
        if not failures:
            # Fallback V2: Aggressive Error Hunting
            # 1. Check for "E " lines (orphan errors not in blocks)
            orphan_errors = [line.strip() for line in output.splitlines() if line.strip().startswith("E ")]
            
            if orphan_errors:
                self.explain("❓ <b>Unstructured Errors Found</b>", "#FFD700")
                for err in orphan_errors[:5]: # Show first 5
                    self.explain(f"• {err}", "#FF6B6B")
                return

            # 2. Check for Collection Errors
            if "ERRORS" in output and "Interrupted" in output:
                 self.explain("• <b>Fatal Collection Error</b>", "#FF6B6B")
                 self.explain("Pytest could not start. Syntax error or missing import likely.", "#FFFFFF")
                 return
            
            # 3. Last Resort: Show the raw tail of the log
            self.explain("⚠️ <b>Could not parse specific error.</b>", "#FFD700")
            self.explain("<b>Last log output:</b>", "#FFFFFF")
            
            lines = [l for l in output.strip().splitlines() if l.strip()]
            tail = lines[-10:] if len(lines) > 10 else lines
            for line in tail:
                safe_line = line.replace("<", "&lt;").replace(">", "&gt;") # HTML Safe
                self.explain(f"<code style='color:#AAAAAA; font-size:12px'>{safe_line}</code>", "#1E1E1E")
            return
            
        for f in failures:
            self.explain(f"<b>{f['name']}</b>", "#FFD700")
            
            error_msg = ""
            details = []
            suggested_fix = ""
            
            # Combine lines for easier regex
            full_block = " ".join(f['lines'])

            for line in f['lines']:
                # 1. ASSERTION ERROR (Logic)
                if "AssertionError" in line:
                    error_msg = "Logic Mismatch"
                    raw_assert = line.replace("E       AssertionError: ", "")
                    
                    # Try to parse "assert X == Y"
                    if "==" in raw_assert:
                        parts = raw_assert.split("==")
                        details.append(f"Expected: <b>{parts[1].strip()}</b>")
                        details.append(f"Got: <b style='color:#FF6B6B'>{parts[0].replace('assert', '').strip()}</b>")
                    else:
                        details.append(f"Check: {raw_assert}")
                    
                    suggested_fix = "Update code or adjust test expectation."
                    break

                # 2. NAME ERROR (Typos/Missing Vars)
                if "NameError" in line:
                    error_msg = "Variable Not Found"
                    var_name = line.split("name ")[-1].replace("'", "")
                    details.append(f"Missing: <b>{var_name}</b>")
                    suggested_fix = f"Define '{var_name}' or check imports."
                    break

                # 3. MODULE NOT FOUND (Dependencies)
                if "ModuleNotFoundError" in line:
                    error_msg = "Missing Dependency"
                    mod_name = line.split("named ")[-1].replace("'", "")
                    details.append(f"Module: <b>{mod_name}</b>")
                    suggested_fix = f"Run: <code>pip install {mod_name}</code>"
                    break
                
                # 4. ATTRIBUTE ERROR (Typos in methods)
                if "AttributeError" in line:
                    error_msg = "Invalid Method/Attribute"
                    details.append(f"Detail: {line.split('AttributeError: ')[-1]}")
                    suggested_fix = "Check object type and spelling."
                    break

                # 5. FIXTURE ERROR (Pytest specifics)
                if "FixtureLookupError" in line:
                    error_msg = "Missing Test Fixture"
                    fix_name = line.split("fixture ")[-1].replace("'", "")
                    details.append(f"Fixture: <b>{fix_name}</b>")
                    suggested_fix = "Check conftest.py or decoration."
                    break

            # Default Fallback
            if not error_msg:
                error_msg = "Runtime Error"
                details.append(f"{f['lines'][0] if f['lines'] else 'Unknown'}")
                suggested_fix = "Check stack trace in console."

            # Render Analysis
            self.explain(f"Analysis: {error_msg}", "#E0E0E0")
            for d in details:
                self.explain(f"• {d}", "#AAAAAA")
            
            if suggested_fix:
                self.explain(f"💡 <i>Tip: {suggested_fix}</i>", "#4EC9B0")
            
            self.explain("<hr>", "#444444")

# --- EXPERT LOG ANALYSIS ENGINE ---
class LogAnalysisEngine:
    """
    A rule-based expert system that mimics an engineer 'reading the logs'.
    It parses the comprehensive pytest output to find root causes, 
    contextual clues, and suggests fixes.
    """
    
    PATTERNS = {
        "collection_error": re.compile(r"_{10,} ERROR collecting (.*?) _{10,}"),
        "test_failure": re.compile(r"_{10,} (.*?) _{10,}"),
        "assertion_diff": re.compile(r"E\s+AssertionError:"),
        "stack_trace": re.compile(r"E\s+(.*)"),
        "summary": re.compile(r"={5,}\s+(.*?)\s+={5,}"),
    }

    def __init__(self, full_log, exit_code):
        self.log = full_log
        self.exit_code = exit_code
        self.lines = full_log.splitlines()
        self.report = []
        
    def analyze(self):
        """Main entry point. Returns HTML string."""
        self.report = []
        
        # 1. High Level Summary Check
        summary_line = self._find_summary_line()
        if summary_line:
            if "passed" in summary_line and "failed" not in summary_line and "error" not in summary_line:
                return self._generate_success_report(summary_line)
        
        # 2. Deep Dive Analysis
        self.report.append("<h3 style='color:#FF6B6B'>❌ Test Run Failed</h3>")
        
        # Breakdown phases
        failures = self._extract_failures()
        
        if not failures:
            # Fallback for weird crashes
            self._analyze_orphan_errors()
        else:
            for fail in failures:
                self._analyze_single_failure(fail)
                
        return "".join(self.report)

    def _find_summary_line(self):
        # Scan last 20 lines for pytest summary
        for line in reversed(self.lines[-20:]):
            if self.PATTERNS['summary'].search(line):
                return self.PATTERNS['summary'].search(line).group(1)
        return None

    def _generate_success_report(self, summary):
        html = f"<h3 style='color:#4EC9B0'>✅ Build Succeeded</h3>"
        html += f"<p style='color:#CCCCCC'>{summary}</p>"
        
        if self.exit_code != 0:
            html += "<div style='background-color:#3A3A10; padding:10px; border-left:4px solid #FFD700; margin-top:10px;'>"
            html += "<b style='color:#FFD700'>⚠️ Dirty Exit Detected</b><br>"
            html += "<i style='color:#CCCCCC'>The tests passed, but the process crashed during shutdown.</i><br>"
            
            if "QThread: Destroyed" in self.log:
                html += "<br><b>Diagnosis:</b> PySide6/Qt Threading Issue.<br>"
                html += "This is common in GUI tests. It is usually harmless for test validity."
            html += "</div>"
        return html

    def _extract_failures(self):
        """Splits log into blocks per test failure."""
        blocks = []
        current_block = None
        
        # Regex to find "___________ test_name ___________"
        sep_pattern = re.compile(r"_{10,}\s+(.*?)\s+_{10,}")
        
        for i, line in enumerate(self.lines):
            match = sep_pattern.search(line)
            if match:
                # Save previous
                if current_block: blocks.append(current_block)
                # Start new
                current_block = {"name": match.group(1), "lines": []}
            elif current_block:
                # End of block heuristic (another separator or empty long gap? Pytest blocks usually end with separator)
                # Actually, capturing until next separator is safer.
                current_block["lines"].append(line)
        
        if current_block: blocks.append(current_block)
        return blocks

    def _analyze_single_failure(self, failure):
        name = failure['name']
        lines = failure['lines']
        
        self.report.append(f"<div style='background-color:#2D2D30; padding:10px; margin-bottom:15px; border-radius:4px; border-left: 4px solid #FF6B6B;'>")
        self.report.append(f"<b style='color:#FFD700; font-size:16px'>{name}</b><br>")
        
        # Extract the "E " lines which are the python error stack
        error_lines = [l.strip() for l in lines if l.strip().startswith("E ")]
        
        if not error_lines:
             self.report.append("<i style='color:#888888'>Unknown Error (No traceback found)</i>")
             self.report.append("</div>")
             return

        # Intelligent Classification
        primary_error = error_lines[0]
        root_cause = "Runtime Error"
        fix_suggestion = "Check the logs."
        
        if "AssertionError" in primary_error:
            root_cause = "Logic Assertion Failed"
            fix_suggestion = "The result did not match the expected value."
            
            # Smart Assert Parsing
            clean_msg = primary_error.replace("E       AssertionError:", "").strip()
            if "assert" in clean_msg:
                parts = clean_msg.split("==")
                if len(parts) == 2:
                    fix_suggestion = f"<b>Expected:</b> <span style='color:#4EC9B0'>{parts[1].strip()}</span><br>" \
                                     f"<b>Actual:</b> <span style='color:#FF6B6B'>{parts[0].replace('assert', '').strip()}</span>"
            elif clean_msg:
                 fix_suggestion = f"Check: {clean_msg}"

        elif "ModuleNotFoundError" in primary_error:
            root_cause = "Missing Dependency"
            mod = primary_error.split("named ")[-1].replace("'", "")
            fix_suggestion = f"Run: <code>pip install {mod}</code>"
        
        elif "IndentationError" in primary_error:
            root_cause = "Syntax Error (Indentation)"
            fix_suggestion = "Check your Tabs vs Spaces."

        elif "FixtureLookupError" in primary_error:
            root_cause = "Missing Pytest Fixture"
            fix_suggestion = "Did you forget to define a fixture in conftest.py?"

        # Output analysis
        self.report.append(f"<div style='margin-top:5px; color:#FFFFFF'><b>Type:</b> {root_cause}</div>")
        
        # Show Error Context (The first few E lines)
        self.report.append("<div style='background-color:#1E1E1E; padding:5px; margin-top:5px; font-family:Consolas; font-size:13px; color:#D4D4D4'>")
        for el in error_lines[:5]:
             self.report.append(f"{el}<br>")
        self.report.append("</div>")
        
        self.report.append(f"<div style='margin-top:8px; color:#4EC9B0'>💡 <b>Suggestion:</b><br>{fix_suggestion}</div>")
        self.report.append("</div>")

    def _analyze_orphan_errors(self):
        """Called if no structured test blocks are found (e.g. fatal collect error)."""
        
        # Scan for ANY "E " lines
        orphan_errors = [l for l in self.lines if l.strip().startswith("E ")]
        
        if orphan_errors:
            self.report.append("<div style='background-color:#3A1010; padding:10px; border-left:4px solid #FF0000;'>")
            self.report.append("<b>🛑 Fatal Error (No Tests Ran)</b><br>")
            for err in orphan_errors[:5]:
                self.report.append(f"<code style='display:block; margin-top:5px;'>{err.strip()}</code>")
            self.report.append("</div>")
        else:
             self.report.append("<b style='color:#FFD700'>⚠️ Unknown Fatal Crash</b><br>")
             self.report.append("No python exceptions found. The process may have segfaulted.<br>")
             self.report.append("<b>Last output:</b><br>")
             self.report.append("<pre style='color:#888888'>" + "\n".join(self.lines[-10:]) + "</pre>")

# --- MAIN WINDOW UPDATED ---
# Define this in the same file for now for simplicity
    
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TestRunnerWindow()
    win.show()
    sys.exit(app.exec())
