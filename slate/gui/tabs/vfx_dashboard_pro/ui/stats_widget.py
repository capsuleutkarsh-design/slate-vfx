"""
The status counters above the grid.

They used to be colour-blind to the rest of the screen (WIP amber here, cyan in
the grid), said REVIEW where the filter said SENT FOR REVIEW and UNKNOWN where
grouping said NO STATUS, looked like filter chips but did nothing when clicked,
and below ~1500 px were cut to '!5 APPROVEI' and 'RETA'.

Now: one colour per status (Gate.status_color), the same names as the filter
and the grid (shot_status), and each counter is a toggle - click RETAKE to see
only the retakes, click it again to see everything. Counters that do not fit go
into a "+N more" menu rather than being cut.
"""

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QSizePolicy, QWidget

from slate.core.domain import shot_status
from slate.core.infra.gate import Gate


class StatsWidget(QWidget):
    # A counter was clicked: the status it counts ('' for "No status"), or
    # None for the total (which clears the status filter).
    status_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(6)
        self.stat_containers: Dict[str, QPushButton] = {}
        self._order: List[str] = []
        self._active: Optional[str] = None
        self.more_button = QPushButton("")
        self.more_button.setObjectName("statsMore")
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.hide()
        self.more_button.clicked.connect(self._show_more)
        self._hidden: List[str] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------ building
    @staticmethod
    def _pill_style(colour: str, active: bool) -> str:
        background = Gate.tint(colour, 0.20) if active else Gate.tint(colour, 0.06)
        border = colour if active else Gate.tint(colour, 0.35)
        return (
            f"QPushButton {{ color: {colour}; background: {background}; border: 1px solid {border};"
            f" border-radius: 11px; padding: 2px 10px; font-weight: 700; font-size: {Gate.SIZE_XS}px;"
            f" min-height: 18px; }}"
            f"QPushButton:hover {{ background: {Gate.tint(colour, 0.16)}; }}"
        )

    def create_pill(self, key, text, colour, tooltip=""):
        button = QPushButton(text)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setProperty("statusKey", key)
        button.setProperty("pillColour", colour)
        button.setToolTip(tooltip)
        button.setStyleSheet(self._pill_style(colour, False))
        button.clicked.connect(lambda _checked=False, k=key: self._clicked(k))
        return button

    def _clicked(self, key):
        if key == "__total__":
            self.status_clicked.emit(None)
        elif self._active is not None and key == self._active:
            self.status_clicked.emit(None)          # a second click clears
        else:
            self.status_clicked.emit(key)

    def set_active(self, status: Optional[str]):
        """Show which status the grid is filtered to (None: none)."""
        self._active = None if status is None else shot_status.canonical(status)
        for key, button in self.stat_containers.items():
            active = key != "__total__" and self._active is not None and key == self._active
            button.setChecked(active)
            button.setStyleSheet(self._pill_style(button.property("pillColour"), active))

    def update_stats(self, shots):
        counts: Dict[str, int] = {}
        for s in shots or []:
            key = shot_status.canonical(getattr(s, "status", ""))
            counts[key] = counts.get(key, 0) + 1

        while self.main_layout.count():
            item = self.main_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.more_button:
                widget.setParent(None)
                widget.deleteLater()
        self.stat_containers = {}
        self._order = []

        total = len(shots or [])
        total_pill = self.create_pill("__total__", f"{total} {'shot' if total == 1 else 'shots'}",
                                      Gate.TEXT_2, "Every shot on screen. Click to clear the status filter.")
        self.main_layout.addWidget(total_pill)
        self.stat_containers["__total__"] = total_pill
        self._order.append("__total__")

        for key in sorted(counts, key=shot_status.order_key):
            count = counts[key]
            if not count:
                continue
            name = shot_status.label(key)
            pill = self.create_pill(
                key, f"{count} {name}", Gate.status_color(key) if key else Gate.TEXT_DIM,
                f"{shot_status.describe(key)}. Click to show only these; click again to show all.")
            self.main_layout.addWidget(pill)
            self.stat_containers[key] = pill
            self._order.append(key)

        self.main_layout.addWidget(self.more_button)
        self.main_layout.addStretch(1)
        self.set_active(self._active)
        self._reflow()

    # ------------------------------------------------------------ fitting
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self):
        """
        Hide counters that do not fit whole; list them under '+N more'.

        Showing or hiding a counter resizes this widget, which calls _reflow
        again; that inner call is ignored, and the hidden list is built fresh
        each time (it was counted twice and collapsed the row to "+14 more").
        """
        if getattr(self, "_reflowing", False):
            return
        available = self.width()
        if available <= 0:
            return
        self._reflowing = True
        try:
            spacing = self.main_layout.spacing()
            needs = {k: self.stat_containers[k].sizeHint().width() + spacing for k in self._order}
            hidden = []
            if sum(needs.values()) > available:
                self.more_button.setText(f"+{len(self._order)} more")
                reserve = self.more_button.sizeHint().width() + spacing
                used = 0
                for key in self._order:
                    if key != "__total__" and (hidden or used + needs[key] + reserve > available):
                        hidden.append(key)
                    else:
                        used += needs[key]
            self._hidden = hidden
            for key in self._order:
                self.stat_containers[key].setVisible(key not in hidden)
            if hidden:
                self.more_button.setText(f"+{len(hidden)} more")
                self.more_button.setToolTip(", ".join(
                    self.stat_containers[k].text() for k in hidden))
                self.more_button.setStyleSheet(self._pill_style(Gate.TEXT_2, False))
                self.more_button.show()
            else:
                self.more_button.hide()
        finally:
            self._reflowing = False

    def _show_more(self):
        menu = QMenu(self)
        for key in self._hidden:
            action = QAction(self.stat_containers[key].text(), menu)
            action.triggered.connect(lambda _c=False, k=key: self._clicked(k))
            menu.addAction(action)
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def visible_texts(self) -> List[str]:
        return [self.stat_containers[k].text() for k in self._order
                if not self.stat_containers[k].isHidden()]
