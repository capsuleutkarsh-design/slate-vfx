from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QLineEdit, QDateEdit, QComboBox
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QFont, QColor
from slate.gui.core.offline_notice import on_database_error

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
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(self)
        
        self.proj_cb = QComboBox()
        self.proj_cb.setStyleSheet("background: #26262D; color: white; padding: 4px;")
        
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
        self.dep_cb.setStyleSheet("background: #26262D; color: white; padding: 4px;")
        
        self.proj_cb.currentTextChanged.connect(self.update_deps)

        self.ms_input = QLineEdit()
        self.start_input = QDateEdit(QDate.currentDate())
        self.start_input.setCalendarPopup(True)
        self.end_input = QDateEdit(QDate.currentDate().addDays(14))
        self.end_input.setCalendarPopup(True)
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
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("background-color: #3EA8BF; font-weight: bold; padding: 5px;")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: #87857F; font-weight: bold; padding: 5px;")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
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
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("Shift by (days):"))
        from PySide6.QtWidgets import QSpinBox
        self.days_spin = QSpinBox()
        self.days_spin.setRange(-1000, 1000)
        self.days_spin.setValue(1)
        self.days_spin.setStyleSheet("padding: 4px;")
        layout.addWidget(self.days_spin)
        
        btn_box = QHBoxLayout()
        ok_btn = QPushButton("Shift Downstream")
        ok_btn.setStyleSheet("background-color: #D9A441; color: white; font-weight: bold;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addWidget(ok_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)

class ProdSchedulingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        self.build_ui(main_layout)

    def create_stat_card(self, title, value, color):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #16161A;
                border: 1px solid #1D1D22;
                border-left: 4px solid {color};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        
        t_label = QLabel(title)
        t_label.setFont(QFont("Inter", 10))
        t_label.setStyleSheet("color: #87857F; font-size: 11px; font-weight: 600; text-transform: uppercase; background: transparent; border: none;")
        
        v_label = QLabel(str(value))
        v_label.setFont(QFont("Inter", 20, QFont.Weight.Bold))
        v_label.setStyleSheet("color: #E8E6E1; background: transparent; border: none;")
        
        lay.addWidget(t_label)
        lay.addWidget(v_label)
        return card, v_label

    def build_ui(self, main_layout):
        header_title = QLabel("Production Scheduling")
        header_title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #E8E6E1; margin-bottom: 2px;")
        main_layout.addWidget(header_title)
        
        # Summary Cards
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)
        card1, self.lbl_active = self.create_stat_card("Active Projects", "0", "#3EA8BF")
        card2, self.lbl_upcoming = self.create_stat_card("In Progress", "0", "#D9A441")
        card3, self.lbl_completed = self.create_stat_card("Completed Milestones", "0", "#5FBF8F")
        cards_lay.addWidget(card1)
        cards_lay.addWidget(card2)
        cards_lay.addWidget(card3)
        main_layout.addLayout(cards_lay)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        add_btn = QPushButton("+ Add New Milestone")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_milestone)
        controls.addWidget(add_btn)
        
        update_btn = QPushButton("Update Status")
        update_btn.setObjectName("secondaryButton")
        update_btn.clicked.connect(self.update_status)
        controls.addWidget(update_btn)
        
        shift_btn = QPushButton("Shift Dates (Dependency)")
        shift_btn.setObjectName("secondaryButton")
        shift_btn.clicked.connect(self.shift_dates)
        controls.addWidget(shift_btn)
        
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 7)
        self.grid.setHorizontalHeaderLabels(["ID", "Project Code", "Milestone", "Depends On", "Start Date", "End Date", "Status"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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

        self.lbl_active.setText(str(active_projects))
        self.lbl_upcoming.setText("%d  (%d overdue)" % (in_progress, overdue)
                                  if overdue else str(in_progress))
        self.lbl_completed.setText(str(completed))

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
            
            self.grid.setItem(r, 4, QTableWidgetItem(str(row.get('start_date', ''))))
            self.grid.setItem(r, 5, QTableWidgetItem(str(row.get('end_date', ''))))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            if status_item.text() == "Completed":
                status_item.setForeground(QColor("green"))
            elif status_item.text() == "In Progress":
                status_item.setForeground(QColor("yellow"))
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
            
            query = "INSERT INTO prod_scheduling (project_code, milestone, start_date, end_date, status, depends_on_id) VALUES (%s, %s, %s, %s, 'Scheduled', %s)"
            if database_manager.execute_query(query, (proj, ms, start, end, dep_id), fetch=False):
                QMessageBox.information(self, "Success", "Added new scheduling milestone.")
                self.load_data()

    def update_status(self):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a milestone to update.")
            return
            
        dialog = QDialog(self)
        dialog.setWindowTitle("Update Status")
        dialog.setStyleSheet("background-color: #1D1D22; color: white;")
        lay = QVBoxLayout(dialog)
        lay.addWidget(QLabel("Select new status:"))
        cb = QComboBox()
        cb.addItems(["Scheduled", "In Progress", "Completed"])
        cb.setStyleSheet("background: #26262D; padding: 4px;")
        lay.addWidget(cb)
        
        btn_box = QHBoxLayout()
        ok_btn = QPushButton("Update")
        ok_btn.setStyleSheet("background-color: #5FBF8F; font-weight:bold;")
        ok_btn.clicked.connect(dialog.accept)
        btn_box.addWidget(ok_btn)
        lay.addLayout(btn_box)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_status = cb.currentText()
            from slate.core.infra.database_manager import database_manager
            for r in selected_rows:
                item = self.grid.item(r, 0)
                if item:
                    mid = item.text()
                    query = "UPDATE prod_scheduling SET status = %s WHERE id = %s"
                    database_manager.execute_query(query, (new_status, int(mid)), fetch=False)
            self.load_data()
            
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
                        database_manager.execute_query(
                            "UPDATE prod_scheduling SET start_date = %s, end_date = %s WHERE id = %s",
                            (str(s_date), str(e_date), int(current_id)),
                            fetch=False
                        )
                    except DatabaseUnavailableError:
                        raise
                    except Exception:
                        pass
                # Find children
                children = database_manager.execute_query("SELECT id FROM prod_scheduling WHERE depends_on_id = %s", (int(current_id),)) or []
                for c in children:
                    child_id = c.get('id')
                    if child_id is not None and child_id not in visited:
                        shift_downstream(child_id, shift_days, visited)
                    
            shift_downstream(mid, days)
            QMessageBox.information(self, "Success", f"Shifted milestone {mid} and all its dependencies by {days} days.")
            self.load_data()

    def style_table(self, table: QTableWidget):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setStyleSheet("""
            QTableWidget { 
                background-color: #0D0D0F; 
                color: #E8E6E1; 
                gridline-color: #1D1D22; 
                border: 1px solid #1D1D22; 
                border-radius: 6px;
                font-size: 12px; 
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: rgba(62, 168, 191, 0.18); color: white; }
            QHeaderView::section { 
                background-color: #16161A; 
                color: #87857F; 
                border: none;
                border-bottom: 2px solid #1D1D22; 
                border-right: 1px solid rgba(255, 255, 255, 0.04);
                padding: 8px 10px; 
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
            }
        """)
