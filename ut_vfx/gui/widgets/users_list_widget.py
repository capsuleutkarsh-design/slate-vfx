from PySide6.QtWidgets import (QWidget, QVBoxLayout, QListWidget, QListWidgetItem, 
                               QLabel, QHBoxLayout)
from PySide6.QtCore import Qt, QMimeData, QSize, QPoint
from PySide6.QtGui import QDrag, QPixmap

class UserItemWidget(QWidget):
    """Visual representation of a User in the list."""
    def __init__(self, username, display_name=None, parent=None):
        super().__init__(parent)
        self.username = username
        self.display_name = display_name or username
        self.setup_ui()
        
    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)
        
        # Avatar Circle
        self.avatar = QLabel(self.display_name[:2].upper())
        self.avatar.setFixedSize(28, 28)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Random-ish color based on name
        color_hash = sum(ord(c) for c in self.username)
        hue = color_hash % 360
        self.avatar.setStyleSheet(f"""
            QLabel {{
                background-color: hsla({hue}, 60%, 40%, 1.0);
                color: white;
                border-radius: 14px;
                font-weight: bold;
                font-size: 10px;
            }}
        """)
        
        layout.addWidget(self.avatar)
        
        # Name
        lbl_name = QLabel(self.display_name.split(" ")[0]) # First name to keep it clean
        lbl_name.setStyleSheet("color: rgba(255, 255, 255, 0.8); font-size: 11px; font-weight: 600;")
        layout.addWidget(lbl_name)
        
        layout.addStretch()

class UsersListWidget(QWidget):
    """
    Sidebar showing draggable users.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Header
        header = QLabel("Active Artists")
        header.setStyleSheet("font-weight: 900; font-size: 14px; color: rgba(255,255,255,0.9); padding: 12px 12px 6px 12px; letter-spacing: 0.5px;")
        layout.addWidget(header)
        
        # List
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                border-left: 1px solid rgba(255, 255, 255, 0.05);
                outline: none;
            }
            QListWidget::item {
                border-bottom: 1px solid rgba(255, 255, 255, 0.03);
                padding: 4px 8px;
            }
            QListWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.03);
            }
            QListWidget::item:selected {
                background-color: rgba(255, 255, 255, 0.06);
            }
            QScrollBar:vertical {
                border: none;
                background: rgba(255, 255, 255, 0.02);
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.1);
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.2);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none; background: none; height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self.list_widget.setDragEnabled(True)
        self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.list_widget)
        
        # Hook up drag logic if not using default
        # QListWidget default drag usually handles internal moves or text.
        # We want custom MIME type for "User Assignment".
        # So we subclass or override startDrag.
        # But wait, we can just subclass QListWidget to be cleaner.
        
        # Re-initialize with custom class logic below by patching methods
        # Or better yet, define inner class or use custom logic here
        
        self.list_widget.startDrag = self.start_drag_custom
        
    def start_drag_custom(self, supportedActions):
        item = self.list_widget.currentItem()
        if not item: return
        
        widget = self.list_widget.itemWidget(item)
        if not widget: return
        
        mime = QMimeData()
        # Custom format: application/x-utvfx-user
        # Content: username
        mime.setData("application/x-utvfx-user", widget.username.encode('utf-8'))
        mime.setText(widget.username) # Fallback
        
        drag = QDrag(self.list_widget)
        drag.setMimeData(mime)
        
        # Create drag pixmap
        pixmap = QPixmap(widget.size())
        widget.render(pixmap)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        
        drag.exec(Qt.CopyAction)
        
    def populate(self, users):
        """
        users: list of strings (usernames) or dicts
        """
        self.list_widget.clear()
        
        for u in users:
            # Handle both string and dict inputs
            if isinstance(u, dict):
                username = u.get('username', 'Unknown')
                display = u.get('display_name', username)
            else:
                username = str(u)
                display = username
                
            item = QListWidgetItem()
            item.setSizeHint(QSize(200, 50))
            
            self.list_widget.addItem(item)
            
            w = UserItemWidget(username, display)
            self.list_widget.setItemWidget(item, w)
