from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QTableWidget, 
    QTableWidgetItem, QPushButton, QLabel, QSplitter,
    QPlainTextEdit, QMessageBox, QFrame, QAbstractItemView, QLineEdit, QMenu
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QLinearGradient

from ..core.infra.db_worker import run_db_async
from ..core.infra.app_context import AppContext
from .components.qt_safety import safe_single_shot

import logging
import re
from functools import partial


def _safe_identifier(name, allowed=None):
    """
    A table or column name that is safe to put in a statement.

    SQL cannot parameterise an identifier, so these have to be interpolated -
    which means the name has to be checked rather than trusted. Anything that
    is not a plain identifier is refused outright, and where the caller can
    supply the real list, membership of it is required as well.
    """
    text = str(name or "")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text):
        raise ValueError("not a valid SQL identifier: %r" % (name,))
    if allowed is not None and text not in set(allowed):
        raise ValueError("unknown table or column: %r" % (name,))
    return text


# --- CUSTOM VISUAL WIDGETS ---

class StatCard(QFrame):
    """A visually appealing card showing a single metric."""
    def __init__(self, title, value, color_start, color_end, icon="\U0001F4CA"):
        super().__init__()
        self.setMinimumSize(220, 120)
        self.color_start = QColor(color_start)
        self.color_end = QColor(color_end)
        self.title = title
        self.value = str(value)
        if isinstance(icon, str) and any(bad in icon for bad in ("\u00f0", "\u00e2", "\ufffd")):
            t = str(title).lower()
            if "asset" in t:
                icon = "\U0001F3A5"
            elif "user" in t:
                icon = "\U0001F465"
            elif "project" in t:
                icon = "\U0001F4C1"
            else:
                icon = "\U0001F4CA"
        self.icon = icon
        
        # Style
        self.setStyleSheet("""
            QFrame {
                background-color: transparent;
                border-radius: 12px;
            }
        """)
        
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Gradient BG
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, self.color_start)
        grad.setColorAt(1, self.color_end)
        
        rect = QRectF(0, 0, self.width(), self.height())
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, 12, 12)
        
        # Icon Background Bubble
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawEllipse(self.width() - 80, -20, 100, 100)
        
        # Text
        p.setPen(QColor("white"))
        
        # Title
        font_title = QFont()
        font_title.setPixelSize(14)
        font_title.setBold(True)
        font_title.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        p.setFont(font_title)
        p.drawText(20, 35, self.title.upper())
        
        # Value
        font_val = QFont()
        font_val.setPixelSize(36)
        font_val.setBold(True)
        p.setFont(font_val)
        p.drawText(20, 85, self.value)
        
        # Icon (Emoji/Text)
        font_icon = QFont()
        font_icon.setPixelSize(40)
        p.setFont(font_icon)
        p.setPen(QColor(255, 255, 255, 80))
        p.drawText(self.width() - 50, 45, self.icon)
        
        p.end()

class SimpleBarChart(QWidget):
    """Draws a simple bar chart given a dict of {label: value}."""
    def __init__(self, title, data_dict, bar_color="#3EA8BF"):
        super().__init__()
        self.setFixedHeight(250)
        self.title = title
        self.data = data_dict
        self.bar_color = QColor(bar_color)
        self.setStyleSheet("background: #16161A; border-radius: 12px; border: 1px solid #26262D;")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Title
        p.setPen(QColor("#E8E6E1"))
        font = QFont(); font.setBold(True); font.setPixelSize(14)
        p.setFont(font)
        p.drawText(20, 30, self.title)
        
        if not self.data:
            p.drawText(self.rect().center(), "No Data")
            return

        # Calculate Scales
        max_val = max(self.data.values()) if self.data else 1
        keys = list(self.data.keys())
        list(self.data.values())
        
        margin_left = 60
        margin_bottom = 40
        margin_top = 50
        margin_right = 20
        
        chart_w = self.width() - margin_left - margin_right
        chart_h = self.height() - margin_bottom - margin_top
        
        bar_width = chart_w / len(keys) * 0.6
        spacing = chart_w / len(keys) * 0.4
        
        # Draw Axis
        p.setPen(QPen(QColor("#2C2C34"), 2))
        p.drawLine(margin_left, self.height() - margin_bottom, self.width() - margin_right, self.height() - margin_bottom) # X
        p.drawLine(margin_left, margin_top, margin_left, self.height() - margin_bottom) # Y
        
        # Draw Bars
        for i, (key, val) in enumerate(self.data.items()):
            x = margin_left + (i * (bar_width + spacing)) + (spacing/2)
            bar_h = (val / max_val) * chart_h
            y = (self.height() - margin_bottom) - bar_h
            
            # Bar
            rect = QRectF(x, y, bar_width, bar_h)
            p.setBrush(self.bar_color)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 4, 4)
            
            # Value Label (Top of bar)
            p.setPen(QColor("white"))
            font_small = QFont(); font_small.setPixelSize(10)
            p.setFont(font_small)
            p.drawText(int(x), int(y - 5), int(bar_width), 20, Qt.AlignmentFlag.AlignCenter, str(val))
            
            # X Label (Bottom)
            p.setPen(QColor("#B4B1AA"))
            p.drawText(int(x - 10), self.height() - margin_bottom + 5, int(bar_width + 20), 40, Qt.AlignmentFlag.AlignCenter | Qt.TextWordWrap, str(key))

