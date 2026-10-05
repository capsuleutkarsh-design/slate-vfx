"""
Admin panel widgets extracted from admin_panel.py.

Contains:
- PCDetailsDialog - one machine's specification, with a PDF export
- PCCard - one machine on the Live Ops grid
- LiveDashboard - the grid, its summary, search, sort and refresh

What a report's age means (online / not responding / offline) lives in
slate/core/domain/fleet_status.py, shared with the fleet report.
"""

import html
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCursor, QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.domain import fleet_status as fs
from ..core.workers.admin_workers import LiveStatusWorker, load_report
from ..core.infra.design_tokens import (
    ColorTokens as C,
    RadiusTokens as R,
    SpacingTokens as S,
    TypographyTokens as T,
)
from .components.qt_safety import safe_single_shot
from .components.feedback import confirm, toast
from ..core.infra.app_context import AppContext
from .core.controls import make_button
from .core.icons import icon as draw_icon
from slate.core.infra.gate import Gate
from slate.gui.core.empty_state import EmptyState, EmptyStack
from slate.gui.core.stat_card import StatStrip

logger = logging.getLogger(__name__)

NOT_REPORTED = "This machine has not reported yet."


def _label(text=""):
    """
    A label that shows its text as written. Workstations write these values
    themselves; as rich text a model called 'Projects<b>' turned the rest of
    the dialog bold, and markup in a report would be rendered.
    """
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText("" if text is None else str(text))
    return label


def disk_colour(percent) -> str:
    """The colour for a disk figure: normal below 80 %, amber from 80, red from 90."""
    return {"warn": Gate.WARN, "bad": Gate.BAD}.get(fs.disk_level(percent), Gate.TEXT_2)


def disk_bar_colour(percent) -> str:
    """A drive bar's fill: the accent while there is room, amber and red as it fills."""
    return {"warn": Gate.WARN, "bad": Gate.BAD}.get(fs.disk_level(percent), Gate.ACCENT)


def status_colour(state: str) -> str:
    return {
        fs.ONLINE: Gate.OK,
        fs.NOT_RESPONDING: Gate.WARN,
        fs.OFFLINE: Gate.BAD,
    }.get(state, Gate.TEXT_DIM)


def signed_in_user(data) -> str:
    """The Slate user on a report, or '' when nobody is signed in."""
    user = str((data or {}).get("user") or "").strip()
    return "" if user.lower() in ("", "unknown", "none", "n/a") else user


# What clients write when they could not read a value (hardware_info writes
# the word 'Unknown'); shown as 'Not reported' rather than verbatim.
_NOT_REPORTED_WORDS = ("", "unknown", "n/a", "none", "null")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def reported(value) -> str:
    """A client-written value, or '' when it is missing or a placeholder."""
    # BIOS strings can carry NULs and other control characters; one of them
    # made the fleet report's CSV and Excel writers refuse the whole file.
    text = "" if value is None else _CONTROL.sub("", str(value)).strip()
    return "" if text.lower() in _NOT_REPORTED_WORDS else text


def ram_gb(value):
    """RAM as a number of GB: clients write 32, '32' or '32 GB'. None when unknown."""
    text = reported(value).upper().replace("GB", "").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _value(data, key):
    if key == "RAM_GB":
        gb = ram_gb((data or {}).get(key))
        return f"{gb} GB" if gb is not None else "Not reported"
    return reported((data or {}).get(key)) or "Not reported"


# --------------------------------------------------------------------------
# PDF report
# --------------------------------------------------------------------------
# A printed page is white whatever the screen theme is, so the report has its
# own fixed ink colours rather than the theme's.
_PDF_STYLE = """
    body { font-family: 'Segoe UI', Arial, sans-serif; color: #222; }
    h1 { color: #255; border-bottom: 2px solid #255; padding-bottom: 10px; }
    h2 { color: #333; margin-top: 20px; border-bottom: 1px solid #999; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th { text-align: left; background-color: #eee; padding: 8px; border: 1px solid #bbb; }
    td { padding: 8px; border: 1px solid #bbb; }
    .highlight { font-weight: bold; color: #255; }
    .footer { margin-top: 30px; font-size: 10px; color: #777; text-align: center;
              border-top: 1px solid #bbb; padding-top: 10px; }
"""


