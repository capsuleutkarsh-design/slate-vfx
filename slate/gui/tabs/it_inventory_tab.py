"""
Hardware: the machines the studio owns, who has them, and their state.

Who has a machine is the loan ledger (Issue to... / Collect), the record the
leaving checklist reads - never a name typed into a field. A machine's status
follows from that: "In service" is Active when somebody holds it and Available
when nobody does, so the two can never disagree. A machine at the end of its
life is Retired, Lost or Disposed (hidden unless asked for) rather than
deleted, so who had it and when stays on record.

The rules live in slate/core/infra/hardware_repository.py; this file is the
screen.
"""

import logging

from PySide6.QtCore import QDate, QRegularExpression, Qt
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDateEdit, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

from ...core.infra.app_context import AppContext
from ..core.empty_state import EmptyState
from ..core.controls import enable_with_selection, make_button, page_title, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import dim_cell, set_cell_status, style_table
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.data_display import date_item, export_table_dialog, from_qdate, setup_date_edit
from slate.core.domain import hardware as hw
from slate.core.domain import people
from slate.core.domain.dates import format_date, parse_date
from slate.core.infra.hardware_repository import HardwareError, HardwareRepository
from slate.gui.components import feedback
from slate.gui.components.table_tools import (
    KeepSelection, TableToolbar, make_item, selected_keys, setup_table,
)
from slate.gui.components.state_notice import clear_state, show_load_error

logger = logging.getLogger(__name__)

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

# Serial number and asset tag are hidden columns: the search finds them (the
# sticker on a machine is what IT type), the screen leaves them out.
COLUMNS = ["Machine", "Assigned to", "Type", "Location", "CPU", "GPU", "RAM", "Storage",
           "Warranty", "Status", "Serial number", "Asset tag"]
COL = {name: i for i, name in enumerate(COLUMNS)}


class _OptionalDate(QWidget):
    """A date that may be 'not recorded'."""

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Gate.SPACE_2)
        self.edit = setup_date_edit(QDateEdit())
        self.unknown = QCheckBox("Not recorded")
        day = parse_date(value)
        self.edit.setDate(QDate(day.year, day.month, day.day) if day else QDate.currentDate())
        self.unknown.toggled.connect(lambda on: self.edit.setEnabled(not on))
        self.unknown.setChecked(day is None)
        row.addWidget(self.edit, 1)
        row.addWidget(self.unknown)

    def value(self):
        return None if self.unknown.isChecked() else from_qdate(self.edit.date())


def _gb_spin(text):
    spin = QSpinBox()
    spin.setRange(0, 1024 * 1024)
    spin.setSuffix(" GB")
    # 0 means nothing recorded - shown as words, stored as nothing.
    spin.setSpecialValueText("Not recorded")
    spin.setValue(hw.gb_value(text))
    return spin