class DashboardHome(QWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self._stats_worker = None  # prevent GC of worker
        self._stats_loaded_once = False
        self._is_closing = False
        self.destroyed.connect(self.cancel_workers)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20,20,20,20)
        self.main_layout.setSpacing(20)
        
        # Header
        lbl = QLabel("OVERVIEW")
        lbl.setStyleSheet("color: white; font-size: 24px; font-weight: 900; letter-spacing: 2px;")
        self.main_layout.addWidget(lbl)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #D9635F; font-size: 12px;")
        self.error_label.hide()
        self.main_layout.addWidget(self.error_label)
        
        # Stats Row
        self.stats_layout = QHBoxLayout()
        self.main_layout.addLayout(self.stats_layout)
        
        # Charts Row
        self.charts_layout = QHBoxLayout()
        self.main_layout.addLayout(self.charts_layout)
        
        self.main_layout.addStretch()

    def showEvent(self, event):
        super().showEvent(event)
        if self._is_closing:
            return
        if not self._stats_loaded_once:
            self._stats_loaded_once = True
            safe_single_shot(0, self, self.load_stats)

    def cancel_workers(self):
        """Cancel asynchronous DB workers to prevent C++ deleted object errors."""
        self._is_closing = True
        if self._stats_worker and hasattr(self._stats_worker, 'cancel'):
            self._stats_worker.cancel()
            self._stats_worker = None

    def _release_stats_worker(self, worker, *_args):
        if self._stats_worker is worker:
            self._stats_worker = None

    @staticmethod
    def _get_count_safe(result):
        if result is None:
            return 0

        row = result
        if isinstance(result, list):
            if not result:
                return 0
            row = result[0]

        if isinstance(row, dict):
            for key in ("count", "c", "count(*)", "COUNT(*)"):
                if key in row and row[key] is not None:
                    return row[key]
            vals = list(row.values())
            return vals[0] if vals else 0

        if isinstance(row, tuple):
            return row[0] if row else 0

        return row if row is not None else 0

    @staticmethod
    def _clear_layout(layout):
        from .core.layout_utils import clear_layout
        clear_layout(layout)
        
    def load_stats(self):
        """Load stats asynchronously to avoid freezing the UI."""
        if self._is_closing:
            return

        def _fetch_all_stats():
            """Runs on thread pool — no UI access here."""
            count_assets = self.db.execute_query("SELECT COUNT(*) AS c FROM stock_library", fetch="one")
            count_users = self.db.execute_query("SELECT COUNT(*) AS c FROM ut_users", fetch="one")
            count_projects_lineup = self.db.execute_query("SELECT COUNT(*) AS c FROM projects", fetch="one")
            count_projects_tracking = self.db.execute_query(
                "SELECT COUNT(*) AS c FROM tracking_projects WHERE active = 1",
                fetch="one",
            )
            res_types = self.db.execute_query(
                """
                SELECT file_type, COUNT(*) AS count
                FROM stock_library
                GROUP BY file_type
                ORDER BY count DESC
                LIMIT 6
                """,
                fetch="all",
            )
            res_roles = self.db.execute_query(
                """
                SELECT COALESCE(NULLIF(TRIM(roles), ''), 'Unassigned') AS role_label, COUNT(*) AS count
                FROM ut_users
                GROUP BY role_label
                ORDER BY count DESC
                """,
                fetch="all",
            )
            return {
                'count_assets': count_assets,
                'count_users': count_users,
                'count_projects_lineup': count_projects_lineup,
                'count_projects_tracking': count_projects_tracking,
                'res_types': res_types,
                'res_roles': res_roles,
            }

        def _on_stats_loaded(data):
            if self._is_closing:
                return
            """Runs on main thread — safe to update UI."""
            try:
                self.error_label.hide()

                val_assets = self._get_count_safe(data['count_assets'])
                val_users = self._get_count_safe(data['count_users'])
                val_proj = self._get_count_safe(data['count_projects_lineup'])
                val_proj_tracking = self._get_count_safe(data['count_projects_tracking'])

                self._clear_layout(self.stats_layout)
                self.stats_layout.addWidget(StatCard("Total Assets", val_assets, "#3EA8BF", "#3EA8BF", "\U0001F3A5"))
                self.stats_layout.addWidget(StatCard("Active Users", val_users, "#D9635F", "#D9635F", "\U0001F465"))
                self.stats_layout.addWidget(StatCard("Projects", val_proj, "#3EA8BF", "#5FBF8F", "\U0001F3AC"))
                self.stats_layout.addWidget(StatCard("Tracking Projects", val_proj_tracking, "#3EA8BF", "#3EA8BF", "\U0001F3AC"))
                self.stats_layout.addStretch()

                # Charts
                type_data = {}
                if data['res_types']:
                    for row in data['res_types']:
                        key = row["file_type"] if isinstance(row, dict) else row[0]
                        key = key if key else "(unknown)"
                        val = row["count"] if isinstance(row, dict) else row[1]
                        type_data[key] = val

                self._clear_layout(self.charts_layout)
                self.charts_layout.addWidget(SimpleBarChart("Asset Distribution", type_data, "#3EA8BF"))

                role_data = {}
                if data['res_roles']:
                    for row in data['res_roles']:
                        key = row["role_label"] if isinstance(row, dict) else row[0]
                        val = row["count"] if isinstance(row, dict) else row[1]
                        role_data[key] = val

                self.charts_layout.addWidget(SimpleBarChart("User Roles", role_data, "#D9635F"))
            except Exception as e:
                logging.exception("Failed to render Data Center dashboard stats")
                self.error_label.setText(f"Error rendering stats: {e}")
                self.error_label.show()

        def _on_stats_error(msg):
            if self._is_closing:
                return
            self.error_label.setText(f"Error loading stats: {msg}")
            self.error_label.show()

        if self._stats_worker and hasattr(self._stats_worker, "cancel"):
            self._stats_worker.cancel()
            self._stats_worker = None

        self._stats_worker = run_db_async(
            _fetch_all_stats,
            _on_stats_loaded,
            _on_stats_error,
            owner=self,
        )
        if self._stats_worker and hasattr(self._stats_worker, "signals"):
            self._stats_worker.signals.finished.connect(partial(self._release_stats_worker, self._stats_worker))
            self._stats_worker.signals.error.connect(partial(self._release_stats_worker, self._stats_worker))

    def closeEvent(self, event):
        self._is_closing = True
        self.cancel_workers()
        super().closeEvent(event)

