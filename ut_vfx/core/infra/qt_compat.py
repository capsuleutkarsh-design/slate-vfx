"""
qt_compat.py
Centralized module for Qt imports to ensure consistent Qt binding versions
across the application (e.g., PySide6 vs PyQt5 vs PyQt6) and to reduce
scattered enum imports.

Usage:
    from ut_vfx.core.infra.qt_compat import Qt, QTimer, Signal, Slot, QObject
"""

try:
    from PySide6.QtCore import (
        Qt,
        QTimer,
        Signal,
        Slot,
        QObject,
        QThread,
        QSize,
        QRect,
        QPoint,
        QEvent,
        QItemSelectionModel,
        QAbstractListModel,
        QModelIndex,
        QUrl,
        QRunnable,
        QThreadPool
    )
    from PySide6.QtGui import (
        QColor,
        QIcon,
        QPixmap,
        QPainter,
        QFont,
        QAction
    )
    from PySide6.QtWidgets import (
        QApplication,
        QWidget,
        QMainWindow,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QPushButton
    )
    
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False
    
    # Fallback structure could go here if needed in the future
    # e.g. from PyQt6.QtCore import Qt ...
