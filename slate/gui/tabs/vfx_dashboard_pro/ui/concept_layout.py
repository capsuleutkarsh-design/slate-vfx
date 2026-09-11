import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QScrollArea, QFrame, QTextEdit, 
                             QProgressBar, QSplitter, QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from slate.core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R

class ModernDarkPalette(QPalette):
    def __init__(self):
        super().__init__()
        self.setColor(QPalette.Window, QColor(30, 30, 30))
        self.setColor(QPalette.WindowText, QColor(220, 220, 220))
        self.setColor(QPalette.Base, QColor(45, 45, 45))
        self.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        self.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        self.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
        self.setColor(QPalette.Text, QColor(220, 220, 220))
        self.setColor(QPalette.Button, QColor(53, 53, 53))
        self.setColor(QPalette.ButtonText, QColor(220, 220, 220))
        self.setColor(QPalette.BrightText, QColor(255, 0, 0))
        self.setColor(QPalette.Link, QColor(42, 130, 218))
        self.setColor(QPalette.Highlight, QColor(42, 130, 218))
        self.setColor(QPalette.HighlightedText, QColor(255, 255, 255))

class Badge(QLabel):
    def __init__(self, text, color="#3EA8BF"):
        super().__init__(text)
        self.setStyleSheet(f"""
            background-color: {color};
            color: white;
            border-radius: 4px;
            padding: 4px 8px;
            font-weight: bold;
            font-size: 10px;
        """)
        self.adjustSize()

