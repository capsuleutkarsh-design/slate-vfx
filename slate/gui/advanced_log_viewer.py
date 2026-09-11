import os
import logging
import time
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, 
    QListWidgetItem, QPushButton, QLabel, QLineEdit,
    QComboBox, QTabWidget, QHeaderView, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QCheckBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from ..core.infra.global_config import GlobalConfig
from ..core.infra.database_manager import DatabaseManager
from ..core.infra.app_context import AppContext


# --- LOG PARSER UTILS ---
class LogInterpreter:
    """
    Translates technical error messages into user-friendly explanations.
    """
    RULES = [
        ("PermissionError", "[LOCK] Access Denied. Check folder permissions."),
        ("FileNotFoundError", "[ERR] File missing. It may have been moved or deleted."),
        ("Disk Full", "[DISK] Disk is full. Please clear space."),
        ("ConnectionRefused", "[CONN] Server refused connection. Is it online?"),
        ("TimeoutError", "[TIME] Operation timed out. Network might be slow."),
        ("KeyError", "[KEY] Internal Logic Error (Missing Data Field)."),
        ("ValueError", "[NUM] Invalid Data Format."),
        ("ImportError", "[PKG] Missing Software Component."),
        ("Warning", "[WARN] Cautionary message."),
        ("Critical", "[FIRE] Serious System Failure."),
    ]

    @staticmethod
    def explain(message):
        for keyword, explanation in LogInterpreter.RULES:
            if keyword.lower() in message.lower():
                return explanation
        return "ℹ️ System Info"

