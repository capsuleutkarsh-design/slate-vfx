"""
A date picker that can also say "no date".

QDateEdit always holds a date, so every empty target showed today and was
saved as today, and 'TBD' was replaced by today the moment the row was saved.
OptionalDateEdit keeps three states apart:

    a date          shown and returned as ISO ('2026-10-03')
    blank           shown as "No date", returned as ''
    text, not date  ('TBD') shown as itself and returned untouched until a
                    real date is picked or the field is cleared

``is_changed()`` says whether the person actually changed it, so opening and
closing the picker never writes anything.
"""

from PySide6.QtCore import QDate, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QDateEdit, QHBoxLayout, QToolButton, QWidget

from slate.core.domain.dates import parse_date
from slate.gui.core.data_display import setup_date_edit


class OptionalDateEdit(QDateEdit):
    BLANK = QDate(1900, 1, 1)
    value_changed = Signal()

    def __init__(self, value=None, parent=None, blank_text: str = "No date"):
        super().__init__(parent)
        setup_date_edit(self)
        self.setMinimumDate(self.BLANK)
        self._blank_text = blank_text
        self._raw = ""
        self._opened_blank = False
        self._picked = False
        cal = self.calendarWidget()
        if cal is not None:
            cal.clicked.connect(self._on_pick)
            cal.activated.connect(self._on_pick)
            self._watcher = _PopupWatcher(self)
            cal.installEventFilter(self._watcher)
        self.dateChanged.connect(lambda *_: self.value_changed.emit())
        self.set_value(value)

    # ------------------------------------------------------------ value
    def set_value(self, value):
        text = str(value or "").strip()
        d = parse_date(text)
        self.blockSignals(True)
        if d is not None:
            self._raw = ""
            self.setSpecialValueText(self._blank_text)
            self.setDate(QDate(d.year, d.month, d.day))
        else:
            self._raw = text
            self.setSpecialValueText(text or self._blank_text)
            self.setDate(self.BLANK)
        self.blockSignals(False)
        self._initial = self.value()

    def is_blank(self) -> bool:
        return self.date() == self.BLANK

    def value(self) -> str:
        """ISO for a date, '' for none, or the stored text it was given."""
        if self.is_blank():
            return self._raw
        return self.date().toString("yyyy-MM-dd")

    def clear(self):
        self._raw = ""
        self.setSpecialValueText(self._blank_text)
        self.setDate(self.BLANK)
        self.value_changed.emit()

    def is_changed(self) -> bool:
        return self.value() != self._initial

    # ------------------------------------------------------------ picking
    def _on_pick(self, *_):
        self._picked = True

    def mousePressEvent(self, event):
        if self.is_blank():
            # Open the calendar on today, not on 1900 - and put it back if
            # nothing is picked.
            self._opened_blank = True
            self._picked = False
            self.blockSignals(True)
            self.setDate(QDate.currentDate())
            self.blockSignals(False)
        super().mousePressEvent(event)
        cal = self.calendarWidget()
        if self._opened_blank and (cal is None or not cal.isVisible()):
            self._popup_closed()

    def _popup_closed(self):
        if self._opened_blank and not self._picked:
            self.blockSignals(True)
            self.setDate(self.BLANK)
            self.blockSignals(False)
        self._opened_blank = False

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and \
                (self.is_blank() or event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.clear()
            return
        super().keyPressEvent(event)


class _PopupWatcher(QObject):
    def __init__(self, edit: OptionalDateEdit):
        super().__init__(edit)
        self._edit = edit

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Hide:
            self._edit._popup_closed()
        return False


class OptionalDateField(QWidget):
    """An OptionalDateEdit with a small clear button beside it."""

    value_changed = Signal()

    def __init__(self, value=None, parent=None, blank_text: str = "No date"):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.edit = OptionalDateEdit(value, self, blank_text=blank_text)
        self.edit.value_changed.connect(self._sync)
        row.addWidget(self.edit, 1)
        self.clear_button = QToolButton(self)
        self.clear_button.setText("×")
        self.clear_button.setToolTip("Clear the date")
        self.clear_button.setAutoRaise(True)
        self.clear_button.clicked.connect(self.edit.clear)
        row.addWidget(self.clear_button)
        self._sync()

    def _sync(self):
        self.clear_button.setEnabled(not self.edit.is_blank() or bool(self.edit._raw))
        self.value_changed.emit()

    def set_value(self, value):
        self.edit.set_value(value)
        self._sync()

    def value(self) -> str:
        return self.edit.value()

    def is_changed(self) -> bool:
        return self.edit.is_changed()

    def setReadOnly(self, read_only: bool):
        self.edit.setReadOnly(read_only)
        self.clear_button.setVisible(not read_only)


def scaled_font(font, factor: float):
    """A copy of `font`, `factor` times larger - in points or in pixels, whichever it is set in."""
    from PySide6.QtGui import QFont
    out = QFont(font)
    if out.pointSizeF() > 0:
        out.setPointSizeF(out.pointSizeF() * factor)
    elif out.pixelSize() > 0:
        out.setPixelSize(max(1, round(out.pixelSize() * factor)))
    return out
