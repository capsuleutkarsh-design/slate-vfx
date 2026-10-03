import html
import time

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTextBrowser,
                               QPushButton, QHBoxLayout, QFrame)
from PySide6.QtCore import Qt
from slate.core.infra.gate import Gate
from slate.gui.core.controls import plain, make_button, set_default_button

# "Remind me later" waits this long before the same version is offered again.
SNOOZE_SECONDS = 24 * 60 * 60
SNOOZE_KEY = "update_snooze"


def _config():
    from slate.core.infra.global_config import GlobalConfig
    return GlobalConfig


def snooze(version: str, now: float = None) -> None:
    """Remember 'remind me later' for this version (this machine only)."""
    now = time.time() if now is None else now
    _config().set(SNOOZE_KEY, {"version": str(version), "until": now + SNOOZE_SECONDS})


def is_snoozed(version: str, now: float = None) -> bool:
    """Whether this version was put off less than a day ago."""
    now = time.time() if now is None else now
    data = _config().get(SNOOZE_KEY, {}) or {}
    if not isinstance(data, dict):
        return False
    try:
        return str(data.get("version")) == str(version) and float(data.get("until", 0)) > now
    except (TypeError, ValueError):
        return False


def notes_html(raw_notes) -> str:
    """
    Release notes as plain text: escaped, line breaks kept. They were put in
    as HTML, so a manifest's '<img src=x>' was rendered as a broken image.
    """
    text = html.escape(str(raw_notes or "No notes provided."))
    return f"<div style='font-size: 13px; line-height: 1.4;'>{text.replace(chr(10), '<br>')}</div>"


class UpdateAvailableDialog(QDialog):
    """
    A new version is available: what is in it, and Download and install or
    Remind me later (which really waits a day before asking again).
    """
    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.setWindowTitle("Update available")
        self.setMinimumSize(500, 560)
        self.resize(500, 600)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        # The rounded frame is drawn on nothing, so its corners are round.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(f"""
            QDialog {{ background: transparent; }}
            QFrame#UpdateFrame {{
                background-color: {Gate.RAISED};
                border: 1px solid {Gate.LINE};
                border-radius: 12px;
            }}
            QFrame#UpdateHeader {{
                background-color: {Gate.PANEL};
                border: none;
                border-bottom: 1px solid {Gate.LINE};
                border-top-left-radius: 12px; border-top-right-radius: 12px;
            }}
            QLabel {{ color: {Gate.TEXT}; background: transparent; border: none; }}
            QTextBrowser {{
                background-color: {Gate.INPUT};
                color: {Gate.TEXT_2};
                border: 1px solid {Gate.LINE};
                border-radius: 6px;
                padding: 10px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("UpdateFrame")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- HEADER ---
        header = QFrame()
        header.setObjectName("UpdateHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 12, 18)
        titles = QVBoxLayout()
        title = QLabel("A new version of Slate is ready")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {Gate.TEXT};")
        from slate.gui.login_dialog import version_text
        version_lbl = QLabel(version_text(manifest.get('version', 'unknown')))
        version_lbl.setStyleSheet(f"font-size: 14px; color: {Gate.TEXT_2}; font-weight: 500;")
        titles.addWidget(title)
        titles.addWidget(version_lbl)
        header_layout.addLayout(titles, 1)

        from slate.gui.core.icons import icon as draw_icon
        self.btn_close = QPushButton()
        self.btn_close.setIcon(draw_icon("close", Gate.TEXT_DIM, 16))
        self.btn_close.setToolTip("Close (remind me later)")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: {Gate.overlay(0.08)}; }}")
        self.btn_close.clicked.connect(self.remind_later)
        header_layout.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        # --- BODY ---
        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(20, 12, 20, 10)
        lbl_notes = QLabel("What's new")
        lbl_notes.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        body_layout.addWidget(lbl_notes)

        self.notes_area = QTextBrowser()
        self.notes_area.setOpenExternalLinks(False)
        self.notes_area.setHtml(self._format_notes(manifest.get("notes", "No notes provided.")))
        body_layout.addWidget(self.notes_area)
        layout.addLayout(body_layout)

        # --- FOOTER ---
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(20, 10, 20, 20)
        footer_layout.addStretch(1)
        self.btn_later = make_button("Remind me later", "secondary", on_click=self.remind_later,
                                     tooltip="Ask again tomorrow")
        self.btn_update = make_button("Download and install", "primary", on_click=self.accept)
        footer_layout.addWidget(self.btn_later)
        footer_layout.addWidget(self.btn_update)
        layout.addLayout(footer_layout)
        set_default_button(self, self.btn_update)

    def remind_later(self):
        """Close, and do not offer this version again for a day."""
        snooze(self.manifest.get("version", ""))
        super().reject()

    def reject(self):
        # Esc puts it off like the close button: it came back on the next start.
        self.remind_later()

    def _format_notes(self, raw_notes):
        return notes_html(raw_notes)

    def mousePressEvent(self, event):
        # Allow dragging the window
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, "drag_position"):
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
