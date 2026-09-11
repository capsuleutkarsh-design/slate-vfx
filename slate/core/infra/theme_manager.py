import os
import re

from PySide6.QtWidgets import QApplication
from .global_config import GlobalConfig
from ..system.adaptation_engine import system_engine

class ThemeManager:
    """
    Manages application standard Dark and Light modes.
    Follows Material Design recommended contrast and color standards.
    """
    
    # Standard Dark Mode (Material Design / VS Code Style)
    DARK_MODE = """
        /* GLOBAL RESET & BASE */
        QMainWindow, QDialog { background-color: #0D0D0F; color: #E8E6E1; }
        QWidget { color: #E8E6E1; font-family: 'Segoe UI', Sans-Serif; font-size: 14px; outline: none; }
        
        /* INPUT FIELDS */
        QLineEdit, QTextEdit, QPlainTextEdit { 
            background-color: #1D1D22; 
            border: 1px solid #2C2C34; 
            border-radius: 4px; 
            padding: 6px; 
            color: #E8E6E1; 
            selection-background-color: #3EA8BF;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
            border: 1px solid #3EA8BF;
            background-color: #1D1D22;
        }
        
        /* COMBO BOXES */
        QComboBox { 
            background-color: #1D1D22; 
            border: 1px solid #2C2C34; 
            border-radius: 4px; 
            padding: 6px; 
            color: #E8E6E1;
        }
        QComboBox:hover { border: 1px solid #2C2C34; }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView {
            background-color: #1D1D22;
            border: 1px solid #2C2C34;
            selection-background-color: #3EA8BF;
        }

        /* TABLES */
        QTableWidget { 
            background-color: #0D0D0F; 
            border: 1px solid #26262D; 
            gridline-color: #26262D;
            alternate-background-color: #1D1D22;
            selection-background-color: #26262D; /* Safety for unstyled cells */
        }
        QHeaderView::section { 
            background-color: #1D1D22; 
            padding: 6px; 
            border: none; 
            border-bottom: 2px solid #26262D; 
            font-weight: 600; 
        }
        /* CRITICAL FIX: Prevent 'white lines' gap bleed-through on HighDPI */
        QTableWidget::item {
            border: none;
            padding: 2px;
            outline: none; 
        }
        QTableWidget::item:selected { 
            background-color: #26262D; 
            border: none;
            outline: none;
        }
        QTableWidget::item:focus {
            border: none;
            outline: none;
        }

        /* BUTTONS */
        QPushButton { 
            background-color: #3EA8BF; 
            color: white; 
            border: none; 
            padding: 8px 16px; 
            border-radius: 4px; 
            font-weight: 600; 
        }
        QPushButton:hover { background-color: #3EA8BF; margin-top: -1px; margin-bottom: 1px; }
        QPushButton:pressed { background-color: #3EA8BF; margin-top: 1px; margin-bottom: -1px; }

        /* SCROLLBARS */
        QScrollBar:vertical { border: none; background: transparent; width: 8px; margin: 0; }
        QScrollBar::handle:vertical { background: #2C2C34; min-height: 28px; border-radius: 4px; }
        QScrollBar::handle:vertical:hover { background: #87857F; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0px; width: 0px; background: transparent; border: none; }

        QScrollBar:horizontal { border: none; background: transparent; height: 8px; margin: 0; }
        QScrollBar::handle:horizontal { background: #2C2C34; min-width: 28px; border-radius: 4px; }
        QScrollBar::handle:horizontal:hover { background: #87857F; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { height: 0px; width: 0px; background: transparent; border: none; }

        /* TABS */
        QTabWidget::pane { border: 1px solid #26262D; background-color: #0D0D0F; }
        QTabBar::tab { 
            background-color: #1D1D22; 
            color: #B4B1AA; 
            padding: 10px 20px; 
            margin-right: 2px; 
            border-top-left-radius: 4px; 
            border-top-right-radius: 4px; 
            font-weight: 500;
        }
        QTabBar::tab:selected { 
            background-color: #0D0D0F; 
            color: white; 
            border-top: 3px solid #3EA8BF; 
        }

        /* SIDEBAR */
        QListWidget#MainSidebar {
            background-color: #1D1D22;
            border: none;
            border-right: 1px solid #26262D;
            outline: none;
            padding-top: 10px;
        }
        QListWidget#MainSidebar::item {
            color: #B4B1AA;
            padding: 12px 20px;
            background: transparent;
            font-family: 'Segoe UI', sans-serif;
            font-size: 14px;
            height: 30px;
        }
        QListWidget#MainSidebar::item:hover { background-color: #26262D; color: white; }
        QListWidget#MainSidebar::item:selected {
            background-color: #26262D;
            color: white;
            border-left: 3px solid #3EA8BF;
        }
        
        /* TOOLTIPS */
        QToolTip { background-color: #26262D; color: white; border: 1px solid #2C2C34; }
    """
    
    # New Flutter-Inspired Material Theme
    FLUTTER_MODE = """
        /* FLUTTER MATERIAL DARK THEME */
        
        /* 1. Global Reset */
        * {
            font-family: 'Roboto', 'Segoe UI', sans-serif;
            font-size: 11pt;
            color: #E8E6E1;
            outline: none;
        }
        
        /* 2. Surfaces */
        QMainWindow, QDialog { background-color: #0D0D0F; }
        QWidget { background-color: transparent; }
        
        /* 3. Cards (Group Boxes, Frames) */
        QGroupBox, QFrame {
            background-color: #1D1D22;
            border: 1px solid #26262D;
            border-radius: 12px;
            margin-top: 24px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0px 8px;
            color: #3EA8BF; /* Material Purple */
            font-weight: bold;
        }
        
        /* 4. Inputs (Filled Style) */
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
            background-color: #26262D; /* Surface Overlay */
            border: none;
            border-bottom: 2px solid #2C2C34;
            border-radius: 8px 8px 0 0;
            padding: 10px 12px;
            selection-background-color: #3EA8BF;
            selection-color: #000;
        }
        QLineEdit:focus, QTextEdit:focus {
            border-bottom: 2px solid #3EA8BF;
            background-color: #26262D;
        }
        
        /* 5. Buttons (Pill Shape aka 'Staduim Border') */
        QPushButton {
            background-color: #3EA8BF;
            color: #000000;
            font-weight: 700;
            border-radius: 18px; /* High radius for pill shape */
            padding: 10px 24px;
            border: none;
        }
        QPushButton:hover {
            background-color: #3EA8BF;
            margin-top: -2px; /* Lift effect */
            margin-bottom: 2px;
        }
        QPushButton:pressed {
            background-color: #3EA8BF;
            margin-top: 2px;
            margin-bottom: -2px;
        }
        /* Secondary Action Buttons */
        QPushButton#secondary {
            background-color: transparent;
            border: 1px solid #3EA8BF;
            color: #3EA8BF;
        }
        QPushButton#secondary:hover {
            background-color: rgba(62, 168, 191, 0.1);
        }
        
        /* 6. Tabs (Material Style) */
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            background: transparent;
            color: #87857F;
            padding: 12px 24px;
            font-weight: bold;
            text-transform: uppercase;
            border-bottom: 3px solid transparent;
        }
        QTabBar::tab:selected {
            color: #3EA8BF;
            border-bottom: 3px solid #3EA8BF;
        }
        QTabBar::tab:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }
        
        /* 7. Sidebar (Navigation Rail) */
        QListWidget#MainSidebar {
            background-color: #1D1D22;
            border-right: 1px solid #26262D;
            padding: 10px;
        }
        QListWidget#MainSidebar::item {
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 4px;
            color: #B4B1AA;
        }
        QListWidget#MainSidebar::item:selected {
            background-color: rgba(62, 168, 191, 0.12); /* Purple Tint */
            color: #3EA8BF;
        }
        QListWidget#MainSidebar::item:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }
        
        /* 8. Scrollbars (Hidden/Minimal) */
        QScrollBar:vertical {
            background: transparent;
            width: 10px;
            margin: 0px;
            border: none;
        }
        QScrollBar::handle:vertical {
            background: #2C2C34;
            border-radius: 5px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover { background: #87857F; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: none; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        
        QScrollBar:horizontal {
            background: transparent;
            height: 10px;
            margin: 0px;
            border: none;
        }
        QScrollBar::handle:horizontal {
            background: #2C2C34;
            border-radius: 5px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover { background: #87857F; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; border: none; background: none; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
    """

    # Standard Light Mode (Clean White / Off-White)
    LIGHT_MODE = """
        /* GLOBAL RESET & BASE */
        QMainWindow, QDialog { background-color: #E8E6E1; color: #26262D; }
        QWidget { color: #26262D; font-family: 'Segoe UI', Sans-Serif; font-size: 14px; outline: none; }
        
        /* INPUT FIELDS */
        QLineEdit, QTextEdit, QPlainTextEdit { 
            background-color: #E8E6E1; 
            border: 1px solid #E8E6E1; 
            border-radius: 4px; 
            padding: 6px; 
            color: #26262D; 
            selection-background-color: #3EA8BF;
        }
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
            border: 1px solid #3EA8BF;
            background-color: #E8E6E1;
        }
        
        /* COMBO BOXES */
        QComboBox { 
            background-color: #E8E6E1; 
            border: 1px solid #E8E6E1; 
            border-radius: 4px; 
            padding: 6px; 
            color: #26262D;
        }
        QComboBox:hover { border: 1px solid #B4B1AA; }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView {
            background-color: #E8E6E1;
            border: 1px solid #E8E6E1;
            selection-background-color: #3EA8BF;
            selection-color: #000;
        }

        /* TABLES */
        QTableWidget { 
            background-color: #E8E6E1; 
            border: 1px solid #E8E6E1; 
            gridline-color: #E8E6E1;
            alternate-background-color: #E8E6E1;
        }
        QHeaderView::section { 
            background-color: #E8E6E1; 
            padding: 6px; 
            border: none; 
            border-bottom: 2px solid #E8E6E1; 
            font-weight: 600; 
            color: #2C2C34;
        }
        QTableWidget::item:selected { background-color: #3EA8BF; color: #000; }

        /* BUTTONS */
        QPushButton { 
            background-color: #3EA8BF; 
            color: white; 
            border: none; 
            padding: 8px 16px; 
            border-radius: 4px; 
            font-weight: 600; 
        }
        QPushButton:hover { background-color: #3EA8BF; margin-top: -1px; margin-bottom: 1px; }
        QPushButton:pressed { background-color: #3EA8BF; margin-top: 1px; margin-bottom: -1px; }

        /* SCROLLBARS */
        QScrollBar:vertical { border: none; background: #E8E6E1; width: 10px; margin: 0; }
        QScrollBar::handle:vertical { background: #B4B1AA; min-height: 30px; border-radius: 5px; }
        QScrollBar::handle:vertical:hover { background: #B4B1AA; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }

        /* TABS */
        QTabWidget::pane { border: 1px solid #E8E6E1; background-color: #E8E6E1; }
        QTabBar::tab { 
            background-color: #E8E6E1; 
            color: #87857F; 
            padding: 10px 20px; 
            margin-right: 2px; 
            border-top-left-radius: 4px; 
            border-top-right-radius: 4px; 
            font-weight: 500;
        }
        QTabBar::tab:selected { 
            background-color: #E8E6E1; 
            color: #26262D; 
            border-top: 3px solid #3EA8BF; 
        }

        /* SIDEBAR */
        QListWidget#MainSidebar {
            background-color: #E8E6E1;
            border: none;
            border-right: 1px solid #E8E6E1;
            outline: none;
            padding-top: 10px;
        }
        QListWidget#MainSidebar::item {
            color: #2C2C34;
            padding: 12px 20px;
            background: transparent;
            font-family: 'Segoe UI', sans-serif;
            font-size: 14px;
            height: 30px;
        }
        QListWidget#MainSidebar::item:hover { background-color: #E8E6E1; color: #000; }
        QListWidget#MainSidebar::item:selected {
            background-color: #E8E6E1;
            color: #3EA8BF;
            border-left: 3px solid #3EA8BF;
        }
        
        /* TOOLTIPS */
        QToolTip { background-color: #E8E6E1; color: #26262D; border: 1px solid #E8E6E1; }
    """

    # ── Slate MODE ─────────────────────────────────────────────────────────
    # Design Language: Apple × Teenage Engineering
    # Palette:
    #   Base:     #0D0D0F  (almost-black, warmer than pure black)
    #   Surface:  #0D0D0F  (card / panel surface)
    #   Raised:   #16161A  (inputs, elevated elements)
    #   Border:   #1D1D22  (hairline borders)
    #   Text:     #E8E6E1  (warm off-white — Apple-ish)
    #   Dim:      #87857F  (secondary text, disabled)
    #   Accent:   #D9A441  (Teenage Engineering signature orange)
    #   Confirm:  #5FBF8F  (success; Apple green)
    #   Danger:   #D9635F  (Apple red)
    # ─────────────────────────────────────────────────────────────────────────
    Slate_MODE = """
        /* ── RESET & BASE ──────────────────────────────────────────────── */
        QMainWindow, QDialog {
            background-color: #0D0D0F;
            color: #E8E6E1;
        }
        QWidget {
            background-color: transparent;
            color: #E8E6E1;
            font-family: 'SF Pro Text', 'Inter', 'Segoe UI', 'Helvetica Neue', sans-serif;
            font-size: 13px;
            outline: none;
        }
        QMainWindow > QWidget,
        QDialog > QWidget {
            background-color: #0D0D0F;
        }

        /* ── SIDEBAR (Navigation Rail) ──────────────────────────────────── */
        QListWidget#MainSidebar {
            background-color: #0D0D0F;
            border: none;
            border-right: 1px solid #1D1D22;
            outline: none;
            padding: 8px 0;
        }
        QListWidget#MainSidebar::item {
            color: #87857F;
            padding: 13px 20px;
            background: transparent;
            font-family: 'SF Pro Text', 'Inter', 'Segoe UI', sans-serif;
            font-size: 12px;
            font-weight: 500;
            letter-spacing: 0.2px;
            border-left: 2px solid transparent;
            min-height: 18px;
        }
        QListWidget#MainSidebar::item:hover {
            color: #B4B1AA;
            background-color: rgba(217, 164, 65, 0.06);
            border-left: 2px solid rgba(217, 164, 65, 0.3);
        }
        QListWidget#MainSidebar::item:selected {
            color: #D9A441;
            background-color: rgba(217, 164, 65, 0.10);
            border-left: 2px solid #D9A441;
            font-weight: 600;
        }

        /* ── GROUP BOXES (Cards) ────────────────────────────────────────── */
        QGroupBox {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 6px;
            margin-top: 20px;
            padding: 10px 12px 12px 12px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: #87857F;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1.2px;
            text-transform: uppercase;
        }

        /* ── INPUT FIELDS ───────────────────────────────────────────────── */
        QLineEdit, QSpinBox, QDoubleSpinBox {
            background-color: #16161A;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            padding: 7px 10px;
            color: #E8E6E1;
            font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Courier New', monospace;
            font-size: 12px;
            selection-background-color: #D9A441;
            selection-color: #0D0D0F;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #D9A441;
            background-color: #1D1D22;
        }
        QLineEdit:disabled {
            color: #2C2C34;
            border-color: #16161A;
        }
        QLineEdit[readOnly="true"] {
            color: #87857F;
            background-color: #0D0D0F;
        }

        /* Text Edit (log views, notes) */
        QTextEdit, QPlainTextEdit {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            padding: 8px;
            color: #B4B1AA;
            font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
            font-size: 11px;
            selection-background-color: rgba(217, 164, 65, 0.35);
            selection-color: #E8E6E1;
        }

        /* ── COMBO BOXES ────────────────────────────────────────────────── */
        QComboBox {
            background-color: #16161A;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            padding: 7px 10px;
            color: #E8E6E1;
            font-size: 12px;
            min-width: 80px;
        }
        QComboBox:hover { border-color: #26262D; }
        QComboBox:focus { border-color: #D9A441; }
        QComboBox::drop-down {
            border: none;
            width: 20px;
            subcontrol-origin: padding;
            subcontrol-position: right center;
        }
        QComboBox::down-arrow {
            width: 8px;
            height: 8px;
        }
        QComboBox QAbstractItemView {
            background-color: #16161A;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            padding: 4px;
            color: #E8E6E1;
            selection-background-color: rgba(217, 164, 65, 0.15);
            selection-color: #D9A441;
            outline: none;
        }
        QComboBox QAbstractItemView::item {
            padding: 8px 12px;
            min-height: 28px;
            border-radius: 3px;
        }

        /* ── BUTTONS ────────────────────────────────────────────────────── */
        QPushButton {
            background-color: #1D1D22;
            color: #B4B1AA;
            border: 1px solid #26262D;
            border-radius: 5px;
            padding: 8px 18px;
            font-size: 12px;
            font-weight: 500;
            letter-spacing: 0.2px;
            min-width: 70px;
        }
        QPushButton:hover {
            background-color: #1D1D22;
            border-color: #D9A441;
            color: #E8E6E1;
        }
        QPushButton:pressed {
            background-color: #16161A;
            border-color: #D9A441;
        }
        QPushButton:disabled {
            color: #2C2C34;
            background-color: #16161A;
            border-color: #16161A;
        }

        /* Primary Action Button */
        QPushButton#primaryButton {
            background-color: #D9A441;
            color: #0D0D0F;
            border: none;
            border-radius: 5px;
            font-weight: 700;
            font-size: 12px;
            padding: 9px 22px;
            letter-spacing: 0.4px;
        }
        QPushButton#primaryButton:hover {
            background-color: #D9A441;
        }
        QPushButton#primaryButton:pressed {
            background-color: #D9A441;
        }
        QPushButton#primaryButton:disabled {
            background-color: #3A2F19;
            color: #3A2F19;
        }

        /* Secondary (ghost) Button */
        QPushButton#secondaryButton {
            background-color: transparent;
            color: #87857F;
            border: 1px solid #1D1D22;
            border-radius: 5px;
            padding: 8px 18px;
        }
        QPushButton#secondaryButton:hover {
            border-color: #2C2C34;
            color: #B4B1AA;
        }

        /* Danger Button */
        QPushButton#dangerButton {
            background-color: #3A1F1E;
            color: #D9635F;
            border: 1px solid #3A1F1E;
            border-radius: 5px;
            padding: 8px 18px;
            font-weight: 600;
        }
        QPushButton#dangerButton:hover {
            background-color: #D9635F;
            color: #E8E6E1;
            border-color: #D9635F;
        }

        /* ── CHECKBOXES & RADIO BUTTONS ─────────────────────────────────── */
        QCheckBox, QRadioButton {
            color: #B4B1AA;
            font-size: 12px;
            spacing: 8px;
        }
        QCheckBox:hover, QRadioButton:hover { color: #E8E6E1; }
        QCheckBox::indicator {
            width: 14px;
            height: 14px;
            border: 1px solid #26262D;
            border-radius: 3px;
            background-color: #16161A;
        }
        QCheckBox::indicator:checked {
            background-color: #D9A441;
            border-color: #D9A441;
        }
        QRadioButton::indicator {
            width: 13px;
            height: 13px;
            border: 1px solid #26262D;
            border-radius: 6px;
            background-color: #16161A;
        }
        QRadioButton::indicator:checked {
            background-color: #D9A441;
            border-color: #D9A441;
        }

        /* ── SLIDERS ────────────────────────────────────────────────────── */
        QSlider::groove:horizontal {
            height: 3px;
            background: #1D1D22;
            border-radius: 2px;
        }
        QSlider::sub-page:horizontal {
            background: #D9A441;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #E8E6E1;
            border: none;
            width: 12px;
            height: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }
        QSlider::handle:horizontal:hover {
            background: #D9A441;
        }

        /* ── TABLES ─────────────────────────────────────────────────────── */
        QTableWidget {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            gridline-color: #16161A;
            alternate-background-color: #0D0D0F;
            color: #B4B1AA;
            font-size: 12px;
            selection-background-color: rgba(217, 164, 65, 0.12);
        }
        QHeaderView::section {
            background-color: #0D0D0F;
            color: #87857F;
            padding: 8px 10px;
            border: none;
            border-right: 1px solid #1D1D22;
            border-bottom: 1px solid #1D1D22;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 1px;
            text-transform: uppercase;
        }
        QHeaderView::section:hover {
            color: #D9A441;
        }
        QTableWidget::item {
            border: none;
            padding: 6px 10px;
            outline: none;
        }
        QTableWidget::item:selected {
            background-color: rgba(217, 164, 65, 0.12);
            color: #D9A441;
            border: none;
            outline: none;
        }
        QTableWidget::item:focus {
            border: none;
            outline: none;
        }

        /* ── TREE WIDGET ────────────────────────────────────────────────── */
        QTreeWidget {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            color: #B4B1AA;
            alternate-background-color: transparent;
            selection-background-color: rgba(217, 164, 65, 0.10);
        }
        QTreeWidget::item { padding: 5px 6px; border: none; }
        QTreeWidget::item:selected { color: #D9A441; }
        QTreeWidget::item:hover { background-color: rgba(217, 164, 65, 0.06); }
        QTreeWidget::branch:has-children:closed { color: #87857F; }
        QTreeWidget::branch:has-children:open { color: #D9A441; }

        /* ── LIST WIDGET (generic, not sidebar) ─────────────────────────── */
        QListWidget:not(#MainSidebar) {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 4px;
            color: #B4B1AA;
            outline: none;
        }
        QListWidget:not(#MainSidebar)::item {
            padding: 6px 10px;
        }
        QListWidget:not(#MainSidebar)::item:selected {
            background-color: rgba(217, 164, 65, 0.12);
            color: #D9A441;
        }
        QListWidget:not(#MainSidebar)::item:hover {
            background-color: rgba(217, 164, 65, 0.06);
        }

        /* ── TABS ───────────────────────────────────────────────────────── */
        QTabWidget::pane {
            border: 1px solid #1D1D22;
            background-color: #0D0D0F;
            border-radius: 0 4px 4px 4px;
        }
        QTabBar::tab {
            background-color: transparent;
            color: #2C2C34;
            padding: 9px 20px;
            margin-right: 1px;
            font-size: 11px;
            font-weight: 500;
            letter-spacing: 0.3px;
            border-bottom: 2px solid transparent;
        }
        QTabBar::tab:hover {
            color: #B4B1AA;
        }
        QTabBar::tab:selected {
            color: #E8E6E1;
            border-bottom: 2px solid #D9A441;
            font-weight: 600;
        }

        /* ── PROGRESS BAR ───────────────────────────────────────────────── */
        QProgressBar {
            background-color: #16161A;
            border: none;
            border-radius: 3px;
            height: 4px;
            color: transparent;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #D9A441;
            border-radius: 3px;
        }

        /* ── SCROLL BARS ────────────────────────────────────────────────── */
        QScrollBar:vertical {
            border: none;
            background: transparent;
            width: 6px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #26262D;
            min-height: 40px;
            border-radius: 3px;
        }
        QScrollBar::handle:vertical:hover { background: #2C2C34; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal {
            border: none;
            background: transparent;
            height: 6px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #26262D;
            min-width: 40px;
            border-radius: 3px;
        }
        QScrollBar::handle:horizontal:hover { background: #2C2C34; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

        /* ── SPLITTER ───────────────────────────────────────────────────── */
        QSplitter::handle {
            background-color: #1D1D22;
        }
        QSplitter::handle:hover {
            background-color: #D9A441;
        }
        QSplitter::handle:horizontal { width: 1px; }
        QSplitter::handle:vertical { height: 1px; }

        /* ── MENU BAR ───────────────────────────────────────────────────── */
        QMenuBar {
            background-color: #0D0D0F;
            color: #87857F;
            border-bottom: 1px solid #1D1D22;
            padding: 2px;
            font-size: 12px;
        }
        QMenuBar::item:selected {
            background-color: rgba(217, 164, 65, 0.12);
            color: #E8E6E1;
            border-radius: 3px;
        }
        QMenu {
            background-color: #16161A;
            border: 1px solid #1D1D22;
            border-radius: 6px;
            padding: 4px;
            color: #E8E6E1;
        }
        QMenu::item {
            padding: 8px 20px 8px 12px;
            border-radius: 3px;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: rgba(217, 164, 65, 0.14);
            color: #D9A441;
        }
        QMenu::separator {
            height: 1px;
            background: #1D1D22;
            margin: 4px 8px;
        }

        /* ── STATUS BAR ─────────────────────────────────────────────────── */
        QStatusBar {
            background-color: #0D0D0F;
            border-top: 1px solid #1D1D22;
            color: #2C2C34;
            font-size: 11px;
            font-family: 'SF Mono', 'Consolas', monospace;
        }

        /* ── TOOLBAR ────────────────────────────────────────────────────── */
        QToolBar {
            background-color: #0D0D0F;
            border-bottom: 1px solid #1D1D22;
            spacing: 4px;
            padding: 4px 8px;
        }
        QToolButton {
            background: transparent;
            border: none;
            color: #87857F;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 12px;
        }
        QToolButton:hover {
            background-color: rgba(217, 164, 65, 0.08);
            color: #E8E6E1;
        }
        QToolButton:pressed {
            background-color: rgba(217, 164, 65, 0.18);
            color: #D9A441;
        }

        /* ── LABELS ─────────────────────────────────────────────────────── */
        QLabel {
            color: #B4B1AA;
            background: transparent;
            font-size: 12px;
        }
        QLabel[heading="true"] {
            color: #E8E6E1;
            font-size: 14px;
            font-weight: 600;
        }

        /* ── TOOLTIPS ───────────────────────────────────────────────────── */
        QToolTip {
            background-color: #16323A;
            color: #B4B1AA;
            border: 1px solid #16323A;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 11px;
            font-family: 'SF Pro Text', 'Inter', 'Segoe UI', sans-serif;
        }

        /* ── FRAMES ─────────────────────────────────────────────────────── */
        QFrame[frameShape="4"],
        QFrame[frameShape="5"] {
            color: #1D1D22;
        }

        /* ── DIALOG BUTTONS ─────────────────────────────────────────────── */
        QDialogButtonBox QPushButton {
            min-width: 80px;
        }

        /* ── MESSAGE BOX ────────────────────────────────────────────────── */
        QMessageBox {
            background-color: #0D0D0F;
            border: 1px solid #1D1D22;
            border-radius: 8px;
        }
        QMessageBox QLabel {
            color: #E8E6E1;
            font-size: 13px;
        }

        /* ── SCROLL AREA ────────────────────────────────────────────────── */
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QScrollArea > QWidget > QWidget {
            background-color: transparent;
        }
    """


    # ---------------------------------------------------------------- tokens
    #
    # main.qss written against gate.py is the single source of colour. It used
    # to be installed by the launcher and then thrown away here, so a fix made
    # in it never reached the client. The four stylesheets below are kept only
    # for Light, which has no token sheet yet.

    @staticmethod
    def _token_stylesheet():
        """main.qss with @TOKENs resolved, or "" if it cannot be built."""
        try:
            from .gate import Gate
        except Exception:
            return ""

        here = os.path.dirname(os.path.abspath(__file__))
        package = os.path.dirname(os.path.dirname(here))       # -> slate/
        qss = os.path.join(package, "resources", "styles", "main.qss")
        if not os.path.exists(qss):
            return ""
        try:
            with open(qss, "r", encoding="utf-8") as handle:
                sheet = handle.read()
        except OSError:
            return ""

        # The sheet reaches a couple of icons through url(). They are written
        # to disk by the GUI layer, which owns the icon set - core does not
        # reach up to fetch them. If they are not there, the rules that use
        # them are dropped and Qt falls back to its own drawing.
        icons = os.path.join(package, "resources", "icons")
        if os.path.exists(os.path.join(icons, "chevron-down.svg")):
            sheet = sheet.replace("@ICONS", icons.replace("\\", "/"))
        else:
            sheet = re.sub(r"\s*image:\s*url\(@ICONS[^)]*\);", "", sheet)

        tokens = {
            "@BG_MAIN": Gate.GROUND, "@BG_PANEL": Gate.PANEL,
            "@BG_ELEVATED": Gate.RAISED, "@ACCENT_HOVER": Gate.ACCENT_HI,
            "@ACCENT": Gate.ACCENT, "@TEXT_PRIMARY": Gate.TEXT,
            "@TEXT_MUTED": Gate.TEXT_DIM, "@BORDER_FOCUS": Gate.ACCENT,
            "@BORDER": Gate.LINE, "@DANGER": Gate.BAD, "@SUCCESS": Gate.OK,
            "@WARNING": Gate.WARN,
            "@RADIUS_SM": "%dpx" % Gate.RADIUS_SM,
            "@RADIUS_MD": "%dpx" % Gate.RADIUS_MD,
            "@RADIUS_LG": "%dpx" % Gate.RADIUS_LG,
            "@FONT_UI": Gate.FONT_UI, "@FONT_LABEL": Gate.FONT_LABEL,
            "@FONT_MONO": Gate.FONT_MONO,
        }
        # Longest first: @BORDER before @BORDER_FOCUS would leave "_FOCUS".
        for name, value in sorted(tokens.items(), key=lambda kv: -len(kv[0])):
            sheet = sheet.replace(name, str(value))
        return sheet


    @staticmethod
    def get_available_themes():
        """Compatibility API for modules that cycle themes dynamically."""
        return ["Dark", "Slate", "Light"]

    @staticmethod
    def get_current_theme():
        return GlobalConfig.get("THEME_MODE", "Slate")

    @staticmethod
    def _with_adaptive_scale(base_stylesheet: str) -> str:
        """Append adaptation-engine font sizing when applying theme."""
        try:
            dams = system_engine.generate_stylesheet_dams()
            return f"{base_stylesheet}\n\nQWidget {{ font-size: {dams['font_size_main']}; }}"
        except Exception:
            return base_stylesheet

    @staticmethod
    def apply_theme(mode):
        """
        mode: 'Dark', 'Slate', or 'Light'
        """
        app = QApplication.instance()
        if not app:
            return

        from PySide6.QtGui import QPalette, QColor

        token_sheet = ThemeManager._token_stylesheet()

        if mode == "Light":
            app.setStyleSheet(ThemeManager._with_adaptive_scale(ThemeManager.LIGHT_MODE))
            GlobalConfig.set("THEME_MODE", "Light")

        elif mode == "Dark":
            app.setStyleSheet(ThemeManager._with_adaptive_scale(
                token_sheet or ThemeManager.DARK_MODE))
            GlobalConfig.set("THEME_MODE", "Dark")
            palette = QPalette()
            palette.setColor(QPalette.Window, QColor(18, 18, 18))
            palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
            palette.setColor(QPalette.Base, QColor(18, 18, 18))
            palette.setColor(QPalette.AlternateBase, QColor(30, 30, 30))
            palette.setColor(QPalette.Text, QColor(224, 224, 224))
            palette.setColor(QPalette.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))
            palette.setColor(QPalette.Highlight, QColor(0, 122, 204))
            palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
            app.setPalette(palette)

        else:
            # Default: Slate Mode (Apple × Teenage Engineering)
            app.setStyleSheet(ThemeManager._with_adaptive_scale(
                token_sheet or ThemeManager.Slate_MODE))
            GlobalConfig.set("THEME_MODE", "Slate")

            # Force palette to match — prevents white gaps on HiDPI
            bg = QColor(10, 10, 12)       # #0D0D0F
            surface = QColor(16, 16, 20)  # #0D0D0F
            text = QColor(230, 228, 220)  # #E8E6E1 — warm off-white
            accent = QColor(255, 107, 0)  # #D9A441 — TE orange

            palette = QPalette()
            palette.setColor(QPalette.Window, bg)
            palette.setColor(QPalette.WindowText, text)
            palette.setColor(QPalette.Base, surface)
            palette.setColor(QPalette.AlternateBase, QColor(22, 22, 28))
            palette.setColor(QPalette.ToolTipBase, QColor(20, 20, 24))
            palette.setColor(QPalette.ToolTipText, text)
            palette.setColor(QPalette.Text, text)
            palette.setColor(QPalette.Button, surface)
            palette.setColor(QPalette.ButtonText, text)
            palette.setColor(QPalette.BrightText, QColor(255, 255, 255))
            palette.setColor(QPalette.Link, accent)
            palette.setColor(QPalette.Highlight, accent)
            palette.setColor(QPalette.HighlightedText, QColor(10, 10, 12))
            app.setPalette(palette)

    @staticmethod
    def toggle_mode():
        order = ["Slate", "Dark", "Light"]
        current = ThemeManager.get_current_theme()
        idx = order.index(current) if current in order else 0
        new_mode = order[(idx + 1) % len(order)]
        ThemeManager.apply_theme(new_mode)

    @staticmethod
    def is_dark_mode():
        return GlobalConfig.get("THEME_MODE", "Slate") in ("Dark", "Slate")

    @staticmethod
    def apply_saved_theme():
        saved = GlobalConfig.get("THEME_MODE", "Slate")
        ThemeManager.apply_theme(saved)
