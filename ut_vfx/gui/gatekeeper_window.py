import socket
import logging
from ctypes import windll, Structure, c_long, byref
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, 
    QFrame, QGraphicsDropShadowEffect
)
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QKeyEvent, QGuiApplication

from ..core.infra.app_context import AppContext
from .components.qt_safety import safe_single_shot

class RECT(Structure):
    _fields_ = [('left', c_long), ('top', c_long), ('right', c_long), ('bottom', c_long)]

class GatekeeperWindow(QWidget):
    unlocked = Signal(dict)

    def __init__(self, user_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.user_manager = user_manager or self.app_context.user_manager()
        self.pc_name = socket.gethostname()
        
        # 1. Full Screen & Topmost
        self.setWindowFlags(
            Qt.WindowType.Window | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.Tool
        )
        
        # --- CRITICAL FIX: REMOVED TRANSLUCENT ATTRIBUTE ---
        # This prevents the "Black Screen" glitch on startup.
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) <--- DELETED
        
        # 2. Solid Blue Background
        self.setAutoFillBackground(True) # Ensure background is painted
        self.setStyleSheet("background-color: #3EA8BF;")
        
        # 3. Setup UI
        self.setup_ui()
        self._cover_all_screens()
        
        # 4. Input Trap (Run once after showing)
        safe_single_shot(100, self, self._trap_mouse)

    def _trap_mouse(self):
        """Physically confines mouse to this window."""
        # Only trap if we are the active window to avoid fighting Windows
        if self.isActiveWindow():
            rect = RECT(0, 0, self.width(), self.height())
            windll.user32.ClipCursor(byref(rect))
            self.user.setFocus()

    def _release_lock(self):
        """Frees the mouse."""
        windll.user32.ClipCursor(None)

    def _cover_all_screens(self):
        screens = QGuiApplication.screens()
        virtual_rect = QRect()
        for screen in screens:
            virtual_rect = virtual_rect.united(screen.geometry())
        self.setGeometry(virtual_rect)
        
        # AGGRESSIVE LOCK: Force window to top
        self.showFullScreen()
        self.activateWindow()
        self.raise_()
        
        # Windows API: Force Foreground
        try:
            hwnd = self.winId()
            windll.user32.SetForegroundWindow(hwnd)
            windll.user32.SetActiveWindow(hwnd)
        except Exception as exc:
            logging.debug("Unable to force Gatekeeper window focus: %s", exc)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        card = QFrame()
        card.setMinimumSize(400, 520)
        # Added border-radius here specifically for the card
        card.setStyleSheet("""
            QFrame { 
                background-color: #16161A; 
                border: 4px solid #E8E6E1; 
                border-radius: 20px; 
            }
        """)
        
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(100)
        shadow.setColor(QColor(0, 0, 0, 150))
        card.setGraphicsEffect(shadow)
        
        layout = QVBoxLayout(card)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 50, 40, 50)
        
        from .core.icons_brand import slate_mark
        from ..core.infra.gate import Gate

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(slate_mark(Gate.ACCENT, 56).pixmap(56, 56))
        logo.setStyleSheet("border: none; background: transparent;")

        lbl = QLabel("SLATE")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; font-size: 32px; "
            f"font-weight: 600; letter-spacing: 6px; border: none; background: transparent;")
        
        sub = QLabel(f"LOCKED: {self.pc_name}")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #87857F; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        
        self.user = QLineEdit()
        self.user.setPlaceholderText("User ID")
        self.user.setStyleSheet(self._input_style())
        
        self.pwd = QLineEdit()
        self.pwd.setPlaceholderText("Password")
        self.pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd.setStyleSheet(self._input_style())
        self.pwd.returnPressed.connect(self.attempt)
        
        btn = QPushButton("UNLOCK SYSTEM")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(55)
        btn.setStyleSheet("""
            QPushButton { background-color: #3EA8BF; color: #000; font-weight: 900; font-size: 16px; border-radius: 8px; border: none; }
            QPushButton:hover { background-color: #E8E6E1; }
        """)
        btn.clicked.connect(self.attempt)
        
        self.status = QLabel("Enter Credentials")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet("color: #87857F; font-weight: bold; border: none; background: transparent;")
        
        layout.addWidget(logo)
        layout.addWidget(lbl)
        layout.addWidget(sub)
        layout.addSpacing(20)
        layout.addWidget(self.user)
        layout.addWidget(self.pwd)
        layout.addSpacing(20)
        layout.addWidget(btn)
        layout.addWidget(self.status)
        layout.addStretch()
        
        main_layout.addWidget(card)

    def _input_style(self):
        return """
            QLineEdit { background-color: #1D1D22; border: 2px solid #26262D; color: white; padding: 15px; border-radius: 8px; font-size: 14px; }
            QLineEdit:focus { border: 2px solid #3EA8BF; background-color: #1D1D22; }
        """

    def attempt(self):
        u = self.user.text().strip()
        p = self.pwd.text().strip()
        user_data = self.user_manager.authenticate(u, p)
        
        if user_data:
            self.status.setText("Unlocking...")
            self.status.setStyleSheet("color: #5FBF8F; border: none; background: transparent;")
            self._release_lock()
            self.unlocked.emit(user_data)
            self.close()
        else:
            self.status.setText("Invalid Credentials")
            self.status.setStyleSheet("color: #D9635F; border: none; background: transparent;")
            self.pwd.clear()

    def keyPressEvent(self, e: QKeyEvent):
        key = e.key()
        modifiers = e.modifiers()
        # Block Alt+Tab, WinKey, Ctrl+Esc
        if (modifiers & Qt.KeyboardModifier.AltModifier and key == Qt.Key.Key_Tab) or \
           (modifiers & Qt.KeyboardModifier.AltModifier and key == Qt.Key.Key_Escape) or \
           (modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Escape) or \
           (key == Qt.Key.Key_Meta) or (key == Qt.Key.Key_Super_L) or (key == Qt.Key.Key_Super_R):
            e.ignore()
            return
        super().keyPressEvent(e)
        
    def closeEvent(self, e):
        e.accept()
