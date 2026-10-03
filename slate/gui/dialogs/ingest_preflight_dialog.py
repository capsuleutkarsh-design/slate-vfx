"""
The check before an ingest: what will happen, where, and what is odd.

One click used to start moving a whole client drive - a mistyped project code
was "fixed" silently and the drive emptied into it. Nothing starts now until a
coordinator has seen this summary, made from the same survey the run uses:

* how many shots, reels, files and how much data, from where to where
* Copy (the client drive is left as it was) or Move (it is emptied)
* every shot with its reel and the name it gets - editable, and checked: a
  name that cannot be a folder, or two folders that would become one shot,
  stop the run with the reason and the rows are marked
* the client material (paperwork, LUTs, references, loose files at the top of
  the drive) and where it is filed - loose media can be made a shot here
* what Slate noticed: names it tidied, shots already in the project, shots
  delivered twice, ignored junk, paths past 260 characters, a template with
  no shot folders, a project folder found under a slightly different name

The primary button says what it will do - "Copy 1,240 files" - and a dry run
is one click away (and from a dry run, the real run).
"""

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QRadioButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from slate.core.domain.ingest_survey import IngestSurvey
from slate.core.domain.naming import shot_name_problem
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button
from slate.gui.core.table_style import (clear_cell_status, dim_cell, numeric_item, set_cell_status,
                                       style_table)

COPY, MOVE = "copy", "move"

# The row's shot (its index in survey.shots) - rows can be sorted.
_SHOT_ROLE = Qt.ItemDataRole.UserRole + 1


def _wrap(path) -> str:
    """A path that may wrap after its separators."""
    return str(path).replace("\\", "\\​").replace("/", "/​")


