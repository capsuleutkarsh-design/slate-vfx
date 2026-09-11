"""
Confirming stitch shots before an ingest moves anything.

A stitch is one shot the client delivered as two or more folders - SH010_A and
SH010_B, SH010_left and SH010_right. Left alone the ingest makes two shots out
of it, which then get two bids, two comps and two deliveries.

Detection is a guess, and the wrong guess fuses two genuinely separate shots,
so nothing is merged without a person saying yes. This dialog shows every
suggestion, defaulted to merge, and lets the coordinator reject any of them or
correct the merged name before a single file moves.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QHeaderView, QLineEdit, QWidget,
)
from PySide6.QtCore import Qt

from ...core.domain.stitch_detect import StitchGroup, apply_groups
from ...core.infra.design_tokens import ColorTokens as C, TypographyTokens as T


class StitchConfirmDialog(QDialog):
    """Asks which of the detected stitch groups should actually be merged."""

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.groups = list(groups or [])
        self._rows = []

        self.setWindowTitle("Stitch shots found in this delivery")
        self.resize(760, 460)

        layout = QVBoxLayout(self)

        heading = QLabel("These folders look like parts of one shot")
        heading.setStyleSheet(
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; font-size: 15px; "
            f"color: {C.ACCENT_CYAN_ALT};"
        )
        layout.addWidget(heading)

        blurb = QLabel(
            "Ticked groups are ingested as a single shot, with every part in "
            "the same scan version side by side.\n"
            "Untick anything that is really two separate shots. You can also "
            "correct the merged shot name."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Merge", "Reel", "Parts", "Becomes one shot"])
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree)

        self._populate()

        buttons = QHBoxLayout()

        keep_all = QPushButton("Keep all separate")
        keep_all.setToolTip("Ingest every folder as its own shot, as before.")
        keep_all.clicked.connect(self._untick_all)
        buttons.addWidget(keep_all)

        buttons.addStretch()

        cancel = QPushButton("Cancel ingest")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        confirm = QPushButton("Continue")
        confirm.setDefault(True)
        confirm.setStyleSheet(
            f"background-color: {C.ACCENT_TEAL}; color: white; "
            f"font-weight: {T.WEIGHT_STYLE_BOLD}; padding: 5px 18px;"
        )
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)

        layout.addLayout(buttons)

    def _populate(self):
        for group in self.groups:
            parts = "  +  ".join(
                f"{part} ({label})"
                for part, label in zip(group.parts, group.part_labels)
            )
            item = QTreeWidgetItem(["", group.reel or "-", parts, ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            self.tree.addTopLevelItem(item)

            # The proposed name is the shared prefix, which is usually right,
            # but a client naming scheme can put the real shot name elsewhere.
            name_edit = QLineEdit(group.shot_name)
            name_edit.setMinimumWidth(160)
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(4, 0, 4, 0)
            holder_layout.addWidget(name_edit)
            self.tree.setItemWidget(item, 3, holder)

            self._rows.append((group, item, name_edit))

    def _untick_all(self):
        for _, item, _ in self._rows:
            item.setCheckState(0, Qt.Unchecked)

    def accepted_groups(self):
        """The groups the coordinator ticked, carrying any renamed shot."""
        chosen = []
        for group, item, name_edit in self._rows:
            if item.checkState(0) != Qt.Checked:
                continue
            name = name_edit.text().strip() or group.shot_name
            chosen.append(StitchGroup(shot_name=name, parts=list(group.parts),
                                      reel=group.reel))
        return chosen

    def mapping(self):
        """{(reel, folder) -> merged shot name} for the accepted groups."""
        return apply_groups([], self.accepted_groups())
