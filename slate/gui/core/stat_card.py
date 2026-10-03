"""
A number with a label: the summary cards above a table.

Scheduling, Bidding and IT Deployment each had their own copy of
create_stat_card(), and all three had the same three faults:

  - the accent was a 4 px left border on a rounded frame, which Qt draws as a
    curved "(" bracket rather than a straight bar;
  - the value's size was set with QFont, which the old global stylesheet
    overruled, so "47" and "$380,328,958" came out at 11 px - smaller than the
    upper-case label above them;
  - each card was ~60 px tall for one small number, so at 1366x768 the table
    under them showed a dozen rows.

StatCard draws the accent as its own straight strip, sets its sizes in its own
stylesheet (so nothing global can shrink them), has a compact one-line form
for screens where the table needs the height, and can be clicked (to filter
the table to what the card counts).

    card = StatCard("Overdue", 12, tone="bad")
    card.set_value(14)

    strip = StatStrip(compact=True)
    total = strip.add("Milestones", 47)
    late = strip.add("Overdue", 12, tone="bad", on_click=self.show_overdue)
    layout.addWidget(strip)

tone is a status word ('ok', 'warn', 'bad', 'info', 'accent', 'idle') or a
colour from Gate. The value is drawn in text colour, not the tone - colour on
the strip is enough, and a whole row of coloured numbers is noise.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from slate.core.infra.gate import Gate


def _tone(tone) -> str:
    if not tone:
        return Gate.LINE
    text = str(tone)
    if text.startswith("#") or text.startswith("rgb"):
        return text
    words = {
        "ok": Gate.OK, "success": Gate.OK, "warn": Gate.WARN, "warning": Gate.WARN,
        "bad": Gate.BAD, "error": Gate.BAD, "danger": Gate.BAD, "info": Gate.INFO,
        "accent": Gate.ACCENT, "idle": Gate.IDLE, "neutral": Gate.LINE,
    }
    return words.get(text.strip().lower(), getattr(Gate, text.upper(), Gate.LINE))


class StatCard(QFrame):
    """One figure. clicked is emitted when the card was made clickable."""

    clicked = Signal()

    def __init__(self, label, value="", tone=None, caption="", compact=False,
                 clickable=False, tooltip="", parent=None):
        super().__init__(parent)
        self._compact = bool(compact)
        self._clickable = bool(clickable)
        self.setObjectName("statCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if tooltip:
            self.setToolTip(tooltip)
        if self._clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The accent is its own straight strip. As a border-left on a rounded
        # frame it was drawn as a bracket.
        self._strip = QFrame()
        self._strip.setFixedWidth(3)
        outer.addWidget(self._strip)

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(body, 1)

        self._label = QLabel()
        self._value = QLabel()
        self._caption = QLabel()
        self._caption.setWordWrap(True)
        for part in (self._label, self._value, self._caption):
            part.setTextFormat(Qt.TextFormat.PlainText)

        if self._compact:
            row = QHBoxLayout(body)
            row.setContentsMargins(Gate.SPACE_3, Gate.SPACE_2, Gate.SPACE_3, Gate.SPACE_2)
            row.setSpacing(Gate.SPACE_2)
            row.addWidget(self._label)
            row.addStretch(1)
            row.addWidget(self._value)
            self._caption.hide()
        else:
            column = QVBoxLayout(body)
            column.setContentsMargins(Gate.SPACE_3 + 2, Gate.SPACE_2 + 2, Gate.SPACE_3 + 2, Gate.SPACE_2 + 2)
            column.setSpacing(2)
            column.addWidget(self._label)
            column.addWidget(self._value)
            column.addWidget(self._caption)

        self._tone = tone
        self._restyle()
        self.set_label(label)
        self.set_value(value)
        self.set_caption(caption)

    # ------------------------------------------------------------ content
    def set_label(self, text):
        # Upper case is a style here, not the data: the label is stored as given.
        self._label.setText(str(text or "").upper())

    def set_value(self, value):
        self._value.setText("" if value is None else str(value))

    def value_text(self) -> str:
        return self._value.text()

    def set_caption(self, text):
        self._caption.setText(str(text or ""))
        self._caption.setVisible(bool(text) and not self._compact)

    def set_tone(self, tone):
        self._tone = tone
        self._restyle()

    def set_active(self, active: bool):
        """Outline the card in its tone while the view is filtered to it."""
        active = bool(active)
        if active != getattr(self, "_active", False):
            self._active = active
            self._restyle()

    # --------------------------------------------------------------- look
    def _restyle(self):
        hover = f"QFrame#statCard:hover {{ border-color: {Gate.TEXT_DIM}; }}" if self._clickable else ""
        border = _tone(self._tone) if getattr(self, "_active", False) else Gate.LINE
        self.setStyleSheet(
            f"QFrame#statCard {{ background-color: {Gate.PANEL}; border: 1px solid {border}; "
            f"border-radius: {Gate.RADIUS_MD}px; }} {hover}")
        # Square outer corners on the strip would poke out of the rounded card,
        # so it takes the card's radius on its left side only.
        self._strip.setStyleSheet(
            f"background-color: {_tone(self._tone)}; border: none; "
            f"border-top-left-radius: {Gate.RADIUS_MD}px; border-bottom-left-radius: {Gate.RADIUS_MD}px;")
        self._label.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_LABEL}; font-size: {Gate.SIZE_XS}px; "
            f"letter-spacing: 1.2px; background: transparent; border: none;")
        size = Gate.SIZE_XL if self._compact else Gate.SIZE_DISPLAY
        self._value.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: {size}px; "
            f"font-weight: 600; background: transparent; border: none;")
        self._caption.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px; background: transparent; border: none;")

    # ------------------------------------------------------------- clicks
    def mouseReleaseEvent(self, event):
        if self._clickable and event.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatStrip(QWidget):
    """A row of StatCards with even spacing."""

    def __init__(self, compact=False, parent=None):
        super().__init__(parent)
        self._compact = bool(compact)
        self.cards = []
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(Gate.SPACE_2)

    def add(self, label, value="", tone=None, caption="", on_click=None, tooltip="") -> StatCard:
        card = StatCard(label, value, tone=tone, caption=caption, compact=self._compact,
                        clickable=on_click is not None, tooltip=tooltip)
        if on_click is not None:
            card.clicked.connect(on_click)
        self._row.addWidget(card)
        self.cards.append(card)
        return card
