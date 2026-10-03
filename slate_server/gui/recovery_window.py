"""
The standalone Recover Slate window.

Works when the Slate Server window cannot start: it needs no server settings
that parse, no running database and no password - only this PC and the
Recovery Key. Started by Recover Slate.bat (a checkout), by
"Slate_Server.exe --recover" (an installed server) or by slate_recover.py.
"""

from __future__ import annotations

import os
import sys


def build_window(data_dir=None, port=None):
    from PySide6.QtWidgets import QMainWindow, QScrollArea, QFrame

    from slate_server.core.recovery.layout import find_layout
    from slate_server.gui.design_system import GLOBAL_STYLESHEET
    from slate_server.gui.views.recovery_view import RecoveryView

    window = QMainWindow()
    window.setWindowTitle("Recover Slate")
    window.setStyleSheet(GLOBAL_STYLESHEET)
    window.resize(980, 860)
    view = RecoveryView(lambda: find_layout(data_dir=data_dir, port=port))
    area = QScrollArea()
    area.setWidget(view)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    from slate_server.gui.design_system import C
    area.setStyleSheet("QScrollArea { background: %s; border: none; }" % C.BG_ROOT)
    area.viewport().setStyleSheet("background: %s;" % C.BG_ROOT)
    window.setCentralWidget(area)
    window.recovery_view = view
    # Credit line at the bottom of the window (licence section 5: must stay).
    from slate.licence import credit_label
    window.statusBar().setSizeGripEnabled(False)
    window.statusBar().addPermanentWidget(credit_label())
    return window


def run_window(data_dir=None, port=None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from slate_server.gui.design_system import GLOBAL_STYLESHEET
    app.setStyleSheet(GLOBAL_STYLESHEET)
    from slate import licence
    if not licence.check_startup():
        os._exit(3)
    window = build_window(data_dir, port)
    if not licence.check_window(window):
        os._exit(3)
    window.show()
    window.recovery_view.run_health()
    return app.exec()
