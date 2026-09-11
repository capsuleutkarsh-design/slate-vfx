from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from slate.core.infra.database_manager import database_manager

class AddBidDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Project Bid")
        self.setStyleSheet("background-color: #1D1D22; color: white;")
        layout = QFormLayout(self)
        
        self.proj_input = QComboBox()
        self.populate_projects()
        
        self.shot_count_input = QSpinBox()
        self.shot_count_input.setRange(0, 100000)
        self.shot_count_input.setReadOnly(True) # Auto-calculated from DB
        
        self.cost_input = QDoubleSpinBox()
        self.cost_input.setRange(0, 1000000)
        self.cost_input.setPrefix("$ ")
        self.cost_input.setValue(300.0) # Default day rate
        self.cost_input.setGroupSeparatorShown(True)
        self.cost_input.valueChanged.connect(self.update_budget)

        self.complexity_input = QComboBox()
        self.complexity_input.addItems(["Simple", "Medium", "Hard"])
        self.complexity_input.setCurrentText("Medium")
        self.complexity_input.currentTextChanged.connect(self.update_budget)

        self.margin_input = QDoubleSpinBox()
        self.margin_input.setRange(0, 100)
        self.margin_input.setSuffix(" %")
        self.margin_input.setValue(20.0)
        self.margin_input.valueChanged.connect(self.update_budget)

        self.days_input = QDoubleSpinBox()
        self.days_input.setRange(0, 1000000)
        self.days_input.setSuffix(" days")
        self.days_input.setReadOnly(True)

        self.budget_input = QDoubleSpinBox()
        self.budget_input.setRange(0, 1000000000)
        self.budget_input.setPrefix("$ ")
        self.budget_input.setGroupSeparatorShown(True)
        self.budget_input.setReadOnly(True)

        layout.addRow("Project Code:", self.proj_input)
        layout.addRow("Total Shots (Auto):", self.shot_count_input)
        layout.addRow("Average Complexity:", self.complexity_input)
        layout.addRow("Artist Day Rate:", self.cost_input)
        layout.addRow("Target Margin:", self.margin_input)
        layout.addRow("Estimated Artist Days:", self.days_input)
        layout.addRow("Final Estimated Budget:", self.budget_input)
        
        self.proj_input.currentTextChanged.connect(self.on_project_changed)
        if self.proj_input.count() > 0:
            self.on_project_changed(self.proj_input.currentText())

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save Bid")
        save_btn.setStyleSheet("background-color: #3EA8BF; font-weight: bold; padding: 5px;")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: #87857F; font-weight: bold; padding: 5px;")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addRow(btn_layout)

    def populate_projects(self):
        query = "SELECT code FROM tracking_projects WHERE active=1 ORDER BY code"
        try:
            projects = database_manager.execute_query(query) or []
            for p in projects:
                self.proj_input.addItem(p.get('code', ''))
        except Exception as e:
            print(f"Error loading projects: {e}")

    def on_project_changed(self, proj_code):
        if not proj_code:
            return
        query = "SELECT COUNT(id) as count FROM tracking_shots WHERE project_code = %s"
        try:
            res = database_manager.execute_query(query, (proj_code,))
            count = res[0]['count'] if res else 0
            self.shot_count_input.setValue(count)
            self.update_budget()
        except Exception as e:
            print(f"Error fetching shots: {e}")

    def update_budget(self):
        shots = self.shot_count_input.value()
        rate = self.cost_input.value()
        margin = self.margin_input.value()
        comp = self.complexity_input.currentText()
        
        multiplier = 1.5
        if comp == "Medium": multiplier = 3.0
        elif comp == "Hard": multiplier = 7.0
        
        est_days = shots * multiplier
        est_cost = est_days * rate
        
        if margin < 100:
            final_budget = est_cost / (1 - (margin / 100.0))
        else:
            final_budget = est_cost
            
        self.days_input.setValue(est_days)
        self.budget_input.setValue(final_budget)

