from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QSpinBox, QAbstractItemView,
    QInputDialog
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ...core.infra.app_context import AppContext
import json
import logging
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons, make_button, tidy_form
from ..core.table_style import style_table, dim_cell, set_cell_status
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.data_display import export_table_dialog
from slate.core.domain import people
from slate.gui.components.table_tools import (
    KeepSelection, TableToolbar, make_item, selected_keys, setup_table,
)
from slate.gui.components.state_notice import show_load_error

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AddPCDialog(QDialog):
    def __init__(self, parent=None, hub=None, edit_data=None):
        super().__init__(parent)
        self.hub = hub
        self.edit_data = edit_data
        
        if self.edit_data:
            self.setWindowTitle("Edit PC")
        else:
            self.setWindowTitle("Add New PC")
            
        self.setMinimumWidth(420)
        # No stylesheet of its own: it drew every value in amber (the warning
        # colour), square fields beside a pill combo, and a large gradient
        # "Save PC" next to a small Cancel.
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = tidy_form(QFormLayout())
        
        self.inp_name = QLineEdit()
        # A computer name is at most 63 characters (a DNS label; Windows
        # itself stops at 15). Anything longer is a typo or a pasted note.
        self.inp_name.setMaxLength(63)
        self.inp_cpu = QLineEdit()
        self.inp_gpu = QLineEdit()
        self.inp_ram = QLineEdit()
        self.inp_storage = QLineEdit()
        self.inp_location = QLineEdit()
        # Examples go in the field, not in the label beside it.
        self.inp_ram.setPlaceholderText("e.g. 64 GB")
        self.inp_storage.setPlaceholderText("e.g. 2 TB NVMe")
        self.inp_location.setPlaceholderText("e.g. Comp bay 2")

        # Status is set here. It used to be impossible to say a machine was in
        # for repair or free to hand out, so every row said Active for ever.
        self.inp_status = QComboBox()
        for state in ("Active", "Available", "Repair"):
            self.inp_status.addItem(state)

        if self.edit_data:
            self.inp_name.setText(self.edit_data.get('machine_name', ''))
            self.inp_name.setReadOnly(True)
            self.inp_cpu.setText(self.edit_data.get('cpu', ''))
            self.inp_gpu.setText(self.edit_data.get('gpu', ''))
            self.inp_ram.setText(self.edit_data.get('ram', ''))
            self.inp_storage.setText(self.edit_data.get('storage', ''))
            self.inp_location.setText(self.edit_data.get('location', ''))
            current = str(self.edit_data.get('status') or '').strip().title()
            if current:
                index = self.inp_status.findText(current)
                if index < 0:
                    self.inp_status.addItem(current)
                    index = self.inp_status.count() - 1
                self.inp_status.setCurrentIndex(index)

        form.addRow("Machine Name:", self.inp_name)
        form.addRow("CPU:", self.inp_cpu)
        form.addRow("GPU:", self.inp_gpu)
        form.addRow("RAM:", self.inp_ram)
        form.addRow("Storage:", self.inp_storage)
        form.addRow("Location/Dept:", self.inp_location)
        form.addRow("Status:", self.inp_status)

        layout.addLayout(form)

        # Who has it is not typed here. Issuing a machine writes a loan record,
        # which is what offboarding reads to know what to collect back; a name
        # typed into this dialog wrote the inventory and not the ledger, so the
        # fleet view and the leaving checklist disagreed about the same machine.
        owner_note = QLabel(
            "Use Issue and Collect on the Hardware tab to say who has this "
            "machine. That writes the loan record offboarding reads."
        )
        owner_note.setWordWrap(True)
        owner_note.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        layout.addWidget(owner_note)
        
        # Enter saves. The auto-fill button used to be the dialog's default,
        # so Enter in any field went scanning the Live Ops share instead.
        if not self.edit_data:
            btn_scan = make_button("Auto-fill from Live Ops", "secondary",
                                   tooltip="Read CPU, GPU, RAM and storage from this machine's Live Ops report",
                                   on_click=self.auto_fill)
            layout.addWidget(btn_scan)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", on_click=self.reject)
        self.ok_btn = make_button("Save PC", "primary", on_click=self.accept)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)
        layout.addLayout(btn_layout)

    def auto_fill(self):
        machine_name = self.inp_name.text().strip()
        if not machine_name:
            QMessageBox.warning(self, "Auto-fill from Live Ops", "Type the machine name first.")
            return
            
        if self.hub:
            status_dir = self.hub.get_livestatus_dir()
            report_path = status_dir / f"{machine_name}.json"
            if report_path.exists():
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Deliberately not the logged-in user. Who was sitting at a
                    # machine when it was scanned is not who it was issued to.
                    self.inp_cpu.setText(data.get("CPU", ""))
                    self.inp_gpu.setText(data.get("GPU", ""))
                    self.inp_ram.setText(str(data.get("RAM_GB", "")) + " GB")
                    
                    drives = data.get("Drives", [])
                    if drives:
                        total_gb = sum(float(d.get("Capacity_GB", 0)) for d in drives)
                        self.inp_storage.setText(f"{total_gb:.0f} GB")
                        
                    QMessageBox.information(self, "Auto-fill from Live Ops", "Filled in from the last Live Ops report.")
                except Exception as e:
                    QMessageBox.warning(self, "Auto-fill from Live Ops", f"The Live Ops report could not be read: {e}")
            else:
                QMessageBox.warning(self, "Auto-fill from Live Ops", "Live Ops has no report for this machine. It may be switched off or not running Slate.")