class ChatBubble(QFrame):
    def __init__(self, user, time, text, is_me=False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header = QLabel(f"{user} • {time}")
        header.setStyleSheet(f"color: {C.TEXT_GRAY_LIGHT}; font-size: {T.SIZE_XS}px;")
        layout.addWidget(header)
        
        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 12px;")
        layout.addWidget(msg)
        
        bg_color = "#26262D" if not is_me else "#6BA4C9"
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border-radius: 8px;
            }}
        """)

class ConceptWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VFX Dashboard 2.0 - Concept")
        self.resize(1400, 900)
        self.setPalette(ModernDarkPalette())
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # --- LEFT: Shot List ---
        shot_list = QListWidget()
        shot_list.setFixedWidth(250)
        shot_list.setStyleSheet("""
            QListWidget {
                background-color: #1D1D22;
                border: none;
                border-right: 1px solid #2C2C34;
            }
            QListWidget::item {
                padding: 10px;
                border-bottom: 1px solid #26262D;
            }
            QListWidget::item:selected {
                background-color: #26262D;
                border-left: 3px solid #3EA8BF;
            }
        """)
        
        for i in range(1, 20):
            item = QListWidgetItem(f"SEQ01_SHOT_{i:03d}")
            item.setForeground(QColor("#E8E6E1"))
            shot_list.addItem(item)
            
        main_layout.addWidget(shot_list)
        
        # --- CENTER: Detail View ---
        detail_area = QWidget()
        detail_layout = QVBoxLayout(detail_area)
        detail_layout.setContentsMargins(20, 20, 20, 20)
        detail_layout.setSpacing(20)
        
        # Header
        header_layout = QHBoxLayout()
        shot_title = QLabel("SEQ01_SHOT_005")
        shot_title.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header_layout.addWidget(shot_title)
        header_layout.addStretch()
        header_layout.addWidget(Badge("IN PROGRESS", "#D9A441"))
        header_layout.addWidget(Badge("V03", "#3EA8BF"))
        detail_layout.addLayout(header_layout)
        
        # Main Content Splitter
        content_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Top: Visuals & Stats
        top_section = QWidget()
        top_layout = QHBoxLayout(top_section)
        
        # Thumbnail
        thumb_frame = QFrame()
        thumb_frame.setFixedSize(640, 360)
        thumb_frame.setStyleSheet(f"background-color: {C.BG_DARKER}; border-radius: {R.SM}px; border: 1px solid {C.BORDER_LIGHT};")
        thumb_layout = QVBoxLayout(thumb_frame)
        thumb_label = QLabel("THUMBNAIL PREVIEW")
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet(f"color: {C.BORDER_LIGHT}; font-weight: {T.WEIGHT_STYLE_BOLD};")
        thumb_layout.addWidget(thumb_label)
        top_layout.addWidget(thumb_frame)
        
        # Right Side Stats
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"background-color: {C.BG_HOVER}; border-radius: {R.SM}px;")
        stats_layout = QVBoxLayout(stats_frame)
        
        stats_layout.addWidget(QLabel("DEPARTMENT STATUS"))
        
        depts = [
            ("ROTO", "APPROVED", "#5FBF8F", 100),
            ("PAINT", "WIP", "#3EA8BF", 45),
            ("COMP", "PENDING", "#87857F", 0)
        ]
        
        for name, status, color, progress in depts:
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            row.addStretch()
            row.addWidget(Badge(status, color))
            stats_layout.addLayout(row)
            
            prog = QProgressBar()
            prog.setValue(progress)
            prog.setFixedHeight(4)
            prog.setTextVisible(False)
            prog.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; }}")
            stats_layout.addWidget(prog)
            stats_layout.addSpacing(10)
            
        stats_layout.addStretch()
        
        # Folder Actions
        actions_label = QLabel("QUICK ACTIONS")
        actions_label.setStyleSheet("margin-top: 20px; font-weight: bold;")
        stats_layout.addWidget(actions_label)
        
        btn_grid = QVBoxLayout()
        for folder in ["Open Scan", "Open Roto", "Open Plate", "Open Comp"]:
            btn = QPushButton(folder)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2C2C34;
                    border: none;
                    padding: 8px;
                    border-radius: 4px;
                    text-align: left;
                }
                QPushButton:hover { background-color: #2C2C34; }
            """)
            btn_grid.addWidget(btn)
        stats_layout.addLayout(btn_grid)
        
        top_layout.addWidget(stats_frame)
        content_splitter.addWidget(top_section)
        
        # Bottom: History & Notes
        history_frame = QFrame()
        history_frame.setStyleSheet(f"background-color: {C.BG_ELEVATED}; border-radius: {R.SM}px; margin-top: {S.MD}px;")
        history_layout = QVBoxLayout(history_frame)
        
        history_label = QLabel("PRODUCTION HISTORY")
        history_label.setStyleSheet("font-weight: bold; padding: 10px;")
        history_layout.addWidget(history_label)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.addStretch()
        
        # Mock Chat
        scroll_layout.addWidget(ChatBubble("Supervisor (Mike)", "Yesterday 10:30 AM", "Edges on the left arm are still jittery. Please refine."))
        scroll_layout.addWidget(ChatBubble("Artist (John)", "Yesterday 2:00 PM", "Fixed the jitter. Uploading v03."))
        scroll_layout.addWidget(ChatBubble("Production", "Today 9:00 AM", "Client approved the concept. Proceed to final."))
        
        scroll.setWidget(scroll_content)
        history_layout.addWidget(scroll)
        
        # Input
        input_layout = QHBoxLayout()
        input_field = QTextEdit()
        input_field.setPlaceholderText("Add a note...")
        input_field.setMaximumHeight(40)
        input_field.setStyleSheet(f"background-color: {C.BG_SURFACE}; border: 1px solid {C.BORDER_DEFAULT}; border-radius: {R.SM}px;")
        
        send_btn = QPushButton("SEND")
        send_btn.setFixedSize(60, 40)
        send_btn.setStyleSheet(f"background-color: {C.ACCENT_BLUE}; color: white; border: none; border-radius: {R.SM}px; font-weight: {T.WEIGHT_STYLE_BOLD};")
        
        input_layout.addWidget(input_field)
        input_layout.addWidget(send_btn)
        history_layout.addLayout(input_layout)
        
        content_splitter.addWidget(history_frame)
        content_splitter.setSizes([500, 300])
        
        detail_layout.addWidget(content_splitter)
        main_layout.addWidget(detail_area)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ConceptWindow()
    window.show()
    sys.exit(app.exec())
