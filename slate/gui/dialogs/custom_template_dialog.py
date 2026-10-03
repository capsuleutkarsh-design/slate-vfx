"""
Creating and editing a pipeline template.

The old dialog sorted folders into categories by guessing from their text -
'SHOT_A' under 'Layout' became an outsource folder because 'layout' contains
'out', 'EXR' under '01_Scan' was dropped, and nested folders lost their parent.
Here the categories are explicit sections, every folder keeps its full path
('02_Dmp/Work/PSD'), and a saved template opens again exactly as it was saved.

* Name (required, checked as you type - a built-in's name is refused) and a
  description that is saved.
* Folders are added and renamed in place (Enter / F2 / double-click) and can
  be dragged within the tree.
* A template with no per-shot folders says what Slate will add instead; an
  empty 'Inside each scan version' means no folders there (it is saved empty).
* Where client deliveries go (documents, LUTs, the ingest reports) is a field
  of the template, the one rule the ingest uses (ingest_survey.client_folder_for).
* Templates are the studio's: saved for every workstation.
"""

from typing import Dict, Iterable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ...core.domain.ingest_survey import CLIENT_FOLDER
from ...core.domain.naming import folder_path_problem, name_problem
from slate.core.infra.gate import Gate
from slate.gui.core.controls import form_layout, make_button

# (template key, what the person sees). The order is the order on screen.
SECTIONS = (
    ("base_folders", "Project folders"),
    ("production_subfolders", "Production sub-folders (inside 04_Production)"),
    ("outsource_subfolders", "Production > outsource sub-folders (also inside 04_Production)"),
    ("shot_folders", "Per shot"),
    ("scan_version_folders", "Inside each scan version"),
)

from ...core.workers.structure import DEFAULT_SHOT_FOLDERS, DEFAULT_VERSION_FOLDERS

_SECTION_ROLE = Qt.ItemDataRole.UserRole + 1


def template_key(name: str) -> str:
    """The key a template is saved under: 'Client X (v2)' -> 'client_x_(v2)'."""
    return str(name or "").strip().lower().replace(" ", "_")


