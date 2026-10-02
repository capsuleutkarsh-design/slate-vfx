from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QTableWidget,
    QTableWidgetItem, QLabel, QSplitter, QGridLayout, QDialog,
    QPlainTextEdit, QMessageBox, QFrame, QAbstractItemView, QLineEdit, QMenu
)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QShortcut, QKeySequence

from ..core.infra.db_worker import run_db_async
from ..core.infra.app_context import AppContext
from .components.qt_safety import safe_single_shot
from .components.feedback import confirm

import json
import logging
import re
from functools import partial
from slate.core.infra.gate import Gate
from slate.gui.core.stat_card import StatCard as _SharedStatCard
from slate.gui.core.controls import make_button, page_title
from slate.gui.core.icons import icon as draw_icon
from slate.gui.core.table_style import style_table

# The value a cell held when it was loaded (or last saved), so a refused edit
# can be put back.
SAVED_VALUE_ROLE = Qt.ItemDataRole.UserRole + 1
# True when the cell holds SQL NULL. NULL is shown as an empty, greyed cell -
# it used to be the word 'NULL', and editing another column could write that
# text back.
NULL_ROLE = Qt.ItemDataRole.UserRole + 2
# The raw table name on a list item; the item shows a friendly label.
TABLE_ROLE = Qt.ItemDataRole.UserRole + 3

# How many rows a typed SELECT shows at most; the title says when there were more.
SQL_RESULT_LIMIT = 5000

# Friendly names for the tables people actually open. Anything else is shown
# with its underscores as spaces; the raw name is always the tooltip.
TABLE_LABELS = {
    "ut_users": "Users",
    "ut_roles": "Roles",
    "stock_library": "Stock library",
    "tracking_projects": "Dashboard projects",
    "tracking_shots": "Dashboard shots",
    "projects": "Lineup projects",
    "change_history": "Change history",
    "attendance_log": "Attendance log",
    "leave_requests": "Leave requests",
    "notifications": "Notifications",
    "studio_settings": "Studio settings",
    "hardware_inventory": "Hardware",
    "asset_assignments": "Machine loans",
    "it_tickets": "IT tickets",
    "slate_migrations": "Schema migrations",
}


def table_label(name: str) -> str:
    text = str(name or "")
    if text in TABLE_LABELS:
        return TABLE_LABELS[text]
    return text.replace("_", " ").strip().capitalize() or text


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


def count_roles(rows) -> dict:
    """
    People per role. ut_users.roles is JSON text ('["Artist", "Lead"]'); it
    was grouped as raw text, so the chart's labels read ["Artist"] and every
    combination of roles made a bar of its own.
    """
    counts = {}
    for row in rows or []:
        raw = row.get("roles") if isinstance(row, dict) else row
        names = []
        if isinstance(raw, (list, tuple)):
            names = list(raw)
        elif raw not in (None, ""):
            text = str(raw).strip()
            try:
                value = json.loads(text)
                names = value if isinstance(value, list) else [value]
            except ValueError:
                names = [part for part in re.split(r"[,;]", text)]
        names = sorted({str(n).strip() for n in names if str(n).strip()})
        for name in names or ["Unassigned"]:
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())))


def _is_active_user(row) -> bool:
    try:
        from ..core.domain.user_manager import UserManager
        return UserManager._flag_active(dict(row))
    except Exception:
        return True


# --- CUSTOM VISUAL WIDGETS ---

class StatCard(_SharedStatCard):
    """
    One figure on the Data Center overview.

    This used to paint its own gradient card with white text and a colour
    emoji, which matched nothing else in the product and ignored the theme.
    It is now the shared card; the gradient's first colour becomes its accent
    strip, and the icon argument is accepted for callers but not drawn.
    """

    def __init__(self, title, value, color_start=None, color_end=None, icon=None, tooltip=""):
        super().__init__(title, value, tone=color_start, tooltip=tooltip)
        self.setMinimumWidth(160)
        self.title = title
        self.value = str(value)


