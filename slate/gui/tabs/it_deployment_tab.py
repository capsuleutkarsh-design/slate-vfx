from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QComboBox, QDialog, QFormLayout, QLineEdit,
    QAbstractItemView
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from ..core.empty_state import EmptyState
from ..core.controls import page_title, gate_selection_buttons
from slate.gui.core.offline_notice import on_database_error

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
        self.setWindowTitle("New Software Deployment")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog {
                background-color: #0D0D0F;
                color: #D9A441;
            }
            QLabel {
                color: #87857F;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            QLineEdit {
                background-color: #16161A;
                border: 1px solid #26262D;
                border-radius: 4px;
                color: #D9A441;
                padding: 6px;
            }
            QLineEdit:focus {
                border-color: #3EA8BF;
            }
        """)
        layout = QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        self.pkg_input = QLineEdit()
        self.target_input = QLineEdit()

        layout.addRow("Package Name:", self.pkg_input)
        layout.addRow("Target Machine:", self.target_input)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        
        save_btn = QPushButton("Deploy")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self.accept)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addRow(btn_layout)


class ItDeploymentTab(QWidget):
    def __init__(self, user_data=None, parent=None):
        super().__init__(parent)
        self.user_data = user_data or {}
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)
        
        self.build_ui(main_layout)

    def create_stat_card(self, title, value, accent_color):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #16161A;
                border: 1px solid #1D1D22;
                border-radius: 8px;
            }
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        
        t_label = QLabel(title.upper())
        t_label.setFont(QFont("Inter", 9, QFont.Weight.Bold))
        t_label.setStyleSheet("color: #87857F; letter-spacing: 0.5px; background: transparent; border: none;")
        
        v_label = QLabel(str(value))
        v_label.setFont(QFont("Inter", 22, QFont.Weight.Bold))
        v_label.setStyleSheet(f"color: {accent_color}; background: transparent; border: none;")
        
        lay.addWidget(t_label)
        lay.addWidget(v_label)
        return card, v_label

    def build_ui(self, main_layout):
        header_title = page_title('Deployment', 'Packages pushed to workstations')
        main_layout.addWidget(header_title)
        
        # Summary Cards
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)
        card1, self.lbl_total = self.create_stat_card("Total Deployments", "0", "#3EA8BF")
        card2, self.lbl_success = self.create_stat_card("Success Rate", "0%", "#5FBF8F")
        card3, self.lbl_pending = self.create_stat_card("Pending Deployments", "0", "#D9A441")
        cards_lay.addWidget(card1)
        cards_lay.addWidget(card2)
        cards_lay.addWidget(card3)
        main_layout.addLayout(cards_lay)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        
        lbl = QLabel("Status:")
        lbl.setStyleSheet("color: #87857F; font-weight: 600; background: transparent; border: none;")
        controls.addWidget(lbl)
        
        self.filter_cb = QComboBox()
        self.filter_cb.addItems(["All", "Pending", "Success", "Failed"])
        self.filter_cb.setStyleSheet("""
            QComboBox {
                background: #16161A;
                color: #D9A441;
                border: 1px solid #26262D;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 24px;
            }
            QComboBox:focus { border-color: #3EA8BF; }
            QComboBox QAbstractItemView {
                background-color: #16161A;
                color: #E8E6E1;
                border: 1px solid #26262D;
                selection-background-color: #1D1D22;
                selection-color: #3EA8BF;
            }
        """)
        self.filter_cb.currentTextChanged.connect(self.load_data)
        controls.addWidget(self.filter_cb)
        
        add_btn = QPushButton("+ Deploy New Package")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_deployment)
        controls.addWidget(add_btn)
        
        success_btn = QPushButton("Mark Success")
        success_btn.setObjectName("secondaryButton")
        success_btn.clicked.connect(lambda: self.update_status("Success"))
        controls.addWidget(success_btn)
        
        fail_btn = QPushButton("Mark Failed")
        fail_btn.setObjectName("dangerButton")
        fail_btn.clicked.connect(lambda: self.update_status("Failed"))
        controls.addWidget(fail_btn)
        
        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 6)
        self.grid.setHorizontalHeaderLabels(["ID", "Package Name", "Target Machine", "Deployed By", "Status", "Deployed At"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

        # What this table says when there is nothing in it. It used to
        # render as several hundred pixels of black, with no way to tell
        # an empty table from a broken one.
        self.empty_state = EmptyState(
            'Nothing deployed yet',
            'Software pushed to workstations will be listed here.',
            glyph='package',
        )
        main_layout.addWidget(self.empty_state)
        self.empty_state.attach_to(self.grid)

        # Nothing is selected yet, so the actions that need a selection
        # start switched off rather than arguing with a dialog.
        gate_selection_buttons(self, self.grid)


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
        except:
            deps = []
            all_deps = []
            
        self.grid.setRowCount(len(deps))
        
        total = len(all_deps)
        successes = sum(1 for d in all_deps if d.get('status') == 'Success')
        pending = sum(1 for d in all_deps if d.get('status') == 'Pending')
        
        self.lbl_total.setText(str(total))
        self.lbl_pending.setText(str(pending))
        if total > 0:
            self.lbl_success.setText(f"{int((successes/total)*100)}%")
        else:
            self.lbl_success.setText("0%")

        for r, row in enumerate(deps):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('package_name', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('target_machine', ''))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('deployed_by', ''))))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            st_text = status_item.text().strip().lower()
            if st_text == "success":
                status_item.setForeground(QColor("#5FBF8F"))
            elif st_text == "failed":
                status_item.setForeground(QColor("#D9635F"))
            elif st_text == "pending":
                status_item.setForeground(QColor("#D9A441"))
            else:
                status_item.setForeground(QColor("#87857F"))
            self.grid.setItem(r, 4, status_item)
            
            self.grid.setItem(r, 5, QTableWidgetItem(str(row.get('deployed_at', ''))))

    def add_deployment(self):
        dialog = AddDeploymentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pkg = dialog.pkg_input.text().strip()
            tgt = dialog.target_input.text().strip()
            
            if not pkg or not tgt:
                QMessageBox.warning(self, "Error", "Package Name and Target Machine are required.")
                return
                
            from slate.core.infra.database_manager import database_manager
            current_user = self.user_data.get('username', 'admin')
            query = "INSERT INTO it_deployments (package_name, target_machine, deployed_by, status) VALUES (%s, %s, %s, 'Pending')"
            if database_manager.execute_query(query, (pkg, tgt, current_user), fetch=False):
                QMessageBox.information(self, "Success", "Deployment task created.")
                self.load_data()

    def update_status(self, new_status):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a deployment to update.")
            return
            
        from slate.core.infra.database_manager import database_manager
        for r in selected_rows:
            did = self.grid.item(r, 0).text()
            query = "UPDATE it_deployments SET status = %s WHERE id = %s"
            database_manager.execute_query(query, (new_status, int(did)), fetch=False)
        self.load_data()
            
    def style_table(self, table: QTableWidget):
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setStyleSheet("""
            QTableWidget { 
                background-color: #16161A; 
                color: #D9A441; 
                gridline-color: #1D1D22; 
                border: 1px solid #1D1D22; 
                border-radius: 6px;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #1D1D22;
            }
            QTableWidget::item:alternate { background-color: #16161A; }
            QTableWidget::item:selected { background-color: #16323A; color: #3EA8BF; }
            QHeaderView::section { 
                background-color: #16161A; 
                color: #87857F; 
                border: 1px solid #1D1D22; 
                padding: 6px 10px; 
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
        """)