class SystemLogViewer(QWidget):
    """
    Viewer for Remote System Logs located in X:/.../Logs/
    Features: Parsing, Filtering by Date, and 'Simple Language' explanation.
    """
    def __init__(self):
        super().__init__()
        try:
            self.log_root = GlobalConfig.server_root() / "Logs"
        except Exception:
            self.log_root = Path(os.environ.get("SLATE_STUDIO_ROOT", str(Path.home() / "RuntimeData" / "UT_Central"))) / "Logs" # Fallback
            
        self.current_file = None
        self.cached_lines = []
        self._is_closing = False
        self._is_cleaned = False
        self.setup_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.auto_refresh)
        self.refresh_timer.start(5000) # Auto-refresh active log

    def setup_ui(self):
        layout = QHBoxLayout(self)
        
        # Left: File List
        left = QWidget()
        v = QVBoxLayout(left); v.setContentsMargins(0,0,0,0)
        v.addWidget(QLabel("<b>Workstations</b>"))
        
        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self.load_log_file)
        self.list_widget.setStyleSheet("QListWidget { background: #16161A; border: 1px solid #26262D; } QListWidget::item:selected { background: #3EA8BF; color: black; }")
        v.addWidget(self.list_widget)
        
        btn_refresh_list = QPushButton("Refresh List")
        btn_refresh_list.clicked.connect(self.refresh_list)
        btn_refresh_list.setStyleSheet("background: #1D1D22; padding: 5px;")
        v.addWidget(btn_refresh_list)
        
        # Right: Log Content (Table)
        right = QWidget()
        v2 = QVBoxLayout(right); v2.setContentsMargins(0,0,0,0)
        
        # Header / Filters
        h_filter = QHBoxLayout()
        self.lbl_viewing = QLabel("Select a machine to view logs")
        self.lbl_viewing.setStyleSheet("color: #3EA8BF; font-weight: bold;")
        h_filter.addWidget(self.lbl_viewing)
        
        h_filter.addStretch()
        
        self.date_filter = QComboBox()
        self.date_filter.addItem("All Dates")
        self.date_filter.currentTextChanged.connect(self.apply_date_filter)
        h_filter.addWidget(QLabel("Filter Date:"))
        h_filter.addWidget(self.date_filter)
        
        self.chk_explain = QCheckBox("Simple Mode")
        self.chk_explain.setChecked(True)
        self.chk_explain.stateChanged.connect(self.toggle_explanation_column)
        h_filter.addWidget(self.chk_explain)
        
        v2.addLayout(h_filter)
        
        # Table View
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Level", "Message", "Explanation (Simple)"])
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch) # Message stretches
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch) # Explanation stretches
        self.log_table.setColumnWidth(0, 140) # Time
        self.log_table.setColumnWidth(1, 80)  # Level
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setStyleSheet("""
            QTableWidget { background: #0D0D0F; border: none; gridline-color: #26262D; color: #E8E6E1; font-family: Consolas; font-size: 11px; }
            QHeaderView::section { background: #1D1D22; padding: 4px; font-family: Segoe UI; }
        """)
        self.log_table.verticalHeader().setVisible(False)
        v2.addWidget(self.log_table)
        
        # Warning Label for Truncation
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color: #87857F; font-style: italic; font-size: 10px;")
        v2.addWidget(self.lbl_info)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 4)
        
        layout.addWidget(splitter)
        
        self.refresh_list()

    def refresh_list(self):
        curr_row = self.list_widget.currentRow()
        self.list_widget.clear()
        if not self.log_root.exists():
            self.list_widget.addItem("Log Folder Not Found")
            return

        files = sorted(list(self.log_root.glob("*.log")), key=lambda f: f.stat().st_mtime, reverse=True)
        now = time.time()
        
        for f in files:
            name = f.stem
            mod_time = f.stat().st_mtime
            age = now - mod_time
            
            if age < 60: status = "live"
            elif age < 300: status = "recent"
            else: status = "stale"
            
            item = QListWidgetItem(f"{status} {name}")
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(f"Last updated: {time.ctime(mod_time)}")
            self.list_widget.addItem(item)
            
        if curr_row >= 0 and curr_row < self.list_widget.count():
            self.list_widget.setCurrentRow(curr_row)

    def load_log_file(self, item):
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str: return
        self.current_file = Path(path_str)
        self.lbl_viewing.setText(f"Viewing: {self.current_file.name}")
        self.read_file()

    def auto_refresh(self):
        if self._is_closing:
            return
        if self.current_file and self.isVisible():
            # Optimize: check mtime before reading?
            # For now just re-read to keep it simple, parsing is fast enough for <1MB text
            self.read_file(auto=True)

    LOG_PATTERN = None

    def read_file(self, auto=False):
        try:
            if self._is_closing:
                return
            if not self.current_file.exists(): return
            
            # 1. Read File (Up to 500KB - Reduced from 1MB for speed)
            size = self.current_file.stat().st_size
            limit = 500 * 1024 # 500KB Limit (~5000 lines)
            
            truncated = False
            with open(self.current_file, 'r', encoding='utf-8', errors='replace') as f:
                if size > limit:
                    f.seek(size - limit)
                    content = f.read()
                    # Discard partial first line
                    content = content.partition('\n')[2]
                    truncated = True
                else:
                    content = f.read()

            if truncated:
                self.lbl_info.setText(f"Showing last 500KB of log ({size/1024/1024:.2f} MB total).")
            else:
                self.lbl_info.setText("Showing Full Log")

            # 2. Parse Lines
            lines = content.splitlines()
            parsed_data = []
            dates = set()
            
            # Compile Regex once if needed
            import re
            if not self.LOG_PATTERN:
                 self.LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[,.]\d+\s+(.*)$')
            
            for line in lines:
                match = self.LOG_PATTERN.match(line)
                if match:
                    date_str, time_str, remainder = match.groups()
                    dates.add(date_str)
                    
                    # Extract Level
                    level = "INFO"
                    msg = remainder
                    
                    level_match = re.search(r'\[(INFO|ERROR|WARN|WARNING|CRITICAL|FATAL|DEBUG)\]', remainder)
                    if level_match:
                        level = level_match.group(1)
                    
                    if "WARN" in level: level = "WARN"
                    elif "CRITICAL" in level: level = "FATAL"
                    
                    parsed_data.append({
                        "date": date_str,
                        "time": time_str,
                        "level": level,
                        "msg": msg,
                        "explanation": LogInterpreter.explain(msg)
                    })
                else:
                    if parsed_data:
                        parsed_data[-1]["msg"] += f"\n{line}"
                    else:
                        parsed_data.append({
                            "date": "Unknown", "time": "--:--", "level": "RAW", "msg": line, "explanation": ""
                        })
            
            # UI SAFETY LIMIT: Keep only last 2000 rows
            if len(parsed_data) > 2000:
                parsed_data = parsed_data[-2000:]
            
            self.cached_lines = parsed_data
            
            # 3. Update Date Filter
            current_filter = self.date_filter.currentText()
            self.date_filter.blockSignals(True)
            self.date_filter.clear()
            self.date_filter.addItem("All Dates")
            self.date_filter.addItems(sorted(list(dates), reverse=True))
            
            if current_filter in dates:
                self.date_filter.setCurrentText(current_filter)
            self.date_filter.blockSignals(False)
            
            # 4. Populate Table
            self.populate_table()
            
            if auto:
                self.log_table.scrollToBottom()
                
        except Exception as e:
            self.lbl_info.setText(f"Error reading file: {e}")

    def apply_date_filter(self, date_text):
        self.populate_table()

    def toggle_explanation_column(self, state):
        self.log_table.setColumnHidden(3, not state)

    def populate_table(self):
        self.log_table.setRowCount(0)
        filter_date = self.date_filter.currentText()
        
        rows_to_show = []
        if filter_date == "All Dates":
            rows_to_show = self.cached_lines
        else:
            rows_to_show = [row for row in self.cached_lines if row['date'] == filter_date]
            
        self.log_table.setRowCount(len(rows_to_show))
        
        for r, row in enumerate(rows_to_show):
            # Time
            t_item = QTableWidgetItem(f"{row['date']} {row['time']}")
            self.log_table.setItem(r, 0, t_item)
            
            # Level
            l_item = QTableWidgetItem(row['level'])
            l_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if row['level'] == "ERROR": l_item.setForeground(QColor("#D9635F"))
            elif row['level'] == "WARN": l_item.setForeground(QColor("#D9A441"))
            elif row['level'] == "INFO": l_item.setForeground(QColor("#5FBF8F"))
            self.log_table.setItem(r, 1, l_item)
            
            # Message
            m_item = QTableWidgetItem(row['msg'])
            m_item.setToolTip(row['msg']) # Tooltip for long messages
            self.log_table.setItem(r, 2, m_item)
            
            # Explanation
            e_item = QTableWidgetItem(row['explanation'])
            if row['explanation'] and row['explanation'] != "ℹ️ System Info":
                 e_item.setForeground(QColor("#3EA8BF"))
            self.log_table.setItem(r, 3, e_item)

        self.toggle_explanation_column(self.chk_explain.isChecked())

    def cleanup_resources(self):
        """Stop periodic refresh and clear transient state."""
        if self._is_cleaned:
            return

        self._is_closing = True
        if hasattr(self, "refresh_timer") and self.refresh_timer.isActive():
            self.refresh_timer.stop()
        self.current_file = None
        self.cached_lines = []
        self._is_cleaned = True

    def closeEvent(self, event):
        self.cleanup_resources()
        super().closeEvent(event)