class SimpleBarChart(QWidget):
    """Draws a simple bar chart given a dict of {label: value}."""
    def __init__(self, title, data_dict, bar_color=Gate.ACCENT):
        super().__init__()
        self.setFixedHeight(250)
        self.title = title
        self.data = data_dict
        self.bar_color = QColor(bar_color)
        self.setObjectName("DcChart")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#DcChart {{ background: {Gate.PANEL}; border-radius: 12px; "
                           f"border: 1px solid {Gate.RAISED_HI}; }}")

    @staticmethod
    def value_label_rect(x, y, bar_width, top):
        """Where a bar's value is written: just above the bar, never above the chart."""
        label_y = max(top - 16, y - 18)
        return QRectF(x - 6, label_y, bar_width + 12, 16)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Title
        p.setPen(QColor(Gate.TEXT))
        font = QFont(); font.setBold(True); font.setPixelSize(14)
        p.setFont(font)
        p.drawText(20, 30, self.title)

        if not self.data:
            # Centred in the chart - drawText(point) put the text's baseline
            # start at the centre, so it sat off to the right.
            p.setPen(QColor(Gate.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
            return

        max_val = max(self.data.values()) or 1
        keys = list(self.data.keys())

        margin_left = 60
        margin_bottom = 40
        margin_top = 50
        margin_right = 20

        chart_w = self.width() - margin_left - margin_right
        chart_h = self.height() - margin_bottom - margin_top

        bar_width = chart_w / len(keys) * 0.6
        spacing = chart_w / len(keys) * 0.4

        p.setPen(QPen(QColor(Gate.LINE), 2))
        p.drawLine(margin_left, self.height() - margin_bottom, self.width() - margin_right, self.height() - margin_bottom) # X
        p.drawLine(margin_left, margin_top, margin_left, self.height() - margin_bottom) # Y

        font_small = QFont(); font_small.setPixelSize(10)
        for i, (key, val) in enumerate(self.data.items()):
            x = margin_left + (i * (bar_width + spacing)) + (spacing/2)
            bar_h = (val / max_val) * chart_h
            y = (self.height() - margin_bottom) - bar_h

            rect = QRectF(x, y, bar_width, bar_h)
            p.setBrush(self.bar_color)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, 4, 4)

            # The value sits above its bar; it used to overlap the bar's top.
            p.setPen(QColor(Gate.TEXT))
            p.setFont(font_small)
            p.drawText(self.value_label_rect(x, y, bar_width, margin_top),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, str(val))

            p.setPen(QColor(Gate.TEXT_2))
            p.drawText(int(x - 10), self.height() - margin_bottom + 5, int(bar_width + 20), 40, Qt.AlignmentFlag.AlignCenter | Qt.TextWordWrap, str(key))


class DashboardHome(QWidget):
    WIDE = 1100   # below this width the four cards wrap into two rows

    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self._stats_worker = None  # prevent GC of worker
        self._stats_loaded_once = False
        self._is_closing = False
        self.cards = []
        self.destroyed.connect(self.cancel_workers)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20,20,20,20)
        self.main_layout.setSpacing(20)

        self.main_layout.addWidget(page_title("Overview", "What the studio database holds"))

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(f"color: {Gate.BAD}; font-size: 12px;")
        self.error_label.hide()
        self.main_layout.addWidget(self.error_label)

        # Cards in a grid that wraps: four in a row needed ~950 px and ran off
        # the edge of a 1280 px window.
        self.stats_layout = QGridLayout()
        self.stats_layout.setSpacing(Gate.SPACE_2)
        self.main_layout.addLayout(self.stats_layout)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_cards()

    def card_columns(self) -> int:
        return 4 if self.width() >= self.WIDE else 2

    def _place_cards(self):
        columns = self.card_columns()
        for card in self.cards:
            self.stats_layout.removeWidget(card)
        for index, card in enumerate(self.cards):
            row, col = divmod(index, columns)
            self.stats_layout.addWidget(card, row, col)

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

    def fetch_stats(self):
        """Runs on the thread pool - no UI access here."""
        count_assets = self.db.execute_query("SELECT COUNT(*) AS c FROM stock_library", fetch="one")
        users = self.db.execute_query("SELECT * FROM ut_users", fetch="all") or []
        active = [dict(u) for u in users if _is_active_user(u)]
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
        return {
            'count_assets': count_assets,
            # People who have not been deactivated or passed their last day.
            'count_users': len(active),
            'count_projects_lineup': count_projects_lineup,
            'count_projects_tracking': count_projects_tracking,
            'res_types': res_types,
            'roles': count_roles(active),
        }

    def show_stats(self, data):
        """Runs on the main thread."""
        if self._is_closing:
            return
        try:
            self.error_label.hide()

            val_assets = self._get_count_safe(data['count_assets'])
            val_users = data['count_users']
            val_proj = self._get_count_safe(data['count_projects_lineup'])
            val_proj_tracking = self._get_count_safe(data['count_projects_tracking'])

            for card in self.cards:
                self.stats_layout.removeWidget(card)
                card.deleteLater()
            self.cards = [
                StatCard("Stock assets", val_assets, Gate.ACCENT,
                         tooltip="Files in the stock library"),
                StatCard("Active users", val_users, Gate.BAD,
                         tooltip="Accounts that are not deactivated and whose last day has not passed"),
                StatCard("Lineup projects", val_proj, Gate.OK,
                         tooltip="Projects in the 'projects' table, used for Timeline lineups"),
                StatCard("Active shows", val_proj_tracking, Gate.ACCENT,
                         tooltip="Active projects on the VFX Dashboard ('tracking_projects')"),
            ]
            self._place_cards()

            type_data = {}
            for row in data['res_types'] or []:
                key = row["file_type"] if isinstance(row, dict) else row[0]
                key = key if key else "(unknown)"
                val = row["count"] if isinstance(row, dict) else row[1]
                type_data[key] = val

            self._clear_layout(self.charts_layout)
            self.charts_layout.addWidget(SimpleBarChart("Stock assets by type", type_data, Gate.ACCENT))
            self.charts_layout.addWidget(SimpleBarChart("Active users by role", data['roles'], Gate.BAD))
        except Exception as e:
            logging.exception("Failed to render Data Center dashboard stats")
            self.error_label.setText(f"The overview could not be shown: {e}")
            self.error_label.show()

    def load_stats(self):
        """Load stats asynchronously to avoid freezing the UI."""
        if self._is_closing:
            return

        def _on_stats_error(msg):
            if self._is_closing:
                return
            self.error_label.setText(f"The overview could not be read: {msg}")
            self.error_label.show()

        if self._stats_worker and hasattr(self._stats_worker, "cancel"):
            self._stats_worker.cancel()
            self._stats_worker = None

        self._stats_worker = run_db_async(
            self.fetch_stats,
            self.show_stats,
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


class MaintenanceDialog(QDialog):
    """
    The one destructive maintenance task, Purge stock library, behind a typed
    confirmation. It used to be a permanent red button in the sidebar that
    truncated with CASCADE after one Yes/No.
    """
    WORD = "PURGE"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data maintenance")
        layout = QVBoxLayout(self)
        heading = QLabel("Purge the stock library")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        body = QLabel(
            "Removes every asset from the stock library table: names, tags, categories and "
            "proxy tracking. Files on disk are not touched. This cannot be undone.\n\n"
            f"Type {self.WORD} to confirm.")
        body.setWordWrap(True)
        layout.addWidget(body)
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText(self.WORD)
        layout.addWidget(self.confirm_input)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = make_button("Cancel", "secondary", on_click=self.reject)
        self.btn_purge = make_button("Purge stock library", "danger", on_click=self.accept)
        self.btn_purge.setEnabled(False)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_purge)
        layout.addLayout(row)
        self.confirm_input.textChanged.connect(
            lambda text: self.btn_purge.setEnabled(text.strip() == self.WORD))

    def confirmed(self) -> bool:
        return self.confirm_input.text().strip() == self.WORD


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
        self.primary_key_col = None   # the first key column (kept for older callers)
        self.key_columns = []         # the table's real primary key, possibly several columns
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

    def _username(self) -> str:
        try:
            return self.app_context.current_username()
        except Exception:
            return ""

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{ background-color: {Gate.RAISED_HI}; }}
            QSplitter::handle:hover {{ background-color: {Gate.ACCENT}; }}
        """)

        # --- LEFT: SIDEBAR ---
        # Scoped by name: without a selector the border-right was drawn on
        # every label and list inside the sidebar, a stray line beside each.
        self.left_widget = left_widget = QWidget()
        left_widget.setObjectName("DcSidebar")
        left_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_widget.setStyleSheet(f"QWidget#DcSidebar {{ background-color: {Gate.PANEL}; "
                                  f"border-right: 1px solid {Gate.RAISED_HI}; }}")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(10, 20, 10, 10)
        left_layout.setSpacing(10)

        btn_dash = make_button("Overview", "ghost", on_click=self.show_dashboard,
                               tooltip="Counts and charts of what the database holds")
        btn_dash.setIcon(draw_icon("chart"))
        left_layout.addWidget(btn_dash)

        tables_label = QLabel("Tables")
        tables_label.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-weight: 600;")
        left_layout.addWidget(tables_label)

        self.table_list = QListWidget()
        self.table_list.setFrameShape(QFrame.NoFrame)
        self.table_list.itemClicked.connect(self.load_table_data)
        # The list takes the height; a stretch above the maintenance button
        # used to leave it seven rows tall.
        left_layout.addWidget(self.table_list, 1)

        self.btn_maintenance = make_button("Data maintenance…", "secondary",
                                           on_click=self.open_maintenance,
                                           tooltip="Purge the stock library")
        self.btn_maintenance.setIcon(draw_icon("gear"))
        left_layout.addWidget(self.btn_maintenance)

        # A minimum, not a fixed width: the splitter handle did nothing.
        left_widget.setMinimumWidth(180)
        splitter.addWidget(left_widget)

        # --- RIGHT: CONTENT ---
        self.right_stack = QWidget()
        self.stack_layout = QVBoxLayout(self.right_stack)
        self.stack_layout.setContentsMargins(0,0,0,0)

        self.dashboard_view = DashboardHome(self.db)

        self.table_view_widget = QWidget()
        self.setup_table_view_ui()

        self.stack_layout.addWidget(self.dashboard_view)
        self.stack_layout.addWidget(self.table_view_widget)
        self.table_view_widget.hide()

        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 1000])
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

        h = QHBoxLayout()
        self.lbl_table_name = QLabel("Users")
        self.lbl_table_name.setStyleSheet(f"font-size: {Gate.SIZE_XL}px; font-weight: 700; color: {Gate.TEXT};")

        self.inp_search = QLineEdit()
        self.inp_search.setPlaceholderText("Search the rows shown…")
        self.inp_search.setClearButtonEnabled(True)
        self.inp_search.addAction(draw_icon("search", Gate.TEXT_DIM, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.inp_search.setFixedWidth(250)
        self.inp_search.textChanged.connect(self.apply_filter)

        h.addWidget(self.lbl_table_name)
        h.addStretch()
        h.addWidget(self.inp_search)
        layout.addLayout(h)

        # Said when the table has no primary key: its rows cannot be told
        # apart, so editing is switched off rather than silently dropped.
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet(f"color: {Gate.WARN};")
        self.lbl_note.hide()
        layout.addWidget(self.lbl_note)

        self.data_grid = QTableWidget()
        style_table(self.data_grid, editable=True, multi_select=False)
        self.data_grid.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.data_grid.itemChanged.connect(self.on_item_changed)
        self.data_grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_grid.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.data_grid)

        # SQL Console
        self.txt_sql = QPlainTextEdit()
        self.txt_sql.setFixedHeight(60)
        self.txt_sql.setPlaceholderText("SQL query - Ctrl+Enter runs it")
        self.txt_sql.setStyleSheet(f"font-family: {Gate.FONT_MONO};")

        btn_run = make_button("Run", "secondary", on_click=self.run_custom_sql,
                              tooltip="Run the SQL (Ctrl+Enter)")
        btn_run.setIcon(draw_icon("play"))
        self.btn_run = btn_run
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self.txt_sql)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self.run_custom_sql)

        h_sql = QHBoxLayout()
        h_sql.addWidget(self.txt_sql)
        h_sql.addWidget(btn_run, 0, Qt.AlignmentFlag.AlignBottom)
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
            self.show_tables(rows)

        self._run_async(_fetch, _on_done, track_primary=True)

    def show_tables(self, rows):
        self.table_list.clear()
        self._valid_tables.clear()
        names = []
        for row in rows or []:
            name = row['table_name'] if isinstance(row, dict) else row[0]
            self._valid_tables.add(name)
            names.append(name)
        for name in sorted(names, key=lambda n: table_label(n).lower()):
            item = QListWidgetItem(table_label(name))
            item.setData(TABLE_ROLE, name)
            item.setToolTip(name)
            self.table_list.addItem(item)

    def _validate_identifier(self, name):
        """Validate that a SQL identifier (table/column name) is safe."""
        if not name:
            return False
        # Only allow alphanumeric + underscores (standard SQL identifiers)
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            logging.warning(f"Rejected invalid SQL identifier: {name!r}")
            return False
        return True

    # ------------------------------------------------------------ table data
    def primary_key_of(self, table_name):
        """
        The table's real primary key columns, in order. The key used to be
        guessed from a column called 'id' or 'user_id' - tables keyed by
        'code' were silently uneditable, and a non-unique 'user_id' would have
        made an edit or delete hit every row of that user.
        """
        table = _safe_identifier(table_name)
        if self._is_sqlite_backend():
            rows = self.db.execute_query(f"PRAGMA table_info({table})", fetch="all") or []
            keyed = []
            for r in rows:
                pk = r.get("pk") if isinstance(r, dict) else r[5]
                name = r.get("name") if isinstance(r, dict) else r[1]
                if pk:
                    keyed.append((int(pk), name))
            return [name for _pk, name in sorted(keyed)]
        rows = self.db.execute_query(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
             AND tc.table_name = kcu.table_name
            WHERE tc.table_schema = 'public' AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """, (table,), fetch="all") or []
        return [r["column_name"] if isinstance(r, dict) else r[0] for r in rows]

    def load_table_data(self, item):
        """Load table data asynchronously."""
        if self._is_closing:
            return
        if not item: return
        self.dashboard_view.hide()
        self.table_view_widget.show()

        table_name = item.data(TABLE_ROLE) or item.text()

        # Validate table name against whitelist
        if not self._validate_identifier(table_name) or table_name not in self._valid_tables:
            QMessageBox.warning(self, "Data Center", f"There is no table called {table_name}.")
            return

        self.current_table = table_name
        self.lbl_table_name.setText(table_label(table_name))
        self.lbl_table_name.setToolTip(table_name)
        self.is_loading = True
        self.inp_search.clear()

        def _fetch():
            if self._is_sqlite_backend():
                cols_res = self.db.execute_query(
                    f"PRAGMA table_info({_safe_identifier(table_name)})", fetch="all") or []
            else:
                cols_res = self.db.execute_query(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                    (table_name,), fetch="all",
                ) or []
            keys = self.primary_key_of(table_name)
            rows = self.db.execute_query(
                f"SELECT * FROM {_safe_identifier(table_name)} LIMIT 500", fetch="all") or []
            return {'cols_res': cols_res, 'rows': rows, 'keys': keys}

        def _on_done(data):
            if self._is_closing:
                return
            try:
                self.show_table(table_name, data)
            except Exception as e:
                QMessageBox.warning(self, "Data Center", f"The table could not be shown:\n{e}")
            finally:
                self.is_loading = False

        def _on_error(msg):
            if self._is_closing:
                return
            self.is_loading = False
            QMessageBox.warning(self, "Data Center", f"The table could not be read:\n{msg}")

        self._run_async(_fetch, _on_done, _on_error, track_primary=True)

    def show_table(self, table_name, data):
        """Fill the grid with a table's rows (on the UI thread)."""
        self.is_loading = True
        try:
            self.current_table = table_name
            self.columns = []
            for c in data['cols_res']:
                if isinstance(c, dict):
                    col_name = c.get("column_name") or c.get("name")
                else:
                    col_name = c[0] if c else None
                if col_name:
                    self.columns.append(col_name)
            self.key_columns = [k for k in (data.get('keys') or []) if k in self.columns]
            self.primary_key_col = self.key_columns[0] if self.key_columns else None

            if self.key_columns:
                self.lbl_note.hide()
            else:
                self.lbl_note.setText("This table has no primary key, so its rows cannot be told "
                                      "apart safely. It is shown read-only.")
                self.lbl_note.show()

            rows = data['rows']
            self.data_grid.clear()
            self.data_grid.setColumnCount(len(self.columns))
            self.data_grid.setHorizontalHeaderLabels(self.columns)
            self.data_grid.setRowCount(len(rows))
            for r, row in enumerate(rows):
                values = [row[col] if isinstance(row, dict) else row[c]
                          for c, col in enumerate(self.columns)]
                key = [values[self.columns.index(k)] for k in self.key_columns] or None
                for c, col in enumerate(self.columns):
                    val = values[c]
                    item = QTableWidgetItem("" if val is None else str(val))
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    # The value as loaded, so a refused edit can be put back.
                    item.setData(SAVED_VALUE_ROLE, None if val is None else str(val))
                    item.setData(NULL_ROLE, val is None)
                    if val is None:
                        item.setForeground(QColor(Gate.TEXT_DIM))
                        item.setToolTip("Empty (NULL)")
                    if not self.key_columns or col in self.key_columns:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.data_grid.setItem(r, c, item)
            style_table(self.data_grid, editable=True, multi_select=False)
            self.data_grid.resizeColumnsToContents()
        finally:
            self.is_loading = False

    def _where_for(self, key):
        clause = " AND ".join(f"{_safe_identifier(k)} = %s" for k in self.key_columns)
        return clause, tuple(key)

    def _key_text(self, key) -> str:
        return ", ".join(f"{k}={v}" for k, v in zip(self.key_columns, key or []))

    def _log_change(self, action, key, field=None, old=None, new=None):
        """Data Center edits and deletes go into the change history, with who did them."""
        log = getattr(self.db, "log_change_event", None)
        if not callable(log):
            return
        try:
            log(None, f"table {self.current_table}", self._key_text(key), self._username(),
                action, field, old, new)
        except Exception as exc:
            logging.warning("Data Center change not written to history: %s", exc)

    def on_item_changed(self, item):
        """Handle inline cell edit — fire-and-forget async update."""
        if self.is_loading: return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key or not self.current_table or not self.key_columns: return

        col_name = self.columns[item.column()]
        if col_name in self.key_columns: return

        if not self._validate_identifier(col_name) or not self._validate_identifier(self.current_table):
            logging.error(f"Invalid identifier in on_item_changed: table={self.current_table}, col={col_name}")
            return

        previous = item.data(SAVED_VALUE_ROLE)
        was_null = bool(item.data(NULL_ROLE))
        val = item.text()
        if was_null and val == "":
            return          # an empty cell left empty: nothing changed
        self.write_cell(item.row(), item.column(), val, previous, was_null)

    def write_cell(self, row, column, value, previous, was_null):
        """Save one cell. value None writes SQL NULL."""
        col_name = self.columns[column]
        cell = self.data_grid.item(row, column)
        key = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
        if not key:
            return
        where, key_params = self._where_for(key)
        sql = (f"UPDATE {_safe_identifier(self.current_table)} SET {_safe_identifier(col_name)} = %s "
               f"WHERE {where}")

        def _do_update():
            return self.db.execute_update(sql, (value,) + key_params)

        def _revert(reason):
            # Put the stored value back, so the grid never shows what the
            # database refused.
            target = self.data_grid.item(row, column)
            if target is not None:
                self.is_loading = True
                try:
                    target.setText("" if was_null or previous is None else previous)
                finally:
                    self.is_loading = False
            QMessageBox.warning(self, "Not saved",
                                f"{col_name} was not changed:\n\n{reason}")

        def _on_done(result):
            if self._is_closing:
                return
            if not result:
                _revert(getattr(result, "error", "") or "The database refused the value.")
                return
            if getattr(result, "rows", 1) == 0:
                _revert("That row no longer exists - it may have been deleted by someone else.")
                return
            target = self.data_grid.item(row, column)
            if target is not None:
                self.is_loading = True
                try:
                    target.setData(SAVED_VALUE_ROLE, value)
                    target.setData(NULL_ROLE, value is None)
                    target.setText("" if value is None else str(value))
                    target.setForeground(QColor(Gate.TEXT_DIM if value is None else Gate.TEXT))
                finally:
                    self.is_loading = False
                self._flash_saved(target)
            self._log_change("UPDATE", key, col_name, None if was_null else previous, value)

        def _on_error(msg):
            if self._is_closing:
                return
            logging.error(f"Update failed: {msg}")
            _revert(msg)

        self._run_async(_do_update, _on_done, _on_error)

    def set_cell_null(self, row, column):
        """The explicit way to empty a cell (typing 'NULL' no longer does it)."""
        cell = self.data_grid.item(row, column)
        if cell is None or bool(cell.data(NULL_ROLE)):
            return
        self.write_cell(row, column, None, cell.data(SAVED_VALUE_ROLE), False)

    def _flash_saved(self, cell):
        """A brief tint on a cell that saved - the only sign it did."""
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QBrush
        self.is_loading = True
        try:
            cell.setBackground(QBrush(Gate.qcolor(Gate.OK, 0.28)))
        finally:
            self.is_loading = False

        def _clear():
            try:
                self.is_loading = True
                cell.setBackground(QBrush())
            except RuntimeError:
                pass
            finally:
                self.is_loading = False
        QTimer.singleShot(900, _clear)

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
        if not self.current_table or not self.key_columns: return
        item = self.data_grid.itemAt(pos)
        if not item: return
        menu = QMenu(self)
        col_name = self.columns[item.column()]
        if col_name not in self.key_columns and not item.data(NULL_ROLE):
            null_act = menu.addAction(f"Set {col_name} to empty (NULL)")
            null_act.triggered.connect(partial(self.set_cell_null, item.row(), item.column()))
            menu.addSeparator()
        del_act = menu.addAction(draw_icon("trash", Gate.BAD, 16), "Delete row…")
        del_act.setData(item.row())
        del_act.triggered.connect(self._on_delete_action_triggered)
        menu.exec(self.data_grid.mapToGlobal(pos))

    def _on_delete_action_triggered(self):
        action = self.sender()
        if action is None:
            return
        row = action.data()
        if row is None:
            return
        self.delete_row(int(row))

    def row_summary(self, r, limit=2) -> str:
        """A couple of identifying values of a row, for the delete question."""
        parts = []
        for c, col in enumerate(self.columns):
            if col in self.key_columns:
                continue
            item = self.data_grid.item(r, c)
            text = item.text() if item is not None else ""
            if text:
                parts.append(f"{col}: {text[:40]}")
            if len(parts) >= limit:
                break
        return ", ".join(parts)

    def delete_row(self, r):
        """Delete a row asynchronously, after a question that says which row."""
        item = self.data_grid.item(r, 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not key or not self.key_columns:
            return
        table = self.current_table
        summary = self.row_summary(r)
        what = self._key_text(key) + (f" ({summary})" if summary else "")
        if not confirm(self, f"Delete this row from {table_label(table)}?",
                       f"{what} will be deleted from the {table} table. This cannot be undone.",
                       yes_label="Delete row", destructive=True):
            return
        where, key_params = self._where_for(key)

        def _do_delete():
            return self.db.execute_update(
                f"DELETE FROM {_safe_identifier(table)} WHERE {where}", key_params)

        def _on_done(result):
            if self._is_closing:
                return
            if not result:
                QMessageBox.warning(self, "Not deleted",
                                    getattr(result, "error", "") or "The database refused it.")
                return
            if getattr(result, "rows", 1) == 0:
                QMessageBox.information(self, "Delete row",
                                        "That row was not there any more.")
            else:
                self._log_change("DELETE", key, None, summary or what, None)
            row_now = self._row_of_key(key)
            if row_now >= 0:
                self.data_grid.removeRow(row_now)

        def _on_error(msg):
            if self._is_closing:
                return
            QMessageBox.warning(self, "Not deleted", msg)

        self._run_async(_do_delete, _on_done, _on_error)

    def _row_of_key(self, key) -> int:
        for r in range(self.data_grid.rowCount()):
            item = self.data_grid.item(r, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                return r
        return -1

    # ------------------------------------------------------------ maintenance
    def open_maintenance(self):
        dialog = MaintenanceDialog(self)
        if dialog.exec() and dialog.confirmed():
            self.purge_stock_library(confirmed=True)

    def _referencing_tables(self, table="stock_library"):
        """Tables with a foreign key to this one (PostgreSQL)."""
        if self._is_sqlite_backend():
            return []
        rows = self.db.execute_query(
            """
            SELECT DISTINCT tc.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = %s
              AND tc.table_name <> %s
            ORDER BY tc.table_name
            """, (table, table), fetch="all") or []
        return [r["table_name"] if isinstance(r, dict) else r[0] for r in rows]

    def purge_stock_library(self, confirmed=False, cascade=False):
        """
        Empty the stock library table. Only after the typed confirmation of
        the maintenance dialog; without CASCADE, so tables that point at it
        are never emptied silently - if they block it, they are named and the
        question is asked again.
        """
        if not confirmed:
            self.open_maintenance()
            return

        sqlite = self._is_sqlite_backend()

        def _do_purge():
            if sqlite:
                return self.db.execute_update("DELETE FROM stock_library")
            return self.db.execute_update(
                "TRUNCATE TABLE stock_library RESTART IDENTITY" + (" CASCADE" if cascade else ""))

        def _on_done(result):
            if self._is_closing:
                return
            if not result:
                error = getattr(result, "error", "") or "the database refused it"
                blocked = self._referencing_tables() if "foreign key" in error.lower() else []
                if blocked and not cascade:
                    if confirm(self, "Purge stock library",
                               "Other tables point at the stock library and would be emptied too:\n\n"
                               + "\n".join(f"  {table_label(t)} ({t})" for t in blocked)
                               + "\n\nEmpty them as well?",
                               yes_label="Empty them too", destructive=True):
                        self.purge_stock_library(confirmed=True, cascade=True)
                    return
                QMessageBox.critical(self, "Purge stock library",
                                     f"The stock library was not cleared:\n\n{error}")
                return
            log = getattr(self.db, "log_change_event", None)
            if callable(log):
                try:
                    log(None, "table stock_library", "all rows", self._username(), "DELETE",
                        None, "purged" + (" with the tables that refer to it" if cascade else ""), None)
                except Exception as exc:
                    logging.warning("Purge not written to history: %s", exc)
            QMessageBox.information(self, "Purge stock library", "The stock library is now empty.")
            if self.dashboard_view.isVisible():
                self.dashboard_view.load_stats()

        def _on_error(msg):
            if self._is_closing:
                return
            QMessageBox.critical(self, "Purge stock library", f"The stock library was not cleared:\n\n{msg}")

        self._run_async(_do_purge, _on_done, _on_error)

    def run_custom_sql(self):
        """Execute custom SQL asynchronously."""
        q = self.txt_sql.toPlainText().strip()
        if not q: return

        if q.upper().startswith("SELECT"):
            def _do_query():
                # execute_sql reports the columns, the rows and the database's
                # own error. execute_query returned None for a bad statement,
                # which showed as nothing at all over the previous table.
                return self.db.execute_sql(q, max_rows=SQL_RESULT_LIMIT)

            def _on_done(result):
                if self._is_closing:
                    return
                # Clear first: a result must never be shown over, or mixed
                # with, the rows of the table that was open before.
                self.is_loading = True
                try:
                    self.current_table = None
                    self.primary_key_col = None
                    self.data_grid.clear()
                    self.data_grid.setRowCount(0)
                    self.data_grid.setColumnCount(0)
                    if not result.ok:
                        self.lbl_table_name.setText("SQL Result - error")
                        QMessageBox.critical(self, "The query failed", result.error)
                        return
                    count = len(result.rows)
                    self.lbl_table_name.setText(
                        "SQL Result - %d row%s%s" % (count, "" if count == 1 else "s",
                                                     " (first %d shown)" % SQL_RESULT_LIMIT
                                                     if result.truncated else ""))
                    cols = result.columns
                    self.data_grid.setColumnCount(len(cols))
                    self.data_grid.setHorizontalHeaderLabels(cols)
                    self.data_grid.setRowCount(count)
                    for r, row in enumerate(result.rows):
                        for c, col in enumerate(cols):
                            val = row.get(col)
                            item = QTableWidgetItem("NULL" if val is None else str(val))
                            # A query result is not a table: not editable.
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                            self.data_grid.setItem(r, c, item)
                finally:
                    self.is_loading = False

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
                return self.db.execute_sql(q)

            def _on_done(result):
                if self._is_closing:
                    return
                if not result.ok:
                    # It used to say "Executed." whatever the database said.
                    QMessageBox.critical(self, "Not executed", result.error)
                    return
                logging.info(f"Custom SQL executed: {q[:200]}")
                QMessageBox.information(
                    self, "Done",
                    "%d row%s affected." % (result.rowcount, "" if result.rowcount == 1 else "s"))

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
