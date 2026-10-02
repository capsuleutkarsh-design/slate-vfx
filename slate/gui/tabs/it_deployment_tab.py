from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QDialog, QFormLayout, QLineEdit,
    QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons, make_button, tidy_form
from ..core.stat_card import StatStrip
from ..core.table_style import style_table, set_cell_status, dim_cell
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.data_display import datetime_item, export_table_dialog, select_row_by_id
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

class AddDeploymentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record a deployment")
        self.setMinimumWidth(440)
        layout = tidy_form(QFormLayout(self))
        layout.setContentsMargins(16, 16, 16, 16)
        
        self.pkg_input = QLineEdit()
        self.target_input = QLineEdit()

        layout.addRow("Package Name:", self.pkg_input)
        layout.addRow("Target Machine:", self.target_input)
        
        # Enter records the deployment; Cancel is never the default.
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = make_button("Cancel", on_click=self.reject)
        self.save_btn = make_button("Record", "primary", on_click=self.accept)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addRow(btn_layout)


class ItDeploymentTab(QWidget):
    """
    A record of software installed on workstations.

    It does not install anything. It never did - the tab was called Deployment
    and its button said "Deploy New Package", which reads as a promise that
    something is pushed to the machine named in the row. Nothing is: this is a
    log somebody fills in, and calling it one is the difference between a
    useful record and a feature that appears broken.

    If it is ever made real, the channel already exists: ServerHub.post_command
    is how the admin panel tells workstations to clear their caches, and a
    deployment is the same shape of instruction.
    """

    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        self.user_data = user_data or {}
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        header_title = page_title(
            'Deployment log',
            'A record of what was installed where. Slate does not push it.')
        main_layout.addWidget(header_title)
        
        # Summary. The figures were set with QFont, which the old global
        # stylesheet overruled, so "45" came out the size of its label.
        strip = StatStrip()
        self.lbl_total = strip.add("Total deployments", "0", tone="accent")
        self.lbl_success = strip.add("Success rate", "-", tone="ok")
        self.lbl_pending = strip.add("Pending", "0", tone="warn")
        main_layout.addWidget(strip)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        
        lbl = QLabel("Status:")
        lbl.setStyleSheet(f"color: {Gate.TEXT_DIM}; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Pending", "Success", "Failed"])
        self.filter_cb.currentTextChanged.connect(lambda _text: self.load_data())
        controls.addWidget(self.filter_cb)
        
        controls.addWidget(make_button("Record deployment", "primary", icon="plus",
                                       on_click=self.add_deployment))
        controls.addWidget(make_button("Mark Success", on_click=lambda: self.update_status("Success")))
        controls.addWidget(make_button("Mark Failed", "danger", on_click=lambda: self.update_status("Failed")))
        
        controls.addStretch()
        export_btn = QPushButton("Export…")
        export_btn.setObjectName("secondaryButton")
        export_btn.setToolTip("Save the rows shown as CSV or Excel")
        export_btn.clicked.connect(self.export_table)
        controls.addWidget(export_btn)
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 6)
        self.grid.setHorizontalHeaderLabels(["ID", "Package Name", "Target Machine", "Deployed By", "Status", "Deployed At"])
        self.style_table(self.grid)
        # Read-only (typed cells were never saved), rows, sortable headers.
        setup_table(self.grid)
        self.toolbar = TableToolbar(self.grid, placeholder="Search package, machine or person…",
                                    columns=(1, 2, 3), on_refresh=self.load_data)
        main_layout.addWidget(self.toolbar)
        # Other people's records appear without a restart: the change feed,
        # or a timer where it is missing.
        from slate.gui.components.auto_refresh import AutoRefresh
        self._auto_refresh = AutoRefresh(self, self.load_data, seconds=30,
                                         topics=("it_deployments",))
        
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

        # What this table says when there is nothing in it. It used to
        # render as several hundred pixels of black, with no way to tell
        # an empty table from a broken one.
        self.empty_state = EmptyState(
            'Nothing recorded yet',
            'Add an install with Record deployment.',
            glyph='package',
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
                query = "SELECT * FROM it_deployments WHERE status = %s ORDER BY id DESC"
                deps = database_manager.execute_query(query, (status_filter,)) or []
            else:
                query = "SELECT * FROM it_deployments ORDER BY id DESC"
                deps = database_manager.execute_query(query) or []
            
            all_deps = database_manager.execute_query("SELECT status FROM it_deployments") or []
        except DatabaseUnavailableError:
            raise
        except Exception as e:
            # Not "Nothing deployed yet": the read failed, so say that.
            import logging
            logging.exception("Deployments could not be read")
            show_load_error(self, e, retry=self.load_data, what="the deployment log")
            return

        total = len(all_deps)
        successes = sum(1 for d in all_deps if d.get('status') == 'Success')
        failures = sum(1 for d in all_deps if d.get('status') == 'Failed')
        pending = sum(1 for d in all_deps if d.get('status') == 'Pending')

        self.lbl_total.set_value(total)
        self.lbl_pending.set_value(pending)
        # Out of the ones that finished. Dividing by every row counted anything
        # still pending as a failure, so the rate fell every time somebody
        # recorded a job they had not done yet.
        finished = successes + failures
        if finished > 0:
            self.lbl_success.set_value(f"{int((successes / finished) * 100)}%")
        else:
            self.lbl_success.set_value("-")

        # The selection follows the deployment (by id), not the row number.
        with KeepSelection(self.grid):
            self._fill(deps)

    def _fill(self, deps):
        self.grid.setRowCount(len(deps))
        for r, row in enumerate(deps):
            dep_id = row.get('id')
            self.grid.setItem(r, 0, make_item(str(dep_id or ''), sort_value=dep_id, key=dep_id))
            self.grid.setItem(r, 1, make_item(str(row.get('package_name', ''))))
            self.grid.setItem(r, 2, make_item(str(row.get('target_machine', ''))))
            # The person's name, not their login (IT-021 / IT-103 family).
            self.grid.setItem(r, 3, make_item(people.display_name(row.get('deployed_by', ''))))

            status_item = make_item(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "success":
                set_cell_status(status_item, "ok", background=False)
            elif st_text == "failed":
                set_cell_status(status_item, "bad", background=False)
            elif st_text == "pending":
                set_cell_status(status_item, "warn", background=False)
            else:
                dim_cell(status_item)
            self.grid.setItem(r, 4, status_item)
            
            # '17 Sep 2026, 23:02' - it printed the raw timestamp with
            # microseconds. Sorted by the moment, not the text.
            self.grid.setItem(r, 5, datetime_item(row.get('deployed_at')))

    def add_deployment(self):
        dialog = AddDeploymentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pkg = dialog.pkg_input.text().strip()
            tgt = dialog.target_input.text().strip()
            
            if not pkg or not tgt:
                QMessageBox.warning(self, "Record deployment", "Give both the package name and the machine.")
                return
                
            from slate.core.infra.database_manager import database_manager
            current_user = self.user_data.get('username', 'admin')
            query = ("INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) "
                     "VALUES (%s, %s, %s, 'Pending') RETURNING id")
            # execute_query(..., fetch=False) returned None even when the row
            # was written, so the message and the reload never happened and
            # the new deployment only appeared after leaving the tab.
            result = database_manager.execute_update(query, (pkg, tgt, current_user))
            self.load_data()
            if not result:
                QMessageBox.warning(self, "Not recorded",
                                    "The deployment was not saved:\n\n%s"
                                    % (result.error or "the database refused it"))
                return
            if result.last_id is not None:
                select_row_by_id(self.grid, result.last_id)
            QMessageBox.information(self, "Recorded",
                                    f"{pkg} on {tgt} is recorded as pending.")

    def update_status(self, new_status):
        # By id, never by row number: after a reload the same rows hold other
        # deployments, and the next Mark Failed used to hit one of those.
        ids = [k for k in selected_keys(self.grid) if k is not None]
        if not ids:
            QMessageBox.warning(self, "Mark %s" % new_status.lower(),
                                "Select the deployment to update.")
            return

        from slate.core.infra.database_manager import database_manager
        failed = []
        for did in ids:
            query = "UPDATE it_deployments SET status = %s WHERE id = %s"
            result = database_manager.execute_update(query, (new_status, int(did)))
            if not result.changed:
                failed.append(result.error or "that deployment no longer exists")
        self.load_data()
        if failed:
            QMessageBox.warning(
                self, "Not all updated",
                "%d of %d deployment(s) were not marked %s:\n\n%s"
                % (len(failed), len(ids), new_status, failed[0]))

    def export_table(self):
        """What the table shows, to CSV or Excel (IT-159)."""
        export_table_dialog(self, self.grid, "deployments")
            
    def style_table(self, table: QTableWidget):
        """The shared table style (it was amber text, ALL-CAPS headers)."""
        style_table(table, {
            "Package Name": "stretch",
            "Target Machine": "contents",
            "Deployed By": "contents",
            "Status": "contents",
            "Deployed At": "contents",
        })
