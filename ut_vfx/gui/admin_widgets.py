"""
Admin panel widgets extracted from admin_panel.py.

Contains:
- PCDetailsDialog
- PCCard
- LiveDashboard
"""

import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QDialog,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.domain.workers.admin_workers import LiveStatusWorker
from ..core.infra.design_tokens import (
    ColorTokens as C,
    RadiusTokens as R,
    SpacingTokens as S,
    TypographyTokens as T,
)
from .components.qt_safety import safe_single_shot
from ..core.infra.app_context import AppContext
from ..core.infra.style_builder import StyleBuilder
from .core.controls import make_button
from .core.icons import icon as draw_icon


def _load_json_with_fallback(path: Path):
    """Load JSON with encoding fallback for mixed workstation clients."""
    last_error = None
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as fh:
                return json.load(fh)
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise ValueError(f"Could not parse JSON file: {path}")


class PCDetailsDialog(QDialog):
    """Detailed view of PC hardware/network specs."""

    def __init__(self, data, parent=None, hub=None, pc_name=None, app_context=None):
        super().__init__(parent)
        self.data = dict(data or {})
        self.app_context = app_context or AppContext()
        self.hub = hub or self.app_context.server_hub()
        self.pc_name = pc_name or self.data.get("pc_name") or "Unknown"
        self.setWindowTitle(f"System Specs: {self.pc_name}")
        self.resize(600, 700)
        self.setStyleSheet(f"background-color: {C.BG_PRIMARY}; color: white;")

        self.main_layout = QVBoxLayout(self)

        lbl = QLabel(self.pc_name)
        lbl.setStyleSheet(
            f"font-size: {T.SIZE_XL}px; font-weight: {T.WEIGHT_STYLE_BOLD}; "
            f"color: {C.ACCENT_PRIMARY}; margin-bottom: {S.MD}px;"
        )
        self.main_layout.addWidget(lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(f"border: none; background: {C.BG_SURFACE};")

        self.build_content_widget(self.data)
        self.main_layout.addWidget(self.scroll)

        btn_refresh = QPushButton("Reload Data")
        btn_refresh.setToolTip("Reload the latest report from disk.")
        btn_refresh.setStyleSheet(
            f"background-color: {C.BORDER_LIGHT}; color: white; border: 1px solid #87857F; "
            f"padding: {S.SM}px {S.LG}px; border-radius: {R.SM}px;"
        )
        btn_refresh.clicked.connect(self.reload_data)

        btn_export = QPushButton("Export PDF")
        btn_export.setStyleSheet(
            f"background-color: {C.WARNING}; color: black; font-weight: {T.WEIGHT_STYLE_BOLD}; "
            f"padding: {S.SM}px {S.LG}px; border-radius: {R.SM}px;"
        )
        btn_export.clicked.connect(self.export_to_pdf)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(StyleBuilder.primary_button())
        btn_close.clicked.connect(self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_refresh)
        btn_layout.addWidget(btn_export)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        self.main_layout.addLayout(btn_layout)

    def build_content_widget(self, data):
        """Construct the scrollable content widget from data."""
        content = QWidget()
        form = QFormLayout(content)
        form.setSpacing(10)

        def add_row(label, value):
            l = QLabel(label)
            l.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-weight: {T.WEIGHT_STYLE_BOLD};")
            v = QLabel(str(value))
            v.setStyleSheet(f"color: white; font-family: {T.FONT_MONO};")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(l, v)

        def add_section(title):
            l = QLabel(title)
            l.setStyleSheet(
                f"color: {C.ACCENT_PRIMARY}; font-weight: {T.WEIGHT_STYLE_BOLD}; "
                f"font-size: {T.SIZE_MD}px; margin-top: {S.LG}px; border-bottom: 1px solid {C.BORDER_LIGHT};"
            )
            form.addRow(l)

        add_section("Identity")
        add_row("Computer Name", data.get("ComputerName", "N/A"))
        add_row("Software User", data.get("user", "N/A"))
        add_row("System User", data.get("os_user", "N/A"))
        add_row("IP Address", data.get("IPAddress", "N/A"))
        add_row("MAC Address", data.get("MACAddress", "N/A"))

        add_section("Hardware")
        add_row("Manufacturer", data.get("Manufacturer", "N/A"))
        add_row("Model", data.get("Model", "N/A"))
        add_row("Serial No", data.get("SerialNo", "N/A"))
        add_row("Motherboard", data.get("Motherboard", "N/A"))
        add_row("CPU", data.get("CPU", "N/A"))
        add_row("GPU", data.get("GPU", "N/A"))
        add_row("RAM", data.get("RAM_GB", "N/A"))

        add_section("Software")
        add_row("OS", data.get("OS", "N/A"))
        add_row("Windows Ver", data.get("WindowsVersion", "N/A"))
        add_row("Client Ver", data.get("client_version", "N/A"))

        add_section("Storage")
        drives = data.get("Drives", [])
        if drives:
            for d in drives:
                info = (
                    f"{d.get('Label')} ({d.get('Root')}) | "
                    f"{d.get('Free_GB')}GB free / {d.get('Capacity_GB')}GB total"
                )
                bar = QProgressBar()
                try:
                    usage = float(d.get("Usage", "0%").strip("%"))
                    bar.setValue(int(usage))
                except (ValueError, AttributeError):
                    bar.setValue(0)

                bar.setStyleSheet(
                    f"""
                    QProgressBar {{ border: 1px solid {C.BORDER_LIGHT}; border-radius: {R.SM}px;
                                   text-align: center; color: white; background: {C.BG_SIDEBAR}; height: 16px; }}
                    QProgressBar::chunk {{ background-color: {C.ACCENT_PRIMARY}; }}
                    """
                )

                row_layout = QVBoxLayout()
                row_layout.addWidget(QLabel(info))
                row_layout.addWidget(bar)
                container = QWidget()
                container.setLayout(row_layout)
                form.addRow(container)
        else:
            add_row("Drives", "No drive info available")

        self.scroll.setWidget(content)

    def reload_data(self):
        """Re-read the JSON status file and refresh the UI."""
        try:
            pc_name = self.pc_name or self.data.get("pc_name")
            if not pc_name:
                QMessageBox.warning(self, "Error", "PC name is missing. Cannot reload status.")
                return

            status_dir = self.hub.get_livestatus_dir()
            report_path = status_dir / f"{pc_name}.json"
            if not report_path.exists():
                QMessageBox.warning(self, "Error", f"Status file not found:\n{report_path}")
                return

            new_data = _load_json_with_fallback(report_path)
            self.data = dict(new_data or {})
            self.pc_name = self.data.get("pc_name") or pc_name
            self.setWindowTitle(f"System Specs: {self.pc_name}")
            self.build_content_widget(self.data)

            last_seen = self.data.get("last_seen", 0)
            try:
                last_seen_txt = datetime.fromtimestamp(float(last_seen)).strftime("%H:%M:%S")
            except (TypeError, ValueError, OSError):
                last_seen_txt = str(last_seen)
            QMessageBox.information(
                self,
                "Reloaded",
                f"Data refreshed from {self.pc_name}.json.\nLast Seen: {last_seen_txt}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def export_to_pdf(self):
        """Generate a PDF report."""
        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QTextDocument, QPageSize
        except ImportError:
            QMessageBox.critical(self, "Error", "PDF Support libraries missing.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PDF",
            f"{self.data.get('pc_name', 'Report')}.pdf",
            "PDF (*.pdf)",
        )
        if not path:
            return

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #26262D; }}
                h1 {{ color: #3EA8BF; border-bottom: 2px solid #3EA8BF; padding-bottom: 10px; }}
                h2 {{ color: #26262D; margin-top: 20px; border-bottom: 1px solid #E8E6E1; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                th {{ text-align: left; background-color: #E8E6E1; padding: 8px; border: 1px solid #E8E6E1; }}
                td {{ padding: 8px; border: 1px solid #E8E6E1; }}
                .highlight {{ font-weight: bold; color: #3EA8BF; }}
                .footer {{ margin-top: 30px; font-size: 10px; color: #87857F; text-align: center; border-top: 1px solid #E8E6E1; padding-top: 10px; }}
            </style>
        </head>
        <body>
            <h1>System Specification Report</h1>
            <p><strong>Workstation:</strong> {self.data.get('pc_name', 'N/A')} &nbsp;|&nbsp; <strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

            <h2>Identity</h2>
            <table>
                <tr><th>Field</th><th>Value</th></tr>
                <tr><td>Computer Name</td><td>{self.data.get('ComputerName', 'N/A')}</td></tr>
                <tr><td>User</td><td>{self.data.get('user', 'N/A')}</td></tr>
                <tr><td>IP Address</td><td>{self.data.get('IPAddress', 'N/A')}</td></tr>
                <tr><td>MAC Address</td><td>{self.data.get('MACAddress', 'N/A')}</td></tr>
            </table>

            <h2>Hardware</h2>
            <table>
                <tr><th>Field</th><th>Value</th></tr>
                <tr><td>Manufacturer</td><td>{self.data.get('Manufacturer', 'N/A')}</td></tr>
                <tr><td>Model</td><td>{self.data.get('Model', 'N/A')}</td></tr>
                <tr><td>Serial Number</td><td>{self.data.get('SerialNo', 'N/A')}</td></tr>
                <tr><td>CPU</td><td>{self.data.get('CPU', 'N/A')}</td></tr>
                <tr><td>GPU</td><td>{self.data.get('GPU', 'N/A')}</td></tr>
                <tr><td>RAM</td><td>{self.data.get('RAM_GB', 'N/A')}</td></tr>
            </table>

            <h2>Storage</h2>
            <table>
                <tr><th>Drive</th><th>Label</th><th>Capacity</th><th>Free Space</th><th>Usage</th></tr>
        """

        for d in self.data.get("Drives", []):
            html += f"""
                <tr>
                    <td>{d.get('Root')}</td>
                    <td>{d.get('Label')}</td>
                    <td>{d.get('Capacity_GB')} GB</td>
                    <td>{d.get('Free_GB')} GB</td>
                    <td class="highlight">{d.get('Usage')}</td>
                </tr>
            """

        html += f"""
            </table>

            <h2>Software Environment</h2>
            <table>
                <tr><td>OS</td><td>{self.data.get('OS', 'N/A')}</td></tr>
                <tr><td>Windows Version</td><td>{self.data.get('WindowsVersion', 'N/A')}</td></tr>
                <tr><td>UT Client</td><td>{self.data.get('client_version', 'N/A')}</td></tr>
            </table>

            <div class="footer">
                Report generated by Slate Admin Panel.
            </div>
        </body>
        </html>
        """

        doc = QTextDocument()
        doc.setHtml(html)

        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

        doc.print_(printer)
        QMessageBox.information(self, "Success", f"PDF Exported successfully:\n{path}")


class PCCard(QFrame):
    def __init__(self, pc_name, hub, verify_callback=None):
        super().__init__()
        self.pc_name = pc_name
        self.hub = hub
        self.current_data = {}
        self.verify_callback = verify_callback
        self.setFixedSize(220, 140)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context)

        self.main_layout = QVBoxLayout(self)
        self.hl = QHBoxLayout()
        self.lbl_name = QLabel(pc_name)
        self.lbl_name.setStyleSheet(
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; color: white; font-size: 13px; border:none;"
        )
        self.hl.addWidget(self.lbl_name)
        self.hl.addStretch()
        self.main_layout.addLayout(self.hl)

        self.lbl_user = QLabel("Loading...")
        self.lbl_user.setStyleSheet(f"color: {C.TEXT_SECONDARY}; border:none;")
        self.main_layout.addWidget(self.lbl_user)
        self.lbl_disk = QLabel("Loading...")
        self.lbl_disk.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; border:none;")
        self.main_layout.addWidget(self.lbl_disk)
        self.main_layout.addStretch()
        self.lbl_status = QLabel("● Connecting...")
        self.lbl_status.setStyleSheet(
            f"color: gray; font-weight: {T.WEIGHT_STYLE_BOLD}; font-size: 10px; border:none;"
        )
        self.main_layout.addWidget(self.lbl_status)

    def update_data(self, data, delta):
        self.current_data = data
        self.lbl_user.setText(data.get("user", "Unknown"))
        disk = data.get("disk_percent", 0)
        d_col = "#B4B1AA" if disk < 80 else "#D9635F"
        self.lbl_disk.setText(f"C: Drive {disk}%")
        self.lbl_disk.setStyleSheet(f"color: {d_col}; font-size: 11px; border:none;")

        status_col = "#5FBF8F" if delta < 60 else "#D9A441"
        status_txt = "Online" if delta < 60 else "Idle"
        self.lbl_status.setText(f"● {status_txt}")
        self.lbl_status.setStyleSheet(
            f"color: {status_col}; font-weight: {T.WEIGHT_STYLE_BOLD}; font-size: 10px; border:none;"
        )

        self.setStyleSheet(
            f"QFrame {{background-color: {C.BG_ELEVATED}; border-left: 4px solid {status_col}; "
            f"border-radius: {R.MD}px; }} QFrame:hover {{ background-color: #1D1D22; }}"
        )

    def show_context(self, pos):
        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background: {C.BORDER_DEFAULT}; color: white; border: 1px solid #2C2C34; }} "
            f"QMenu::item:selected {{ background: {C.ACCENT_PRIMARY}; color: black; }}"
        )

        act_details = menu.addAction("ℹ️ View System Specs")
        menu.addSeparator()
        act_rst = menu.addAction("Force Restart")
        act_off = menu.addAction("Force Shutdown")

        action = menu.exec_(QCursor.pos())

        if action == act_details:
            details_data = dict(self.current_data or {})
            if "pc_name" not in details_data:
                details_data["pc_name"] = self.pc_name
            d = PCDetailsDialog(details_data, self, hub=self.hub, pc_name=self.pc_name)
            d.exec()
        elif action == act_rst:
            if QMessageBox.question(None, "Confirm", f"Restart {self.pc_name}?") == QMessageBox.StandardButton.Yes:
                if self.verify_callback and not self.verify_callback():
                    return
                self.hub.post_command("restart", self.pc_name)
        elif action == act_off:
            if QMessageBox.question(None, "Confirm", f"Shutdown {self.pc_name}?") == QMessageBox.StandardButton.Yes:
                if self.verify_callback and not self.verify_callback():
                    return
                self.hub.post_command("shutdown", self.pc_name)


class LiveDashboard(QWidget):
    def __init__(self, hub, verify_callback=None):
        super().__init__()
        self.hub = hub
        self.verify_callback = verify_callback
        self.worker_controller = None
        self._is_closing = False
        self._is_cleaned = False
        self.setStyleSheet(f"background-color: {C.BG_MAIN}; color: {C.TEXT_PRIMARY};")
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_area = QScrollArea()
        self.grid_area.setWidgetResizable(True)
        self.grid_area.setStyleSheet("background: transparent; border: none;")
        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(15)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid_area.setWidget(self.grid_widget)
        self.main_layout.addWidget(self.grid_area)

        self.pc_widgets = {}
        self.worker = LiveStatusWorker(self.hub)
        self.worker.data_ready.connect(self.on_data_ready)
        self.worker.finished.connect(self._on_worker_thread_done)

        # Refreshing is routine and happens on a timer anyway, so it is a plain
        # secondary control rather than a full-width bar in the accent colour.
        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        btn_refresh = make_button("Refresh", "secondary", on_click=self.refresh_grid)
        btn_refresh.setIcon(draw_icon("refresh"))
        refresh_row.addWidget(btn_refresh)
        self.main_layout.addLayout(refresh_row)

        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(30000)
        self.auto_timer.timeout.connect(self.refresh_grid)
        self.auto_timer.start()
        safe_single_shot(1000, self, self.refresh_grid)

    def bind_worker_controller(self, controller):
        """Allow host module to inject standardized worker orchestration."""
        self.worker_controller = controller

    def refresh_grid(self):
        """Start the background worker to fetch data."""
        if self._is_closing:
            return
        if self.worker_controller is not None:
            self.worker_controller.request_refresh()
            return
        if not self.worker.isRunning():
            self.worker.start()

    def on_data_ready(self, loaded_data):
        """Handle data from worker and update UI."""
        sender = self.sender()
        if sender is not None and sender is not self.worker:
            return
        if self._is_closing:
            return

        now = time.time()
        found_pcs = []

        for data in loaded_data:
            pc_name = data.get("pc_name")
            if not pc_name:
                continue
            found_pcs.append(pc_name)

            delta = now - data.get("last_seen", 0)
            if delta > 300:
                continue

            if pc_name not in self.pc_widgets:
                card = PCCard(pc_name, self.hub, self.verify_callback)
                self.pc_widgets[pc_name] = card
                count = self.grid_layout.count()
                row, col = divmod(count, 4)
                self.grid_layout.addWidget(card, row, col)

            self.pc_widgets[pc_name].update_data(data, delta)

        for pc in list(self.pc_widgets.keys()):
            if pc not in found_pcs:
                w = self.pc_widgets.pop(pc)
                w.setParent(None)

        # Say something when the grid is empty.
        #
        # Machines that have not checked in for five minutes are skipped, so a
        # fleet that is simply switched off produced a blank panel with no
        # explanation at all - indistinguishable from the panel being broken.
        self._update_fleet_placeholder(reporting=len(self.pc_widgets),
                                       known=len(found_pcs))

    def _update_fleet_placeholder(self, reporting, known):
        if getattr(self, "_fleet_placeholder", None) is None:
            self._fleet_placeholder = QLabel("", self.grid_widget)
            self._fleet_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fleet_placeholder.setWordWrap(True)
            self._fleet_placeholder.setStyleSheet(
                f"color: {C.TEXT_TERTIARY}; font-size: {T.SIZE_MD}px; "
                f"background: transparent; padding: 40px;")
            self.grid_layout.addWidget(self._fleet_placeholder, 0, 0, 1, 4)

        if reporting:
            self._fleet_placeholder.hide()
            return

        if known:
            self._fleet_placeholder.setText(
                "No workstation has checked in for the last five minutes. "
                "%d machine(s) are known, but all are currently offline." % known)
        else:
            self._fleet_placeholder.setText(
                "No workstations have reported yet. "
                "Machines appear here once the client is running on them.")
        self._fleet_placeholder.show()

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
