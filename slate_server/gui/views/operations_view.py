"""
Operations: backups, maintenance, logs and diagnostics.

None of this had a screen. pg_dump, pg_restore, vacuumdb and reindexdb are all
bundled beside the server and none of them could be run from it; the diagnostics
existed as a command-line script; and the log grew without limit while the
dashboard showed a few lines of it held in memory.

Everything on this screen is read-only until a button is pressed, and the two
buttons that can lose data - Restore, and Clear log - say exactly what they will
destroy before they do it.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..design_system import C, T


def _panel() -> QWidget:
    box = QWidget()
    box.setStyleSheet(f"""
        QWidget {{
            background-color: {C.BG_SURFACE};
            border: 1px solid {C.BORDER_DEFAULT};
            border-radius: 12px;
        }}
    """)
    return box


def _heading(text: str, note: str = "") -> QWidget:
    holder = QWidget()
    holder.setStyleSheet("background: transparent; border: none;")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    title = QLabel(text)
    title.setStyleSheet(
        f"font-size: 15px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY}; "
        f"background: transparent; border: none;")
    layout.addWidget(title)

    if note:
        sub = QLabel(note)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: 12px; color: {C.TEXT_SECONDARY}; background: transparent; "
            f"border: none;")
        layout.addWidget(sub)
    return holder


def _button(text: str, kind: str = "secondary") -> QPushButton:
    colours = {
        "primary": (C.ACCENT_PRIMARY, "#FFFFFF"),
        "secondary": (C.BG_SURFACE_HOVER, C.TEXT_PRIMARY),
        "danger": ("#D9635F", "#FFFFFF"),
    }
    background, foreground = colours.get(kind, colours["secondary"])
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(34)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {background};
            color: {foreground};
            border: 1px solid {C.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 0 14px;
            font-size: 13px;
            font-weight: {T.WEIGHT_SEMI};
        }}
        QPushButton:hover {{ border-color: {C.ACCENT_PRIMARY}; }}
        QPushButton:disabled {{ color: {C.TEXT_SECONDARY}; }}
    """)
    return btn


def every(days: int) -> str:
    """'Every day', 'Every 7 days'."""
    return "Every day" if int(days) == 1 else "Every %d days" % int(days)


def age(days: int) -> str:
    """'today', '1 day', '30 days'."""
    days = int(days)
    return "today" if days == 0 else "1 day" if days == 1 else "%d days" % days


def _table(headers) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setStyleSheet(f"""
        QTableWidget {{
            background-color: {C.BG_ROOT};
            color: {C.TEXT_PRIMARY};
            border: 1px solid {C.BORDER_DEFAULT};
            border-radius: 8px;
            gridline-color: {C.BORDER_DEFAULT};
            font-size: 12px;
        }}
        QHeaderView::section {{
            background-color: {C.BG_SURFACE};
            color: {C.TEXT_SECONDARY};
            border: none;
            border-bottom: 1px solid {C.BORDER_DEFAULT};
            padding: 6px 8px;
            font-size: 11px;
            font-weight: {T.WEIGHT_BOLD};
        }}
    """)
    return table


