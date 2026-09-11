from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget, QLabel
)
from PySide6.QtCore import Qt
from ui.build_tab import BuildTab
from ui.test_tab import TestTab

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UT Developer Console")
        self.setStyleSheet("QMainWindow { background-color: #1e1e23; }")
        
        # Central Widget
        self.central = QWidget()
        self.setCentralWidget(self.central)
        
        layout = QVBoxLayout(self.central)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title Bar / Branding
        title = QLabel("UT DEVELOPER CONSOLE")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            QLabel {
                font-size: 24px; font-weight: bold; color: #E0E0E0;
                padding: 10px; border-bottom: 2px solid #00B4D8;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1e1e23, stop:0.5 #2a2a30, stop:1 #1e1e23);
            }
        """)
        layout.addWidget(title)
        
        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444; background: #222; }
            QTabBar::tab {
                background: #333; color: #aaa; padding: 10px 20px;
                border: 1px solid #444; border-bottom: none;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #222; color: #00B4D8; font-weight: bold;
                border-top: 2px solid #00B4D8;
            }
            QTabBar::tab:hover { background: #3d3d3d; }
        """)
        
        self.build_tab = BuildTab()
        self.test_tab = TestTab()
        
        self.tabs.addTab(self.build_tab, "🔥 THE FORGE")
        self.tabs.addTab(self.test_tab, "🧪 THE LAB")
        
        layout.addWidget(self.tabs)

    def closeEvent(self, event):
        # Ensure active build subprocess trees are stopped when console closes.
        if hasattr(self, "build_tab") and self.build_tab:
            self.build_tab.stop_running_process()
        super().closeEvent(event)
