from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, 
    QPushButton, QLabel, QHeaderView
)
from PySide6.QtGui import QColor, QBrush, QIcon

class VisualDiffDialog(QDialog):
    """
    Visualizes proposed file operations (Copy, Move, Rename, Delete) in a tree view.
    Used for 'Dry Run' confirmations.
    """
    def __init__(self, operations, parent=None):
        super().__init__(parent)
        self.operations = operations # List of dicts: {type, src, dest, status}
        self.setWindowTitle("Dry Run Preview - confirm changes")
        self.resize(800, 600)
        
        self.main_layout = QVBoxLayout(self)
        
        # Header
        lbl_info = QLabel(f"Reviewing {len(operations)} operations:")
        lbl_info.setStyleSheet("font-weight: bold; font-size: 14px; color: #3EA8BF;")
        self.main_layout.addWidget(lbl_info)
        
        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Operation", "Source", "Destination"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.main_layout.addWidget(self.tree)
        
        self.populate_tree()
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_confirm = QPushButton("Confirm & Execute")
        btn_confirm.setStyleSheet("background-color: #5FBF8F; color: white; font-weight: bold; padding: 5px 15px;")
        btn_confirm.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_confirm)
        self.main_layout.addLayout(btn_layout)
        
    def populate_tree(self):
        """Parse operations and fill tree with color-coded items."""
        for op in self.operations:
            op_type = op.get('type', 'UNKNOWN').upper()
            src = str(op.get('source', ''))
            dest = str(op.get('destination', ''))
            
            item = QTreeWidgetItem([op_type, src, dest])
            
            # Color Coding
            if op_type in ['COPY', 'CREATE']:
                QColor("#5FBF8F") # Greenish
                text_color = QColor("#1B3A2C")
                item.setIcon(0, QIcon.fromTheme("list-add"))
            elif op_type in ['MOVE', 'RENAME']:
                QColor("#D9A441") # Yellowish
                text_color = QColor("#D9A441")
                item.setIcon(0, QIcon.fromTheme("go-next"))
            elif op_type in ['DELETE', 'REMOVE']:
                QColor("#D9635F") # Reddish
                text_color = QColor("#D9635F")
                item.setIcon(0, QIcon.fromTheme("process-stop"))
            else:
                QColor("#E8E6E1")
                text_color = QColor("#2C2C34")
            
            # Apply Colors (Background requires brush)
            for i in range(3):
                item.setForeground(i, QBrush(text_color))
                # Backgrounds in TreeWidgets can be tricky with styles, 
                # but let's try setting it for clarity
                # item.setBackground(i, QBrush(color)) 
            
            self.tree.addTopLevelItem(item)
            
        self.tree.expandAll()