class OperationsView(QWidget):
    """Backups, maintenance, the log, and the diagnostics report."""

    backup_requested = Signal()
    prune_requested = Signal(int, int)
    restore_requested = Signal(str)
    job_requested = Signal(str)
    diagnostics_requested = Signal()
    open_log_requested = Signal()
    clear_log_requested = Signal()
    force_kill_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._backups = []
        self.setup_ui()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 40)
        root.setSpacing(22)

        title = QLabel("Operations")
        title.setStyleSheet(
            f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        root.addWidget(title)

        subtitle = QLabel(
            "Everything here acts on the database this server is serving. "
            "Check the data directory on the Dashboard first if you are not sure "
            "which one that is.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"font-size: 13px; color: {C.TEXT_SECONDARY};")
        root.addWidget(subtitle)

        root.addWidget(self._backup_panel())
        root.addWidget(self._maintenance_panel(), 1)
        root.addWidget(self._log_panel())
        root.addWidget(self._danger_panel())

    # ------------------------------------------------------------------ backups
    def _backup_panel(self) -> QWidget:
        panel = _panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(_heading(
            "Backups",
            "A copy of the whole database: the studio's tracking, leave and "
            "attendance records."))
        header.addStretch()

        self.lbl_last_backup = QLabel("No backup yet")
        self.lbl_last_backup.setStyleSheet(
            f"font-size: 13px; font-weight: {T.WEIGHT_SEMI}; color: #D9635F; "
            f"background: transparent; border: none;")
        header.addWidget(self.lbl_last_backup, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.btn_backup = _button("Back up now", "primary")
        self.btn_backup.clicked.connect(self.backup_requested.emit)
        controls.addWidget(self.btn_backup)

        controls.addSpacing(20)
        keep_label = QLabel("Keep")
        keep_label.setStyleSheet(
            f"color: {C.TEXT_SECONDARY}; background: transparent; border: none;")
        controls.addWidget(keep_label)

        self.spin_keep_days = QSpinBox()
        self.spin_keep_days.setRange(1, 3650)
        self.spin_keep_days.setValue(30)
        self.spin_keep_days.setSuffix(" days")
        self.spin_keep_days.setStyleSheet(
            f"background: {C.BG_ROOT}; color: {C.TEXT_PRIMARY}; "
            f"border: 1px solid {C.BORDER_DEFAULT}; border-radius: 4px; padding: 4px;")
        controls.addWidget(self.spin_keep_days)

        self.spin_keep_least = QSpinBox()
        self.spin_keep_least.setRange(1, 500)
        self.spin_keep_least.setValue(7)
        self.spin_keep_least.setPrefix("always keep ")
        self.spin_keep_least.setStyleSheet(self.spin_keep_days.styleSheet())
        self.spin_keep_least.setToolTip(
            "The newest this many are never deleted, however old they are. "
            "Without a floor, a server left off for two months would delete "
            "every backup it had on the next tidy-up.")
        controls.addWidget(self.spin_keep_least)

        self.btn_prune = _button("Delete old backups")
        self.btn_prune.clicked.connect(
            lambda: self.prune_requested.emit(self.spin_keep_days.value(),
                                              self.spin_keep_least.value()))
        controls.addWidget(self.btn_prune)
        controls.addStretch()
        # Replacing the whole database is not a neighbour of the routine
        # backup: it sits at the far end, and asks for RESTORE to be typed.
        self.btn_restore = _button("Restore from file\u2026", "danger")
        self.btn_restore.clicked.connect(self._pick_restore_file)
        controls.addWidget(self.btn_restore)
        layout.addLayout(controls)

        self.table_backups = _table(["Taken", "Age", "Database", "Size", "File"])
        self.table_backups.setMaximumHeight(160)
        head = self.table_backups.horizontalHeader()
        head.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_backups)
        self.lbl_no_backups = QLabel("No backups yet. Back up now makes the first one.")
        self.lbl_no_backups.setStyleSheet(
            f"color: {C.TEXT_SECONDARY}; background: transparent; border: none;")
        layout.addWidget(self.lbl_no_backups)

        return panel

    def _pick_restore_file(self):
        start = ""
        if self._backups:
            start = str(self._backups[0]["path"].parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a backup to restore", start, "Slate backups (*.dump)")
        if path:
            self.restore_requested.emit(path)

    def set_backups(self, rows, latest_note: str, overdue: bool):
        self._backups = list(rows or [])
        self.lbl_last_backup.setText(latest_note)
        self.lbl_last_backup.setStyleSheet(
            f"font-size: 13px; font-weight: {T.WEIGHT_SEMI}; "
            f"color: {'#D9635F' if overdue else '#5FBF8F'}; "
            f"background: transparent; border: none;")

        from slate_server.core.server_facts import human_size

        self.table_backups.setRowCount(len(self._backups))
        self.table_backups.setVisible(bool(self._backups))
        self.lbl_no_backups.setVisible(not self._backups)
        for r, row in enumerate(self._backups):
            cells = [
                row["taken_at"].strftime("%Y-%m-%d %H:%M"),
                age(row["age_days"]),
                row.get("database") or "-",
                human_size(row["size_bytes"]),
                row["name"],
            ]
            for c, text in enumerate(cells):
                self.table_backups.setItem(r, c, QTableWidgetItem(text))

    # -------------------------------------------------------------- maintenance
    def _maintenance_panel(self) -> QWidget:
        panel = _panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(_heading(
            "Maintenance",
            "When each job last ran. \"Never\" means it has not run yet - check "
            "the schedule."))

        # Last run and its state in one column ("Never run"); the job's name
        # whole, and what it does wrapped in the rest.
        self.table_jobs = _table(["Job", "Every", "Last run", "What it does"])
        self.table_jobs.setWordWrap(True)
        head = self.table_jobs.horizontalHeader()
        for column in (0, 1, 2):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_jobs.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # There are four jobs and there always will be, so the table should show
        # four. Left to the layout it was given 51 pixels for 192 pixels of rows
        # and showed one, which makes a panel about jobs nobody can tell have
        # stopped into a panel that hides three of them.
        self.table_jobs.setMinimumHeight(
            self.table_jobs.horizontalHeader().height()
            + self.table_jobs.verticalHeader().defaultSectionSize() * 4 + 8)
        layout.addWidget(self.table_jobs, 1)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        for key, label in (("vacuum", "Clean up the database (vacuum)"),
                           ("reindex", "Rebuild indexes"),
                           ("comp_off", "Credit comp off")):
            btn = _button(label)
            btn.clicked.connect(lambda _checked=False, job=key: self.job_requested.emit(job))
            controls.addWidget(btn)
        controls.addStretch()

        self.btn_diagnostics = _button("Run diagnostics", "primary")
        self.btn_diagnostics.setToolTip(
            "The same checks as the command-line doctor: configuration, database, "
            "server settings, the pool and the update channel.")
        self.btn_diagnostics.clicked.connect(self.diagnostics_requested.emit)
        controls.addWidget(self.btn_diagnostics)
        layout.addLayout(controls)

        return panel

    def set_jobs(self, rows):
        rows = list(rows or [])
        self.table_jobs.setRowCount(len(rows))
        for r, row in enumerate(rows):
            when = row["last_at"]
            state = str(row["state"] or "")
            last = (when.strftime("%Y-%m-%d %H:%M") + (" - " + state if state else "")) if when \
                else (state[:1].upper() + state[1:] if state else "Never run")
            cells = [
                row["title"],
                every(row["every_days"]),
                last,
                row["why"],
            ]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2:
                    if row["last_ok"] is False:
                        item.setForeground(Qt.GlobalColor.red)
                    elif row["overdue"]:
                        item.setForeground(Qt.GlobalColor.darkYellow)
                if row["last_message"]:
                    item.setToolTip(row["last_message"])
                self.table_jobs.setItem(r, c, item)
        self.table_jobs.resizeRowsToContents()

    # ---------------------------------------------------------------------- log
    def _log_panel(self) -> QWidget:
        panel = _panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(_heading(
            "Server log",
            "PostgreSQL's own log. Trim it when it gets large."))
        header.addStretch()

        self.lbl_log_size = QLabel("-")
        self.lbl_log_size.setStyleSheet(
            f"font-size: 13px; color: {C.TEXT_SECONDARY}; background: transparent; "
            f"border: none;")
        header.addWidget(self.lbl_log_size, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_open_log = _button("Open log")
        self.btn_open_log.clicked.connect(self.open_log_requested.emit)
        header.addWidget(self.btn_open_log)

        self.btn_clear_log = _button("Trim log", "danger")
        self.btn_clear_log.clicked.connect(self.clear_log_requested.emit)
        header.addWidget(self.btn_clear_log)
        layout.addLayout(header)

        return panel

    def set_log_state(self, note: str, large: bool):
        self.lbl_log_size.setText(note)
        self.lbl_log_size.setStyleSheet(
            f"font-size: 13px; color: {'#D9A441' if large else C.TEXT_SECONDARY}; "
            f"background: transparent; border: none;")

    # ------------------------------------------------------------- danger zone
    def _danger_panel(self) -> QWidget:
        """
        Force kill, on its own and last. It sat on the Dashboard beside Restart
        Pool and Open Web Dashboard, at the same size.
        """
        panel = _panel()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(_heading(
            "Danger zone",
            "Only when the power switch cannot stop the database. Work being saved "
            "is lost and the database may need recovery on its next start."), 1)
        self.btn_force_kill = _button("Force kill the database…", "danger")
        self.btn_force_kill.clicked.connect(self.force_kill_requested.emit)
        layout.addWidget(self.btn_force_kill, 0, Qt.AlignmentFlag.AlignVCenter)
        return panel

    # ------------------------------------------------------------- diagnostics
    def show_report(self, title: str, body: str, problems: int):
        """The diagnostics report, as text somebody can read and copy."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning if problems else QMessageBox.Icon.Information)
        box.setText("%d problem(s) found." % problems if problems
                    else "No problems found.")
        box.setDetailedText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)

        # The detail is the whole point, so open it rather than making somebody
        # find the button that reveals it.
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.ButtonRole.ActionRole:
                button.click()
                break
        box.exec()
