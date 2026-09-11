import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPalette, QColor
from PySide6.QtCore import Qt

# Ensure we can import modules if needed (add root to path)
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(current_dir))
sys.path.append(root_dir)

from ui.main_window import MainWindow

def setup_dark_theme(app):
    """Apply a Sci-Fi Dark Theme"""
    app.setStyle("Fusion")
    
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 35))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(20, 20, 25))
    palette.setColor(QPalette.AlternateBase, QColor(35, 35, 40))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText, QColor(30, 30, 35))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(45, 45, 50))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, QColor(0, 255, 255)) # Cyan
    palette.setColor(QPalette.Link, QColor(0, 180, 216))
    palette.setColor(QPalette.Highlight, QColor(0, 180, 216))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    
    app.setPalette(palette)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    setup_dark_theme(app)
    
    # Optional: Set Icon
    # app.setWindowIcon(QIcon("path/to/icon.ico"))
    
    window = MainWindow()
    window.resize(1000, 700)
    window.show()
    
    sys.exit(app.exec())