def build_specs_html(data, pc_name=None, generated=None) -> str:
    """
    The specification report as HTML. Every value is escaped: these strings
    are written by the clients, and a '<' or '&' in a model name used to break
    the whole report.
    """
    data = dict(data or {})
    esc = lambda value: html.escape("" if value is None else str(value))  # noqa: E731
    name = pc_name or data.get("pc_name") or "Workstation"
    generated = generated or datetime.now().strftime("%Y-%m-%d %H:%M")

    def rows(pairs):
        return "".join(f"<tr><td>{esc(label)}</td><td>{esc(_value(data, key))}</td></tr>"
                       for label, key in pairs)

    drives = ""
    for drive in data.get("Drives") or []:
        if not isinstance(drive, dict):
            continue
        drives += (
            "<tr>"
            f"<td>{esc(drive.get('Root'))}</td>"
            f"<td>{esc(drive.get('Label'))}</td>"
            f"<td>{esc(drive.get('Capacity_GB'))} GB</td>"
            f"<td>{esc(drive.get('Free_GB'))} GB</td>"
            f"<td class=\"highlight\">{esc(drive.get('Usage'))}</td>"
            "</tr>")
    if not drives:
        drives = "<tr><td colspan=\"5\">No drive information reported.</td></tr>"

    return f"""
    <html><head><style>{_PDF_STYLE}</style></head>
    <body>
      <h1>System specification</h1>
      <p><strong>Workstation:</strong> {esc(name)} &nbsp;|&nbsp; <strong>Generated:</strong> {esc(generated)}</p>
      <h2>Storage</h2>
      <table><tr><th>Drive</th><th>Label</th><th>Capacity</th><th>Free space</th><th>Used</th></tr>{drives}</table>
      <h2>Identity</h2>
      <table><tr><th>Field</th><th>Value</th></tr>{rows([
          ("Computer name", "ComputerName"), ("Signed in to Slate as", "user"),
          ("Windows account", "os_user"), ("IP address", "IPAddress"), ("MAC address", "MACAddress")])}</table>
      <h2>Hardware</h2>
      <table><tr><th>Field</th><th>Value</th></tr>{rows([
          ("Manufacturer", "Manufacturer"), ("Model", "Model"), ("Serial number", "SerialNo"),
          ("Motherboard", "Motherboard"), ("CPU", "CPU"), ("GPU", "GPU"), ("RAM", "RAM_GB")])}</table>
      <h2>Software</h2>
      <table>{rows([("OS", "OS"), ("Windows version", "WindowsVersion"),
                    ("Slate version", "client_version")])}</table>
      <div class="footer">Report generated by Slate Admin Panel.</div>
    </body></html>
    """


