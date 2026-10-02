"""
Confirming stitch shots before an ingest moves anything.

A stitch is one shot the client delivered as two or more folders - SH010_A and
SH010_B, SH010_left and SH010_right. Left alone the ingest makes two shots out
of it, which then get two bids, two comps and two deliveries.

Detection is a guess, and the wrong guess fuses two genuinely separate shots,
so nothing is merged without a person saying yes. This dialog shows every
suggestion - ticked when the parts are clearly marked (A/B, left/right, fg/bg),
unticked when only a bare number tells them apart - and lets the coordinator
reject any of them or correct the merged name before a single file moves. A
merged name that could not be a folder ('..', '/', ':') stops Continue with
the reason, instead of building a shot outside the project.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...core.domain.naming import name_problem
from ...core.domain.stitch_detect import StitchGroup, apply_groups
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button


def _part_detail(survey, reel, part):
    """'240 frames, 1001-1240' for a part, from the survey when there is one."""
    if survey is None:
        return ""
    from slate.utils.sequence_utils import group_frames

    for shot in survey.shots:
        if shot.reel == reel and part in (shot.name, shot.base, shot.source_name):
            sequences, stills = group_frames([f.path for f in shot.files])
            if sequences:
                seq = sequences[0]
                more = f" (+{len(sequences) - 1} more)" if len(sequences) > 1 else ""
                return f"{seq.frame_count} frames, {seq.start}-{seq.end}{more}"
            return f"{len(stills)} file(s)"
    return ""


class StitchConfirmDialog(QDialog):
    """Asks which of the detected stitch groups should actually be merged."""

    def __init__(self, groups, parent=None, survey=None):
        super().__init__(parent)
        self.groups = list(groups or [])
        self.survey = survey
        self._rows = []

        self.setWindowTitle("Stitch shots found in this delivery")
        self.resize(820, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_2)

        heading = QLabel("These folders look like parts of one shot")
        heading.setStyleSheet(f"font-weight: 600; font-size: {Gate.SIZE_LG}px; color: {Gate.TEXT};")
        layout.addWidget(heading)

        blurb = QLabel(
            "Ticked groups are ingested as a single shot, with every part in the same scan "
            "version. Untick anything that is really two separate shots, and correct the merged "
            "name if it is wrong. Groups only told apart by a number start unticked.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(blurb)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(["Merge", "Reel", "Parts", "Becomes one shot"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(3, 220)
        layout.addWidget(self.tree, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {Gate.BAD};")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.merge_all_btn = make_button("Merge all", "ghost", on_click=self._tick_all,
                                         tooltip="Tick every group.")
        self.keep_all_btn = make_button("Keep all separate", "ghost", on_click=self._untick_all,
                                        tooltip="Ingest every folder as its own shot.")
        buttons.addWidget(self.merge_all_btn)
        buttons.addWidget(self.keep_all_btn)
        buttons.addStretch()
        self.cancel_btn = make_button("Cancel ingest", "secondary", on_click=self.reject)
        buttons.addWidget(self.cancel_btn)
        self.confirm_btn = make_button("Continue", "primary", on_click=self.accept)
        buttons.addWidget(self.confirm_btn)
        layout.addLayout(buttons)

        self._populate()
        self.tree.itemChanged.connect(lambda *_: self._validate())
        self._validate()

    def _populate(self):
        for group in self.groups:
            labels = group.part_labels
            parts_text = "  +  ".join(f"{part} ({label})" for part, label in zip(group.parts, labels))
            details = [f"{part}: {_part_detail(self.survey, group.reel, part)}".rstrip(": ")
                       for part in group.parts]
            item = QTreeWidgetItem(["", group.reel or "-", parts_text, ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if getattr(group, "confident", True)
                               else Qt.CheckState.Unchecked)
            item.setToolTip(2, "\n".join(details))
            item.setToolTip(0, "Ticked: these folders become one shot.")
            self.tree.addTopLevelItem(item)

            # The proposed name is the shared prefix, which is usually right,
            # but a client naming scheme can put the real shot name elsewhere.
            name_edit = QLineEdit(group.shot_name)
            name_edit.setMinimumWidth(160)
            name_edit.setToolTip("The shot these parts become.")
            name_edit.textChanged.connect(lambda *_: self._validate())
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(4, 0, 4, 0)
            holder_layout.addWidget(name_edit)
            self.tree.setItemWidget(item, 3, holder)

            self._rows.append((group, item, name_edit))

    def _set_all(self, state):
        for _, item, _ in self._rows:
            item.setCheckState(0, state)

    def _tick_all(self):
        self._set_all(Qt.CheckState.Checked)

    def _untick_all(self):
        self._set_all(Qt.CheckState.Unchecked)

    def problems(self):
        """Why Continue is off: a merged name that cannot be a folder."""
        out = []
        for group, item, name_edit in self._rows:
            if item.checkState(0) != Qt.CheckState.Checked:
                name_edit.setStyleSheet("")
                continue
            text = name_edit.text().strip()
            problem = name_problem(text, f"The merged name for {' + '.join(group.parts)}") if text else None
            name_edit.setStyleSheet(f"QLineEdit {{ border: 1px solid {Gate.BAD}; }}" if problem else "")
            if problem:
                out.append(problem)
        return out

    def _validate(self):
        problems = self.problems()
        self.error_label.setVisible(bool(problems))
        self.error_label.setText("\n".join(problems[:3]))
        self.confirm_btn.setEnabled(not problems)
        self.confirm_btn.setToolTip(problems[0] if problems else "Carry on with these choices.")

    def accept(self):
        if self.problems():
            self._validate()
            return
        super().accept()

    def accepted_groups(self):
        """The groups the coordinator ticked, carrying any renamed shot."""
        chosen = []
        for group, item, name_edit in self._rows:
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            name = name_edit.text().strip() or group.shot_name
            if name_problem(name):
                continue
            chosen.append(StitchGroup(shot_name=name, parts=list(group.parts), reel=group.reel,
                                      confident=getattr(group, "confident", True)))
        return chosen

    def mapping(self):
        """{(reel, folder) -> merged shot name} for the accepted groups."""
        return apply_groups([], self.accepted_groups())