class AddPCDialog(QDialog):
    """Add a machine, or change one (Edit). Who has it is Issue to... / Collect."""

    def __init__(self, parent=None, hub=None, edit_data=None, repo: HardwareRepository = None,
                 locations=(), by: str = ""):
        super().__init__(parent)
        self.hub = hub
        self.repo = repo
        self.by = by
        self.edit_data = edit_data
        self.renamed_to = None
        data = edit_data or {}
        self.setWindowTitle("Edit machine" if edit_data else "Add machine")
        self.setMinimumWidth(460)
        # No stylesheet of its own: it drew every value in amber (the warning
        # colour), square fields beside a pill combo, and a large gradient
        # "Save PC" next to a small Cancel.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        layout.setSpacing(Gate.SPACE_3)
        form = tidy_form(QFormLayout())

        # The name a machine has on the network: letters, digits, '-', '_', '.'.
        self.inp_name = QLineEdit()
        self.inp_name.setMaxLength(hw.NAME_MAX)
        self._hostname = QRegularExpressionValidator(
            QRegularExpression(r"[A-Za-z0-9][A-Za-z0-9._\-]*"), self.inp_name)
        self.inp_name.setValidator(self._hostname)
        self.inp_name.setPlaceholderText("e.g. WS-COMP-07")
        self.inp_type = QComboBox()
        for kind in hw.TYPES:
            self.inp_type.addItem(kind)
        self.inp_serial = QLineEdit(str(data.get("serial_number") or ""))
        self.inp_serial.setMaxLength(120)
        self.inp_tag = QLineEdit(str(data.get("asset_tag") or ""))
        self.inp_tag.setMaxLength(60)
        self.inp_cpu = QLineEdit(str(data.get("cpu") or ""))
        self.inp_gpu = QLineEdit(str(data.get("gpu") or ""))
        self.inp_ram = _gb_spin(data.get("ram"))
        self.inp_storage = _gb_spin(data.get("storage"))
        self.inp_location = QLineEdit(str(data.get("location") or ""))
        # The locations already in use, so 'Comp Floor 2' is not typed three ways.
        known = sorted({str(l).strip() for l in locations if str(l or "").strip()}, key=str.lower)
        if known:
            places = QCompleter(known, self.inp_location)
            places.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            places.setFilterMode(Qt.MatchFlag.MatchContains)
            self.inp_location.setCompleter(places)
        self.inp_cpu.setPlaceholderText("e.g. Threadripper 7960X")
        self.inp_gpu.setPlaceholderText("e.g. RTX 4090")
        self.inp_location.setPlaceholderText("e.g. Comp bay 2")
        self.inp_purchased = _OptionalDate(data.get("purchased_on"))
        self.inp_warranty = _OptionalDate(data.get("warranty_until"))

        # A new machine is Available (nobody has it) or in Repair - never
        # Active, which only Issue can make it. Edit says "In service" and the
        # ledger decides between Active and Available.
        self.inp_status = QComboBox()
        if edit_data:
            for state in hw.EDIT_STATUSES:
                self.inp_status.addItem(state)
            current = hw.normalise_status(data.get("status"))
            self.inp_status.setCurrentText(
                hw.IN_SERVICE if current in (hw.ACTIVE, hw.AVAILABLE, "") else current)
        else:
            for state in hw.ADD_STATUSES:
                self.inp_status.addItem(state)
            self.inp_status.setCurrentText(hw.AVAILABLE)

        if edit_data:
            # The name is not a field to type into here: Rename moves the
            # machine's history with it.
            name_row = QHBoxLayout()
            self.name_label = QLabel(str(data.get("machine_name") or ""))
            self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.name_label.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
            name_row.addWidget(self.name_label, 1)
            self.btn_rename = make_button("Rename…", "ghost", on_click=self._rename,
                                          tooltip="Rename the machine and its loan history")
            name_row.addWidget(self.btn_rename)
            self.inp_name.setText(str(data.get("machine_name") or ""))
            form.addRow("Machine", name_row)
            kind = str(data.get("type") or "").strip()
            if kind and self.inp_type.findText(kind) < 0:
                self.inp_type.addItem(kind)
            self.inp_type.setCurrentText(kind or "Workstation")
        else:
            # Auto-fill sits beside the name it reads (it was a full-width bar
            # under the footnote that looked like a second Save).
            name_row = QHBoxLayout()
            name_row.setSpacing(Gate.SPACE_2)
            name_row.addWidget(self.inp_name, 1)
            name_row.addWidget(make_button(
                "Auto-fill", "ghost", on_click=self.auto_fill,
                tooltip="Read CPU, GPU, RAM and storage from this machine's Live Ops report"))
            form.addRow("Machine name", name_row)
        form.addRow("Type", self.inp_type)
        form.addRow("Serial number", self.inp_serial)
        form.addRow("Asset tag", self.inp_tag)
        form.addRow("CPU", self.inp_cpu)
        form.addRow("GPU", self.inp_gpu)
        form.addRow("RAM", self.inp_ram)
        form.addRow("Storage", self.inp_storage)
        form.addRow("Location", self.inp_location)
        form.addRow("Purchased on", self.inp_purchased)
        form.addRow("Warranty until", self.inp_warranty)
        form.addRow("Status", self.inp_status)
        layout.addLayout(form)
        self.form = form

        self.name_hint = QLabel("")
        self.name_hint.setWordWrap(True)
        self.name_hint.setStyleSheet(f"color: {Gate.WARN}; font-size: {Gate.SIZE_SM}px;")
        self.name_hint.hide()          # only Add says something here
        layout.addWidget(self.name_hint)

        owner_note = QLabel(
            "To hand this machine to someone, close this and use Issue to… in the toolbar.")
        owner_note.setWordWrap(True)
        owner_note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        layout.addWidget(owner_note)

        # Enter saves (Auto-fill is never the default).
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", "ghost", on_click=self.reject)
        self.ok_btn = make_button("Save", "primary", on_click=self._save)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)

        self.inp_name.textChanged.connect(self._validate)
        self.inp_type.currentTextChanged.connect(self._type_changed)
        self._type_changed()

    # ----------------------------------------------------------- checks
    def _type_changed(self, *_):
        """A monitor or tablet: any name, and no CPU / GPU / RAM / storage to fill in."""
        peripheral = hw.is_peripheral(self.inp_type.currentText())
        self.inp_name.setValidator(None if peripheral else self._hostname)
        self.inp_name.setPlaceholderText("e.g. Dell U2723QE #3" if peripheral else "e.g. WS-COMP-07")
        for field in (self.inp_cpu, self.inp_gpu, self.inp_ram, self.inp_storage):
            self.form.setRowVisible(field, not peripheral)
        self._validate()

    def _validate(self, *_):
        """Save stays off until there is a usable name - nothing typed is thrown away."""
        if self.edit_data:
            self.ok_btn.setEnabled(True)
            return
        problem = (hw.name_problem(self.inp_name.text(), self.inp_type.currentText())
                   if self.inp_name.text() else "Give the machine a name.")
        self.ok_btn.setEnabled(not problem)
        self.ok_btn.setToolTip(problem)
        self.name_hint.setText(problem if self.inp_name.text() else "")
        self.name_hint.setVisible(bool(self.inp_name.text()) and bool(problem))

    def _save(self):
        if self.ok_btn.isEnabled():
            self.accept()

    def values(self) -> dict:
        ram, storage = self.inp_ram.value(), self.inp_storage.value()
        if hw.is_peripheral(self.inp_type.currentText()):
            ram = storage = 0
        return {
            "type": self.inp_type.currentText(),
            "serial_number": self.inp_serial.text(),
            "asset_tag": self.inp_tag.text(),
            "cpu": self.inp_cpu.text(),
            "gpu": self.inp_gpu.text(),
            "ram": hw.gb_text(ram) if ram else "",
            "storage": hw.gb_text(storage) if storage else "",
            "location": self.inp_location.text(),
            "purchased_on": self.inp_purchased.value(),
            "warranty_until": self.inp_warranty.value(),
        }

    def _rename(self):
        if self.repo is None or not self.edit_data:
            return
        from PySide6.QtWidgets import QInputDialog
        old = self.name_label.text()
        ask = QInputDialog(self)
        ask.setWindowTitle("Rename machine")
        ask.setLabelText("New name for %s:" % old)
        ask.setTextValue(old)
        if ask.exec() != QDialog.DialogCode.Accepted:
            return
        new = (ask.textValue() or "").strip()
        if not new or new == old:
            return
        try:
            self.repo.rename(old, new, by=self.by)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            feedback.warn(self, "Rename machine", str(exc))
            return
        self.renamed_to = new
        self.name_label.setText(new)
        self.inp_name.setText(new)
        feedback.toast(self, "Renamed %s to %s. Its loan history moved with it." % (old, new), "success")

    def auto_fill(self):
        machine_name = self.inp_name.text().strip()
        if not machine_name:
            feedback.warn(self, "Auto-fill from Live Ops", "Type the machine name first.")
            return
        if not self.hub:
            return
        report_path = self.hub.get_livestatus_dir() / f"{machine_name}.json"
        if not report_path.exists():
            feedback.warn(self, "Auto-fill from Live Ops",
                          "Live Ops has no report for this machine. It may be switched off or not running Slate.")
            return
        try:
            spec = HardwareRepository.read_report(report_path)
        except Exception as e:
            feedback.warn(self, "Auto-fill from Live Ops", f"The Live Ops report could not be read: {e}")
            return
        # Deliberately not the logged-in user. Who was sitting at a machine
        # when it was scanned is not who it was issued to. Empty values in the
        # report leave what is already typed alone.
        if spec["cpu"]:
            self.inp_cpu.setText(spec["cpu"])
        if spec["gpu"]:
            self.inp_gpu.setText(spec["gpu"])
        if spec["ram"]:
            self.inp_ram.setValue(hw.gb_value(spec["ram"]))
        if spec["storage"]:
            self.inp_storage.setValue(hw.gb_value(spec["storage"]))
        feedback.toast(self, "Filled in from the last Live Ops report.", "success")