# --- MAIN EXPLORER ---

class DatabaseExplorer(QWidget):
    """
    Admin tool to view AND EDIT the PostgreSQL database.
    Now supports: Visual Dashboard, Inline Editing, Row Deletion, Search.
    """
    def __init__(self, db_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.db = db_manager or self.app_context.db_manager()
        self.current_table = None
        self.primary_key_col = "id" # Default assumption
        self.columns = []
        self.is_loading = False
        self._is_closing = False
        self._valid_tables = set()  # Whitelist populated from DB
        self._active_worker = None  # prevent GC of worker
        self._workers = set()
        self.destroyed.connect(self.cancel_workers)
        self.setup_ui()
        
    def cancel_workers(self):
        """Cancel asynchronous DB workers to prevent C++ deleted object errors."""
        if self._active_worker and hasattr(self._active_worker, 'cancel'):
            self._active_worker.cancel()
            self._active_worker = None
        for worker in list(self._workers):
            try:
                if worker and hasattr(worker, "cancel"):
                    worker.cancel()
            except Exception as exc:
                logging.debug("Worker cancel skipped during DatabaseExplorer cleanup: %s", exc)
        self._workers.clear()
        if hasattr(self, 'dashboard_view') and hasattr(self.dashboard_view, 'cancel_workers'):
            self.dashboard_view.cancel_workers()

    def _untrack_worker(self, worker, *_args):
        if worker in self._workers:
            self._workers.discard(worker)
        if worker is self._active_worker:
            self._active_worker = None

    def _run_async(self, fn, on_success=None, on_error=None, track_primary=False):
        worker = run_db_async(fn, on_success, on_error, owner=self)
        if worker is None:
            return None
        self._workers.add(worker)
        if hasattr(worker, "signals"):
            worker.signals.finished.connect(partial(self._untrack_worker, worker))
            worker.signals.error.connect(partial(self._untrack_worker, worker))
        if track_primary:
            if self._active_worker and hasattr(self._active_worker, "cancel"):
                self._active_worker.cancel()
                self._untrack_worker(self._active_worker)
            self._active_worker = worker
        return worker

    def _is_sqlite_backend(self):
        backend = getattr(self.db, "backend", self.db)
        return "sqlite" in backend.__class__.__name__.lower()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle { background-color: #26262D; }
            QSplitter::handle:hover { background-color: #3EA8BF; }
        """)
        
        # --- LEFT: SIDEBAR ---
        left_widget = QWidget()
        left_widget.setStyleSheet("background-color: #16161A; border-right: 1px solid #26262D;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(10, 20, 10, 10)
        left_layout.setSpacing(15)
        
        # Dashboard Button
        btn_dash = QPushButton("  \U0001F4CA  DASHBOARD")
        btn_dash.setStyleSheet("""
            QPushButton { 
                background-color: rgba(255,255,255,0.05); 
                color: white; 
                font-weight: bold; 
                text-align: left; 
                padding: 12px; 
                border-radius: 6px; 
                border: 1px solid #2C2C34;
            }
            QPushButton:hover { background-color: #26262D; border: 1px solid #3EA8BF; }
        """)
        btn_dash.clicked.connect(self.show_dashboard)
        left_layout.addWidget(btn_dash)
        
        left_layout.addWidget(QLabel("TABLES")) # Spacer label
        
        self.table_list = QListWidget()
        self.table_list.setFrameShape(QFrame.NoFrame)
        self.table_list.setStyleSheet("""
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget::item { padding: 10px; color: #87857F; border-radius: 6px; font-weight: 500; }
            QListWidget::item:hover { background-color: #1D1D22; color: #E8E6E1; }
            QListWidget::item:selected { background-color: #3EA8BF; color: black; font-weight: bold; }
        """)
        self.table_list.itemClicked.connect(self.load_table_data)
        left_layout.addWidget(self.table_list)
        
        # --- ACTIONS SECTION ---
        left_layout.addStretch()
        left_layout.addWidget(QLabel("DATA MAINTENANCE"))
        
        btn_purge = QPushButton("\U0001F5D1 PURGE LIBRARY")
        btn_purge.setStyleSheet("""
            QPushButton {
                background-color: #3A1F1E;
                color: #D9635F;
                border: 1px solid #D9635F;
                border-radius: 6px;
                padding: 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #D9635F; color: white; border-color: #D9635F; }
        """)
        btn_purge.clicked.connect(self.purge_stock_library)
        left_layout.addWidget(btn_purge)
        
        left_widget.setFixedWidth(220)
        splitter.addWidget(left_widget)
        
        # --- RIGHT: CONTENT ---
        self.right_stack = QWidget()
        self.stack_layout = QVBoxLayout(self.right_stack)
        self.stack_layout.setContentsMargins(0,0,0,0)
        
        # 1. Dashboard View
        self.dashboard_view = DashboardHome(self.db)
        
        # 2. Table Data View (Container)
        self.table_view_widget = QWidget()
        self.setup_table_view_ui()
        
        # Initial State: Show Dashboard, Hide Table
        self.stack_layout.addWidget(self.dashboard_view)
        self.stack_layout.addWidget(self.table_view_widget)
        self.table_view_widget.hide()
        
        splitter.addWidget(self.right_stack)
        layout.addWidget(splitter)
        
        self.refresh_tables()
        
    def show_dashboard(self):
        if self._is_closing:
            return
        self.table_view_widget.hide()
        self.dashboard_view.show()
        self.dashboard_view.load_stats()
        self.table_list.clearSelection()

    def setup_table_view_ui(self):
        """Builds the Data Grid + SQL UI inside self.table_view_widget"""
        layout = QVBoxLayout(self.table_view_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Header + Search
        h = QHBoxLayout()
        self.lbl_table_name = QLabel("Users")
        self.lbl_table_name.setStyleSheet("font-size: 28px; font-weight: 800; color: white;")
        
        self.inp_search = QLineEdit()
        self.inp_search.setPlaceholderText("\U0001F50D Search data...")
        self.inp_search.setFixedWidth(250)
        self.inp_search.setStyleSheet("background: #16323A; border: 1px solid #26262D; border-radius: 15px; padding: 8px 15px; color: white;")
        self.inp_search.textChanged.connect(self.apply_filter)
        
        h.addWidget(self.lbl_table_name)
        h.addStretch()
        h.addWidget(self.inp_search)
        layout.addLayout(h)
        
        # Grid
        self.data_grid = QTableWidget()
        self.data_grid.setStyleSheet("""
            QTableWidget { background: #0D0D0F; border: none; gridline-color: transparent; }
            QHeaderView::section { background: #1D1D22; padding: 8px; border: none; color: #B4B1AA; font-weight: bold; }
        """)
        self.data_grid.setAlternatingRowColors(True)
        # ENABLE EDITING
        self.data_grid.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.data_grid.itemChanged.connect(self.on_item_changed)
        self.data_grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_grid.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.data_grid)
        
        # SQL Console
        self.txt_sql = QPlainTextEdit()
        self.txt_sql.setFixedHeight(60)
        self.txt_sql.setPlaceholderText("SQL Query...")
        self.txt_sql.setStyleSheet("background: #000; color: #5FBF8F; border: 1px solid #26262D; font-family: Consolas;")
        
        btn_run = QPushButton("\u25B6")
        btn_run.setFixedSize(60, 60)
        btn_run.setStyleSheet("background: #D9635F; color: white; font-weight: bold; border: none;")
        btn_run.clicked.connect(self.run_custom_sql)
        
        h_sql = QHBoxLayout()
        h_sql.addWidget(self.txt_sql)
        h_sql.addWidget(btn_run)
        layout.addLayout(h_sql)

    def refresh_tables(self):
        """Load table list asynchronously."""
        def _fetch():
            if self._is_sqlite_backend():
                sql = """
                    SELECT name AS table_name
                    FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """
            else:
                sql = """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """
            return self.db.execute_query(sql, fetch="all")

        def _on_done(rows):
            if self._is_closing:
                return
            self.table_list.clear()
            self._valid_tables.clear()
            if rows:
                for row in rows:
                    name = row['table_name'] if isinstance(row, dict) else row[0]
                    self._valid_tables.add(name)
                    self.table_list.addItem(name)

        self._run_async(_fetch, _on_done, track_primary=True)

    def _validate_identifier(self, name):
        """Validate that a SQL identifier (table/column name) is safe."""
        if not name:
            return False
        # Only allow alphanumeric + underscores (standard SQL identifiers)
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            logging.warning(f"Rejected invalid SQL identifier: {name!r}")
            return False
        return True

    def load_table_data(self, item):
        """Load table data asynchronously."""
        if self._is_closing:
            return
        if not item: return
        self.dashboard_view.hide()
        self.table_view_widget.show()
        
        table_name = item.text()
        
        # Validate table name against whitelist
        if not self._validate_identifier(table_name) or table_name not in self._valid_tables:
            QMessageBox.warning(self, "Error", f"Invalid table name: {table_name}")
            return
        
        self.current_table = table_name
        self.lbl_table_name.setText(table_name)
        self.is_loading = True
        self.inp_search.clear()

        def _fetch():
            if self._is_sqlite_backend():
                cols_res = self.db.execute_query(
                f"PRAGMA table_info({_safe_identifier(table_name)})", fetch="all") or []
            else:
                cols_res = self.db.execute_query(
                    f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
                    fetch="all",
                ) or []
            rows = self.db.execute_query(
                f"SELECT * FROM {_safe_identifier(table_name)} LIMIT 500", fetch="all") or []
            return {'cols_res': cols_res, 'rows': rows}

        def _on_done(data):
            if self._is_closing:
                return
            try:
                cols_res = data['cols_res']
                rows = data['rows']
                self.columns = []
                for c in cols_res:
                    if isinstance(c, dict):
                        col_name = c.get("column_name") or c.get("name")
                    else:
                        col_name = c[0] if c else None
                    if col_name:
                        self.columns.append(col_name)
                
                # Detect ID
                if 'id' in self.columns: self.primary_key_col = 'id'
                elif 'user_id' in self.columns: self.primary_key_col = 'user_id'
                else: self.primary_key_col = None
                
                self.data_grid.setColumnCount(len(self.columns))
                self.data_grid.setHorizontalHeaderLabels(self.columns)
                self.data_grid.setRowCount(0)
                
                if rows:
                    self.data_grid.setRowCount(len(rows))
                    for r, row in enumerate(rows):
                        row_id = None
                        if self.primary_key_col:
                            idx = self.columns.index(self.primary_key_col)
                            row_id = row[self.primary_key_col] if isinstance(row, dict) else row[idx]
                        
                        for c, col in enumerate(self.columns):
                            val = row[col] if isinstance(row, dict) else row[c]
                            if val is None: val = "NULL"
                            item = QTableWidgetItem(str(val))
                            # Store ID for edits
                            if row_id is not None: item.setData(Qt.ItemDataRole.UserRole, row_id)
                            
                            # Set Read-Only if not editable
                            if self.primary_key_col and col == self.primary_key_col:
                                 item.setFlags(item.flags() ^ Qt.ItemIsEditable) 
                                 
                            self.data_grid.setItem(r, c, item)
                
                self.data_grid.resizeColumnsToContents()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
            finally:
                self.is_loading = False

        def _on_error(msg):
            if self._is_closing:
                return
            self.is_loading = False
            QMessageBox.warning(self, "Error", msg)

        self._run_async(_fetch, _on_done, _on_error, track_primary=True)

    def on_item_changed(self, item):
        """Handle inline cell edit — fire-and-forget async update."""
        if self.is_loading: return
        row_id = item.data(Qt.ItemDataRole.UserRole)
        # If no ID or table, ignore
        if not row_id or not self.current_table or not self.primary_key_col: return
        
        col_name = self.columns[item.column()]
        # Double check we aren't editing ID
        if col_name == self.primary_key_col: return 
        
        # Validate identifiers
        if not self._validate_identifier(col_name) or not self._validate_identifier(self.current_table):
            logging.error(f"Invalid identifier in on_item_changed: table={self.current_table}, col={col_name}")
            return
        
        val = item.text()
        if val == "NULL": val = None
        sql = f"UPDATE {self.current_table} SET {col_name} = %s WHERE {self.primary_key_col} = %s"
        

        def _do_update():
            return self.db.execute_update(sql, (val, row_id))

        def _on_error(msg):
            if self._is_closing:
                return
            logging.error(f"Update failed: {msg}")
            QMessageBox.warning(self, "Save Failed", msg)

        self._run_async(_do_update, on_error=_on_error)

    def apply_filter(self, text):
        text = text.lower()
        for r in range(self.data_grid.rowCount()):
            hidden = True
            for c in range(self.data_grid.columnCount()):
                item = self.data_grid.item(r, c)
                if item and text in item.text().lower():
                    hidden = False
                    break
            self.data_grid.setRowHidden(r, hidden)

    def show_context_menu(self, pos):
        if not self.current_table or not self.primary_key_col: return
        item = self.data_grid.itemAt(pos)
        if not item: return
        menu = QMenu(self)
        del_act = menu.addAction("\u274C Delete Row")
        del_act.setData(item.row())
        del_act.triggered.connect(self._on_delete_action_triggered)
        menu.exec_(self.data_grid.mapToGlobal(pos))

    def _on_delete_action_triggered(self):
        action = self.sender()
        if action is None:
            return
        row = action.data()
        if row is None:
            return
        self.delete_row(int(row))

    def delete_row(self, r):
        """Delete a row asynchronously."""
        item = self.data_grid.item(r, 0)
        row_id = item.data(Qt.ItemDataRole.UserRole)
        if not row_id: return
        if QMessageBox.question(self, "Confirm", "Delete row?") == QMessageBox.StandardButton.Yes:
            table = self.current_table
            pk = self.primary_key_col

            def _do_delete():
                return self.db.execute_update(
            f"DELETE FROM {_safe_identifier(table)} WHERE {_safe_identifier(pk)} = %s", (row_id,))

            def _on_done(result):
                if self._is_closing:
                    return
                self.data_grid.removeRow(r)

            def _on_error(msg):
                if self._is_closing:
                    return
                QMessageBox.warning(self, "Delete Failed", msg)

            self._run_async(_do_delete, _on_done, _on_error)

    def purge_stock_library(self):
        """Wipes the Stock Library table asynchronously."""
        ret = QMessageBox.warning(
            self, 
            "Purge Stock Library?", 
            "Are you sure you want to DELETE ALL ASSETS from the database?\n\nThis cannot be undone. Files on disk will NOT be touched, but all metadata/proxies tracking will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            def _do_purge():
                return self.db.execute_update("TRUNCATE TABLE stock_library RESTART IDENTITY CASCADE;")

            def _on_done(result):
                if self._is_closing:
                    return
                QMessageBox.information(self, "Success", "Stock Library has been cleared.")
                if self.dashboard_view.isVisible():
                    self.dashboard_view.load_stats()

            def _on_error(msg):
                if self._is_closing:
                    return
                QMessageBox.critical(self, "Error", f"Failed to purge library: {msg}")

            self._run_async(_do_purge, _on_done, _on_error)

    def run_custom_sql(self):
        """Execute custom SQL asynchronously."""
        q = self.txt_sql.toPlainText().strip()
        if not q: return

        if q.upper().startswith("SELECT"):
            def _do_query():
                return self.db.execute_query(q, fetch="all")

            def _on_done(rows):
                if self._is_closing:
                    return
                self.lbl_table_name.setText("SQL Result")
                if rows:
                    if isinstance(rows[0], dict): cols = list(rows[0].keys())
                    else: cols = [f"Col {i}" for i in range(len(rows[0]))]
                    self.data_grid.setColumnCount(len(cols))
                    self.data_grid.setHorizontalHeaderLabels(cols)
                    self.data_grid.setRowCount(len(rows))
                    for r, row in enumerate(rows):
                        for c, col in enumerate(cols):
                            val = row[col] if isinstance(row, dict) else row[c]
                            self.data_grid.setItem(r, c, QTableWidgetItem(str(val)))

            def _on_error(msg):
                if self._is_closing:
                    return
                QMessageBox.critical(self, "Error", msg)

            self._run_async(_do_query, _on_done, _on_error, track_primary=True)
        else:
            # Non-SELECT queries require confirmation
            confirm = QMessageBox.warning(
                self, "Execute SQL?",
                f"You are about to execute a non-SELECT query:\n\n{q[:200]}{'...' if len(q) > 200 else ''}\n\nThis may modify or delete data. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

            def _do_update():
                return self.db.execute_update(q)

            def _on_done(result):
                if self._is_closing:
                    return
                logging.info(f"Custom SQL executed: {q[:200]}")
                QMessageBox.information(self, "OK", "Executed.")

            def _on_error(msg):
                if self._is_closing:
                    return
                QMessageBox.critical(self, "Error", msg)

            self._run_async(_do_update, _on_done, _on_error, track_primary=True)

    def closeEvent(self, event):
        """Ensure workers are cancelled before widget teardown."""
        self._is_closing = True
        self.cancel_workers()
        super().closeEvent(event)