class ProdBiddingTab(QWidget):
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
        header_title = QLabel("Production Bidding")
        header_title.setFont(QFont("Inter", 16, QFont.Weight.Bold))
        header_title.setStyleSheet("color: #E8E6E1; margin-bottom: 2px;")
        main_layout.addWidget(header_title)
        
        # Summary Cards
        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(12)
        card1, self.lbl_total = self.create_stat_card("Total Bids", "0", "#3EA8BF")
        card2, self.lbl_value = self.create_stat_card("Total Pipeline Value", "$0", "#5FBF8F")
        card3, self.lbl_approved = self.create_stat_card("Approved", "0", "#3EA8BF")
        cards_lay.addWidget(card1)
        cards_lay.addWidget(card2)
        cards_lay.addWidget(card3)
        main_layout.addLayout(cards_lay)
        
        controls = QHBoxLayout()
        controls.setSpacing(10)
        add_btn = QPushButton("+ Create New Bid")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self.add_bid)
        controls.addWidget(add_btn)
        
        approve_btn = QPushButton("Approve Bid")
        approve_btn.setObjectName("secondaryButton")
        approve_btn.clicked.connect(lambda: self.update_status("Approved"))
        controls.addWidget(approve_btn)

        reject_btn = QPushButton("Reject Bid")
        reject_btn.setObjectName("dangerButton")
        reject_btn.clicked.connect(lambda: self.update_status("Rejected"))
        controls.addWidget(reject_btn)

        controls.addStretch()
        main_layout.addLayout(controls)

        self.grid = QTableWidget(0, 9)
        self.grid.setHorizontalHeaderLabels(["ID", "Project Code", "Shots", "Complexity", "Est. Days", "Margin", "Est. Cost", "Final Budget", "Status"])
        self.style_table(self.grid)
        self.load_data()
        
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.grid.hideColumn(0) # Hide ID
        main_layout.addWidget(self.grid)

    def load_data(self):
        try:
            query = "SELECT * FROM prod_bidding ORDER BY id DESC"
            bids = database_manager.execute_query(query) or []
        except:
            bids = []
            
        self.grid.setRowCount(len(bids))
        
        total = len(bids)
        total_val = sum(row.get('estimated_budget', 0) for row in bids)
        approved = sum(1 for row in bids if row.get('status') == 'Approved')
        
        self.lbl_total.setText(str(total))
        self.lbl_value.setText(f"${total_val:,.0f}")
        self.lbl_approved.setText(str(approved))

        for r, row in enumerate(bids):
            self.grid.setItem(r, 0, QTableWidgetItem(str(row.get('id', ''))))
            self.grid.setItem(r, 1, QTableWidgetItem(str(row.get('project_code', ''))))
            self.grid.setItem(r, 2, QTableWidgetItem(str(row.get('shot_count', 0))))
            self.grid.setItem(r, 3, QTableWidgetItem(str(row.get('complexity', ''))))
            self.grid.setItem(r, 4, QTableWidgetItem(f"{row.get('estimated_days', 0):.1f} d"))
            self.grid.setItem(r, 5, QTableWidgetItem(f"{row.get('target_margin', 0):.0f}%"))
            self.grid.setItem(r, 6, QTableWidgetItem(f"${row.get('estimated_cost', 0):,.2f}"))
            self.grid.setItem(r, 7, QTableWidgetItem(f"${row.get('estimated_budget', 0):,.2f}"))
            
            status_item = QTableWidgetItem(str(row.get('status', '')))
            if status_item.text() == "Approved":
                status_item.setForeground(QColor("green"))
            elif status_item.text() == "Rejected":
                status_item.setForeground(QColor("red"))
            self.grid.setItem(r, 8, status_item)

    def add_bid(self):
        dialog = AddBidDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            proj = dialog.proj_input.currentText().replace("'", "''")
            shots = dialog.shot_count_input.value()
            comp = dialog.complexity_input.currentText()
            margin = dialog.margin_input.value()
            days = dialog.days_input.value()
            
            # Recompute cost based on days and rate
            cost = days * dialog.cost_input.value()
            budget = dialog.budget_input.value()
            
            if not proj:
                QMessageBox.warning(self, "Error", "Project Code is required.")
                return
                
            query = """
            INSERT INTO prod_bidding 
            (project_code, project_name, shot_count, complexity, estimated_days, target_margin, estimated_cost, estimated_budget, status) 
            VALUES 
            (%s, %s, %s, %s, %s, %s, %s, %s, 'Draft')
            """
            params = (proj, proj, shots, comp, days, margin, cost, budget)
            if database_manager.execute_query(query, params, fetch=False):
                QMessageBox.information(self, "Success", "Added new draft bid.")
                self.load_data()

    def update_status(self, new_status):
        selected_rows = set(item.row() for item in self.grid.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Selection Empty", "Please select a bid to update.")
            return
            
        for r in selected_rows:
            item = self.grid.item(r, 0)
            if not item: continue
            bid = item.text()
            query = "UPDATE prod_bidding SET status = %s WHERE id = %s"
            database_manager.execute_query(query, (new_status, int(bid)), fetch=False)
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
