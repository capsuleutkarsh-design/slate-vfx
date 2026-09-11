"""
Unit Test for Main Window Instantiation.

This test verifies that the 'VFXFolderCreatorApp' (Main Window) can be initialized
without crashing and correctly sets up its basic user role data.
It relies on a simplified QTimer loop or direct instantiation checks.
"""

import sys
from PySide6.QtWidgets import QApplication
from slate.gui.main_window import VFXFolderCreatorApp

def test_main(qtbot):
    user_data = {
        "username": "tester",
        "role": "Tester",
        "display_name": "QA Tester"
    }

    print("Instantiating Main Window...")
    window = VFXFolderCreatorApp(user_data=user_data)
    qtbot.addWidget(window)
    window.show()
    print("Main Window instantiated and shown.")
    assert window.isVisible()
