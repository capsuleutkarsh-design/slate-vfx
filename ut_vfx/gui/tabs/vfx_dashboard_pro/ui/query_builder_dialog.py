from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QComboBox, QLineEdit, QScrollArea, QWidget, QFrame
)
from PySide6.QtCore import Qt, Signal
from ut_vfx.core.infra.design_tokens import ColorTokens as C, RadiusTokens as R

class QueryRuleWidget(QWidget):
    remove_requested = Signal(QWidget)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        # Field
        self.field_combo = QComboBox()
        self.field_combo.addItems([
            "Shot Code", "Sequence", "Status", "Client Status", 
            "Assigned Artist", "Priority", "Description", "Internal Comment", "Client Feedback"
        ])
        self.field_combo.setMinimumWidth(120)
        self.field_combo.setStyleSheet(f"background: {C.BG_INPUT}; color: white; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px; padding: 4px;")
        
        # Operator
        self.op_combo = QComboBox()
        self.op_combo.addItems(["Equals", "Not Equals", "Contains", "Does Not Contain", "Is Empty", "Is Not Empty"])
        self.op_combo.setMinimumWidth(120)
        self.op_combo.setStyleSheet(f"background: {C.BG_INPUT}; color: white; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px; padding: 4px;")
        
        # Value
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Value...")
        self.value_input.setStyleSheet(f"background: {C.BG_INPUT}; color: white; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px; padding: 4px;")
        
        # Remove Btn
        self.remove_btn = QPushButton("✕")
        self.remove_btn.setFixedSize(24, 24)
        self.remove_btn.setStyleSheet(f"background: transparent; color: {C.ERROR}; font-weight: bold; border: none;")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        
        layout.addWidget(self.field_combo)
        layout.addWidget(self.op_combo)
        layout.addWidget(self.value_input, 1) # Expand value
        layout.addWidget(self.remove_btn)
        
    def get_rule(self):
        return {
            "field": self.field_combo.currentText(),
            "operator": self.op_combo.currentText(),
            "value": self.value_input.text()
        }

class QueryBuilderDialog(QDialog):
    query_applied = Signal(list, str) # Emits (rules_list, match_type)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Query Builder")
        self.resize(600, 400)
        self.rules = []
        self.setup_ui()
        
    def setup_ui(self):
        self.setStyleSheet(f"background-color: {C.BG_ELEVATED}; color: white;")
        
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Header Area
        header_layout = QHBoxLayout()
        title = QLabel("Advanced Filters")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {C.TEXT_PRIMARY};")
        
        self.match_combo = QComboBox()
        self.match_combo.addItems(["Match ALL rules (AND)", "Match ANY rule (OR)"])
        self.match_combo.setStyleSheet(f"background: {C.BG_INPUT}; color: white; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px; padding: 4px;")
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(QLabel("Condition:"))
        header_layout.addWidget(self.match_combo)
        main_layout.addLayout(header_layout)
        
        # Rules Container (Scrollable)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: none; background: transparent;")
        
        self.rules_container = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_container)
        self.rules_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rules_layout.setSpacing(10)
        
        self.scroll_area.setWidget(self.rules_container)
        main_layout.addWidget(self.scroll_area, 1)
        
        # Add Rule Button
        self.add_rule_btn = QPushButton("+ Add Rule")
        self.add_rule_btn.setStyleSheet(f"background: transparent; color: {C.ACCENT_BLUE}; font-weight: bold; border: 1px dashed {C.ACCENT_BLUE}; border-radius: {R.SM}px; padding: 8px;")
        self.add_rule_btn.clicked.connect(self.add_rule)
        main_layout.addWidget(self.add_rule_btn)
        
        # Footer Action Buttons
        footer_layout = QHBoxLayout()
        
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setStyleSheet(f"background: {C.BG_INPUT}; color: {C.TEXT_SECONDARY}; border: none; padding: 8px 16px; border-radius: {R.SM}px;")
        self.clear_btn.clicked.connect(self.clear_rules)
        
        self.apply_btn = QPushButton("Apply Query")
        self.apply_btn.setStyleSheet(f"background: {C.ACCENT_BLUE}; color: white; font-weight: bold; border: none; padding: 8px 24px; border-radius: {R.SM}px;")
        self.apply_btn.clicked.connect(self.apply_query)
        
        footer_layout.addWidget(self.clear_btn)
        footer_layout.addStretch()
        footer_layout.addWidget(self.apply_btn)
        
        main_layout.addLayout(footer_layout)
        
        # Add an initial empty rule
        self.add_rule()
        
    def add_rule(self):
        rule_widget = QueryRuleWidget(self)
        rule_widget.remove_requested.connect(self.remove_rule)
        self.rules_layout.addWidget(rule_widget)
        self.rules.append(rule_widget)
        
    def remove_rule(self, widget):
        if widget in self.rules:
            self.rules.remove(widget)
            self.rules_layout.removeWidget(widget)
            widget.deleteLater()
            
    def clear_rules(self):
        for widget in list(self.rules):
            self.remove_rule(widget)
        # Emit empty query to clear filters
        self.query_applied.emit([], "AND")
        self.accept()
            
    def apply_query(self):
        query_data = []
        for w in self.rules:
            rule = w.get_rule()
            if rule["value"].strip() or rule["operator"] in ["Is Empty", "Is Not Empty"]:
                query_data.append(rule)
                
        match_type = "AND" if "ALL" in self.match_combo.currentText() else "OR"
        self.query_applied.emit(query_data, match_type)
        self.accept()
