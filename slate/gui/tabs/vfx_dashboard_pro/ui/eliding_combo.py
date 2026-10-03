"""
A combo box that cuts a long current text with an ellipsis.

QComboBox clips its text at the edge, mid-letter ("KLC - Kaalchakra – The Wh").
This draws the same frame and arrow and elides the text to the room left, with
the whole text as the tooltip.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class ElidingComboBox(QComboBox):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentIndexChanged.connect(lambda *_: self.setToolTip(self.currentText()))
        # Asks for room for the whole current text; a full toolbar can still
        # squeeze it (it is cut with an ellipsis then), an empty one gives it.
        self.currentIndexChanged.connect(lambda *_: self.updateGeometry())

    def sizeHint(self):
        hint = super().sizeHint()
        fm = self.fontMetrics()
        # The frame, padding and arrow: what the base hint adds to its own text.
        chrome = max(48, hint.width() - fm.horizontalAdvance("x" * max(1, self.minimumContentsLength())))
        want = fm.horizontalAdvance(self.currentText()) + chrome + 8
        cap = self.maximumWidth()
        if self.parentWidget() is not None and self.parentWidget().width() > 0:
            cap = min(cap, int(self.parentWidget().width() * 0.4))     # never more than 40% of the bar
        return hint.expandedTo(hint.__class__(min(want, cap), hint.height()))

    def elided_text(self) -> str:
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        rect = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                           QStyle.SubControl.SC_ComboBoxEditField, self)
        return self.fontMetrics().elidedText(self.currentText(), Qt.TextElideMode.ElideRight,
                                             max(10, rect.width() - 4))

    def paintEvent(self, event):
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        opt.currentText = self.elided_text()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)
