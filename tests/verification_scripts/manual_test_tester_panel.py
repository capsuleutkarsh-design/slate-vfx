
import sys
import os
from PySide6.QtWidgets import QApplication

# Ensure package root is in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(root_dir)

from ut_vfx.gui.tester_panel import TesterPanel

def run():
    app = QApplication(sys.argv)
    
    # Simple StyleSheet for visibility
    app.setStyleSheet("QWidget { background-color: #333; color: white; }")
    
    panel = TesterPanel()
    panel.setWindowTitle("Manual Verification - Tester Panel")
    panel.resize(800, 600)
    panel.show()
    
    print("Tester Panel Launched. Verify UI elements are responsive.")
    print("Close the window to complete the test.")
    
    sys.exit(app.exec())

if __name__ == "__main__":
    run()