class CustomTemplateDialog(QDialog):
    """New or edit: pass `template` (and its key) to edit an existing one."""

    def __init__(self, parent=None, template: Optional[Dict] = None, *, original_key: str = "",
                 builtin_keys: Iterable[str] = (), user_keys: Iterable[str] = (), title: str = ""):
        super().__init__(parent)
        self.original_key = original_key
        self.builtin_keys = {k.lower() for k in builtin_keys}
        self.user_keys = {k.lower() for k in user_keys}
        self.setWindowTitle(title or ("Edit template" if template else "New template"))
        self.setModal(True)
        self.resize(640, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(Gate.SPACE_2)

        form = form_layout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Required, e.g. Client X episodic")
        self.name_input.textChanged.connect(self._validate)
        form.addRow("Name", self.name_input)
        self.description_input = QLineEdit()
        self.description_input.setPlaceholderText("What this template is for (optional)")
        form.addRow("Description", self.description_input)
        self.client_input = QLineEdit()
        self.client_input.setPlaceholderText(CLIENT_FOLDER)
        self.client_input.setToolTip("The project folder client deliveries go in: documents, LUTs, references "
                                     "and the ingest reports. Empty means " + CLIENT_FOLDER + ".")
        self.client_input.textChanged.connect(self._validate)
        form.addRow("Client deliveries go in", self.client_input)
        layout.addLayout(form)
        shared = QLabel("Templates are shared with the studio: every workstation builds projects the same way.")
        shared.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        shared.setWordWrap(True)
        layout.addWidget(shared)

        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setColumnCount(1)
        self.tree_widget.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree_widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree_widget.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                         | QAbstractItemView.EditTrigger.EditKeyPressed
                                         | QAbstractItemView.EditTrigger.SelectedClicked)
        self.tree_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree_widget.invisibleRootItem().setFlags(Qt.ItemFlag.ItemIsEnabled)
        layout.addWidget(self.tree_widget, 1)

        self.sections: Dict[str, QTreeWidgetItem] = {}
        for key, label in SECTIONS:
            item = QTreeWidgetItem(self.tree_widget, [label])
            item.setData(0, _SECTION_ROLE, key)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setExpanded(True)
            self.sections[key] = item
        self.tree_widget.itemChanged.connect(lambda *_: self._validate())
        self.tree_widget.currentItemChanged.connect(lambda *_: self._sync_buttons())

        self.shot_note = QLabel(
            "No folders under 'Per shot': every shot gets " + ", ".join(DEFAULT_SHOT_FOLDERS) + ".")
        self.shot_note.setStyleSheet(f"color: {Gate.WARN};")
        self.shot_note.setWordWrap(True)
        layout.addWidget(self.shot_note)

        btn_layout = QHBoxLayout()
        self.add_base_btn = make_button("Add folder", "secondary", icon="plus", on_click=self.add_base_folder,
                                        tooltip="Add a folder to the selected section (or beside the selected folder).")
        self.add_sub_btn = make_button("Add sub-folder", "secondary", on_click=self.add_sub_folder,
                                       tooltip="Add a folder inside the selected folder.")
        self.rename_btn = make_button("Rename", "ghost", on_click=self.rename_selected,
                                      tooltip="Rename the selected folder (F2 or double-click works too).")
        self.remove_btn = make_button("Remove", "ghost", on_click=self.remove_selected,
                                      tooltip="Remove the selected folder and everything in it.")
        for b in (self.add_base_btn, self.add_sub_btn, self.rename_btn, self.remove_btn):
            btn_layout.addWidget(b)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {Gate.BAD};")
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                           | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText("Save template")
        self.ok_btn.setProperty("kind", "primary")
        self.ok_btn.setDefault(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        if template:
            self.load_template(template)
        else:
            # The folder every scan version has had; a person can remove it.
            for folder in DEFAULT_VERSION_FOLDERS:
                self._add_path(self.sections["scan_version_folders"], folder)
        self._sync_buttons()
        self._validate()

    # ------------------------------------------------------------ the tree
    def load_template(self, template: Dict):
        source = template.get("structure") if isinstance(template.get("structure"), dict) else template
        self.name_input.setText(str(template.get("name", "")))
        self.description_input.setText(str(template.get("description", "")))
        self.client_input.setText(str(source.get("client_folder") or template.get("client_folder") or ""))
        for key, _label in SECTIONS:
            paths = source.get(key, [])
            if key == "scan_version_folders" and paths is None:
                paths = DEFAULT_VERSION_FOLDERS      # a template from before the list existed
            for path in paths or []:
                self._add_path(self.sections[key], str(path))
        self._validate()

    def _add_path(self, section: QTreeWidgetItem, path: str):
        parent = section
        for part in [p for p in path.replace("\\", "/").split("/") if p]:
            found = None
            for i in range(parent.childCount()):
                if parent.child(i).text(0) == part:
                    found = parent.child(i)
                    break
            if found is None:
                found = self._folder_item(parent, part)
            parent = found
        parent.setExpanded(True)

    def _folder_item(self, parent, name: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [name])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                      | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        parent.setExpanded(True)
        return item

    def _selected(self) -> Optional[QTreeWidgetItem]:
        return self.tree_widget.currentItem()

    @staticmethod
    def _is_section(item) -> bool:
        return item is not None and item.data(0, _SECTION_ROLE) is not None

    def _new_name(self, parent) -> str:
        names = {parent.child(i).text(0) for i in range(parent.childCount())}
        name, n = "New folder", 2
        while name in names:
            name = f"New folder {n}"
            n += 1
        return name

    def add_base_folder(self):
        """Add a folder to the selected section, or beside the selected folder."""
        current = self._selected()
        if current is None:
            parent = self.sections["base_folders"]
        elif self._is_section(current):
            parent = current
        else:
            parent = current.parent() or self.sections["base_folders"]
        self._start_new(parent)

    def add_sub_folder(self):
        current = self._selected()
        parent = current if current is not None else self.sections["base_folders"]
        self._start_new(parent)

    def _start_new(self, parent):
        item = self._folder_item(parent, self._new_name(parent))
        self.tree_widget.setCurrentItem(item)
        self.tree_widget.editItem(item, 0)
        self._validate()
        return item

    def rename_selected(self):
        current = self._selected()
        if current is not None and not self._is_section(current):
            self.tree_widget.editItem(current, 0)

    def _sync_buttons(self):
        """Rename and Remove act on a folder: off until one is selected."""
        folder = self._selected() is not None and not self._is_section(self._selected())
        self.rename_btn.setEnabled(folder)
        self.remove_btn.setEnabled(folder)

    def remove_selected(self):
        current = self._selected()
        if current is None or self._is_section(current):
            return
        parent = current.parent()
        if parent is not None:
            parent.removeChild(current)
        self._validate()

    # ------------------------------------------------------------ result
    def _paths(self, item: QTreeWidgetItem, prefix: str = "") -> List[str]:
        out = []
        for i in range(item.childCount()):
            child = item.child(i)
            path = f"{prefix}/{child.text(0).strip()}" if prefix else child.text(0).strip()
            if child.childCount():
                out.extend(self._paths(child, path))
            else:
                out.append(path)
        return out

    def template_key(self) -> str:
        return template_key(self.name_input.text())

    def problems(self) -> List[str]:
        out = []
        name = self.name_input.text().strip()
        if not name:
            out.append("Enter a name for the template.")
        else:
            problem = name_problem(name, "The template name")
            if problem:
                out.append(problem)
            elif self.template_key() in self.builtin_keys:
                out.append(f"'{name}' is the name of a built-in template. Choose another name.")
        for key, label in SECTIONS:
            for path in self._paths(self.sections[key]):
                problem = folder_path_problem(path, f"A folder in '{label}'")
                if problem:
                    out.append(problem)
                    break
        client = self.client_input.text().strip()
        if client:
            problem = folder_path_problem(client, "The client deliveries folder")
            if problem:
                out.append(problem)
        return out

    def _validate(self, *_):
        problems = self.problems()
        self.error_label.setText("\n".join(problems[:3]))
        # An empty name is a hint, not an error: why Save is off, said in plain view.
        named = bool(self.name_input.text().strip())
        self.error_label.setStyleSheet(f"color: {Gate.BAD if named else Gate.TEXT_DIM};")
        self.error_label.setVisible(bool(problems))
        self.ok_btn.setEnabled(not problems)
        self.ok_btn.setToolTip(problems[0] if problems else "")
        self.shot_note.setVisible(not self._paths(self.sections["shot_folders"]))

    def overwrites_another(self) -> bool:
        """Saving would replace a different user template of the same name."""
        key = self.template_key()
        return key in self.user_keys and key != (self.original_key or "").lower()

    def accept(self):
        if self.problems():
            self._validate()
            return
        super().accept()

    def get_template_data(self) -> Dict:
        """The template, with every folder as its full path."""
        data = {
            "name": self.name_input.text().strip(),
            "description": self.description_input.text().strip(),
        }
        if self.client_input.text().strip():
            data["client_folder"] = self.client_input.text().strip()
        for key, _label in SECTIONS:
            # Saved as they are - an empty 'Inside each scan version' means none.
            data[key] = self._paths(self.sections[key])
        return data
