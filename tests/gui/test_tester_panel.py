"""
Unit Test for the Tester Panel.

This test verifies the standalone 'TesterPanel' widget, which provides
developer tools for debugging and manual testing within the application.
"""

import sys
from PySide6.QtWidgets import QApplication
from ut_vfx.gui.tester_panel import TesterPanel

def test_panel(qtbot):
    window = TesterPanel()
    qtbot.addWidget(window)
    window.resize(600, 400)
    window.show()
    assert window.isVisible()