class ItInventoryTab(QWidget):
    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        # Who is issuing a machine gets recorded on the loan, so the tab needs
        # to know who is using it.
        self.user_data = user_data or {}
        self.app_context = AppContext()
        self.hub = self.app_context.server_hub()
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        header_title = page_title('Hardware', 'Machines the studio owns, who has them, and their state')
        main_layout.addWidget(header_title)
        
        # Controls
        controls = QHBoxLayout()
        controls.setSpacing(10)
        
        lbl = QLabel("Status:")
        lbl.setStyleSheet(f"color: {Gate.TEXT_DIM}; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Active", "Repair", "Available"])
        self.filter_cb.currentTextChanged.connect(lambda _text: self.load_data())
        controls.addWidget(self.filter_cb)
        controls.addStretch()
        
        controls.addWidget(make_button("Add PC", "primary", icon="plus", on_click=self.add_workstation))
        controls.addWidget(make_button("Edit Selected", on_click=self.edit_workstation))
        self.issue_btn = make_button("Issue to...", tooltip="Hand this machine to somebody, and record the loan",
                                     on_click=self.issue_selected)
        controls.addWidget(self.issue_btn)
        self.collect_btn = make_button("Collect", tooltip="Take this machine back and free it in the inventory",
                                       on_click=self.collect_selected)
        controls.addWidget(self.collect_btn)
        controls.addWidget(make_button("Delete Selected", "danger", on_click=self.delete_workstation))
        controls.addWidget(make_button("Sync from Live Ops",
                                       tooltip="Automatically import any unknown online PCs from Live Ops",
                                       on_click=self.sync_from_live_ops))
        controls.addWidget(make_button("Export…", tooltip="Save the machines shown as CSV or Excel",
                                       on_click=lambda: export_table_dialog(self, self.grid, "hardware")))

        main_layout.addLayout(controls)

        # Table
        self.grid = QTableWidget(0, 8)
        self.grid.setHorizontalHeaderLabels(["Machine Name", "Assigned To", "Location", "CPU", "GPU", "RAM", "Storage", "Status"])
        self.style_table(self.grid)
        # Read-only (typing into a cell saved nothing), whole rows, sortable
        # headers; double-click edits the machine instead.
        setup_table(self.grid)
        self.grid.doubleClicked.connect(lambda _index: self.edit_workstation())

        # Search by name, person, location, CPU or GPU, and Refresh.
        self.toolbar = TableToolbar(
            self.grid, placeholder="Search machine, person, location, CPU or GPU…",
            columns=(0, 1, 2, 3, 4, 7), on_refresh=self.load_data)
        main_layout.addWidget(self.toolbar)
        # Other people's changes, without a restart (the change feed; a timer if it is missing).
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30, topics=("hardware_inventory", "asset_assignments"))
        
        main_layout.addWidget(self.grid)

        # What this table says when there is nothing in it. It used to
        # render as several hundred pixels of black, with no way to tell
        # an empty table from a broken one.
        self.empty_state = EmptyState(
            'No machines registered yet',
            'Add a workstation, or pull the current fleet from Live Ops.',
            glyph='monitor',
        )
        main_layout.addWidget(self.empty_state)
        self.empty_state.attach_to(self.grid)

        # Nothing is selected yet, so the actions that need a selection
        # start switched off rather than arguing with a dialog.
        gate_selection_buttons(self, self.grid)

        # First read only now that the table is in the layout: a notice for a
        # failed read takes the table's place, and with no layout yet it
        # floated as a window of its own while the empty state said
        # there was nothing here.
        self.load_data()

    @on_database_error
    def load_data(self):
        try:
            from slate.core.infra.database_manager import database_manager
            status_filter = self.filter_cb.currentText()
            if status_filter != "All":
                query = """
                    SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, h.assigned_to,
                           u.display_name, u.username
                    FROM hardware_inventory h
                    LEFT JOIN ut_users u ON h.assigned_to = u.username
                    WHERE h.status = %s
                    ORDER BY h.machine_name ASC
                """
                self.hardware_data = database_manager.execute_query(query, (status_filter,)) or []
            else:
                query = """
                    SELECT h.id, h.machine_name, h.gpu, h.cpu, h.storage, h.ram, h.status, h.location, h.assigned_to,
                           u.display_name, u.username
                    FROM hardware_inventory h
                    LEFT JOIN ut_users u ON h.assigned_to = u.username
                    ORDER BY h.machine_name ASC
                """
                self.hardware_data = database_manager.execute_query(query) or []
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # A failed read is not an empty inventory. It used to become []
            # here and the screen said "No machines registered yet".
            logging.exception("Hardware could not be read")
            show_load_error(self, e, retry=self.load_data, what="the machine list")
            return

        # The selection follows the machine, not the row number, across the
        # 30-second refresh and after every action.
        with KeepSelection(self.grid):
            self._fill(self.hardware_data)

    def _fill(self, rows):
        self.grid.setRowCount(len(rows))
        for r, row in enumerate(rows):
            assigned = row.get('display_name') or row.get('username') or row.get('assigned_to')
            name = str(row.get('machine_name', ''))
            self.grid.setItem(r, 0, make_item(name, key=name))
            assigned_item = make_item(assigned or "Unassigned")
            if not assigned:
                dim_cell(assigned_item)        # a placeholder, not a name
            self.grid.setItem(r, 1, assigned_item)
            self.grid.setItem(r, 2, make_item(str(row.get('location', ''))))
            self.grid.setItem(r, 3, make_item(str(row.get('cpu', 'N/A'))))
            self.grid.setItem(r, 4, make_item(str(row.get('gpu', 'N/A'))))
            self.grid.setItem(r, 5, make_item(str(row.get('ram', 'N/A'))))
            self.grid.setItem(r, 6, make_item(str(row.get('storage', 'N/A'))))

            status_item = make_item(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "active":
                set_cell_status(status_item, "ok", background=False)
            elif st_text == "repair":
                set_cell_status(status_item, "bad", background=False)
            elif st_text == "available":
                set_cell_status(status_item, "accent", background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, 7, status_item)

    def _selected_row(self):
        """The selected machine's record - found by name, so sorting cannot
        make an action hit the wrong machine."""
        keys = selected_keys(self.grid)
        if not keys:
            return None
        for record in getattr(self, "hardware_data", []) or []:
            if str(record.get('machine_name', '')) == str(keys[0]):
                return record
        return None

    def _service(self):
        from slate.core.domain.onboarding_service import OnboardingService
        return OnboardingService()

    def add_workstation(self):
        dialog = AddPCDialog(self, self.hub)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        mname = dialog.inp_name.text().strip()
        if not mname:
            return

        from slate.core.infra.database_manager import database_manager

        # Adding a name that already exists used to overwrite its specs and
        # report success, while quietly keeping the old owner. Say so instead.
        existing = database_manager.execute_query(
            "SELECT machine_name FROM hardware_inventory WHERE LOWER(machine_name) = LOWER(%s)",
            (mname,), fetch="one")
        if existing:
            QMessageBox.information(
                self, "Already registered",
                "%s is already in the inventory. Select it and use Edit Selected "
                "to change its specification." % mname)
            return

        query = """
            INSERT INTO hardware_inventory (machine_name, type, status, cpu, gpu, ram, storage, location)
            VALUES (%s, 'Workstation', %s, %s, %s, %s, %s, %s)
        """
        # A machine nobody has is Available, not Active. Everything arrived as
        # Active, so the fleet view never showed a single free machine and the
        # issue list had to work it out from assigned_to instead.
        result = database_manager.execute_update(
            query,
            (mname, dialog.inp_status.currentText() or "Available",
             dialog.inp_cpu.text().strip(), dialog.inp_gpu.text().strip(),
             dialog.inp_ram.text().strip(), dialog.inp_storage.text().strip(),
             dialog.inp_location.text().strip()))
        self.load_data()
        if not result:
            QMessageBox.warning(self, "Not added", "%s was not added:\n\n%s"
                                % (mname, result.error or "the database refused it"))

    def edit_workstation(self):
        edit_data = self._selected_row()
        if edit_data is None:
            QMessageBox.warning(self, "Edit machine", "Select the machine to edit.")
            return

        dialog = AddPCDialog(self, self.hub, edit_data=edit_data)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        mname = edit_data.get('machine_name')
        from slate.core.infra.database_manager import database_manager
        query = """
            UPDATE hardware_inventory
            SET cpu = %s, gpu = %s, ram = %s, storage = %s, location = %s, status = %s
            WHERE machine_name = %s
        """
        result = database_manager.execute_update(
            query,
            (dialog.inp_cpu.text().strip(), dialog.inp_gpu.text().strip(),
             dialog.inp_ram.text().strip(), dialog.inp_storage.text().strip(),
             dialog.inp_location.text().strip(),
             dialog.inp_status.currentText(), mname))
        self.load_data()
        if not result.changed:
            QMessageBox.warning(self, "Not saved", "%s was not changed:\n\n%s"
                                % (mname, result.error or "it is no longer in the inventory"))

    def issue_selected(self):
        """
        Hand a machine to somebody, and write the loan that says so.

        This is the only way a machine gets an owner now. The free-text box
        that used to be in the dialog wrote hardware_inventory and nothing
        else, so the loan ledger offboarding reads never heard about it - and a
        machine issued that way was never asked for back.
        """
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Issue machine", "Select the machine to issue.")
            return

        machine = row.get("machine_name")
        if str(row.get("status") or "").strip().lower() == "repair":
            QMessageBox.warning(
                self, "In for repair",
                "%s is marked as being in for repair. Mark it Available first."
                % machine)
            return

        service = self._service()
        held = service.held_by_machine(machine) if hasattr(service, "held_by_machine") else []
        if held:
            QMessageBox.warning(
                self, "Already issued",
                "%s is already out with %s. Collect it back first."
                % (machine, people.display_name(held[0].get("user_id"))))
            return

        # People by name, alphabetically, with nobody who has left and no
        # service accounts. It was a list of raw logins in joining order with
        # admin and tester first and last week's leaver still in it. The box
        # is editable, so typing part of a name jumps to it.
        choices = people.people_for_picker()
        if not choices:
            QMessageBox.warning(self, "Nobody to issue to",
                                "There is nobody in the studio's user list to issue it to.")
            return
        labels = [p.label for p in choices]
        picked, ok = QInputDialog.getItem(
            self, "Issue %s" % machine, "Issue this machine to:", labels, 0, True)
        if not ok or not picked:
            return
        match = [p for p in choices if p.label == picked or p.username.lower() == picked.strip().lower()]
        if not match:
            QMessageBox.warning(self, "Not issued",
                                "Nobody called \"%s\" is in the list. Pick a name from it." % picked)
            return
        who = match[0].username

        by_whom = str((self.user_data or {}).get("user_id")
                      or (self.user_data or {}).get("username") or "IT")
        if service.issue_machine(machine, who, by_whom):
            QMessageBox.information(
                self, "Issued",
                "%s is now with %s. When they leave, the leaving checklist will "
                "ask for it back." % (machine, match[0].name))
        else:
            QMessageBox.warning(self, "Not issued",
                                "The loan could not be recorded, so nothing was changed.")
        self.load_data()

    def collect_selected(self):
        """Take a machine back, and close the loan."""
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Collect machine", "Select the machine to collect.")
            return

        machine = row.get("machine_name")
        service = self._service()
        held = service.held_by_machine(machine) if hasattr(service, "held_by_machine") else []
        if not held:
            QMessageBox.information(
                self, "Nobody has it",
                "%s is not out on loan, so there is nothing to collect." % machine)
            return

        holder = held[0].get("user_id")
        if QMessageBox.question(
            self, "Collect %s" % machine,
            "Take %s back from %s?" % (machine, people.display_name(holder)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        if service.return_machine(machine, holder):
            QMessageBox.information(self, "Collected",
                                    "%s is back and free to issue." % machine)
        else:
            QMessageBox.warning(self, "Not collected",
                                "The return could not be recorded.")
        self.load_data()

    def delete_workstation(self):
        row = self._selected_row()
        if row is None:
            QMessageBox.warning(self, "Delete machine", "Select the machine to delete.")
            return

        mname = row.get('machine_name')

        # Deleting a machine somebody still has left the loan record behind, so
        # it haunted the leaving checklist as a machine that could never be
        # collected because it no longer existed.
        service = self._service()
        held = service.held_by_machine(mname) if hasattr(service, "held_by_machine") else []
        if held:
            QMessageBox.warning(
                self, "Still issued",
                "%s is out with %s. Collect it back before deleting it, or the "
                "loan record is left behind with nothing to return."
                % (mname, people.display_name(held[0].get("user_id"))))
            return

        reply = QMessageBox.question(self, "Delete machine", f"Delete {mname} from the inventory?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            from slate.core.infra.database_manager import database_manager
            result = database_manager.execute_update(
                "DELETE FROM hardware_inventory WHERE machine_name = %s", (mname,))
            self.load_data()
            if not result:
                QMessageBox.warning(self, "Not deleted", "%s was not deleted:\n\n%s"
                                    % (mname, result.error or "the database refused it"))

    def sync_from_live_ops(self):
        """
        Import machines Live Ops has seen that the inventory does not know.

        Two things this used to get wrong. It counted every row it touched as
        "added", so re-running it on a settled fleet reported forty new PCs
        every time. And it set the owner to whoever happened to be logged in
        when the scan ran, which both invented a loan nobody recorded and hid
        the machine from the issue list.
        """
        from slate.core.infra.database_manager import database_manager
        status_dir = self.hub.get_livestatus_dir()
        if not status_dir.exists():
            QMessageBox.warning(self, "Live Ops sync", "The Live Ops folder on the server could not be found, so nothing was imported.")
            return

        added = updated = failed = 0
        for f in status_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)

                mname = data.get("ComputerName", data.get("pc_name", f.stem))
                if not mname:
                    continue
                cpu = data.get("CPU", "")
                gpu = data.get("GPU", "")
                ram = str(data.get("RAM_GB", "")) + " GB"

                drives = data.get("Drives", [])
                storage = ""
                if drives:
                    total_gb = sum(float(d.get("Capacity_GB", 0)) for d in drives)
                    storage = f"{total_gb:.0f} GB"

                existing = database_manager.execute_query(
                    "SELECT machine_name FROM hardware_inventory "
                    "WHERE LOWER(machine_name) = LOWER(%s)", (mname,), fetch="one")

                if existing:
                    # Counted only when the database took it: a refused write
                    # used to be counted as a refreshed machine.
                    if database_manager.execute_update(
                            "UPDATE hardware_inventory SET cpu = %s, gpu = %s, ram = %s, "
                            "storage = %s WHERE LOWER(machine_name) = LOWER(%s)",
                            (cpu, gpu, ram, storage, mname)):
                        updated += 1
                    else:
                        failed += 1
                else:
                    # No owner. Live Ops knows who was sitting at it, which is
                    # not the same as who it was issued to - and guessing puts a
                    # machine beyond the reach of the issue list.
                    if database_manager.execute_update(
                            "INSERT INTO hardware_inventory "
                            "(machine_name, type, status, cpu, gpu, ram, storage) "
                            "VALUES (%s, 'Workstation', 'Available', %s, %s, %s, %s)",
                            (mname, cpu, gpu, ram, storage)):
                        added += 1
                    else:
                        failed += 1
            except DatabaseUnavailableError:
                raise
            except Exception as exc:
                logging.debug("Live Ops sync skipped %s: %s", f.name, exc)

        QMessageBox.information(
            self, "Live Ops sync",
            "%d new machine(s) added, %d existing one(s) refreshed.%s\n\n"
            "New machines are left unassigned - use Issue to say who has them."
            % (added, updated,
               "" if not failed else "\n%d could not be saved - see the log." % failed))
        self.load_data()

    def style_table(self, table: QTableWidget):
        """The shared table style: data in text colour (it was all amber, the
        warning colour), readable size and padding, one selection colour."""
        style_table(table, {
            "Machine Name": "contents",
            "Assigned To": ("interactive", 160),
            "Location": ("interactive", 130),
            "CPU": "stretch",
            "GPU": "stretch",
            "RAM": "contents",
            "Storage": "contents",
            "Status": "contents",
        })