class IssueDialog(QDialog):
    """Who gets the machine (a searchable list of people by name), from when, and a note."""

    def __init__(self, machine: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Issue %s" % machine)
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        form = tidy_form(QFormLayout())
        from slate.gui.components.person_picker import PersonPicker
        # Active people by name, alphabetical, no leavers, no service accounts.
        self.person = PersonPicker(placeholder="Type a name…", allow_empty=False)
        form.addRow("Issue to", self.person)
        # Handed over last Friday is recorded as last Friday, with what went with it.
        self.issued_on = setup_date_edit(QDateEdit())
        self.issued_on.setDate(QDate.currentDate())
        self.issued_on.setMaximumDate(QDate.currentDate())
        form.addRow("Issued on", self.issued_on)
        self.note = QLineEdit()
        self.note.setMaxLength(200)
        self.note.setPlaceholderText("Optional - e.g. with charger and Wacom")
        form.addRow("Note", self.note)
        root.addLayout(form)
        note = QLabel("The loan is recorded, so the leaving checklist will ask for it back.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        root.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", "ghost", on_click=self.reject))
        self.ok_btn = make_button("Issue", "primary", on_click=self.accept)
        row.addWidget(self.ok_btn)
        root.addLayout(row)
        self.person.person_changed.connect(lambda u: self.ok_btn.setEnabled(bool(u)))
        self.ok_btn.setEnabled(False)

    def username(self) -> str:
        return self.person.username()

    def values(self) -> dict:
        return {"issued_on": from_qdate(self.issued_on.date()), "note": self.note.text().strip()}