class AuditInterpreter:
    """
    Translates database actions into user-friendly summaries.
    """
    @staticmethod
    def analyze(action, field, old, new):
        if action == "CREATE":
            return "New Item Created"
        if action == "UPDATE":
            if field == "status":
                if "Approv" in new: return "Approved"
                if "Review" in new: return "Needs Review"
                if "Progress" in new: return "In Progress"
                return "Status Change"
            if field == "priority":
                return "Priority Update"
            if field == "access_level":
                return "Security Change"
            
        return f"{action.title()} {field if field else ''}".strip()

class DatabaseAuditViewer(QWidget):
    """
    Viewer for 'change_history' table in SQLite.
    Features: Date Filtering and Simple Interpretation.
    """
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.full_history = []
        self.setup_ui()
        
    def setup_ui(self):
         layout = QVBoxLayout(self)
         
         # Filters
         h = QHBoxLayout()
         self.search = QLineEdit(); self.search.setPlaceholderText("Search User, Project or Action..."); self.search.setStyleSheet("padding:6px; background:#1D1D22; color:white; border:1px solid #2C2C34; border-radius:4px;")
         self.search.textChanged.connect(self.filter_table)
         
         self.date_filter = QComboBox()
         self.date_filter.addItem("All Dates")
         self.date_filter.currentTextChanged.connect(self.apply_date_filter)
         self.date_filter.setStyleSheet("padding:6px; background:#1D1D22; color:white; border:1px solid #2C2C34; border-radius:4px;")

         self.chk_explain = QCheckBox("Simple Mode")
         self.chk_explain.setChecked(True)
         self.chk_explain.stateChanged.connect(self.toggle_explanation_column)

         btn_refresh = QPushButton("Refresh DB")
         btn_refresh.clicked.connect(self.refresh_data)
         btn_refresh.setStyleSheet("padding:6px; background:#3EA8BF; color:black; font-weight:bold; border-radius:4px;")
         
         h.addWidget(self.search)
         h.addWidget(QLabel("Filter Date:"))
         h.addWidget(self.date_filter)
         h.addWidget(self.chk_explain)
         h.addWidget(btn_refresh)
         layout.addLayout(h)
         
         # Table
         self.table = QTableWidget()
         self.table.setColumnCount(6)
         self.table.setHorizontalHeaderLabels(["Time", "User", "Project", "Action", "Description", "Analysis (Simple)"])
         self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch) # Desc stretches
         self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch) # Analysis stretches
         self.table.setColumnWidth(0, 140)
         self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
         self.table.setStyleSheet("QTableWidget { background: #16161A; border:none; gridline-color: #26262D; color: #E8E6E1; } QHeaderView::section { background: #1D1D22; padding: 4px; }")
         self.table.verticalHeader().setVisible(False)
         layout.addWidget(self.table)
         
         self.refresh_data()

    def refresh_data(self):
        try:
            # Increase limit for better history visibility
            if hasattr(self.db_manager, 'get_history'):
                rows = self.db_manager.get_history(limit=2000)
            else:
                rows = [] # Fallback
            
            # Cache data
            self.full_history = []
            dates = set()
            
            for row in rows:
                r = dict(row)
                # Parse date for filter
                ts = r.get('timestamp','')
                if ts:
                    if isinstance(ts, datetime):
                        r['timestamp'] = ts.strftime("%Y-%m-%d %H:%M:%S")
                        date_str = ts.date().isoformat()
                    else:
                        ts_text = str(ts)
                        r['timestamp'] = ts_text
                        # ISO format YYYY-MM-DD...
                        date_str = ts_text.split("T")[0] if "T" in ts_text else ts_text.split(" ")[0]
                    dates.add(date_str)
                    r['_date'] = date_str
                else:
                    r['_date'] = "Unknown"
                
                # Pre-calculate Analysis
                action = r.get('action_type') or r.get('entity_type')
                field = r.get('field_changed')
                old = r.get('old_value')
                new = r.get('new_value')
                r['_analysis'] = AuditInterpreter.analyze(action, field, old, new)
                
                self.full_history.append(r)
                
            # Update Date Filter
            curr_date = self.date_filter.currentText()
            self.date_filter.blockSignals(True)
            self.date_filter.clear()
            self.date_filter.addItem("All Dates")
            self.date_filter.addItems(sorted(list(dates), reverse=True))
            if curr_date in dates:
                self.date_filter.setCurrentText(curr_date)
            self.date_filter.blockSignals(False)
            
            self.populate_table()
                
        except Exception as e:
            logging.exception(f"Audit load error: {e}")

    def apply_date_filter(self, date_text):
        self.populate_table()

    def toggle_explanation_column(self, state):
        self.table.setColumnHidden(5, not state)

    def populate_table(self):
        self.table.setRowCount(0)
        filter_date = self.date_filter.currentText()
        filter_text = self.search.text().lower()
        
        filtered_rows = []
        for row in self.full_history:
            # 1. Date Filter
            if filter_date != "All Dates" and row['_date'] != filter_date:
                continue
                
            # 2. Text Search
            row_vals = [
                str(row.get('timestamp','')),
                str(row.get('display_name') or 'Unknown'),
                str(row.get('project_code','')),
                str(row.get('action_type') or row.get('entity_type','')),
                f"{row.get('field_changed')}: {row.get('old_value')} -> {row.get('new_value')}",
                row['_analysis']
            ]
            
            if filter_text and not any(filter_text in v.lower() for v in row_vals):
                continue
                
            filtered_rows.append(row_vals)

        self.table.setRowCount(len(filtered_rows))
        for r, vals in enumerate(filtered_rows):
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                # Color coding
                if c == 1 and val == "Unknown": item.setForeground(QColor("#87857F")) # Grey unknowns
                if c == 3: # Action
                    if "UPDATE" in val: item.setForeground(QColor("#D9A441"))
                    elif "CREATE" in val: item.setForeground(QColor("#5FBF8F"))
                if c == 5: # Analysis
                    item.setForeground(QColor("#3EA8BF"))
                    
                self.table.setItem(r, c, item)
                
        self.toggle_explanation_column(self.chk_explain.isChecked())
    
    def filter_table(self):
        # Redirect to populate which handles both filters
        self.populate_table()


class UnifiedLogViewer(QWidget):
    """
    Main Tab containing both System Logs and DB Audit.
    """
    def __init__(self, db_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.db_manager = db_manager or self.app_context.db_manager()
        
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #26262D; background: #16161A; }
            QTabBar::tab { background: #1D1D22; color: #87857F; padding: 8px 20px; }
            QTabBar::tab:selected { background: #16161A; color: #3EA8BF; border-top: 2px solid #3EA8BF; }
        """)
        
        self.sys_logs = SystemLogViewer()
        self.db_audit = DatabaseAuditViewer(self.db_manager)
        
        self.tabs.addTab(self.sys_logs, "System Logs (Remote)")
        self.tabs.addTab(self.db_audit, "Database Audit (Global)")
        
        layout.addWidget(self.tabs)

    def cleanup_resources(self):
        if hasattr(self, "sys_logs") and hasattr(self.sys_logs, "cleanup_resources"):
            self.sys_logs.cleanup_resources()

    def closeEvent(self, event):
        self.cleanup_resources()
        super().closeEvent(event)
