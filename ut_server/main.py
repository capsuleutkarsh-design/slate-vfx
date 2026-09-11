import sys
import os
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from PySide6.QtWidgets import QApplication
from ut_server.gui.app_window import UTServerWindow
import logging

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    app = QApplication(sys.argv)
    
    # Set global application info
    app.setApplicationName("UT Central Server")
    app.setOrganizationName("UT Studio")
    
    window = UTServerWindow()
    window.show()
    
    ret = app.exec()
    try:
        if hasattr(window, 'db_engine'):
            window.db_engine.stop()
    except Exception:
        pass
    os._exit(ret)

if __name__ == "__main__":
    main()