def size_text(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


class IngestPreflightDialog(QDialog):
    """Shows the plan; returns the operation, dry run and any corrected names."""

    COL_REEL, COL_SHOT, COL_FROM, COL_FILES, COL_SIZE, COL_NOTES = range(6)

    def __init__(self, survey: IngestSurvey, *, project_code: str, project_path,
                 operation: str = COPY, dry_run: bool = False, template_name: str = "",
                 shot_folders_defaulted: bool = False, stitch_mapping: Dict = None,
                 long_paths: int = 0, project_choice: Optional[Dict] = None,
                 project_exists: bool = False, client_folder: str = "01_Frm Client",
                 untracked: int = 0, parent=None):
        super().__init__(parent)
        self.survey = survey
        self.project_code = project_code
        self.project_path = Path(project_path)
        self.stitch_mapping = dict(stitch_mapping or {})
        self.dry_run = bool(dry_run)
        self.operation = MOVE if operation == MOVE else COPY
        self.project_choice = project_choice
        self.client_folder = client_folder
        self.untracked = int(untracked or 0)
        self._filling = False

        self.setWindowTitle(f"Check before {'simulating' if dry_run else 'bringing files into'} {project_code}")
        self.resize(980, 640)
        try:
            from slate.gui.components.screen_fit import fit_to_screen
            fit_to_screen(self, 980, 640)
        except Exception:
            pass

        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_2)

        self.headline = QLabel()
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet(f"font-size: {Gate.SIZE_LG}px; font-weight: 600; color: {Gate.TEXT};")
        layout.addWidget(self.headline)

        facts = form_layout()
        self.from_label = QLabel(_wrap(survey.source))
        self.to_label = QLabel(str(self.project_path))
        for label in (self.from_label, self.to_label):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
        facts.addRow("From", self.from_label)
        facts.addRow("Into", self.to_label)
        if template_name:
            facts.addRow("Template", QLabel(template_name))

        # Copy or move, said in words, decided here.
        op_row = QHBoxLayout()
        self.copy_radio = QRadioButton("Copy - leave the client drive as it is")
        self.move_radio = QRadioButton("Move - empty the client drive")
        self.copy_radio.setToolTip("Every file is copied and checked; nothing on the client drive changes.")
        self.move_radio.setToolTip("Every file is copied and checked, then removed from the client drive "
                                   "(on the same disk it is simply moved).")
        group = QButtonGroup(self)
        group.addButton(self.copy_radio)
        group.addButton(self.move_radio)
        (self.move_radio if self.operation == MOVE else self.copy_radio).setChecked(True)
        self.copy_radio.toggled.connect(self._refresh)
        op_row.setSpacing(Gate.SPACE_4)
        op_row.addWidget(self.copy_radio)
        op_row.addWidget(self.move_radio)
        op_row.addStretch()
        op_holder = QWidget()
        op_holder.setLayout(op_row)
        op_row.setContentsMargins(0, 0, 0, 0)
        facts.addRow("Files", op_holder)

        # A folder that looks like the project but is spelled differently.
        self.use_existing_radio = None
        if project_choice:
            choice_row = QVBoxLayout()
            choice_row.setContentsMargins(0, 0, 0, 0)
            self.use_existing_radio = QRadioButton(
                f"Use the existing folder '{project_choice['existing'].name}' as the project")
            self.create_new_radio = QRadioButton(
                f"Create '{project_code}' in {project_choice['created'].parent}")
            choice_group = QButtonGroup(self)
            choice_group.addButton(self.use_existing_radio)
            choice_group.addButton(self.create_new_radio)
            self.use_existing_radio.setChecked(True)
            self.use_existing_radio.toggled.connect(self._refresh)
            choice_row.addWidget(self.use_existing_radio)
            choice_row.addWidget(self.create_new_radio)
            holder = QWidget()
            holder.setLayout(choice_row)
            facts.addRow("Project folder", holder)
        layout.addLayout(facts)

        # The shots.
        table_tools = QHBoxLayout()
        self.attention_cb = QCheckBox("Only rows that need a look")
        self.attention_cb.setToolTip("Hide the rows with nothing to check: no note, no problem.")
        self.attention_cb.toggled.connect(self._apply_filter)
        table_tools.addWidget(self.attention_cb)
        table_tools.addStretch()
        layout.addLayout(table_tools)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Reel", "Shot", "Delivered as", "Files", "Size", "Notes"])
        style_table(self.table, {"Reel": "contents", "Shot": ("interactive", 180),
                                 "Delivered as": ("interactive", 200), "Files": "numeric",
                                 "Size": "numeric", "Notes": "stretch"},
                    multi_select=False, editable=True, sortable=True)
        self.table.setToolTip("Double-click a shot name to change it before anything is copied. "
                              "Click a column title to sort.")
        self.table.itemChanged.connect(self._name_edited)
        layout.addWidget(self.table, 1)

        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setTextFormat(Qt.TextFormat.RichText)
        self.notes_label.setStyleSheet(f"color: {Gate.TEXT_2};")
        layout.addWidget(self.notes_label)

        self.again_cb = QCheckBox("Bring the unchanged shots in again anyway (as a new scan version)")
        self.again_cb.setVisible(any(s.unchanged_from for s in survey.shots))
        self.again_cb.toggled.connect(self._again_toggled)
        layout.addWidget(self.again_cb)

        loose = len(survey.loose_media)
        self.loose_cb = QCheckBox(f"Bring the {loose} loose file(s) at the top of the drive in as the shot "
                                  f"'{survey.source.name}' (instead of filing them with the documents)")
        self.loose_cb.setVisible(bool(loose))
        self.loose_cb.setChecked(any(s.is_root for s in survey.shots))
        self.loose_cb.toggled.connect(self._loose_toggled)
        layout.addWidget(self.loose_cb)

        self.untracked_cb = QCheckBox(f"Add the {self.untracked} shot(s) already in the project to the "
                                      f"Dashboard (they are not on it yet)")
        self.untracked_cb.setVisible(bool(self.untracked))
        self.untracked_cb.setChecked(bool(self.untracked))
        self.untracked_cb.toggled.connect(self._refresh)
        layout.addWidget(self.untracked_cb)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {Gate.BAD};")
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = make_button("Cancel", "secondary", on_click=self.reject)
        self.dry_btn = make_button("Dry run", "secondary", on_click=self._accept_dry,
                                   tooltip="Go through everything without copying or creating anything.")
        self.dry_btn.setVisible(not self.dry_run)
        # Opened as a dry run: the real run is one click away too.
        self.real_btn = make_button("Copy for real", "secondary", on_click=self._accept_for_real,
                                    tooltip="Skip the simulation and bring the files in now.")
        self.real_btn.setVisible(self.dry_run)
        self.go_btn = make_button("Copy", "primary", on_click=self._accept_real)
        for b in (self.cancel_btn, self.dry_btn, self.real_btn, self.go_btn):
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self._long_paths = long_paths
        self._shot_folders_defaulted = shot_folders_defaulted
        self._project_exists = project_exists
        self._fill()
        self._refresh()

    # ------------------------------------------------------------ table
    def _shot_of(self, row):
        item = self.table.item(row, self.COL_SHOT)
        index = item.data(_SHOT_ROLE) if item is not None else None
        return self.survey.shots[index] if isinstance(index, int) and 0 <= index < len(self.survey.shots) else None

    def _fill(self):
        self._filling = True
        self.table.setSortingEnabled(False)
        try:
            shots = self.survey.shots
            material = self.survey.documents
            self.table.setRowCount(len(shots) + (1 if material else 0))
            for row, shot in enumerate(shots):
                reel = QTableWidgetItem(shot.reel)
                reel.setFlags(reel.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if shot.reel != shot.source_reel:
                    reel.setToolTip(f"On the drive: {shot.source_reel}")
                stitched = self.survey.stitched_name(shot, self.stitch_mapping)
                name = QTableWidgetItem(stitched or shot.name)
                name.setData(_SHOT_ROLE, row)
                if stitched:
                    name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    name.setToolTip("Part of a stitch - the name was set in the stitch dialog.")
                else:
                    name.setToolTip("Double-click to change the shot name.")
                source = QTableWidgetItem(shot.source_name if not shot.is_root else "(top of the drive)")
                source.setToolTip(str(shot.source))
                source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
                files = numeric_item(shot.file_count, f"{shot.file_count:,}")
                size = numeric_item(shot.size, size_text(shot.size))
                for item in (files, size):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                note = QTableWidgetItem("; ".join(self._row_notes(shot, stitched)))
                note.setToolTip(note.text())
                note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if shot.skip:
                    for item in (reel, name, source, files, size, note):
                        dim_cell(item)
                for col, item in enumerate((reel, name, source, files, size, note)):
                    self.table.setItem(row, col, item)
            if material:
                # The client material is part of the headline's file count, so
                # it is a row too: the rows add up to what is copied.
                row = len(shots)
                count, size = len(material), sum(d.size for d in material)
                cells = ("-", "(client material)", "documents, LUTs, references, loose files",
                         numeric_item(count, f"{count:,}"), numeric_item(size, size_text(size)),
                         f"filed under {self.client_folder}/<date>_docs, not as a shot")
                for col, value in enumerate(cells):
                    item = value if isinstance(value, QTableWidgetItem) else QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip("\n".join(str(d.path) for d in material[:20])
                                    + (f"\n… and {count - 20} more" if count > 20 else ""))
                    dim_cell(item)
                    self.table.setItem(row, col, item)
        finally:
            self.table.setSortingEnabled(True)
            self._filling = False

    @staticmethod
    def _row_notes(shot, stitched) -> List[str]:
        notes = list(shot.notes)
        if stitched:
            notes.append(f"stitch part of {stitched}")
        if shot.rescan:
            notes.append(f"re-delivery ({shot.rescan})")
        if shot.client_version:
            notes.append(f"client version {shot.client_version}")
        if shot.unchanged_from:
            notes.append(f"already in the project as {shot.unchanged_from} - not copied again")
        return notes

    def _name_edited(self, item):
        if self._filling or item.column() != self.COL_SHOT:
            return
        shot = self._shot_of(item.row())
        if shot is not None:
            shot.name = item.text().strip()
        self._refresh()

    def _again_toggled(self, checked):
        for shot in self.survey.shots:
            if shot.unchanged_from:
                shot.skip = not checked
        self._fill()
        self._refresh()

    def _loose_toggled(self, checked):
        self.survey.loose_media_as_shot(checked)
        self._fill()
        self._refresh()

    def _apply_filter(self, *_):
        only = self.attention_cb.isChecked()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SHOT)
            notes = self.table.item(row, self.COL_NOTES)
            marked = item is not None and item.data(Qt.ItemDataRole.UserRole + 99) in ("bad", "warn")
            look = marked or bool(notes is not None and notes.text())
            self.table.setRowHidden(row, only and not look)

    # ------------------------------------------------------------ text
    def chosen_operation(self) -> str:
        return MOVE if self.move_radio.isChecked() else COPY

    def chosen_project_dir(self) -> Path:
        if self.project_choice and self.use_existing_radio is not None and not self.use_existing_radio.isChecked():
            return Path(self.project_choice["created"])
        if self.project_choice:
            return Path(self.project_choice["existing"])
        return self.project_path

    def add_untracked(self) -> bool:
        """Whether the shots already in the project go on the Dashboard with this run."""
        return bool(self.untracked) and self.untracked_cb.isChecked()

    def problems(self) -> List[str]:
        return self.survey.problems(self.stitch_mapping)

    def _nothing_new(self) -> bool:
        return self.survey.total_files == 0 and not self.survey.structure_only

    def notes(self) -> List[str]:
        survey = self.survey
        out = []
        nothing = self._nothing_new()
        tidied = [s for s in survey.shots if s.proposed != s.base and not s.is_root]
        if tidied:
            out.append(f"{len(tidied)} shot name(s) were tidied (e.g. '{tidied[0].base}' -> "
                       f"'{tidied[0].proposed}'); change them in the table if needed.")
        unchanged = {(s.reel, survey.destination_of(s, self.stitch_mapping))
                     for s in survey.shots if s.unchanged_from}
        if unchanged and not nothing:
            out.append(f"{len(unchanged)} shot(s) are already in the project with exactly these files "
                       f"and are not copied again.")
        merged = {survey.stitched_name(s, self.stitch_mapping) for s in survey.active_shots()} - {None}
        if merged:
            out.append(f"{len(merged)} stitch(es) become one shot each.")
        out += self._twice_notes()
        if survey.documents_filed_before:
            out.append(f"{len(survey.documents_filed_before)} document(s) were filed by an earlier run "
                       f"and are left out.")
        if survey.documents:
            out.append(f"{len(survey.documents)} document(s) and other client material (paperwork-only "
                       f"folders, LUTs, references, loose files at the top of the drive) are filed under "
                       f"{self.client_folder}/&lt;date&gt;_docs, not as shots.")
        root = [s for s in survey.shots if s.is_root]
        if root:
            out.append(f"Loose files at the top of the drive become the shot '{root[0].name}' in "
                       f"{root[0].reel}.")
        ignored = len(survey.empty_folders) + len(survey.junk_files)
        if ignored:
            out.append(f"{ignored} empty folder(s) and system file(s) are ignored and listed in the report.")
        if survey.unreadable:
            out.append(f"{len(survey.unreadable)} folder(s) could not be read.")
        if self._long_paths:
            out.append(f"{self._long_paths} file(s) will have a path longer than 260 characters. Slate "
                       f"copies them, but Explorer and some older tools cannot open them.")
        if self._shot_folders_defaulted:
            out.append("The template has no shot folders - every shot gets 01_Scan, 07_Comp and 08_Output.")
        if survey.structure_only:
            out.append("The drive has folders but no files: Slate builds the shot folders only.")
        if self._project_exists and not nothing:
            out.append("The project already exists - each shot gets a new scan version; nothing in the "
                       "project is overwritten.")
        if self.add_untracked():
            out.append(f"{self.untracked} shot(s) already in the project are added to the Dashboard.")
        if self.chosen_operation() == MOVE and not self.dry_run and not nothing:
            out.append("<b>Move</b>: every file is removed from the client drive once its copy is checked.")
        return out

    def _twice_notes(self) -> List[str]:
        """A shot delivered twice (ScanA, ScanB / v02, v03) becomes two scan versions - said before the run."""
        survey = self.survey
        by_shot: Dict = {}
        for shot in survey.active_shots():
            if survey.stitched_name(shot, self.stitch_mapping) or not shot.files:
                continue
            by_shot.setdefault((shot.reel, shot.name), []).append(shot)
        twice = [(name, members) for (_reel, name), members in by_shot.items() if len(members) > 1]
        out = []
        for name, members in twice[:3]:
            tails = ", ".join(m.tail or m.source_name for m in members)
            out.append(f"{name} arrives {len(members)} times ({tails}) - each becomes its own scan version, "
                       f"in that order.")
        if len(twice) > 3:
            out.append(f"… and {len(twice) - 3} more shot(s) delivered more than once.")
        return out

    def _refresh(self, *_):
        survey = self.survey
        files = survey.total_files
        size = size_text(survey.total_bytes)
        shots = len(survey.destination_shots(self.stitch_mapping))
        reels = len(survey.reels)
        verb = "Simulate" if self.dry_run else ("Move" if self.chosen_operation() == MOVE else "Copy")
        target = self.chosen_project_dir()
        self.to_label.setText(_wrap(target))
        nothing = self._nothing_new()
        if nothing:
            known = len({(s.reel, survey.destination_of(s, self.stitch_mapping))
                         for s in survey.shots if s.unchanged_from})
            self.headline.setText(f"Nothing new on this drive - all {known} shot(s) are already in {target.name}"
                                  if known else f"Nothing on this drive to bring into {target.name}")
        else:
            self.headline.setText(
                f"{verb} {files:,} file(s) ({size}) - {shots} shot(s) in {reels} reel(s) - into {target.name}")
        notes = self.notes()
        self.notes_label.setText("<br>".join(f"&bull; {n}" for n in notes))
        self.notes_label.setVisible(bool(notes))

        problems = self.problems()
        shown = problems[:4]
        if len(problems) > 4:
            shown.append(f"(+{len(problems) - 4} more)")
        self.error_label.setText("\n".join(shown))
        self.error_label.setVisible(bool(problems))
        register_only = nothing and self.add_untracked()
        if nothing and not register_only:
            # Nothing to do: the one button closes.
            self.go_btn.setText("Close")
            self.go_btn.setEnabled(True)
            self.dry_btn.setVisible(False)
            self.real_btn.setVisible(False)
        else:
            if register_only:
                self.go_btn.setText(f"Add {self.untracked} shot(s) to the Dashboard")
            elif files:
                self.go_btn.setText(f"{verb} {files:,} file(s)")
            else:
                self.go_btn.setText("Build the folders" if survey.structure_only else verb)
            ok = not problems
            self.go_btn.setEnabled(ok)
            self.dry_btn.setVisible(not self.dry_run and not register_only)
            self.dry_btn.setEnabled(ok)
            real = "Move" if self.chosen_operation() == MOVE else "Copy"
            self.real_btn.setText(f"{real} {files:,} file(s) for real")
            self.real_btn.setVisible(self.dry_run and bool(files))
            self.real_btn.setEnabled(ok)
        self._mark_rows()
        self._apply_filter()

    def _mark_rows(self):
        """Paint every shot name that cannot be used - on its own, or because it clashes with another row."""
        clashing = {id(s) for members in self.survey.name_clashes(self.stitch_mapping).values() for s in members}
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)          # colouring a cell must not move rows under the loop
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SHOT)
            shot = self._shot_of(row)
            if item is None or shot is None:
                continue
            bad = shot_name_problem(item.text().strip(), "A shot name")
            if not bad and id(shot) in clashing:
                bad = "Another folder becomes this shot too - rename one of them."
            self._filling = True
            try:
                if bad:
                    set_cell_status(item, "bad")
                    item.setToolTip(bad)
                elif shot.skip:
                    clear_cell_status(item)
                    dim_cell(item)
                elif shot.attention:
                    set_cell_status(item, "warn")
                    item.setToolTip("Slate spelled this name out in Latin letters - check it.")
                elif shot.proposed != shot.base:
                    set_cell_status(item, "accent", background=False)
                    item.setToolTip("Name tidied by Slate - double-click to change it.")
                else:
                    clear_cell_status(item)
                    item.setToolTip("Double-click to change the shot name.")
            finally:
                self._filling = False
        self.table.setSortingEnabled(sorting)

    # ------------------------------------------------------------ answers
    def _accept_dry(self):
        self.dry_run = True
        self.accept()

    def _accept_for_real(self):
        self.dry_run = False
        self.accept()

    def _accept_real(self):
        if self._nothing_new() and not self.add_untracked():
            self.reject()                 # 'Close': nothing to do
            return
        self.accept()

    def accept(self):
        if self.problems():
            self._refresh()
            return
        self.operation = self.chosen_operation()
        super().accept()
