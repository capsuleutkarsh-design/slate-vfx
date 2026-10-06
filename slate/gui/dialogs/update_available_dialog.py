from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QFrame
from PySide6.QtCore import Qt, QTimer
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button, set_default_button

REQUIRED_SECONDS = 5 * 60


class UpdateAvailableDialog(QDialog):
    """
    An update is downloaded and checked. Update now (accept) or When I close
    Slate (reject). A required update has no "later": it counts down and
    updates by itself. Shown without blocking, so the artist can save first.
    """
    def __init__(self, manifest, parent=None, seconds=REQUIRED_SECONDS):
        super().__init__(parent)
        self.required = bool(manifest.get("required"))
        self.remaining = int(seconds)
        self.setWindowTitle("Update Slate")
        self.setMinimumWidth(460)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        # The rounded frame is drawn on nothing, so its corners are round.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(f"""
            QDialog {{ background: transparent; }}
            QFrame#UpdateFrame {{
                background-color: {Gate.RAISED};
                border: 1px solid {Gate.LINE};
                border-radius: 12px;
            }}
            QLabel {{ color: {Gate.TEXT}; background: transparent; border: none; }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("UpdateFrame")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(10)

        from slate.gui.login_dialog import version_text
        title = QLabel("Slate must update" if self.required else "A new version of Slate is ready")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {Gate.TEXT};")
        version_lbl = QLabel(version_text(manifest.get("version", "unknown")))
        version_lbl.setStyleSheet(f"font-size: 14px; color: {Gate.TEXT_2};")
        self.message = QLabel()
        self.message.setWordWrap(True)
        for widget in (title, version_lbl, self.message):
            layout.addWidget(widget)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_later = None
        if not self.required:
            self.message.setText("Update now closes Slate, installs the update and opens "
                                 "Slate again. Or carry on: it installs when you close Slate.")
            self.btn_later = make_button("When I close Slate", "secondary", on_click=self.reject)
            buttons.addWidget(self.btn_later)
        self.btn_update = make_button("Update now", "primary", on_click=self.accept)
        buttons.addWidget(self.btn_update)
        layout.addLayout(buttons)
        set_default_button(self, self.btn_update)

        if self.required:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(1000)
            self._show_countdown()

    def _show_countdown(self):
        minutes, seconds = divmod(max(0, self.remaining), 60)
        self.message.setText(
            "The studio needs everybody on this version. Slate will close and update in "
            "%d:%02d, so save your work now. It opens again by itself." % (minutes, seconds))

    def _tick(self):
        self.remaining -= 1
        self._show_countdown()
        if self.remaining <= 0:
            self.timer.stop()
            self.accept()

    def reject(self):
        # Esc and the window's close mean "later", which a required update does not have.
        if not self.required:
            super().reject()

    def mousePressEvent(self, event):
        # Allow dragging the window
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, "drag_position"):
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
