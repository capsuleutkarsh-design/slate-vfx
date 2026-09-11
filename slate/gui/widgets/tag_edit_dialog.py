from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QLineEdit, QPushButton, QCompleter, QListWidget
)
from PySide6.QtCore import Qt

class TagEditDialog(QDialog):
    def __init__(self, parent=None, current_tags=[], available_tags=[]):
        super().__init__(parent)
        self.setWindowTitle("Edit Tags")
        self.setFixedWidth(400)
        
        self.tags = list(current_tags)
        self.available_tags = available_tags
        
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Current Tags List
        self.list_tags = QListWidget()
        self.update_list()
        layout.addWidget(QLabel("Current Tags:"))
        layout.addWidget(self.list_tags)
        
        # Remove Button
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_tag)
        layout.addWidget(btn_remove)
        
        layout.addSpacing(10)
        
        # Add Tag Section
        layout.addWidget(QLabel("Add New Tag:"))
        input_layout = QHBoxLayout()
        
        self.txt_input = QLineEdit()
        self.txt_input.setPlaceholderText("Type tag...")
        self.txt_input.returnPressed.connect(self.add_tag)
        
        # Auto-Complete Logic
        completer = QCompleter(self.available_tags)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.txt_input.setCompleter(completer)
        
        input_layout.addWidget(self.txt_input)
        
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self.add_tag)
        input_layout.addWidget(btn_add)
        
        layout.addLayout(input_layout)
        
        layout.addSpacing(10)
        
        # Save/Cancel
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Save & Close")
        btn_save.clicked.connect(self.accept)
        btn_save.setStyleSheet("background-color: #3EA8BF; color: white; font-weight: bold;")
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

    def update_list(self):
        self.list_tags.clear()
        for t in self.tags:
            self.list_tags.addItem(t)

    def add_tag(self):
        t = self.txt_input.text().strip()
        if t and t not in self.tags:
            self.tags.append(t)
            self.update_list()
            self.txt_input.clear()

    def remove_tag(self):
        row = self.list_tags.currentRow()
        if row >= 0:
            self.tags.pop(row)
            self.update_list()

    def get_tags(self):
        return self.tags
