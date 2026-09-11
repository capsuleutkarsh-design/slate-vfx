from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTextBrowser, 
                               QPushButton, QHBoxLayout, QFrame)
from PySide6.QtCore import Qt

class UpdateAvailableDialog(QDialog):
    """
    Modern Dialog to show update details and release notes.
    """
    def __init__(self, manifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.setWindowTitle("Update Available")
        self.setMinimumSize(500, 600)
        self.resize(500, 600)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint) # Modern styling
        self.setStyleSheet("""
            QDialog {
                background-color: #16323A;
                border: 1px solid #2C2C34;
                border-radius: 12px;
            }
            QLabel { color: #E8E6E1; }
            QTextBrowser {
                background-color: #16323A;
                color: #B4B1AA;
                border: 1px solid #2C2C34;
                border-radius: 6px;
                padding: 10px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # --- HEADER ---
        header = QFrame()
        header.setStyleSheet("background-color: #16323A; border-top-left-radius: 12px; border-top-right-radius: 12px; border-bottom: 1px solid #2C2C34;")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("New Update Available")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #3EA8BF; border: none;")
        
        version_lbl = QLabel(f"Version {manifest.get('version', 'Unknown')}")
        version_lbl.setStyleSheet("font-size: 14px; color: #6BA4C9; font-weight: 500; border: none;")
        
        header_layout.addWidget(title)
        header_layout.addWidget(version_lbl)
        
        layout.addWidget(header)
        
        # --- BODY ---
        body_layout = QVBoxLayout()
        body_layout.setContentsMargins(20, 10, 20, 10)
        
        lbl_notes = QLabel("Release Notes:")
        lbl_notes.setStyleSheet("font-weight: bold; margin-bottom: 5px;")
        body_layout.addWidget(lbl_notes)
        
        self.notes_area = QTextBrowser()
        self.notes_area.setHtml(self._format_notes(manifest.get("notes", "No notes provided.")))
        body_layout.addWidget(self.notes_area)
        
        layout.addLayout(body_layout)
        
        # --- FOOTER ---
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(20, 10, 20, 20)
        
        self.btn_later = QPushButton("Remind Me Later")
        self.btn_later.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_later.setFixedHeight(36)
        self.btn_later.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #6BA4C9;
                border: 1px solid #2C2C34;
                border-radius: 6px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #16323A;
                color: #E8E6E1;
            }
        """)
        self.btn_later.clicked.connect(self.reject)
        
        self.btn_update = QPushButton("Download & Install")
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.setFixedHeight(36)
        self.btn_update.setStyleSheet("""
            QPushButton {
                background-color: #3EA8BF;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3EA8BF;
            }
        """)
        self.btn_update.clicked.connect(self.accept)
        
        footer_layout.addWidget(self.btn_later)
        footer_layout.addWidget(self.btn_update)
        
        layout.addLayout(footer_layout)
        
    def _format_notes(self, raw_notes):
        # Basic markdown-ish to HTML
        html = raw_notes.replace("\n", "<br>")
        return f"<div style='font-size: 13px; line-height: 1.4;'>{html}</div>"
    
    def mousePressEvent(self, event):
        # Allow dragging the window
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()
