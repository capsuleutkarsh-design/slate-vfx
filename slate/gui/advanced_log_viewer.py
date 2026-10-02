"""
Audit Logs: what the workstations logged, what changed in the database, and
who did what (the audit trail).

Three tabs:
- Workstation logs - each client's <server>/Logs/<HOST>_<user>.log, parsed into
  time / level / source / message, with a level filter, search and the full
  traceback of the selected line.
- Change history - the change_history table (shots, tasks, settings...), with
  the item each change was made to, a date range and paging.
- Audit trail - sign-ins, user and role changes and admin actions: the daily
  JSON files AuditLogger writes to <server>/Logs/Audit and the Admin Panel's
  own <server>/Config/audit.log. They were written all along and never shown.

The parsing and wording are plain functions at the top of the file, so they
can be tested without a window.
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
    QListWidgetItem, QLabel, QLineEdit, QComboBox, QTabWidget, QTableWidget,
    QAbstractItemView, QCheckBox, QPlainTextEdit, QDateEdit
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QDate

from ..core.infra.global_config import GlobalConfig
from ..core.infra.database_manager import DatabaseManager
from ..core.infra.app_context import AppContext
from ..core.domain.dates import format_datetime
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.empty_state import EmptyState
from slate.gui.core.icons import icon as draw_icon
from slate.gui.core.table_style import style_table
from slate.gui.components.table_tools import (
    KeepSelection, make_item, setup_table, selected_rows, key_of_row,
)

logger = logging.getLogger(__name__)

# How much of a workstation log is read, and how many lines are shown.
READ_LIMIT_BYTES = 500 * 1024
ROW_LIMIT = 2000
# Change history rows per page.
HISTORY_PAGE = 2000


# ==========================================================================
# Workstation logs - parsing
# ==========================================================================
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}
_LEVEL_WORD = r"(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)"

_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:[,.]\d+)?\s*(.*)$")
# The formats Slate and its tools write after the time:
#   [ERROR] slate.x: message          (logging_utils - the workstation logs)
#   - ERROR - message                  ('%(asctime)s - %(levelname)s - %(message)s')
#   - slate.x - ERROR - message        ('%(asctime)s - %(name)s - %(levelname)s - ...')
#   ERROR root: message / ERROR message
_FORMATS = (
    re.compile(r"^\[" + _LEVEL_WORD + r"\]\s*(?:(?P<source>[\w.\-]+):\s)?(?P<msg>.*)$", re.S),
    re.compile(r"^-\s*(?:(?P<source>[\w.\-]+)\s+-\s+)?" + _LEVEL_WORD + r"\s+-\s+(?P<msg>.*)$", re.S),
    re.compile(r"^" + _LEVEL_WORD + r"\b:?\s*(?:(?P<source>[\w.\-]+):\s)?(?P<msg>.*)$", re.S),
)


def canonical_level(word) -> str:
    word = str(word or "").upper()
    return _LEVEL_ALIASES.get(word, word if word in LEVELS else "")


def split_level(remainder: str):
    """(level, source, message) from what follows the timestamp. Unknown level is ''."""
    text = remainder.strip()
    for pattern in _FORMATS:
        m = pattern.match(text)
        if m:
            return canonical_level(m.group("level")), (m.group("source") or ""), m.group("msg").strip()
    # Never a made-up INFO: a line whose level cannot be read says so.
    return "", "", text


def parse_log_line(line: str):
    """One log line as a dict, or None when it does not start with a timestamp."""
    m = _TIMESTAMP.match(line)
    if not m:
        return None
    day, clock, remainder = m.groups()
    level, source, message = split_level(remainder)
    return {"date": day, "time": clock, "level": level, "source": source,
            "msg": message, "detail": message}


def parse_log_text(text: str, limit: int = ROW_LIMIT):
    """
    Entries of a log, oldest first. A line without a timestamp (a traceback)
    belongs to the entry above it: it is kept in that entry's detail and the
    message stays on one line. Returns (entries, dates, capped).
    """
    entries = []
    dates = set()
    for line in text.splitlines():
        entry = parse_log_line(line)
        if entry is not None:
            dates.add(entry["date"])
            entries.append(entry)
        elif entries:
            entries[-1]["detail"] += "\n" + line
        elif line.strip():
            entries.append({"date": "", "time": "", "level": "", "source": "",
                            "msg": line, "detail": line})
    capped = len(entries) > limit
    if capped:
        entries = entries[-limit:]
    for entry in entries:
        entry["explanation"] = LogInterpreter.explain(entry["detail"])
    return entries, dates, capped


class LogInterpreter:
    """
    A plain sentence for errors a person can act on, and nothing for the rest.

    It used to match plain words, so 'No critical issues found' read as
    'Serious System Failure' and an INFO line containing 'Warning:' raised an
    alarm; every other row said 'System Info'. Only exception names and the
    operating system's own disk-full messages are matched now.
    """
    RULES = [
        (r"\bPermissionError\b|\bWinError 5\b|Access is denied",
         "Slate was not allowed to open a file or folder - check its permissions."),
        (r"\bFileNotFoundError\b|\bWinError [23]\b",
         "A file or folder Slate needed was missing - it may have been moved or deleted."),
        (r"No space left on device|\bWinError 112\b|There is not enough space on the disk",
         "A disk is full - free some space on it."),
        (r"\bConnectionRefusedError\b|\bWinError 10061\b",
         "A server refused the connection - check that it is running."),
        (r"\bTimeoutError\b|timed out",
         "Something took too long to answer - the network or a server may be slow."),
        (r"\bImportError\b|\bModuleNotFoundError\b",
         "Part of Slate is missing on this computer - reinstall or update it."),
        (r"\bKeyError\b",
         "Slate expected a value that was not there - report this to the developers."),
        (r"\bValueError\b",
         "Slate met a value in a form it did not expect - report this to the developers."),
        (r"\bOSError\b",
         "The operating system refused a file operation."),
    ]
    _COMPILED = [(re.compile(p, re.I), t) for p, t in RULES]

    @staticmethod
    def explain(message) -> str:
        text = str(message or "")
        for pattern, explanation in LogInterpreter._COMPILED:
            if pattern.search(text):
                return explanation
        return ""


def read_log_tail(path: Path, limit: int = READ_LIMIT_BYTES):
    """(text, size, truncated): the last `limit` bytes of a log, whole lines only."""
    size = path.stat().st_size
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        if size > limit:
            f.seek(size - limit)
            return f.read().partition("\n")[2], size, True
        return f.read(), size, False


def machine_label(stem: str) -> str:
    """'COMP-03_rahul' -> 'COMP-03 · rahul' (the file is <HOST>_<windows user>.log)."""
    host, _sep, user = str(stem).partition("_")
    return f"{host} · {user}" if user else host


def freshness(age_seconds: float):
    """(word, colour) for how recently a log was written."""
    if age_seconds < 60:
        return "live", Gate.OK
    if age_seconds < 300:
        return "recent", Gate.WARN
    return "quiet", Gate.TEXT_DIM


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


class _LogReader(QThread):
    """Reads and parses a log off the UI thread (it sits on a network share)."""
    done = Signal(object)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)

    def run(self):
        try:
            stat = self.path.stat()
            text, size, truncated = read_log_tail(self.path)
            entries, dates, capped = parse_log_text(text)
            self.done.emit({"path": self.path, "stamp": (stat.st_mtime, stat.st_size),
                            "entries": entries, "dates": dates, "size": size,
                            "truncated": truncated, "capped": capped})
        except Exception as exc:  # the file vanished, the share dropped...
            self.done.emit({"path": self.path, "error": str(exc)})


LEVEL_FILTERS = (("All levels", ()), ("Warnings and errors", ("WARNING", "ERROR", "CRITICAL")),
                 ("Errors only", ("ERROR", "CRITICAL")))


class SystemLogViewer(QWidget):
    """
    The workstations' own log files: pick a machine, read its log.
    """
    COLUMNS = ["Time", "Level", "Source", "Message", "What it means"]

    def __init__(self, log_root=None):
        super().__init__()
        if log_root is not None:
            self.log_root = Path(log_root)
        else:
            try:
                self.log_root = GlobalConfig.server_root() / "Logs"
            except Exception:
                self.log_root = Path(os.environ.get("SLATE_STUDIO_ROOT", str(Path.home() / "RuntimeData" / "Slate_Central"))) / "Logs" # Fallback

        self.current_file = None
        self.cached_lines = []
        self._stamp = None
        self._reader = None
        self._is_closing = False
        self._is_cleaned = False
        self.setup_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.auto_refresh)
        self.refresh_timer.start(5000) # Auto-refresh active log

    def setup_ui(self):
        layout = QHBoxLayout(self)

        # Left: the machines
        left = QWidget()
        v = QVBoxLayout(left); v.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Workstations")
        title.setStyleSheet("font-weight: 600;")
        v.addWidget(title)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self.load_log_file)
        v.addWidget(self.list_widget, 1)
        self.list_empty = EmptyState(
            "No workstation logs",
            "The log folder on the server was not found. Machines write their "
            "logs there once Slate runs on them.", glyph="folder")
        self.list_empty.hide()
        v.addWidget(self.list_empty, 1)

        btn_refresh_list = make_button("Refresh list", "secondary", on_click=self.refresh_list)
        v.addWidget(btn_refresh_list)

        # Right: the log
        right = QWidget()
        v2 = QVBoxLayout(right)
        # Inset like the table below it; the label used to touch the splitter.
        v2.setContentsMargins(10, 0, 0, 0)
        self.right_layout = v2

        head = QHBoxLayout()
        self.lbl_viewing = QLabel("Pick a machine to read its log")
        self.lbl_viewing.setStyleSheet(f"color: {Gate.ACCENT}; font-weight: 600;")
        head.addWidget(self.lbl_viewing)
        head.addStretch()
        self.chk_follow = QCheckBox("Follow")
        self.chk_follow.setToolTip("Keep the newest lines in view as the log grows")
        head.addWidget(self.chk_follow)
        v2.addLayout(head)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the log…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.populate_table)
        filters.addWidget(self.search, 1)
        self.level_filter = QComboBox()
        for text, levels in LEVEL_FILTERS:
            self.level_filter.addItem(text, levels)
        self.level_filter.currentIndexChanged.connect(self.populate_table)
        filters.addWidget(self.level_filter)
        self.date_filter = QComboBox()
        self.date_filter.addItem("All dates")
        self.date_filter.currentTextChanged.connect(self.apply_date_filter)
        filters.addWidget(self.date_filter)
        self.chk_explain = QCheckBox("Explain")
        self.chk_explain.setToolTip("Show a plain-language line for errors people can act on")
        self.chk_explain.setChecked(True)
        self.chk_explain.toggled.connect(self.toggle_explanation_column)
        filters.addWidget(self.chk_explain)
        v2.addLayout(filters)

        self.log_table = QTableWidget()
        self.log_table.setColumnCount(len(self.COLUMNS))
        self.log_table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.log_table, {"Time": ("interactive", 150), "Level": ("fixed", 90),
                                     "Source": ("interactive", 140), "Message": "stretch",
                                     "What it means": ("interactive", 260)})
        setup_table(self.log_table, multi_select=False)
        self.log_table.itemSelectionChanged.connect(self._show_detail)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Pick a line to see all of it, with its traceback.")
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.log_table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        v2.addWidget(split, 1)

        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        v2.addWidget(self.lbl_info)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)

        self.refresh_list()

    # ------------------------------------------------------------- machines
    def refresh_list(self):
        current = self.current_file
        self.list_widget.clear()
        if not self.log_root.exists():
            self.list_widget.hide()
            self.list_empty.show()
            return
        self.list_empty.hide()
        self.list_widget.show()

        try:
            files = sorted(self.log_root.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError as exc:
            logger.warning("Could not list workstation logs: %s", exc)
            files = []
        now = time.time()
        for f in files:
            try:
                mod_time = f.stat().st_mtime
            except OSError:
                continue
            word, colour = freshness(now - mod_time)
            item = QListWidgetItem(draw_icon("dot", colour, 12),
                                   f"{machine_label(f.stem)}\nupdated {_age(now - mod_time)}")
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(f"{f.name} - {word}, last written {format_datetime(datetime.fromtimestamp(mod_time))}")
            self.list_widget.addItem(item)
            if current is not None and Path(str(f)) == current:
                self.list_widget.setCurrentItem(item)

    def load_log_file(self, item):
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        self.current_file = Path(path_str)
        self._stamp = None
        self.lbl_viewing.setText(machine_label(self.current_file.stem))
        self.read_file()

    # ---------------------------------------------------------------- read
    def auto_refresh(self):
        """
        Re-read the log only when it changed, off the UI thread. It used to be
        re-read, the table rebuilt and scrolled to the bottom every 5 s, so the
        line you were reading jumped away.
        """
        if self._is_closing or not self.current_file or not self.isVisible():
            return
        try:
            stat = self.current_file.stat()
        except OSError:
            return
        if self._stamp == (stat.st_mtime, stat.st_size):
            return
        if self._reader is not None and self._reader.isRunning():
            return
        self._reader = _LogReader(self.current_file, self)
        self._reader.done.connect(self._on_read)
        self._reader.start()

    def read_file(self, auto=False):
        """Read the current log now (a machine was just picked)."""
        if self._is_closing or not self.current_file:
            return
        try:
            stat = self.current_file.stat()
            text, size, truncated = read_log_tail(self.current_file)
            entries, dates, capped = parse_log_text(text)
        except Exception as exc:
            self.lbl_info.setText(f"The log could not be read: {exc}")
            return
        self._on_read({"path": self.current_file, "stamp": (stat.st_mtime, stat.st_size),
                       "entries": entries, "dates": dates, "size": size,
                       "truncated": truncated, "capped": capped}, auto=auto)

    def _on_read(self, result, auto=True):
        if self._is_closing or result.get("path") != self.current_file:
            return
        if result.get("error"):
            self.lbl_info.setText(f"The log could not be read: {result['error']}")
            return
        self._stamp = result["stamp"]
        self.cached_lines = result["entries"]

        parts = []
        if result["truncated"]:
            parts.append(f"Showing the last 500 KB of a {result['size'] / 1024 / 1024:.1f} MB log")
        else:
            parts.append("Showing the whole log")
        if result["capped"]:
            parts.append(f"the last {ROW_LIMIT:,} entries")
        self.lbl_info.setText(" - ".join(parts) + ".")

        dates = result["dates"]
        current_filter = self.date_filter.currentText()
        self.date_filter.blockSignals(True)
        self.date_filter.clear()
        self.date_filter.addItem("All dates")
        self.date_filter.addItems(sorted(dates, reverse=True))
        if current_filter in dates:
            self.date_filter.setCurrentText(current_filter)
        self.date_filter.blockSignals(False)

        bar = self.log_table.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        self.populate_table()
        # The newest lines come into view on opening, and later only when
        # following or already reading the end - never while reading above.
        if not auto or self.chk_follow.isChecked() or at_bottom:
            self.log_table.scrollToBottom()

    # -------------------------------------------------------------- display
    def apply_date_filter(self, _text=None):
        self.populate_table()

    def toggle_explanation_column(self, state):
        self.log_table.setColumnHidden(4, not bool(state))

    def visible_entries(self):
        filter_date = self.date_filter.currentText()
        levels = self.level_filter.currentData() or ()
        text = self.search.text().strip().lower()
        rows = []
        for index, row in enumerate(self.cached_lines):
            if filter_date != "All dates" and row["date"] != filter_date:
                continue
            if levels and row["level"] not in levels:
                continue
            if text and text not in row["detail"].lower() and text not in row["source"].lower():
                continue
            rows.append((index, row))
        return rows

    def populate_table(self, *_args):
        rows = self.visible_entries()
        with KeepSelection(self.log_table):
            self.log_table.setRowCount(0)
            self.log_table.setRowCount(len(rows))
            # Keys survive a re-read: the same line keeps its place in the
            # file's order even when new lines arrive below it.
            seen = {}
            for r, (index, row) in enumerate(rows):
                stamp = f"{row['date']} {row['time']}".strip()
                base = (stamp, row["msg"][:120])
                seen[base] = seen.get(base, 0) + 1
                key = (base, seen[base])
                t_item = make_item(stamp, sort_value=stamp or None, key=key)
                t_item.setData(Qt.ItemDataRole.UserRole, index)
                self.log_table.setItem(r, 0, t_item)

                level = row["level"]
                colour = {"ERROR": Gate.BAD, "CRITICAL": Gate.BAD, "WARNING": Gate.WARN,
                          "DEBUG": Gate.TEXT_DIM}.get(level)
                l_item = make_item(level.title() if level else "", foreground=colour,
                                   sort_value=LEVELS.index(level) if level in LEVELS else None,
                                   align=Qt.AlignmentFlag.AlignCenter)
                self.log_table.setItem(r, 1, l_item)
                self.log_table.setItem(r, 2, make_item(row["source"]))

                message = row["msg"]
                if row["detail"] != row["msg"]:
                    message += "  (more…)"
                self.log_table.setItem(r, 3, make_item(message, tooltip=row["msg"][:500]))
                self.log_table.setItem(r, 4, make_item(row.get("explanation", ""),
                                                       foreground=Gate.ACCENT))
        self.toggle_explanation_column(self.chk_explain.isChecked())
        self._show_detail()

    def _show_detail(self):
        rows = selected_rows(self.log_table)
        if not rows:
            self.detail.setPlainText("")
            return
        item = self.log_table.item(rows[0], 0)
        index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if index is None or not (0 <= index < len(self.cached_lines)):
            self.detail.setPlainText("")
            return
        row = self.cached_lines[index]
        self.detail.setPlainText(row["detail"])

    def cleanup_resources(self):
        """Stop periodic refresh and clear transient state."""
        if self._is_cleaned:
            return

        self._is_closing = True
        if hasattr(self, "refresh_timer") and self.refresh_timer.isActive():
            self.refresh_timer.stop()
        if self._reader is not None:
            try:
                self._reader.wait(3000)
            except RuntimeError:
                pass
        self.current_file = None
        self.cached_lines = []
        self._is_cleaned = True

    def closeEvent(self, event):
        self.cleanup_resources()
        super().closeEvent(event)


# ==========================================================================
# Change history
# ==========================================================================
SECURITY_FIELDS = ("access_level", "roles", "role", "permissions", "password", "is_admin")


def _blank(value) -> bool:
    return value is None or str(value).strip() in ("", "None", "NULL", "null")


def item_label(row) -> str:
    """What a change was made to: 'shot SH012', 'SH012 · comp', 'project KLC'."""
    shot = row.get("shot_name")
    department = row.get("department")
    if not _blank(shot):
        text = str(shot)
        reel = row.get("reel")
        if not _blank(reel):
            text = f"{reel} / {text}"
        if not _blank(department):
            text += f" · {department}"
        return text
    kind = "" if _blank(row.get("entity_type")) else str(row.get("entity_type"))
    ident = "" if _blank(row.get("entity_id")) else str(row.get("entity_id"))
    return " ".join(part for part in (kind, ident) if part)


def describe_change(row) -> str:
    """
    One plain line for a change, from its non-empty parts only. Rows used to
    read ': ->' for a create and 'None: None -> None' for empty values.
    """
    action = str(row.get("action_type") or "").upper()
    field = "" if _blank(row.get("field_changed")) else str(row.get("field_changed"))
    old = "" if _blank(row.get("old_value")) else str(row.get("old_value"))
    new = "" if _blank(row.get("new_value")) else str(row.get("new_value"))
    item = item_label(row)

    if action in ("CREATE", "INSERT") and not field:
        return f"Created {item}".strip()
    if action == "DELETE" and not field:
        return f"Deleted {item}".strip()
    if field:
        if old and new:
            return f"{field}: {old} → {new}"
        if new:
            return f"{field} set to {new}"
        if old:
            return f"{field} cleared (was {old})"
        return f"{field} changed"
    if new:
        return new
    return action.title() or "Changed"


class AuditInterpreter:
    """
    Translates database actions into user-friendly summaries.
    """
    @staticmethod
    def analyze(action, field, old, new):
        action = str(action or "").upper()
        field = "" if _blank(field) else str(field)
        new = "" if _blank(new) else str(new)
        if action in ("CREATE", "INSERT"):
            return "Created"
        if action == "DELETE":
            return "Deleted"
        if field in SECURITY_FIELDS:
            return "Security change"
        if field == "status":
            if "Approv" in new: return "Approved"
            if "Review" in new: return "Needs review"
            if "Progress" in new: return "In progress"
            return "Status change"
        if field == "priority":
            return "Priority change"
        return ""


def action_colour(action, field=None):
    action = str(action or "").upper()
    if action == "DELETE":
        return Gate.BAD
    if not _blank(field) and str(field) in SECURITY_FIELDS:
        return Gate.WARN
    if action in ("CREATE", "INSERT"):
        return Gate.OK
    return None


def _timestamp_text(ts):
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts or "")


class DatabaseAuditViewer(QWidget):
    """
    The change_history table: who changed what, when.
    """
    COLUMNS = ["Time", "User", "Project", "Item", "Action", "Description", "What it means"]

    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.full_history = []
        self.total = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.populate_table)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        h = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search user, project, item or change…")
        self.search.setClearButtonEnabled(True)
        # Debounced: every key press used to rebuild 2,000 rows.
        self.search.textChanged.connect(self._search_timer.start)
        h.addWidget(self.search, 1)

        self.chk_range = QCheckBox("From")
        self.chk_range.setToolTip("Only show changes made between these two dates")
        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_to = QDateEdit(QDate.currentDate())
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("d MMM yyyy")
            edit.setEnabled(False)
        self.chk_range.toggled.connect(self.date_from.setEnabled)
        self.chk_range.toggled.connect(self.date_to.setEnabled)
        self.chk_range.toggled.connect(lambda *_: self.refresh_data())
        self.date_from.dateChanged.connect(lambda *_: self.chk_range.isChecked() and self.refresh_data())
        self.date_to.dateChanged.connect(lambda *_: self.chk_range.isChecked() and self.refresh_data())
        h.addWidget(self.chk_range)
        h.addWidget(self.date_from)
        h.addWidget(QLabel("to"))
        h.addWidget(self.date_to)

        self.chk_explain = QCheckBox("Explain")
        self.chk_explain.setChecked(True)
        self.chk_explain.toggled.connect(self.toggle_explanation_column)
        h.addWidget(self.chk_explain)

        self.btn_export = make_button("Export", "secondary", on_click=self.export_rows,
                                      tooltip="Save the rows shown as CSV or Excel")
        h.addWidget(self.btn_export)
        btn_refresh = make_button("Refresh", "secondary", on_click=self.refresh_data)
        btn_refresh.setIcon(draw_icon("refresh"))
        h.addWidget(btn_refresh)
        layout.addLayout(h)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.table, {"Time": ("interactive", 150), "User": "contents", "Project": "contents",
                                 "Item": ("interactive", 160), "Action": "contents",
                                 "Description": "stretch", "What it means": ("interactive", 140)})
        setup_table(self.table)
        layout.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        foot.addWidget(self.lbl_count)
        foot.addStretch(1)
        self.btn_more = make_button("Load more", "secondary", on_click=self.load_more)
        self.btn_more.hide()
        foot.addWidget(self.btn_more)
        layout.addLayout(foot)

        self.refresh_data()

    # ----------------------------------------------------------------- data
    def _range(self):
        if not self.chk_range.isChecked():
            return None, None
        start = self.date_from.date().toPython()
        end = self.date_to.date().toPython()
        if end < start:
            start, end = end, start
        return (datetime.combine(start, datetime.min.time()),
                datetime.combine(end + timedelta(days=1), datetime.min.time()))

    def _fetch(self, offset=0):
        if not hasattr(self.db_manager, 'get_history'):
            return []
        since, until = self._range()
        kwargs = {"limit": HISTORY_PAGE}
        if offset:
            kwargs["offset"] = offset
        if since is not None:
            kwargs["since"] = since
            kwargs["until"] = until
        try:
            return self.db_manager.get_history(**kwargs) or []
        except TypeError:
            # A backend without paging: the first page is all there is.
            return self.db_manager.get_history(limit=HISTORY_PAGE) if not offset else []

    def _count(self):
        try:
            from slate.core.infra.change_history import count_history
            since, until = self._range()
            backend = getattr(self.db_manager, "backend", self.db_manager)
            return count_history(backend, since=since, until=until)
        except Exception as exc:
            logger.debug("Change history could not be counted: %s", exc)
            return None

    @staticmethod
    def _prepare(row):
        r = dict(row)
        r['timestamp'] = _timestamp_text(r.get('timestamp'))
        r['_date'] = r['timestamp'][:10] if r['timestamp'] else ""
        r['_user'] = r.get('display_name') or r.get('user_name') or r.get('author') or ""
        r['_item'] = item_label(r)
        r['_description'] = describe_change(r)
        r['_analysis'] = AuditInterpreter.analyze(r.get('action_type'), r.get('field_changed'),
                                                  r.get('old_value'), r.get('new_value'))
        return r

    def refresh_data(self):
        try:
            rows = self._fetch()
            self.full_history = [self._prepare(row) for row in rows]
            self.total = self._count()
        except Exception as e:
            logger.exception(f"Audit load error: {e}")
            self.full_history = []
            self.total = None
            self.lbl_count.setText("The change history could not be read.")
            return
        self.populate_table()

    def load_more(self):
        rows = self._fetch(offset=len(self.full_history))
        self.full_history.extend(self._prepare(row) for row in rows)
        self.populate_table()

    def apply_date_filter(self, _text=None):
        self.refresh_data()

    def toggle_explanation_column(self, state):
        self.table.setColumnHidden(6, not bool(state))

    def visible_rows(self):
        text = self.search.text().strip().lower()
        out = []
        for row in self.full_history:
            values = [row['timestamp'], str(row['_user']), str(row.get('project_code') or ''),
                      row['_item'], str(row.get('action_type') or ''), row['_description'],
                      row['_analysis']]
            if text and not any(text in v.lower() for v in values):
                continue
            out.append((row, values))
        return out

    def populate_table(self):
        rows = self.visible_rows()
        with KeepSelection(self.table):
            self.table.setRowCount(0)
            self.table.setRowCount(len(rows))
            for r, (row, values) in enumerate(rows):
                for c, val in enumerate(values):
                    if c == 0:
                        item = make_item(val, sort_value=val or None)
                    elif c == 4:
                        item = make_item(val.title() if val else "",
                                         foreground=action_colour(val, row.get('field_changed')))
                    elif c == 6:
                        item = make_item(val, foreground=Gate.ACCENT)
                    elif c == 1 and not val:
                        item = make_item("Unknown", foreground=Gate.TEXT_DIM)
                    else:
                        item = make_item(val, tooltip=val if c == 5 and len(val) > 60 else None)
                    self.table.setItem(r, c, item)
        self.toggle_explanation_column(self.chk_explain.isChecked())

        loaded = len(self.full_history)
        shown = len(rows)
        if self.total is not None and self.total > loaded:
            if shown == loaded:
                text = f"Showing the newest {loaded:,} of {self.total:,} changes."
            else:
                text = f"Showing {shown:,} of the newest {loaded:,} changes ({self.total:,} in all)."
            self.lbl_count.setText(text + " Load more for older ones.")
            self.btn_more.show()
        else:
            self.lbl_count.setText(f"Showing all {loaded:,} changes." if shown == loaded
                                   else f"Showing {shown:,} of {loaded:,} changes.")
            self.btn_more.setVisible(self.total is None and loaded >= HISTORY_PAGE)

    def filter_table(self):
        self.populate_table()

    def export_rows(self):
        from slate.gui.core.data_display import export_table_dialog
        return export_table_dialog(self, self.table, "change_history", title="Export change history")


# ==========================================================================
# Audit trail
# ==========================================================================
_LEGACY_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+([^:]+):\s?(.*)$")


def read_audit_trail(audit_dir=None, legacy_file=None, limit=5000):
    """
    Everything the audit trail holds, newest first: the daily JSON-lines files
    of AuditLogger (sign-ins, user changes...) and the Admin Panel's own log
    ('[2026-09-30 11:00:00] admin: Broadcast Alert: ...').
    """
    entries = []
    if audit_dir is not None:
        folder = Path(audit_dir)
        files = sorted(folder.glob("audit_*.log"), reverse=True) if folder.exists() else []
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.warning("Could not read %s: %s", path, exc)
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                stamp = str(data.get("timestamp") or "").replace("T", " ")[:19]
                entries.append({"time": stamp, "user": str(data.get("user") or ""),
                                "type": str(data.get("type") or ""),
                                "status": str(data.get("status") or ""),
                                "details": str(data.get("details") or "")})
            if len(entries) >= limit * 2:
                break
    if legacy_file is not None:
        legacy = Path(legacy_file)
        if legacy.exists():
            try:
                lines = legacy.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                logger.warning("Could not read %s: %s", legacy, exc)
                lines = []
            for line in lines[-limit:]:
                m = _LEGACY_LINE.match(line.strip())
                if m:
                    entries.append({"time": m.group(1), "user": m.group(2).strip(),
                                    "type": "ADMIN", "status": "", "details": m.group(3)})
    entries.sort(key=lambda e: e["time"], reverse=True)
    return entries[:limit]


TRAIL_TYPES = {"AUTH": "Sign-in", "USER_MGMT": "Users", "ROLE_MGMT": "Roles",
               "SYSTEM": "System", "ADMIN": "Admin action"}


def trail_details(entry: dict) -> str:
    """
    The details column: role changes name permissions as Users & Roles does
    ("Timeline Viewer", not the stored key "Shot Review").
    """
    details = entry.get("details") or ""
    if entry.get("type") == "ROLE_MGMT":
        try:
            from slate.core.domain.permissions_catalog import describe_role_change
            return describe_role_change(details)
        except Exception as exc:
            logger.debug("Role change not relabelled: %s", exc)
    return details


class AuditTrailViewer(QWidget):
    COLUMNS = ["Time", "User", "Type", "Result", "Details"]

    def __init__(self, audit_dir=None, legacy_file=None):
        super().__init__()
        self.audit_dir = audit_dir
        self.legacy_file = legacy_file
        self.entries = []
        layout = QVBoxLayout(self)

        h = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search user, type or details…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.populate_table)
        h.addWidget(self.search, 1)
        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", "")
        for key, text in TRAIL_TYPES.items():
            self.type_filter.addItem(text, key)
        self.type_filter.currentIndexChanged.connect(self.populate_table)
        h.addWidget(self.type_filter)
        self.chk_failures = QCheckBox("Failures only")
        self.chk_failures.toggled.connect(self.populate_table)
        h.addWidget(self.chk_failures)
        btn = make_button("Refresh", "secondary", on_click=self.refresh_data)
        btn.setIcon(draw_icon("refresh"))
        h.addWidget(btn)
        layout.addLayout(h)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        style_table(self.table, {"Time": ("interactive", 150), "User": "contents", "Type": "contents",
                                 "Result": "contents", "Details": "stretch"})
        setup_table(self.table)
        layout.addWidget(self.table, 1)
        self.empty = EmptyState.over(self.table, "Nothing in the audit trail yet",
                                     "Sign-ins, user and role changes and admin actions appear here.",
                                     glyph="shield")
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        layout.addWidget(self.lbl_count)
        self.refresh_data()

    def refresh_data(self):
        try:
            self.entries = read_audit_trail(self.audit_dir, self.legacy_file)
        except Exception as exc:
            logger.exception("Audit trail could not be read: %s", exc)
            self.entries = []
        self.populate_table()

    def populate_table(self, *_args):
        text = self.search.text().strip().lower()
        wanted = self.type_filter.currentData() or ""
        failures = self.chk_failures.isChecked()
        rows = []
        for e in self.entries:
            if wanted and e["type"] != wanted:
                continue
            if failures and e["status"].upper() not in ("FAILURE", "FAILED", "WARNING"):
                continue
            values = [e["time"], self._who(e["user"]), TRAIL_TYPES.get(e["type"], e["type"].title()),
                      e["status"].title(), trail_details(e)]
            if text and not any(text in str(v).lower() for v in values):
                continue
            rows.append((e, values))
        with KeepSelection(self.table):
            self.table.setRowCount(0)
            self.table.setRowCount(len(rows))
            for r, (e, values) in enumerate(rows):
                for c, val in enumerate(values):
                    colour = None
                    if c == 3 and e["status"].upper() in ("FAILURE", "FAILED"):
                        colour = Gate.BAD
                    elif c == 3 and e["status"].upper() == "WARNING":
                        colour = Gate.WARN
                    self.table.setItem(r, c, make_item(val, sort_value=val if c == 0 else None,
                                                       foreground=colour,
                                                       tooltip=val if c == 4 and len(val) > 80 else None))
        self.empty.set_filtered(bool(self.entries) and not rows, noun="entries")
        self.lbl_count.setText(f"{len(rows):,} of {len(self.entries):,} entries.")

    @staticmethod
    def _who(user):
        if not user or user.upper() == "SYSTEM":
            return user or ""
        try:
            from slate.core.domain.people import label
            return label(user)
        except Exception:
            return user


# ==========================================================================
# The tab
# ==========================================================================
class UnifiedLogViewer(QWidget):
    """
    Main Tab containing the workstation logs, the change history and the audit trail.
    """
    def __init__(self, db_manager=None, app_context=None, audit_file=None, audit_dir=None, log_root=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.db_manager = db_manager or self.app_context.db_manager()

        if audit_dir is None:
            try:
                from slate.core.infra.audit_logger import AuditLogger
                audit_dir = AuditLogger.log_directory()
            except Exception:
                audit_dir = None

        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        self.sys_logs = SystemLogViewer(log_root)
        self.db_audit = DatabaseAuditViewer(self.db_manager)
        self.audit_trail = AuditTrailViewer(audit_dir, audit_file)

        self.tabs.addTab(self.sys_logs, "Workstation logs")
        self.tabs.addTab(self.db_audit, "Change history")
        self.tabs.addTab(self.audit_trail, "Audit trail")

        layout.addWidget(self.tabs)

    def refresh_all(self):
        self.sys_logs.refresh_list()
        self.db_audit.refresh_data()
        self.audit_trail.refresh_data()

    def cleanup_resources(self):
        if hasattr(self, "sys_logs") and hasattr(self.sys_logs, "cleanup_resources"):
            self.sys_logs.cleanup_resources()

    def closeEvent(self, event):
        self.cleanup_resources()
        super().closeEvent(event)
