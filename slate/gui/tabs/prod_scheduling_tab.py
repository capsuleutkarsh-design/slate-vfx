from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QLineEdit, QDateEdit, QComboBox
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont, QColor
from slate.gui.core.offline_notice import on_database_error
from slate.gui.core.controls import make_button, page_title, tidy_form
from slate.gui.core.stat_card import StatStrip
from slate.gui.core.table_style import style_table, set_cell_status
from slate.core.infra.gate import Gate
from slate.gui.core.data_display import date_item, select_row_by_id, setup_date_edit

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""

class AddMilestoneDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Milestone")
        # The dialog takes the application's control styles: its own sheet
        # (and the per-field ones) made the combos pills, the date edits flat
        # 22 px boxes and the labels sit above the middle of their rows.
        layout = tidy_form(QFormLayout(self))

        self.proj_cb = QComboBox()
        
        # Populate project code dropdown.
        #
        # Guarded because this runs in the constructor: an unreachable database
        # used to raise here, so clicking "New Milestone" produced no dialog at
        # all and no explanation - the button simply appeared to do nothing.
        # The dialog opens either way now and says which case it is in, because
        # "N/A" for both means the person cannot tell a studio with no projects
        # from a database that is down.
        from slate.core.infra.database_manager import database_manager
        try:
            # The column is "code". This asked for "project_code", which does
            # not exist on tracking_projects - so the query raised, the list was
            # always empty, and no milestone could be created at all. The tab
            # was unusable and said nothing about why.
            projects = database_manager.execute_query(
                "SELECT code FROM tracking_projects WHERE active = 1 "
                "ORDER BY code") or []
        except DatabaseUnavailableError:
            projects = []
            self.proj_cb.addItem("Database unavailable")
            self.proj_cb.setEnabled(False)
        else:
            for p in projects:
                code = p.get("code") if isinstance(p, dict) else p[0]
                if code:
                    self.proj_cb.addItem(str(code))

            if not self.proj_cb.count():
                self.proj_cb.addItem("N/A")
            
        self.dep_cb = QComboBox()
        
        self.proj_cb.currentTextChanged.connect(self.update_deps)

        self.ms_input = QLineEdit()
        # The studio's date format ('3 Oct 2026', as in the table) and a
        # calendar whose weeks start on Monday. It showed 30-09-2026 next to
        # a table of 2026-09-30, with Sunday-first weeks.
        self.start_input = setup_date_edit(QDateEdit(QDate.currentDate()))
        self.end_input = setup_date_edit(QDateEdit(QDate.currentDate().addDays(14)))
        # A milestone cannot finish before it starts, and cannot start before
        # the thing it waits on has finished. Neither was checked, so a Gantt
        # chart could be built that described an impossible schedule.
        self.start_input.dateChanged.connect(self._start_moved)
        self.dep_cb.currentIndexChanged.connect(self._dependency_changed)

        layout.addRow("Project Code:", self.proj_cb)
        layout.addRow("Milestone Name:", self.ms_input)
        layout.addRow("Depends On:", self.dep_cb)
        layout.addRow("Start Date:", self.start_input)
        layout.addRow("End Date:", self.end_input)
        
        self.update_deps()
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(make_button("Cancel", on_click=self.reject))
        btn_layout.addWidget(make_button("Save", "primary", on_click=self.accept))
        layout.addRow(btn_layout)
        
    def _start_moved(self, value):
        if self.end_input.date() < value:
            self.end_input.setDate(value)

    def _dependency_changed(self, *_):
        """Nothing can start before what it waits on has finished."""
        dep_id = self.dep_cb.currentData()
        if not dep_id:
            self.start_input.setMinimumDate(QDate(1900, 1, 1))
            return
        from slate.core.infra.database_manager import database_manager
        try:
            row = database_manager.execute_query(
                "SELECT end_date FROM prod_scheduling WHERE id = %s",
                (int(dep_id),), fetch="one")
        except DatabaseUnavailableError:
            # An outage must not silently drop the constraint - a milestone
            # could then be scheduled before the one it waits on.
            raise
        except Exception:
            return
        if not row:
            return
        raw = str(dict(row).get("end_date") or "")[:10]
        earliest = QDate.fromString(raw, "yyyy-MM-dd")
        if earliest.isValid():
            earliest = earliest.addDays(1)
            self.start_input.setMinimumDate(earliest)
            if self.start_input.date() < earliest:
                self.start_input.setDate(earliest)

    @on_database_error
    def update_deps(self):
        self.dep_cb.clear()
        self.dep_cb.addItem("None", None)
        proj = self.proj_cb.currentText()
        if proj and proj != "N/A":
            from slate.core.infra.database_manager import database_manager
            query = "SELECT id, milestone FROM prod_scheduling WHERE project_code = %s ORDER BY start_date ASC"
            ms_list = database_manager.execute_query(query, (proj,)) or []
            for ms in ms_list:
                self.dep_cb.addItem(ms.get('milestone'), ms.get('id'))

class ShiftDatesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Shift Dates")
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Shift by (days):"))
        from PySide6.QtWidgets import QSpinBox
        self.days_spin = QSpinBox()
        self.days_spin.setRange(-1000, 1000)
        self.days_spin.setValue(1)
        layout.addWidget(self.days_spin)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_box.addWidget(make_button("Cancel", on_click=self.reject))
        btn_box.addWidget(make_button("Shift Downstream", "primary", on_click=self.accept))
        layout.addLayout(btn_box)

class ProdSchedulingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.build_ui(main_layout)

    def build_ui(self, main_layout):
        main_layout.addWidget(page_title("Scheduling", "Milestones, dependencies and dates"))

        # Summary on one compact line, so the table keeps the height at 1366x768.
        strip = StatStrip(compact=True)
        self.lbl_active = strip.add("Active projects", "0", tone="accent")
        self.lbl_upcoming = strip.add("In progress", "0", tone="warn")
        self.lbl_completed = strip.add("Completed milestones", "0", tone="ok")
        main_layout.addWidget(strip)

        controls = QHBoxLayout()
        controls.setSpacing(Gate.SPACE_2)
        controls.addWidget(make_button("Add Milestone", "primary", icon="plus", on_click=self.add_milestone))
        controls.addWidget(make_button("Update Status", on_click=self.update_status))
        controls.addWidget(make_button("Shift Dates (Dependency)", on_click=self.shift_dates))
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 7)
        self.grid.setHorizontalHeaderLabels(["ID", "Project Code", "Milestone", "Depends On", "Start Date", "End Date", "Status"])
        self.style_table(self.grid)
        self.load_data()

        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

    @on_database_error
    def load_data(self):
        try:
            from slate.core.infra.database_manager import database_manager
            query = "SELECT * FROM prod_scheduling ORDER BY id DESC"
            sched = database_manager.execute_query(query) or []
        except DatabaseUnavailableError:
            raise
        except:
            sched = []
            
        self.grid.setRowCount(len(sched))
        
        from datetime import date as _date

        completed = sum(1 for row in sched if row.get('status') == 'Completed')

        # Active projects means active projects, not "projects that happen to
        # have a milestone". And an overdue milestone is not "upcoming" - it was
        # counted as one, so the card that should have been shouting was the one
        # reporting healthy numbers.
        try:
            from slate.core.infra.database_manager import database_manager
            row = database_manager.execute_query(
                "SELECT COUNT(*) AS c FROM tracking_projects WHERE active = 1",
                fetch="one")
            active_projects = int(dict(row).get("c", 0)) if row else 0
        except DatabaseUnavailableError:
            raise
        except Exception:
            # The table is missing rather than unreachable. Counting the
            # projects that have milestones is the old behaviour, and is better
            # than a zero that reads as "no projects".
            active_projects = len({row.get('project_code') for row in sched})

        today = _date.today().isoformat()
        overdue = sum(1 for row in sched
                      if row.get('status') != 'Completed'
                      and str(row.get('end_date') or '')[:10]
                      and str(row.get('end_date'))[:10] < today)
        in_progress = len(sched) - completed - overdue

        self.lbl_active.set_value(active_projects)
        self.lbl_upcoming.set_value("%d  (%d overdue)" % (in_progress, overdue)
                                    if overdue else str(in_progress))
        self.lbl_upcoming.set_tone("bad" if overdue else "warn")
        self.lbl_completed.set_value(completed)

        for r, row in enumerate(sched):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('milestone', ''))))
            
            dep_id = row.get('depends_on_id')
            dep_name = "None"
            if dep_id:
                dep_row = next((x for x in sched if x.get('id') == dep_id), None)
                if dep_row: dep_name = dep_row.get('milestone', str(dep_id))
            self.grid.setItem(r, 3, QTableWidgetItem(dep_name))
            
            self.grid.setItem(r, 4, date_item(row.get('start_date')))
            self.grid.setItem(r, 5, date_item(row.get('end_date')))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            if status_item.text() == "Completed":
                set_cell_status(status_item, "ok", background=False)
            elif status_item.text() == "In Progress":
                set_cell_status(status_item, "warn", background=False)
            self.grid.setItem(r, 6, status_item)

    def add_milestone(self):
        dialog = AddMilestoneDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            proj = dialog.proj_cb.currentText()
            # No quote doubling. The query below is parameterised, so the driver
            # escapes it - doubling first stored "Director''s cut" verbatim.
            ms = dialog.ms_input.text().strip()
            start = dialog.start_input.date().toString("yyyy-MM-dd")
            end = dialog.end_input.date().toString("yyyy-MM-dd")
            dep_id = dialog.dep_cb.currentData()
            
            if not proj or not ms or proj == "N/A":
                QMessageBox.warning(self, "Error", "Project Code and Milestone Name are required.")
                return
            if dialog.end_input.date() < dialog.start_input.date():
                QMessageBox.warning(
                    self, "Dates the wrong way round",
                    "The end date is before the start date, so this milestone "
                    "would finish before it began.")
                return
                
            from slate.core.infra.database_manager import database_manager
            
            query = ("INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status, depends_on_id) "
                     "VALUES (%s, %s, %s, %s, 'Scheduled', %s) RETURNING id")
            # execute_query(..., fetch=False) returned None even when the row
            # was written: no message, no refresh, and people saved again and
            # made duplicates. The result is checked and the table always
            # reloads, with the new milestone selected.
            result = database_manager.execute_update(query, (proj, ms, start, end, dep_id))
            self.load_data()
            if not result:
                QMessageBox.warning(self, "Not saved",
                                    "The milestone was not saved:\n\n%s"
                                    % (result.error or "the database refused it"))
                return
            if result.last_id is not None:
                select_row_by_id(self.grid, result.last_id)
            QMessageBox.information(self, "Added", f"Added \"{ms}\" to {proj}.")

    def update_status(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a milestone to update.")
            return
            
        dialog = QDialog(self)
        dialog.setWindowTitle("Update Status")
        lay = QVBoxLayout(dialog)
        lay.addWidget(QLabel("Select new status:"))
        cb = QComboBox()
        cb.addItems(["Scheduled", "In Progress", "Completed"])
        lay.addWidget(cb)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_box.addWidget(make_button("Cancel", on_click=dialog.reject))
        btn_box.addWidget(make_button("Update", "primary", on_click=dialog.accept))
        lay.addLayout(btn_box)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_status = cb.currentText()
            from slate.core.infra.database_manager import database_manager
            failed = []
            for r in selected_rows:
                item = self.grid.item(r, 0)
                if item:
                    mid = item.text()
                    query = "UPDATE prod_scheduling SET status = %s WHERE id = %s"
                    result = database_manager.execute_update(query, (new_status, int(mid)))
                    if not result.changed:
                        failed.append(result.error or "that milestone no longer exists")
            self.load_data()
            if failed:
                QMessageBox.warning(self, "Not all updated",
                                    "%d milestone(s) were not updated:\n\n%s" % (len(failed), failed[0]))
            
    def shift_dates(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if len(selected_rows) != 1:
            QMessageBox.warning(self, "Selection Error", "Please select exactly one milestone to shift.")
            return
            
        r = list(selected_rows)[0]
        id_item = self.grid.item(r, 0)
        if not id_item: return
        mid = int(id_item.text())
        
        dialog = ShiftDatesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            days = dialog.days_spin.value()
            if days == 0: return
            
            from slate.core.infra.database_manager import database_manager
            import datetime
            failures = []
            # Recursive shift function with cycle detection
            def shift_downstream(current_id, shift_days, visited=None):
                if visited is None:
                    visited = set()
                if current_id in visited:
                    return
                visited.add(current_id)

                curr = database_manager.execute_query("SELECT start_date, end_date FROM prod_scheduling WHERE id = %s", (int(current_id),))
                if curr:
                    s_raw = str(curr[0].get('start_date') or '').split()[0]
                    e_raw = str(curr[0].get('end_date') or '').split()[0]
                    try:
                        s_date = datetime.date.fromisoformat(s_raw) + datetime.timedelta(days=shift_days)
                        e_date = datetime.date.fromisoformat(e_raw) + datetime.timedelta(days=shift_days)
                        result = database_manager.execute_update(
                            "UPDATE prod_scheduling SET start_date = %s, end_date = %s WHERE id = %s",
                            (str(s_date), str(e_date), int(current_id)))
                        if not result:
                            failures.append(result.error)
                    except DatabaseUnavailableError:
                        raise
                    except Exception as exc:
                        # A milestone with no usable dates cannot be moved;
                        # it is reported rather than silently skipped.
                        failures.append(str(exc))
                # Find children
                children = database_manager.execute_query("SELECT id FROM prod_scheduling WHERE depends_on_id = %s", (int(current_id),)) or []
                for c in children:
                    child_id = c.get('id')
                    if child_id is not None and child_id not in visited:
                        shift_downstream(child_id, shift_days, visited)
                    
            shift_downstream(mid, days)
            self.load_data()
            select_row_by_id(self.grid, mid)
            if failures:
                QMessageBox.warning(self, "Not all shifted",
                                    "%d milestone(s) could not be moved:\n\n%s"
                                    % (len(failures), failures[0]))
            else:
                QMessageBox.information(self, "Shifted",
                                        f"The milestone and everything after it moved by {days} day(s).")

    def style_table(self, table: QTableWidget):
        """The shared table setup: the milestone name takes the spare width,
        codes, dates and status are as wide as their content, no row numbers."""
        style_table(table, {
            "Project Code": "contents",
            "Milestone": "stretch",
            "Depends On": ("interactive", 200),
            "Start Date": "contents",
            "End Date": "contents",
            "Status": "contents",
        }, multi_select=True)