class HistoryDialog(QDialog):
    """
    Everything that happened to this machine, newest first: who had it and
    when (with the note given when it was issued), and its status changes and
    renames, with who made them.
    """

    def __init__(self, machine: str, loans: list, parent=None, events=()):
        super().__init__(parent)
        self.setWindowTitle("History - %s" % machine)
        self.setMinimumSize(620, 360)
        root = QVBoxLayout(self)
        root.setContentsMargins(Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4, Gate.SPACE_4)
        root.setSpacing(Gate.SPACE_3)
        heading = QLabel(machine)
        heading.setStyleSheet(f"color: {Gate.TEXT}; font-size: {Gate.SIZE_LG}px; font-weight: 600;")
        root.addWidget(heading)
        lines = []
        for loan in loans:
            what = "Issued to %s" % people.label(loan.get("user_id"))
            if loan.get("note"):
                what += " - %s" % loan.get("note")
            lines.append((loan.get("issued_on"), what, loan.get("issued_by")))
            if loan.get("returned_on"):
                lines.append((loan.get("returned_on"), "Returned by %s"
                              % people.display_name(loan.get("user_id")), None))
        lines += [(e.get("happened_at"), e.get("what") or "", e.get("done_by")) for e in events]
        from slate.core.domain.dates import parse_date
        lines.sort(key=lambda line: str(line[0] or ""), reverse=True)
        self.table = QTableWidget(len(lines), 3)
        self.table.setHorizontalHeaderLabels(["When", "What", "By"])
        style_table(self.table, {"When": "contents", "What": "stretch", "By": "contents"})
        for r, (when, what, by) in enumerate(lines):
            self.table.setItem(r, 0, date_item(parse_date(when)))
            self.table.setItem(r, 1, make_item(what, tooltip=what))
            self.table.setItem(r, 2, make_item(people.display_name(by, empty=hw.MISSING)))
        root.addWidget(self.table, 1)
        held = next((loan for loan in loans if not loan.get("returned_on")), None)
        if held:
            note = QLabel("Out with %s since %s." % (people.display_name(held.get("user_id")),
                                                    format_date(held.get("issued_on"))))
            note.setStyleSheet(f"color: {Gate.INFO}; font-size: {Gate.SIZE_SM}px;")
            root.addWidget(note)
        if not lines:
            EmptyState.over(self.table, "Nothing yet", "This machine has not been issued or changed yet.")
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Close", "ghost", on_click=self.accept))
        root.addLayout(row)