class PCDetailsDialog(QDialog):
    """Detailed view of PC hardware/network specs."""

    def __init__(self, data, parent=None, hub=None, pc_name=None, app_context=None):
        super().__init__(parent)
        self.data = dict(data or {})
        self.app_context = app_context or AppContext()
        self.hub = hub or self.app_context.server_hub()
        self.pc_name = pc_name or self.data.get("pc_name") or "Workstation"
        self.setWindowTitle(f"System specs: {self.pc_name}")
        self.setObjectName("PCDetails")
        # Scoped to the dialog itself. Without a selector Qt applied the
        # background to every child, and the fields lost their own look.
        self.setStyleSheet(f"QDialog#PCDetails {{ background-color: {C.BG_PRIMARY}; color: {Gate.TEXT}; }}")

        # 700 px tall did not fit a 1280x720 or 1366x768 screen.
        from .components.screen_fit import fit_to_screen
        fit_to_screen(self, 600, 700, fraction=0.85)

        self.main_layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.lbl_title = _label(self.pc_name)
        self.lbl_title.setStyleSheet(
            f"font-size: {T.SIZE_XL}px; font-weight: {T.WEIGHT_STYLE_BOLD}; color: {C.ACCENT_PRIMARY};")
        header.addWidget(self.lbl_title)
        header.addStretch(1)
        self.lbl_last = _label("")
        self.lbl_last.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        header.addWidget(self.lbl_last)
        self.main_layout.addLayout(header)

        # Shown instead of a message box when a reload cannot find the report.
        self.lbl_note = _label("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet(f"color: {Gate.WARN};")
        self.lbl_note.hide()
        self.main_layout.addWidget(self.lbl_note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.build_content_widget(self.data)
        self._show_last_report()
        self.main_layout.addWidget(self.scroll, 1)

        # One button kit: the two harmless actions are secondary - Export PDF
        # was painted in the warning colour - and Close is the default.
        self.btn_reload = make_button("Reload", "secondary", tooltip="Read the latest report again",
                                      on_click=self.reload_data)
        self.btn_export = make_button("Export PDF", "secondary", on_click=self.export_to_pdf)
        self.btn_close = make_button("Close", "primary", on_click=self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_reload)
        btn_layout.addWidget(self.btn_export)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        self.main_layout.addLayout(btn_layout)

    def _show_last_report(self):
        seen = fs.last_seen_of(self.data)
        if seen is None:
            self.lbl_last.setText("")
            return
        self.lbl_last.setText(f"Last report {fs.seen_at_text(seen)} ({fs.age_text(seen)})")

    def build_content_widget(self, data):
        """Construct the scrollable content widget from data."""
        content = QWidget()
        content.setObjectName("PCDetailsContent")
        form = QFormLayout(content)
        form.setSpacing(10)
        self.section_titles = []

        def add_row(label, value, mono=False):
            l = _label(label)
            l.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-weight: {T.WEIGHT_STYLE_BOLD};")
            v = _label(str(value))
            # Codes (addresses, serials) in monospace; sentences and the
            # dimmed 'Not reported' in the normal font.
            missing = value == "Not reported"
            font = f" font-family: {T.FONT_MONO};" if mono and not missing else ""
            v.setStyleSheet(f"color: {Gate.TEXT_DIM if missing else Gate.TEXT};{font}")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(l, v)

        def add_field(label, key, mono=False):
            # A row the client never sends at all (an older client version)
            # is left out rather than filling the dialog with 'Not reported'.
            if key in data:
                add_row(label, _value(data, key), mono)

        def add_section(title):
            l = _label(title)
            l.setStyleSheet(
                f"color: {C.ACCENT_PRIMARY}; font-weight: {T.WEIGHT_STYLE_BOLD}; "
                f"font-size: {T.SIZE_MD}px; margin-top: {S.LG}px; border-bottom: 1px solid {C.BORDER_LIGHT};"
            )
            self.section_titles.append(title)
            form.addRow(l)

        # Storage first: how full the disks are is what people open this for,
        # and it used to be below the fold even at 1600x900.
        add_section("Storage")
        drives = [d for d in (data.get("Drives") or []) if isinstance(d, dict)]
        self.drive_bars = []
        if drives:
            def fullness(d):
                return fs.disk_percent(d.get("Usage")) or 0.0
            for d in sorted(drives, key=fullness, reverse=True):
                usage = fs.disk_percent(d.get("Usage"))
                label = d.get("Label") or "Local disk"
                info = (f"{label} ({d.get('Root') or '?'}) - "
                        f"{d.get('Free_GB', '?')} GB free of {d.get('Capacity_GB', '?')} GB")
                bar = QProgressBar()
                bar.setValue(int(round(usage)) if usage is not None else 0)
                bar.setFormat("%p% full" if usage is not None else "usage not reported")
                bar.setStyleSheet(
                    f"QProgressBar {{ border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px; "
                    f"text-align: center; color: {Gate.TEXT}; background: {C.BG_SIDEBAR}; height: 16px; }}"
                    f"QProgressBar::chunk {{ background-color: {disk_bar_colour(usage)}; }}")
                self.drive_bars.append(bar)

                box = QWidget()
                row_layout = QVBoxLayout(box)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(_label(info))
                row_layout.addWidget(bar)
                form.addRow(box)
            updated = data.get("drives_updated")
            if updated:
                add_row("Disk figures from", f"{fs.seen_at_text(updated)} ({fs.age_text(updated)})")
        else:
            add_row("Drives", "No drive information reported")

        add_section("Identity")
        add_field("Computer name", "ComputerName")
        user = signed_in_user(data)
        add_row("Signed in to Slate as", user or "Nobody signed in")
        add_field("Windows account", "os_user")
        add_field("IP address", "IPAddress", mono=True)
        add_field("MAC address", "MACAddress", mono=True)

        add_section("Hardware")
        add_field("Manufacturer", "Manufacturer")
        add_field("Model", "Model")
        add_field("Serial number", "SerialNo", mono=True)
        add_field("Motherboard", "Motherboard")
        add_field("CPU", "CPU")
        add_field("GPU", "GPU")
        add_field("RAM", "RAM_GB")

        add_section("Software")
        add_field("OS", "OS")
        add_field("Windows version", "WindowsVersion")
        add_row("Slate version", _value(data, "client_version"))

        self.scroll.setWidget(content)

    def reload_data(self):
        """
        Re-read the status file and refresh the view, quietly. Each reload used
        to pop a box naming the JSON file; the header's 'Last report' time is
        the confirmation now, and a problem is said in the dialog.
        """
        self.lbl_note.hide()
        try:
            status_dir = self.hub.get_livestatus_dir()
            report_path = Path(status_dir) / f"{self.pc_name}.json"
            if not report_path.exists():
                self.lbl_note.setText(NOT_REPORTED)
                self.lbl_note.show()
                return False
            new_data = load_report(report_path)
        except Exception as exc:
            logger.warning("Could not reload the report for %s: %s", self.pc_name, exc)
            self.lbl_note.setText("The latest report could not be read. Try again in a moment.")
            self.lbl_note.show()
            return False
        self.data = dict(new_data or {})
        self.build_content_widget(self.data)
        self._show_last_report()
        return True

    def export_to_pdf(self):
        """Generate a PDF report."""
        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QTextDocument, QPageSize
        except ImportError:
            QMessageBox.critical(self, "Export PDF", "PDF export is not available in this installation.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", f"{self.pc_name}.pdf", "PDF (*.pdf)")
        if not path:
            return

        # QPrinter does not raise when the file cannot be written (open in a
        # PDF viewer, read-only folder), so 'Saved' used to appear regardless:
        # try the file first, and check something was written after.
        try:
            with open(path, "ab"):
                pass
        except OSError as exc:
            QMessageBox.warning(self, "Export PDF",
                                f"The PDF could not be saved to:\n{path}\n\n{exc.strerror or exc}. "
                                "If it is open in a PDF viewer, close it and try again.")
            return False
        doc = QTextDocument()
        doc.setHtml(build_specs_html(self.data, self.pc_name))

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        doc.print_(printer)
        try:
            written = Path(path).stat().st_size > 0
        except OSError:
            written = False
        if not written:
            QMessageBox.warning(self, "Export PDF", f"The PDF could not be written to:\n{path}")
            return False
        QMessageBox.information(self, "Export PDF", f"Saved the specification to:\n{path}")
        return True


def _this_version() -> str:
    try:
        from slate import __version__
        return str(__version__)
    except Exception:
        return ""


class PCCard(QFrame):
    # The narrowest a card gets; cards share the row's width beyond that.
    CARD_WIDTH = 220
    CARD_HEIGHT = 140

    def __init__(self, pc_name, hub, verify_callback=None, read_only=False, log_action=None,
                 on_removed=None):
        super().__init__()
        self.pc_name = pc_name
        self.hub = hub
        self.current_data = {}
        self.state = fs.UNKNOWN
        self.verify_callback = verify_callback
        self.read_only = bool(read_only)
        self.log_action = log_action
        self.on_removed = on_removed
        # (what, when) of the last restart / shut-down sent, shown on the card
        # until the machine's next report.
        self.requested = None
        self._elided = {}
        self.setObjectName("PCCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.CARD_WIDTH)
        self.setFixedHeight(self.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Double-click for the system specs")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setSpacing(4)
        self.hl = QHBoxLayout()
        self.lbl_name = _label(pc_name)
        self.lbl_name.setStyleSheet(
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; color: {Gate.TEXT}; font-size: 13px;")
        self.hl.addWidget(self.lbl_name, 1)

        # The menu used to be reachable only by right-clicking, with nothing
        # on the card to say so. Drawn in the text colour: in TEXT_2 it all
        # but disappeared on the card.
        self.btn_menu = QToolButton()
        self.btn_menu.setIcon(draw_icon("more", Gate.TEXT, 16))
        self.btn_menu.setAutoRaise(True)
        self.btn_menu.setToolTip("Actions for this machine")
        self.btn_menu.setStyleSheet("QToolButton { border: none; background: transparent; }")
        self.btn_menu.clicked.connect(lambda: self.show_context(None))
        self.hl.addWidget(self.btn_menu)
        self.main_layout.addLayout(self.hl)

        # The state is the most important line, so it sits under the name at
        # body size; it was a 10 px line at the bottom of the card.
        self.lbl_status = _label("● Connecting...")
        self.lbl_status.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-weight: {T.WEIGHT_STYLE_BOLD};")
        self.main_layout.addWidget(self.lbl_status)
        self.lbl_user = _label("Loading...")
        self.lbl_user.setStyleSheet(f"color: {C.TEXT_SECONDARY};")
        self.main_layout.addWidget(self.lbl_user)
        self.lbl_disk = _label("")
        self.lbl_disk.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px;")
        self.main_layout.addWidget(self.lbl_disk)
        self.lbl_version = _label("")
        self.lbl_version.setStyleSheet(f"color: {Gate.WARN}; font-size: 11px;")
        self.lbl_version.hide()
        self.main_layout.addWidget(self.lbl_version)
        self.main_layout.addStretch()
        self._set_elided(self.lbl_name, pc_name, reserve=30)   # room for the actions button
        self._restyle(Gate.TEXT_DIM)

    # ----------------------------------------------------------------- text
    def _text_width(self) -> int:
        margins = self.main_layout.contentsMargins()
        return max(self.width(), self.CARD_WIDTH) - margins.left() - margins.right() - 8

    def _set_elided(self, label, text, reserve=0):
        """Long machine and user names end in '…' with the whole name as a tooltip."""
        text = str(text or "")
        self._elided[label] = (text, reserve)
        width = max(40, self._text_width() - reserve)
        shown = QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight, width)
        label.setText(shown)
        label.setToolTip(f"<p>{html.escape(text)}</p>" if shown != text else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Cards widen with the page: show more of a long name when they do.
        for label, (text, reserve) in list(self._elided.items()):
            self._set_elided(label, text, reserve)

    def _restyle(self, colour):
        self.setStyleSheet(
            f"QFrame#PCCard {{ background-color: {C.BG_ELEVATED}; border-left: 4px solid {colour}; "
            f"border-radius: {R.MD}px; }} QFrame#PCCard:hover {{ background-color: {Gate.RAISED}; }}"
            f"QFrame#PCCard:focus {{ border: 1px solid {Gate.ACCENT}; border-left: 4px solid {colour}; }}")

    def update_data(self, data, delta=None, now=None):
        """
        Show one report. The state comes from the report's own last_seen (or
        from delta, the seconds since it, for older callers), so a machine that
        stops reporting turns amber and then red instead of staying green.
        """
        self.current_data = dict(data or {})
        now = time.time() if now is None else now
        if self.current_data.get("unreadable"):
            # The file is there but could not be read (half-written, corrupt):
            # the machine stays on the grid rather than vanishing.
            self.state = fs.UNKNOWN
            self.lbl_user.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-style: italic;")
            self._set_elided(self.lbl_user, "Its status file could not be read")
            self.lbl_disk.setText("")
            self.lbl_version.hide()
            self.lbl_status.setText("● Report unreadable")
            self.lbl_status.setToolTip(fs.EXPLAIN[fs.UNKNOWN])
            self.lbl_status.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-weight: {T.WEIGHT_STYLE_BOLD};")
            self._restyle(Gate.TEXT_DIM)
            return
        seen = fs.last_seen_of(self.current_data)
        if seen is None and delta is not None:
            try:
                seen = now - float(delta)
            except (TypeError, ValueError):
                seen = None
        self.state = fs.status_for(seen, now)

        user = signed_in_user(self.current_data)
        if user:
            self.lbl_user.setStyleSheet(f"color: {C.TEXT_SECONDARY};")
            self._set_elided(self.lbl_user, user)
        else:
            self.lbl_user.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-style: italic;")
            self._set_elided(self.lbl_user, "Nobody signed in")

        percent = fs.disk_percent(self.current_data.get("disk_percent"))
        if percent is None:
            disk_text = "C: disk usage not reported"
        else:
            disk_text = f"C: {percent:.0f}% full"
            updated = fs.last_seen_of({"last_seen": self.current_data.get("drives_updated")})
            if updated is not None and now - updated > 600:
                disk_text += f" ({fs.age_text(updated, now)})"
        self.lbl_disk.setText(disk_text)
        self.lbl_disk.setStyleSheet(f"color: {disk_colour(percent)}; font-size: 11px;")

        # A machine on another Slate version than this one says so.
        version = reported(self.current_data.get("client_version"))
        mine = _this_version()
        differs = bool(version and mine and version != mine)
        self.lbl_version.setText(f"Slate {version}" if differs else "")
        self.lbl_version.setVisible(differs)

        # A restart / shut-down sent from here shows until the next report.
        if self.requested and seen is not None and seen > self.requested[1]:
            self.requested = None

        colour = status_colour(self.state)
        text = fs.label(self.state)
        if self.state == fs.NOT_RESPONDING:
            text += f" · {fs.age_text(seen, now)}"
        elif self.state == fs.OFFLINE:
            # The clock time on the day itself, the date for anything older.
            recent = seen is not None and now - seen < 86400
            when = fs.seen_at_text(seen) if recent else fs.age_text(seen, now)
            text += f" · last seen {when}"
        elif self.state == fs.UNKNOWN:
            text += " · report has no time"
        if self.requested:
            text += f" · {self.requested[0]} requested {fs.seen_at_text(self.requested[1])}"
        self.lbl_status.setText(f"● {text}")
        self.lbl_status.setToolTip(fs.EXPLAIN.get(self.state, ""))
        self.lbl_status.setStyleSheet(f"color: {colour}; font-weight: {T.WEIGHT_STYLE_BOLD};")
        self._restyle(colour)

    # -------------------------------------------------------------- actions
    def open_details(self):
        details_data = dict(self.current_data or {})
        details_data.setdefault("pc_name", self.pc_name)
        dialog = PCDetailsDialog(details_data, self, hub=self.hub, pc_name=self.pc_name)
        dialog.exec()
        return dialog

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_details()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_details()
            return
        if event.key() == Qt.Key.Key_Menu:
            self.show_context(None)
            return
        super().keyPressEvent(event)

    def build_menu(self):
        menu = QMenu(self)
        act_details = menu.addAction("View system specs")
        act_rst = act_off = None
        if not self.read_only:
            menu.addSeparator()
            act_rst = menu.addAction(draw_icon("refresh", Gate.BAD, 16), "Restart…")
            act_off = menu.addAction(draw_icon("stop", Gate.BAD, 16), "Shut down…")
        return menu, act_details, act_rst, act_off

    def show_context(self, pos=None):
        menu, act_details, act_rst, act_off = self.build_menu()
        act_remove = None
        if not self.read_only and self.state in (fs.OFFLINE, fs.UNKNOWN):
            menu.addSeparator()
            act_remove = menu.addAction("Remove from Live Ops…")
        if pos is None:
            where = self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft())
        else:
            where = QCursor.pos()
        action = menu.exec(where)

        if action is None:
            return
        if action == act_details:
            self.open_details()
        elif act_rst is not None and action == act_rst:
            self.request_power("restart")
        elif act_off is not None and action == act_off:
            self.request_power("shutdown")
        elif act_remove is not None and action == act_remove:
            self.remove_from_live_ops()

    def request_power(self, command: str) -> bool:
        """
        Restart or shut this machine down, after asking. The question names the
        machine and says what it costs, and Cancel is the default.
        """
        if self.read_only:
            return False
        verb = "Restart" if command == "restart" else "Shut down"
        if not confirm(self.window(), f"{verb} {self.pc_name}?",
                       "Anyone working on it will lose unsaved work.",
                       yes_label=verb, destructive=True):
            return False
        if self.verify_callback and not self.verify_callback():
            return False
        self.hub.post_command(command, self.pc_name)
        done = "Restart" if command == "restart" else "Shut-down"
        if callable(self.log_action):
            self.log_action(f"{done} sent to {self.pc_name}")
        # Say it went, and keep saying so on the card until the next report:
        # the click used to give no sign at all, so people sent it again.
        self.requested = (done.lower(), time.time())
        if self.current_data:
            self.update_data(self.current_data)
        toast(self, f"{done} sent to {self.pc_name}", "success")
        return True

    def remove_from_live_ops(self) -> bool:
        """
        Take a retired machine off Live Ops: its status file moves to
        LiveStatus/Retired (kept, not deleted). If the machine reports again
        it simply comes back.
        """
        if self.read_only:
            return False
        if not confirm(self.window(), f"Remove {self.pc_name} from Live Ops?",
                       "Use this for a machine that has been retired. Its last report is kept in "
                       "the LiveStatus\\Retired folder, and it comes back if it reports again.",
                       yes_label="Remove"):
            return False
        try:
            status_dir = Path(self.hub.get_livestatus_dir())
            retired = status_dir / "Retired"
            retired.mkdir(exist_ok=True)
            (status_dir / f"{self.pc_name}.json").replace(retired / f"{self.pc_name}.json")
        except OSError as exc:
            QMessageBox.warning(self, "Remove from Live Ops",
                                f"{self.pc_name} could not be removed:\n{exc.strerror or exc}")
            return False
        if callable(self.log_action):
            self.log_action(f"Removed {self.pc_name} from Live Ops")
        if callable(self.on_removed):
            self.on_removed(self.pc_name)
        return True


class _ResizeWatch(QObject):
    """Calls back when the watched widget changes width."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._callback()
        return False


SORTS = (("Status (problems first)", "status"), ("Name", "name"), ("Signed-in user", "user"))
FILTERS = (("All machines", ""), (fs.label(fs.ONLINE), fs.ONLINE),
           (fs.label(fs.NOT_RESPONDING), fs.NOT_RESPONDING),
           (fs.label(fs.OFFLINE), fs.OFFLINE), (fs.label(fs.UNKNOWN), fs.UNKNOWN))


class LiveDashboard(QWidget):
    GRID_SPACING = 15

    def __init__(self, hub, verify_callback=None, read_only=False, log_action=None):
        super().__init__()
        self.hub = hub
        self.verify_callback = verify_callback
        self.read_only = bool(read_only)
        self.log_action = log_action
        self.worker_controller = None
        self._is_closing = False
        self._is_cleaned = False
        self._records = {}
        self._columns = 0
        self._last_read = None      # time of the last good read of the folder
        self._read_error = ""
        self.setObjectName("LiveDashboard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped: a selector-less sheet here was inherited by every field and
        # button on the page.
        self.setStyleSheet(f"QWidget#LiveDashboard {{ background-color: {C.BG_MAIN}; color: {C.TEXT_PRIMARY}; }}")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # ---- summary and tools: what is up, what is down, find a machine
        bar = QWidget()
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(15, 10, 15, 6)
        bar_layout.setSpacing(8)

        self.summary = StatStrip(compact=True)
        self.summary_cards = {}
        for state, tone in ((fs.ONLINE, "ok"), (fs.NOT_RESPONDING, "warn"),
                            (fs.OFFLINE, "bad"), (fs.UNKNOWN, "idle")):
            self.summary_cards[state] = self.summary.add(
                fs.label(state), 0, tone=tone,
                on_click=lambda s=state: self.toggle_status_filter(s),
                tooltip=f"{fs.EXPLAIN[state]}.\nClick to show only these machines, click again for all.")
        bar_layout.addWidget(self.summary)

        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search machine, user, IP or version…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self.search.setMaximumWidth(300)
        self.search.textChanged.connect(self._relayout)
        self.toolbar.addWidget(self.search)

        self.status_filter = QComboBox()
        for text, value in FILTERS:
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self._relayout)
        self.toolbar.addWidget(self.status_filter)

        self.sort_combo = QComboBox()
        for text, value in SORTS:
            self.sort_combo.addItem(f"Sort by {text.lower()}", value)
        self.sort_combo.currentIndexChanged.connect(self._relayout)
        self.toolbar.addWidget(self.sort_combo)
        self.toolbar.addStretch(1)

        # Hosts put their own buttons here (the Admin Panel's Fleet report),
        # next to Refresh rather than in a row of their own.
        self.tools_layout = QHBoxLayout()
        self.tools_layout.setSpacing(8)
        self.toolbar.addLayout(self.tools_layout)

        self.btn_refresh = make_button("Refresh", "secondary", on_click=self.refresh_grid,
                                       tooltip="Read every machine's report now (it also refreshes every 30 s)")
        self.btn_refresh.setIcon(draw_icon("refresh"))
        self.toolbar.addWidget(self.btn_refresh)
        self.lbl_updated = _label("Not updated yet")
        self.lbl_updated.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        self.toolbar.addWidget(self.lbl_updated)
        bar_layout.addLayout(self.toolbar)
        # Said when the status folder could not be read: the cards are the
        # last good read, not a fleet that vanished.
        self.lbl_stale = _label("")
        self.lbl_stale.setWordWrap(True)
        self.lbl_stale.setStyleSheet(f"color: {Gate.WARN}; font-weight: {T.WEIGHT_STYLE_BOLD};")
        self.lbl_stale.hide()
        bar_layout.addWidget(self.lbl_stale)
        self.main_layout.addWidget(bar)

        # ---- the grid
        self.grid_area = QScrollArea()
        self.grid_area.setWidgetResizable(True)
        self.grid_area.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_widget = QWidget()
        self.grid_widget.setObjectName("FleetGrid")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(15, 10, 15, 15)
        self.grid_layout.setSpacing(self.GRID_SPACING)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_area.setWidget(self.grid_widget)
        self._resize_watch = _ResizeWatch(self._on_viewport_resized, self)
        self.grid_area.viewport().installEventFilter(self._resize_watch)
        # The empty message lives beside the grid, not in its first cell: in
        # the grid it wrapped in a narrow column in the top-left corner and,
        # hidden, still took cell (0, 0) so the first machine card was shifted.
        self._fleet_empty = EmptyState(
            "No workstations have reported yet",
            "Machines appear here once the client is running on them.",
            glyph="monitor")
        self.fleet_stack = EmptyStack(self.grid_area, self._fleet_empty)
        self.main_layout.addWidget(self.fleet_stack, 1)

        self.pc_widgets = {}
        self.worker = LiveStatusWorker(self.hub)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.failed.connect(self.on_read_failed)
        self.worker.finished.connect(self._on_worker_thread_done)

        # Runs only while Live Ops is on screen (showEvent / hideEvent): it
        # used to re-read every report over the share every 30 s for the
        # rest of the session once the Admin Panel had been opened.
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(30000)
        self.auto_timer.timeout.connect(self.refresh_grid)

    def showEvent(self, event):
        super().showEvent(event)
        if self._is_closing:
            return
        self.auto_timer.start()
        if self._last_read is None or time.time() - self._last_read > 25:
            safe_single_shot(0, self, self.refresh_grid)

    def hideEvent(self, event):
        self.auto_timer.stop()
        super().hideEvent(event)

    def bind_worker_controller(self, controller):
        """Allow host module to inject standardized worker orchestration."""
        self.worker_controller = controller

    def add_tool(self, widget):
        """Put a host's button in the toolbar, beside Refresh."""
        self.tools_layout.addWidget(widget)

    def refresh_grid(self):
        """Start the background worker to fetch data."""
        if self._is_closing:
            return
        if self.worker_controller is not None:
            self.worker_controller.request_refresh()
            return
        if not self.worker.isRunning():
            self.worker.start()

    # ------------------------------------------------------------- data
    def on_data_ready(self, loaded_data, now=None):
        """
        Show every machine that has a report - including the ones that have
        gone quiet. Machines silent for five minutes used to be skipped, so
        the one thing an admin needs, which machines are down, was invisible
        and a card whose machine stopped reporting froze on 'Online'.
        """
        sender = self.sender()
        if sender is not None and sender is not self.worker:
            return
        if self._is_closing:
            return

        now = time.time() if now is None else now
        records = {}
        for data in loaded_data or []:
            if not isinstance(data, dict):
                continue
            pc_name = str(data.get("pc_name") or "").strip()
            if pc_name:
                records[pc_name] = data

        for pc in list(self.pc_widgets.keys()):
            if pc not in records:
                w = self.pc_widgets.pop(pc)
                self.grid_layout.removeWidget(w)
                w.setParent(None)
                w.deleteLater()

        for pc_name, data in records.items():
            card = self.pc_widgets.get(pc_name)
            if card is None:
                card = PCCard(pc_name, self.hub, self.verify_callback,
                              read_only=self.read_only, log_action=self.log_action,
                              on_removed=lambda _name: self.refresh_grid())
                self.pc_widgets[pc_name] = card
            # One bad report must not stop the cards after it from updating.
            try:
                card.update_data(data, now=now)
            except Exception:
                logger.exception("Live Ops could not show the report of %s", pc_name)

        self._records = records
        # Counted from the cards, so an unreadable report is counted where its
        # card is shown (Unknown).
        counts = {state: 0 for state in self.summary_cards}
        for card in self.pc_widgets.values():
            counts[card.state] = counts.get(card.state, 0) + 1
        for state, card in self.summary_cards.items():
            card.set_value(counts[state])
        self._last_read = now
        self._read_error = ""
        self.lbl_stale.hide()
        self.lbl_updated.setText("Updated " + datetime.fromtimestamp(now).strftime("%H:%M:%S"))
        self._relayout(force=True)

    def on_read_failed(self, reason=""):
        """
        The LiveStatus folder could not be read (share dropped, permission,
        timeout). The cards stay as they were and the page says so; this used
        to delete every card and claim no machine had ever reported.
        """
        if self._is_closing:
            return
        self._read_error = str(reason or "") or "unknown error"
        if self._last_read is not None:
            when = datetime.fromtimestamp(self._last_read).strftime("%H:%M")
            self.lbl_stale.setText("The status folder could not be read (is the server share "
                                   f"reachable?) - showing the last good read from {when}.")
        else:
            self.lbl_stale.setText("The status folder could not be read (is the server share "
                                   "reachable?). Slate tries again every 30 seconds.")
        self.lbl_stale.setToolTip(self._read_error)
        self.lbl_stale.show()
        self._relayout(force=True)

    def set_status_filter(self, state):
        index = self.status_filter.findData(state)
        self.status_filter.setCurrentIndex(max(0, index))

    def toggle_status_filter(self, state):
        """A summary tile filters to its machines; clicking it again shows all."""
        self.set_status_filter("" if self.status_filter.currentData() == state else state)

    def clear_filters(self):
        self.search.clear()
        self.status_filter.setCurrentIndex(0)

    def visible_cards(self):
        """The cards that pass the search and filter, in display order."""
        text = self.search.text().strip().lower()
        wanted = self.status_filter.currentData() or ""
        sort = self.sort_combo.currentData() or "status"
        cards = []
        for name, card in self.pc_widgets.items():
            if wanted and card.state != wanted:
                continue
            if text:
                # IT looks a machine up by its IP too, or checks who still
                # runs an old Slate.
                data = card.current_data
                haystack = " ".join([name, signed_in_user(data)] + [
                    reported(data.get(k)) for k in ("IPAddress", "os_user", "client_version")])
                if text not in haystack.lower():
                    continue
            cards.append(card)

        def key(card):
            name = card.pc_name.lower()
            if sort == "name":
                return (name,)
            if sort == "user":
                user = signed_in_user(card.current_data).lower()
                return (user == "", user, name)
            return (fs.ORDER.get(card.state, 9), name)
        cards.sort(key=key)
        return cards

    def column_count(self) -> int:
        """As many card columns as fit the page (it was always four)."""
        margins = self.grid_layout.contentsMargins()
        # A hidden grid (the empty state is up) is not resized with the page,
        # so its viewport's width is stale: measure the page instead.
        visible = self.grid_area.isVisible()
        width = (self.grid_area.viewport().width() if visible else self.fleet_stack.width()) \
            - margins.left() - margins.right()
        step = PCCard.CARD_WIDTH + self.GRID_SPACING
        return max(1, (width + self.GRID_SPACING) // step)

    def _on_viewport_resized(self):
        if self._columns and self.column_count() != self._columns:
            self._relayout(force=True)

    def _relayout(self, *_args, force=False):
        """
        Lay the cards out again from a sorted list, so there are never holes
        where machines dropped out and the first card is always top-left.
        """
        if self._is_closing:
            return
        cards = self.visible_cards()
        columns = self.column_count()
        self._columns = columns
        for card in self.pc_widgets.values():
            self.grid_layout.removeWidget(card)
            card.hide()
        # The cards share the row's width, so the grid lines up with the
        # summary strip above instead of leaving a band empty on the right.
        for col in range(max(columns, self.grid_layout.columnCount())):
            self.grid_layout.setColumnStretch(col, 1 if col < columns else 0)
        for index, card in enumerate(cards):
            row, col = divmod(index, columns)
            self.grid_layout.addWidget(card, row, col)
            card.show()
        wanted = self.status_filter.currentData() or ""
        for state, tile in self.summary_cards.items():
            tile.set_active(state == wanted)
        self._update_fleet_placeholder(shown=len(cards), known=len(self.pc_widgets))

    def _update_fleet_placeholder(self, shown, known):
        if shown:
            self._fleet_empty.set_filtered(False)
            self.fleet_stack.show_empty(False)
            return
        if known:
            self._fleet_empty.set_filtered(True, on_clear=self.clear_filters, noun="machines")
        elif self._read_error:
            self._fleet_empty.set_filtered(False)
            self._fleet_empty.set_message(
                "The status folder could not be read",
                "Check that the server share is reachable. Slate tries again every 30 seconds.")
        else:
            self._fleet_empty.set_filtered(False)
            self._fleet_empty.set_message(
                "No workstations have reported yet",
                "Machines appear here once the client is running on them.")
        self.fleet_stack.show_empty(True)

    def _on_worker_thread_done(self):
        if self._is_closing:
            return

    def cleanup(self):
        """Stop background worker and auto-refresh timer."""
        if self._is_cleaned:
            return

        self._is_cleaned = True
        self._is_closing = True

        if self.auto_timer.isActive():
            self.auto_timer.stop()

        if self.worker_controller is not None:
            self.worker_controller.shutdown(timeout_ms=3000)

        if self.worker.isRunning():
            stop = getattr(self.worker, "stop", None)
            if callable(stop):
                stop()
            else:
                self.worker.requestInterruption()
            self.worker.wait(3000)

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)
