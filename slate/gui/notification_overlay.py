from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QPainter, QBrush, QPen, QFontMetrics
from .components.qt_safety import safe_single_shot
from slate.core.infra.gate import Gate


class NotificationOverlay(QWidget):
    """
    A toast that slides in at the bottom-right of its window.

    It is sized to what it says (300 to 420 px wide, up to six lines; anything
    longer is shortened, with the whole text in the tooltip), stays while the
    pointer is over it, has a close button, and only a left click on the body
    runs its action. It used to be a fixed 300x80 that cut messages off,
    placed with the window's size as screen coordinates (so on a second
    monitor it appeared somewhere else), and any click dismissed it.
    """

    MIN_WIDTH = 300
    MAX_WIDTH = 420
    MAX_LINES = 6
    MARGIN = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 10, 12)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.lbl_title = QLabel("")
        self.lbl_title.setStyleSheet(f"color: {Gate.ACCENT}; font-weight: bold; font-size: 14px; background: transparent;")
        top.addWidget(self.lbl_title, 1)

        from .core.icons import icon as draw_icon
        self.btn_close = QPushButton()
        self.btn_close.setIcon(draw_icon("close", Gate.TEXT_DIM, 14))
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip("Close")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {Gate.overlay(0.1)}; }}")
        self.btn_close.clicked.connect(self.hide_notification)
        top.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self.lbl_msg = QLabel("")
        self.lbl_msg.setStyleSheet(f"color: {Gate.TEXT}; font-size: 12px; background: transparent;")
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.lbl_msg)

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.setDuration(400)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.hide_notification)
        self.timer.setSingleShot(True)
        self._remaining = 0

        self.callback = None
        self.resize(self.MIN_WIDTH, 80)

    # ----------------------------------------------------------- size
    def _fit_text(self, message: str) -> str:
        """The message, shortened to MAX_LINES at the toast's width."""
        metrics = QFontMetrics(self.lbl_msg.font())
        width = self.MAX_WIDTH - 30
        lines, line = [], ""
        for word in str(message or "").split():
            candidate = f"{line} {word}".strip()
            if metrics.horizontalAdvance(candidate) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        if len(lines) <= self.MAX_LINES:
            return str(message or "")
        kept = lines[:self.MAX_LINES]
        kept[-1] = metrics.elidedText(kept[-1] + " " + " ".join(lines[self.MAX_LINES:]),
                                      Qt.TextElideMode.ElideRight, width)
        return " ".join(kept)

    def _size_to_content(self):
        metrics = QFontMetrics(self.lbl_msg.font())
        natural = metrics.horizontalAdvance(self.lbl_msg.text()) + 40
        width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, natural))
        self.setFixedWidth(width)
        layout = self.layout()
        layout.activate()
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else self.sizeHint().height()
        self.setFixedHeight(max(70, height))

    def _target(self):
        """Bottom-right of the parent window, in screen coordinates (its own screen)."""
        parent = self.parent()
        corner = parent.mapToGlobal(parent.rect().bottomRight())
        x = corner.x() - self.width() - self.MARGIN
        y = corner.y() - self.height() - self.MARGIN
        return QPoint(x, y), corner.y() + 10

    # ----------------------------------------------------------- show / hide
    def show_message(self, title, message, duration=4000, on_click=None):
        self.lbl_title.setText(title)
        shown = self._fit_text(message)
        self.lbl_msg.setText(shown)
        self.setToolTip(str(message) if shown != str(message) else "")
        self.callback = on_click
        self._size_to_content()

        if self.parent():
            target, below = self._target()
            start = QPoint(target.x(), below)
            self.move(start)
            self.show()
            self.raise_()
            self.anim.stop()
            self.anim.setStartValue(start)
            self.anim.setEndValue(target)
            self.anim.start()
            self._remaining = int(duration)
            self.timer.start(self._remaining)

    def hide_notification(self):
        self.timer.stop()
        if self.parent():
            _target, below = self._target()
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(self.x(), below))
            self.anim.start()
            safe_single_shot(400, self, self.hide)
        else:
            self.hide()

    # ----------------------------------------------------------- hover / click
    def enterEvent(self, event):
        # Being read: the timer waits.
        if self.timer.isActive():
            self._remaining = max(1500, self.timer.remainingTime())
            self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible() and not self.timer.isActive() and self._remaining:
            self.timer.start(self._remaining)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.callback:
            self.callback()
        self.hide_notification()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(Gate.qcolor(Gate.RAISED, 0.96)))
        painter.setPen(QPen(QColor(Gate.LINE), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