class ItInventoryTab(QWidget):
    def __init__(self, user_data=None, parent=None, read_only: bool = False):
        super().__init__(parent)
        # Changing anything needs manage_it; the IT tab key alone reads.
        self.read_only = bool(read_only)
        self._figure = ""
        self._figures = {}
        # Who is issuing a machine gets recorded on the loan, so the tab needs
        # to know who is using it.
        self.user_data = user_data or {}
        self.app_context = AppContext()
        self.hub = self.app_context.server_hub()
        self.repo = HardwareRepository()
        self.hardware_data = []

        main_layout = QVBoxLayout(self)
        # The same margins as the other IT screens (the title used to jump
        # sideways when switching tabs).
        main_layout.setContentsMargins(Gate.SPACE_5, Gate.SPACE_4, Gate.SPACE_5, Gate.SPACE_4)
        main_layout.setSpacing(Gate.SPACE_3)

        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        main_layout.addWidget(page_title(
            'Hardware', 'Machines the studio owns, who has them, and their state'
            + (' - read only' if self.read_only else '')))

        # The figures, each a filter that shows exactly what it counts
        # (HardwareRepository.figure): together they add up to Machines.
        self.stats = StatStrip(compact=True)
        self.fig_total = self.stats.add("Machines", 0, tone="neutral",
                                        on_click=lambda: self._filter_figure(""),
                                        tooltip="Every machine not retired, lost or disposed")
        self.fig_available = self.stats.add("Available", 0, tone="info",
                                            on_click=lambda: self._filter_figure("available"),
                                            tooltip="Free to issue")
        self.fig_repair = self.stats.add("In repair", 0, on_click=lambda: self._filter_figure("repair"),
                                         tooltip="In for repair, and nobody has it")
        self.fig_loan = self.stats.add("Out on loan", 0, tone="ok",
                                       on_click=lambda: self._filter_figure("on_loan"),
                                       tooltip="Issued to somebody (the loan record)")
        self._figure_cards = {"available": self.fig_available, "repair": self.fig_repair,
                              "on_loan": self.fig_loan}
        main_layout.addWidget(self.stats)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        controls.addStretch()
        self.add_btn = make_button("Add machine", "primary", icon="plus", on_click=self.add_workstation)
        self.edit_btn = make_button("Edit", on_click=self.edit_workstation)
        self.issue_btn = make_button("Issue to…", tooltip="Hand this machine to somebody, and record the loan",
                                     on_click=self.issue_selected)
        self.collect_btn = make_button("Collect", tooltip="Take this machine back and close the loan",
                                       on_click=self.collect_selected)
        self.history_btn = make_button("History", "ghost", tooltip="Who had this machine, and when",
                                       on_click=self.show_history)
        self.delete_btn = make_button("Delete", "danger", on_click=self.delete_workstation,
                                      tooltip="For a machine added by mistake. Retire one that has been used.")
        self.sync_btn = make_button("Sync from Live Ops",
                                    tooltip="Add machines Live Ops has seen and refresh their specs",
                                    on_click=self.sync_from_live_ops)
        for b in (self.add_btn, self.edit_btn, self.issue_btn, self.collect_btn, self.history_btn,
                  self.delete_btn, self.sync_btn):
            controls.addWidget(b)
            b.setVisible(b is self.history_btn or not self.read_only)
        controls.addWidget(make_button("Export…", "ghost", tooltip="Save the machines shown as CSV or Excel",
                                       on_click=lambda: export_table_dialog(self, self.grid, "hardware")))
        main_layout.addLayout(controls)

        # Table
        self.grid = QTableWidget(0, len(COLUMNS))
        self.grid.setHorizontalHeaderLabels(COLUMNS)
        self.style_table(self.grid)
        # Read-only (typing into a cell saved nothing), one machine at a time
        # (every action is per machine), sortable headers; double-click edits.
        setup_table(self.grid, multi_select=False)
        self.grid.doubleClicked.connect(
            lambda _index: self.show_history() if self.read_only else self.edit_workstation())
        for column in ("Serial number", "Asset tag"):
            self.grid.hideColumn(COL[column])

        # Search by name, person, location, CPU, GPU, serial number or asset
        # tag; filter by status.
        self.toolbar = TableToolbar(
            self.grid, placeholder="Search machine, person, location, CPU, GPU or asset tag…",
            columns=(0, 1, 2, 3, 4, 5, 9, COL["Serial number"], COL["Asset tag"]),
            on_refresh=self.load_data, noun="machine")
        self.filter_cb = self.toolbar.add_filter(
            "Status", [("All statuses", "")] + [(s, s) for s in hw.STATUSES], column=COL["Status"])
        self.filter_cb.currentIndexChanged.connect(lambda _i: self._show_figure(""))
        self.toolbar.filter.add_predicate(
            lambda row: not self._figure or self._figures.get(self._key(row)) == self._figure)
        self.show_retired = QCheckBox("Show retired")
        self.show_retired.setToolTip("Include machines that are Retired, Lost or Disposed")
        self.toolbar._filters_row.addWidget(self.show_retired)
        self.toolbar.filter.add_predicate(self._retired_visible)
        self.show_retired.toggled.connect(lambda _on: self.toolbar.filter.apply())
        main_layout.addWidget(self.toolbar)
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30, topics=("hardware_inventory", "asset_assignments"))

        main_layout.addWidget(self.grid)

        self.empty_state = EmptyState(
            'No machines registered yet',
            'Add a machine, or pull the current fleet from Live Ops.',
            glyph='monitor',
        )
        main_layout.addWidget(self.empty_state)
        self.empty_state.attach_to(self.grid)

        # Every action needs a machine; they start switched off rather than
        # arguing with a dialog.
        for button in (self.edit_btn, self.issue_btn, self.collect_btn, self.history_btn, self.delete_btn):
            enable_with_selection(button, self.grid)
        self.grid.itemSelectionChanged.connect(self._sync_buttons)

        # First read only now that the table is in the layout: a notice for a
        # failed read takes the table's place, and with no layout yet it
        # floated as a window of its own while the empty state said
        # there was nothing here.
        self.load_data()

    # ------------------------------------------------------------ reading
    def _retired_visible(self, row) -> bool:
        if self.show_retired.isChecked():
            return True
        if self.filter_cb.currentData() in hw.END_OF_LIFE:
            return True            # asked for by name
        item = self.grid.item(row, COL["Status"])
        return not (item is not None and hw.is_end_of_life(item.text()))

    def _key(self, row):
        item = self.grid.item(row, COL["Machine"])
        return item.text() if item is not None else None

    def _filter_figure(self, figure):
        """A figure was clicked: show exactly the machines it counted."""
        self.filter_cb.blockSignals(True)
        self.filter_cb.setCurrentIndex(0)
        self.filter_cb.blockSignals(False)
        self._show_figure(figure)
        self.toolbar.filter.apply()

    def _show_figure(self, figure):
        self._figure = figure
        for key, card in self._figure_cards.items():
            card.set_active(key == figure)

    @on_database_error
    def load_data(self, *_):
        try:
            self.hardware_data = self.repo.machines()
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # A failed read is not an empty inventory. It used to become []
            # here and the screen said "No machines registered yet".
            logging.exception("Hardware could not be read")
            show_load_error(self, e, retry=self.load_data, what="the machine list")
            return
        clear_state(self)

        counts = self.repo.counts(self.hardware_data)
        self.fig_total.set_value(counts["total"])
        self.fig_available.set_value(counts["available"])
        self.fig_repair.set_value(counts["repair"])
        self.fig_repair.set_tone("warn" if counts["repair"] else "idle")
        self.fig_loan.set_value(counts["on_loan"])
        self._figures = {str(r.get("machine_name") or ""): self.repo.figure(r) for r in self.hardware_data}
        self.show_retired.setText("Show retired (%d)" % counts["retired"] if counts["retired"] else "Show retired")

        # The selection follows the machine, not the row number, across the
        # 30-second refresh and after every action.
        with KeepSelection(self.grid):
            self._fill(self.hardware_data)
        self._sync_buttons()

    def _fill(self, rows):
        self.grid.setRowCount(len(rows))
        for r, row in enumerate(rows):
            name = str(row.get('machine_name', ''))
            # Sorted by position in the repository's natural order: WS-COMP-2
            # before WS-COMP-10 when the header is clicked too.
            item = make_item(name, key=name, tooltip=name, sort_value=r,
                             align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.grid.setItem(r, COL["Machine"], item)

            # The loan record only. A name typed before loans were tracked is
            # a note on the tooltip, not an owner.
            holder = row.get("holder")
            legacy = str(row.get("assigned_to") or "").strip()
            assigned = row.get('display_name') or holder
            tip = people.label(holder) if holder else ""
            if legacy and not holder:
                tip = "Recorded as %s before loans were tracked" % legacy
            assigned_item = make_item(assigned or "Unassigned", tooltip=tip)
            if not assigned:
                dim_cell(assigned_item)        # a placeholder, not a name
            self.grid.setItem(r, COL["Assigned to"], assigned_item)

            right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            for column, key in (("Type", "type"), ("Location", "location"), ("CPU", "cpu"),
                                ("GPU", "gpu"), ("RAM", "ram"), ("Storage", "storage"),
                                ("Serial number", "serial_number"), ("Asset tag", "asset_tag")):
                sized = key in ("ram", "storage")
                text = hw.size_text(row.get(key)) if sized else hw.cell(row.get(key))
                cell = make_item(text, tooltip=text if text != hw.MISSING else "",
                                 sort_value=hw.gb_value(row.get(key)) if sized and text != hw.MISSING else None,
                                 align=right if sized else None)
                if text == hw.MISSING:
                    dim_cell(cell)
                self.grid.setItem(r, COL[column], cell)

            warranty = row.get("warranty_until")
            w_item = date_item(warranty, empty=hw.MISSING)
            state = self.repo.warranty_state(warranty)
            if state == "expired":
                set_cell_status(w_item, "bad", background=False)
                w_item.setToolTip("Warranty expired")
            elif state == "soon":
                set_cell_status(w_item, "warn", background=False)
                w_item.setToolTip("Warranty ends within %d days" % 60)
            elif not warranty:
                dim_cell(w_item)
            self.grid.setItem(r, COL["Warranty"], w_item)

            status = hw.normalise_status(row.get('status')) or hw.MISSING
            status_item = make_item(status)
            tone = hw.TONE.get(status)
            if tone:
                set_cell_status(status_item, tone, background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, COL["Status"], status_item)

    def _selected_row(self):
        """The selected machine's record - found by name, so sorting cannot
        make an action hit the wrong machine."""
        keys = selected_keys(self.grid)
        if not keys:
            return None
        for record in self.hardware_data or []:
            if str(record.get('machine_name', '')) == str(keys[0]):
                return record
        return None

    def _sync_buttons(self, *_):
        row = self._selected_row()
        held = bool(row and row.get("holder"))
        self.issue_btn.setEnabled(bool(row) and not held and hw.can_be_issued(row.get("status")))
        self.collect_btn.setEnabled(held)
        # Delete is only for a machine added by mistake - say so before it is pressed.
        used = bool(row) and (held or int(row.get("loans") or 0) > 0)
        self.delete_btn.setEnabled(bool(row) and not used)
        self.delete_btn.setToolTip(
            "Issued before - use Edit > Retired to keep its history" if used
            else "For a machine added by mistake. Retire one that has been used.")

    def _service(self):
        return self.repo.service

    def _by_whom(self) -> str:
        return str((self.user_data or {}).get("user_id")
                   or (self.user_data or {}).get("username") or "IT")

    # ------------------------------------------------------------ actions
    def add_workstation(self):
        if self.read_only:
            return
        dialog = AddPCDialog(self, self.hub, repo=self.repo, locations=self._locations(),
                             by=self._by_whom())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.inp_name.text().strip()
        try:
            result = self.repo.add(name, dialog.values(), dialog.inp_status.currentText())
        except DatabaseUnavailableError:
            raise
        except HardwareError as exc:
            feedback.warn(self, "Add machine", str(exc))
            return
        self.load_data()
        if not result:
            feedback.warn(self, "Add machine", "%s was not added:\n\n%s"
                          % (name, getattr(result, "error", "") or "the database refused it"))
            return
        from slate.gui.components.table_tools import select_keys
        select_keys(self.grid, [name])
        feedback.toast(self, "%s added." % name, "success")

    def edit_workstation(self):
        edit_data = self._selected_row()
        if edit_data is None:
            feedback.warn(self, "Edit machine", "Select the machine to edit.")
            return

        if self.read_only:
            return
        dialog = AddPCDialog(self, self.hub, edit_data=edit_data, repo=self.repo,
                             locations=self._locations(), by=self._by_whom())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        name = dialog.renamed_to or edit_data.get('machine_name')
        if not accepted:
            if dialog.renamed_to:
                self.load_data()
            return
        try:
            result = self.repo.update(name, dialog.values())
            if result is not None and not getattr(result, "changed", bool(result)):
                feedback.warn(self, "Edit machine", "%s was not changed:\n\n%s"
                              % (name, getattr(result, "error", "") or "it is no longer in the inventory"))
                self.load_data()
                return
            choice = dialog.inp_status.currentText()
            new_status = self.repo.status_after(name, choice)
            if new_status != hw.normalise_status(edit_data.get("status")):
                self._apply_status(name, choice)
        except DatabaseUnavailableError:
            raise
        except HardwareError as exc:
            feedback.warn(self, "Edit machine", str(exc))
        else:
            feedback.toast(self, "%s saved." % name, "success")
        self.load_data()

    def _locations(self):
        return [r.get("location") for r in self.hardware_data or []]

    def _apply_status(self, name, choice):
        """Repair or end of life on an issued machine: collect it first (asked)."""
        holder = self.repo.holder(name)
        target = self.repo.status_after(name, choice)
        collect = False
        if holder and target in (hw.REPAIR,) + hw.END_OF_LIFE:
            if not feedback.confirm(
                    self, "Edit machine",
                    "%s is out with %s. Collect it back now and mark it %s?"
                    % (name, people.display_name(holder), target),
                    yes_label="Collect and mark %s" % target, no_label="Leave it with them"):
                raise HardwareError("Status not changed - %s still has %s." % (
                    people.display_name(holder), name))
            collect = True
        self.repo.set_status(name, choice, by=self._by_whom(), collect=collect)

    def issue_selected(self):
        """
        Hand a machine to somebody, and write the loan that says so.

        This is the only way a machine gets an owner. The free-text box that
        used to be in the dialog wrote hardware_inventory and nothing else, so
        the loan ledger offboarding reads never heard about it.
        """
        row = self._selected_row()
        if row is None:
            feedback.warn(self, "Issue machine", "Select the machine to issue.")
            return
        machine = row.get("machine_name")
        status = hw.normalise_status(row.get("status"))
        if not hw.can_be_issued(status):
            feedback.warn(self, "Issue machine",
                          "%s is marked %s. Set it back in service first (Edit)." % (machine, status))
            return
        holder = self.repo.holder(machine)
        if holder:
            feedback.warn(self, "Issue machine", "%s is already out with %s. Collect it back first."
                          % (machine, people.display_name(holder)))
            return

        # A searchable list of people by name, active only, no leavers and no
        # service accounts. It used to be a raw list of logins in joining
        # order with admin and tester first and last week's leaver in it.
        dialog = IssueDialog(machine, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.username():
            return
        who = dialog.username()
        service = self._service()
        # The people side refuses a machine for somebody whose last day has
        # passed or who is on the leaving list. Say why and let IT go ahead on
        # purpose - the bare "could not be recorded" explained nothing.
        override = False
        refusal = service.issue_refusal(who)
        if refusal:
            if not feedback.confirm(
                    self, "Issue machine", refusal,
                    informative="Issue %s to %s anyway?" % (machine, people.display_name(who)),
                    yes_label="Issue anyway", no_label="Cancel"):
                return
            override = True
        given = dialog.values()
        if service.issue_machine(machine, who, self._by_whom(), note=given["note"], override=override,
                                 issued_on=given["issued_on"]):
            feedback.toast(self, "%s is now with %s. When they leave, the leaving checklist will ask "
                                 "for it back." % (machine, people.display_name(who)), "success")
        else:
            feedback.warn(self, "Issue machine", "The loan could not be recorded, so nothing was changed.")
        self.load_data()

    def collect_selected(self):
        """Take a machine back, and close the loan."""
        row = self._selected_row()
        if row is None:
            feedback.warn(self, "Collect machine", "Select the machine to collect.")
            return
        machine = row.get("machine_name")
        holder = self.repo.holder(machine)
        if not holder:
            feedback.inform(self, "Collect machine",
                            "%s is not out on loan, so there is nothing to collect." % machine)
            return
        if not feedback.confirm(self, "Collect %s" % machine,
                                "Take %s back from %s?" % (machine, people.display_name(holder)),
                                yes_label="Collect", no_label="Cancel"):
            return
        if self._service().return_machine(machine, holder):
            self.load_data()
            now = next((r for r in self.hardware_data if r.get("machine_name") == machine), {})
            # Worded from what the machine is now: one that came back for
            # repair is not "free to issue".
            if hw.normalise_status(now.get("status")) == hw.REPAIR:
                feedback.toast(self, "%s is back, still marked for repair." % machine, "success")
            else:
                feedback.toast(self, "%s is back and free to issue." % machine, "success")
        else:
            feedback.warn(self, "Collect machine", "The return could not be recorded.")
            self.load_data()

    def show_history(self):
        row = self._selected_row()
        if row is None:
            return
        machine = row.get("machine_name")
        HistoryDialog(machine, self.repo.history(machine), self, events=self.repo.events(machine)).exec()

    def delete_workstation(self):
        row = self._selected_row()
        if row is None:
            feedback.warn(self, "Delete machine", "Select the machine to delete.")
            return
        name = row.get('machine_name')
        if self.repo.holder(name) or self.repo.history(name):
            # A machine that was ever issued keeps its history: retire it.
            try:
                self.repo.delete(name)
            except HardwareError as exc:
                feedback.warn(self, "Delete machine", str(exc))
            return
        if not feedback.confirm(
                self, "Delete machine",
                "Remove %s from the inventory for good? This is for a machine added by "
                "mistake - use Edit > Retired to keep a used machine's history." % name,
                yes_label="Delete %s" % name, no_label="Keep it", destructive=True):
            return
        try:
            self.repo.delete(name)
        except DatabaseUnavailableError:
            raise
        except HardwareError as exc:
            feedback.warn(self, "Delete machine", str(exc))
        else:
            feedback.toast(self, "%s deleted." % name, "success")
        self.load_data()

    def sync_from_live_ops(self):
        """
        Import machines Live Ops has seen that the inventory does not know, and
        refresh known ones' specs. A spec somebody typed is never replaced by
        an empty one, and reports that could not be read are named.
        """
        status_dir = self.hub.get_livestatus_dir()
        if not status_dir.exists():
            feedback.warn(self, "Live Ops sync",
                          "The Live Ops folder on the server could not be found, so nothing was imported.")
            return
        summary = self.repo.sync_from_reports(status_dir)
        lines = ["%d new machine%s added, %d existing one%s refreshed." % (
            summary["added"], "" if summary["added"] == 1 else "s",
            summary["updated"], "" if summary["updated"] == 1 else "s")]
        if summary["failed"]:
            lines.append("%d could not be saved - see the log." % summary["failed"])
        def listed(names):
            return ", ".join(names[:6]) + ("" if len(names) <= 6 else " and %d more" % (len(names) - 6))

        if summary["unreadable"]:
            lines.append("%d report%s could not be read: %s." % (
                len(summary["unreadable"]), "" if len(summary["unreadable"]) == 1 else "s",
                listed(summary["unreadable"])))
        if summary.get("bad_names"):
            lines.append("Skipped (the name cannot be a machine name): %s." % listed(summary["bad_names"]))
        for name, status in summary.get("end_of_life", []):
            lines.append("%s is marked %s but reported to Live Ops - check where it is." % (name, status))
        if summary["added"]:
            lines.append("New machines are left unassigned - use Issue to… to say who has them.")
        self.last_sync_message = "\n".join(lines)
        feedback.inform(self, "Live Ops sync", self.last_sync_message)
        self.load_data()

    def style_table(self, table: QTableWidget):
        """The shared table style: data in text colour (it was all amber, the
        warning colour), readable size and padding, one selection colour.
        One long machine name no longer takes the whole width: the column has
        a fixed starting width and long names end in '...' with a tooltip."""
        style_table(table, {
            "Machine": ("interactive", 170),
            "Assigned to": ("interactive", 150),
            "Type": "contents",
            "Location": ("interactive", 120),
            "CPU": "stretch",
            "GPU": "stretch",
            "RAM": "contents",
            "Storage": "contents",
            "Warranty": "contents",
            "Status": "contents",
        }, multi_select=False)
