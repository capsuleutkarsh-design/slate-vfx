"""
Choosing what to send to OpenRV.

A shot has a plate and, as work comes in, a render from each department. Which
one a supervisor wants depends on why they are looking - checking a comp,
checking the prep under it, comparing a de-age against the original.

So the dialog asks. Only departments that have actually rendered something are
listed: offering one that has not just opens RV on nothing.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt

from ...core.domain.rv_review import build_request, launch
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T


class RVReviewDialog(QDialog):
    """Pick the departments to open in RV for one shot."""

    def __init__(self, request, parent=None, launcher=None):
        super().__init__(parent)
        self.request = request
        self._launcher = launcher

        self.setWindowTitle(f"Review {request.shot_name} in RV")
        self.resize(520, 400)

        layout = QVBoxLayout(self)

        heading = QLabel(f"What do you want to look at for {request.shot_name}?")
        heading.setStyleSheet(
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; font-size: 14px; "
            f"color: {C.ACCENT_CYAN_ALT};"
        )
        layout.addWidget(heading)

        blurb = QLabel(
            "Tick one to play it. Tick several to load them as a playlist and "
            "compare them in the same RV session."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.list = QListWidget()
        layout.addWidget(self.list)
        self._populate()

        buttons = QHBoxLayout()
        buttons.addStretch()

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.open_btn = QPushButton("Open in RV")
        self.open_btn.setDefault(True)
        self.open_btn.setStyleSheet(
            f"background-color: {C.ACCENT_TEAL}; color: white; "
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; padding: 5px 18px;"
        )
        self.open_btn.clicked.connect(self.open_in_rv)
        self.open_btn.setEnabled(bool(self.request.options))
        buttons.addWidget(self.open_btn)

        layout.addLayout(buttons)

    def _populate(self):
        if not self.request.options:
            item = QListWidgetItem(
                "Nothing to review yet - no scan or render found for this shot."
            )
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
            return

        for index, option in enumerate(self.request.options):
            item = QListWidgetItem(f"{option.label}    {option.detail}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # The plate is what a review usually starts from.
            item.setCheckState(Qt.Checked if index == 0 else Qt.Unchecked)
            item.setData(Qt.UserRole, option.key)
            self.list.addItem(item)

    def selected_keys(self):
        """The departments ticked, in the order they are shown."""
        keys = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() == Qt.Checked:
                key = item.data(Qt.UserRole)
                if key:
                    keys.append(key)
        return keys

    def open_in_rv(self):
        keys = self.selected_keys()
        if not keys:
            QMessageBox.information(
                self, "Nothing selected",
                "Tick at least one thing to open."
            )
            return

        paths = self.request.media_paths(keys)
        if not launch(paths, launcher=self._launcher):
            QMessageBox.warning(
                self, "RV would not start",
                "Could not open OpenRV.\n\n"
                "Check that it is installed alongside the software, or set "
                "RV_PATH to the folder holding rv.exe."
            )
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
