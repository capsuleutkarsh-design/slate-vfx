"""
Choosing what to send to OpenRV.

A shot has a plate and, as work comes in, a render from each department. Which
one a supervisor wants depends on why they are looking - checking a comp,
checking the prep under it, comparing a de-age against the original.

So the dialog asks. Only departments that have actually rendered something are
listed: offering one that has not just opens RV on nothing. Each row reads the
same way - layer, version, frame range or file - with a visible tick box, and
Tick all / Untick all (MED-122).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ...core.domain.rv_review import build_request, launch
from ..core.controls import make_button, set_default_button
from ..core.table_style import style_table
from slate.core.infra.gate import Gate

KEY_ROLE = Qt.ItemDataRole.UserRole


class RVReviewDialog(QDialog):
    """Pick the departments to open in RV for one shot."""

    def __init__(self, request, parent=None, launcher=None):
        super().__init__(parent)
        self.request = request
        self._launcher = launcher
        self.setWindowTitle(f"Review {request.shot_name} in RV")
        self.resize(560, 400)

        layout = QVBoxLayout(self)
        heading = QLabel(f"What do you want to look at for {request.shot_name}?")
        heading.setStyleSheet(f"font-weight: 600; font-size: 14px; color: {Gate.TEXT};")
        layout.addWidget(heading)
        blurb = QLabel("Tick one to play it. Tick several to load them as a playlist and "
                       "compare them in the same RV session.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(blurb)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Layer", "Version", "Frames / file"])
        style_table(self.table, {0: ("fixed", 34), "Layer": ("interactive", 120),
                                 "Version": ("interactive", 80), "Frames / file": "stretch"},
                    sortable=False)
        self.table.itemChanged.connect(lambda *_: self._update_open())
        self.table.cellDoubleClicked.connect(self._toggle_row)
        layout.addWidget(self.table, 1)
        # Back-compat name.
        self.list = self.table

        buttons = QHBoxLayout()
        # The same words as the Timeline Viewer's list (MED2-052).
        self.btn_all = make_button("Tick all", "ghost", on_click=lambda: self._tick_all(True))
        self.btn_none = make_button("Untick all", "ghost", on_click=lambda: self._tick_all(False))
        buttons.addWidget(self.btn_all)
        buttons.addWidget(self.btn_none)
        buttons.addStretch()
        cancel = make_button("Cancel", "secondary", on_click=self.reject)
        buttons.addWidget(cancel)
        self.open_btn = make_button("Open in RV", "primary", on_click=self.open_in_rv)
        buttons.addWidget(self.open_btn)
        layout.addLayout(buttons)
        set_default_button(self, self.open_btn)
        self._populate()

    def _populate(self):
        self.table.blockSignals(True)
        options = self.request.options
        self.table.setRowCount(len(options))
        for row, option in enumerate(options):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                          | Qt.ItemFlag.ItemIsSelectable)
            # The plate is what a review usually starts from.
            tick.setCheckState(Qt.CheckState.Checked if row == 0 else Qt.CheckState.Unchecked)
            tick.setData(KEY_ROLE, option.key)
            self.table.setItem(row, 0, tick)
            for col, text in enumerate((option.label, option.version or "—", option.frames), 1):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                if col == 3:
                    item.setToolTip(str(option.clip.path))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        if not options:
            from ..core.empty_state import EmptyState
            EmptyState.over(self.table, "Nothing to review yet",
                            "No scan or render was found for this shot.")
        self._update_open()

    def _toggle_row(self, row, _col):
        item = self.table.item(row, 0)
        if item is not None:
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)

    def _tick_all(self, on):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _update_open(self):
        count = len(self.selected_keys())
        self.open_btn.setEnabled(count > 0)
        self.open_btn.setText("Open in RV" if count <= 1 else f"Open {count} in RV")

    def selected_keys(self):
        """The departments ticked, in the order they are shown."""
        keys = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                key = item.data(KEY_ROLE)
                if key:
                    keys.append(key)
        return keys

    def open_in_rv(self):
        from ..components.feedback import warn
        keys = self.selected_keys()
        if not keys:
            warn(self, "Open in RV", "Tick at least one thing to open.")
            return
        paths = self.request.media_paths(keys)
        if not launch(paths, launcher=self._launcher):
            # No environment variables for a supervisor; the reason is in the
            # log for IT (MED2-063).
            warn(self, "Open in RV", "RV is not installed on this machine, or could not start "
                                     "- tell IT.")
            return
        self.accept()


def review_shot_in_rv(shot, parent=None, project_root=None, folder_resolver=None,
                      launcher=None):
    """
    Open the picker for one shot.

    Returns False when the shot has nothing on disk yet, so the caller can say
    so rather than showing an empty dialog.
    """
    request = build_request(shot, project_root, folder_resolver=folder_resolver)
    if not request.options:
        return False
    dialog = RVReviewDialog(request, parent, launcher=launcher)
    dialog.exec()
    return True
