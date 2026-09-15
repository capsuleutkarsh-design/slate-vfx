"""
Design System for Slate Central Server.
Contains Color Tokens, Typography, and shared styling for the Server Control Panel.
Premium deep dark mode with glassmorphism aesthetics.
"""

class C:
    """Color Tokens"""
    # Backgrounds
    BG_ROOT = "#0D0D0F"          # Deepest background (Root window)
    BG_SURFACE = "#16161A"       # Main surface color (Cards, Panels)
    BG_SURFACE_HOVER = "#1D1D22" # Interactive surface hover
    
    # Borders
    BORDER_DEFAULT = "#16323A"   # Subtle borders for cards
    BORDER_FOCUS = "#6BA4C9"     # Active borders
    
    # Text
    TEXT_PRIMARY = "#E8E6E1"     # High contrast headers and values
    TEXT_SECONDARY = "#B4B1AA"   # Subtitles, labels, disabled text
    
    # Accents & States
    ACCENT_PRIMARY = "#3EA8BF"   # Deep iOS Blue for active generic states
    STATUS_OK = "#5FBF8F"        # Emerald Green (Server Running)
    STATUS_ERROR = "#D9635F"     # Crimson Red (Server Stopped/Error)
    STATUS_WARNING = "#D9A441"   # Amber (Starting/Stopping)

class T:
    """Typography Tokens"""
    FAMILY = "Segoe UI, -apple-system, sans-serif"
    
    # Weights
    WEIGHT_NORMAL = "400"
    WEIGHT_SEMI = "600"
    WEIGHT_BOLD = "700"

# Global Stylesheet
GLOBAL_STYLESHEET = f"""
    QWidget {{
        font-family: {T.FAMILY};
        color: {C.TEXT_PRIMARY};
    }}
    
    QMainWindow {{
        background-color: {C.BG_ROOT};
    }}

    /* Dialogs. The QWidget rule above turns every dialog's text off-white,
       and without these the dialog itself kept the system's white background
       - so the "Where is the studio's database?" question opened as a white
       box with unreadable text. Everything a pop-up is made of is painted
       here: the box, its labels, its buttons, its text fields. */
    QDialog, QMessageBox, QInputDialog {{
        background-color: {C.BG_SURFACE};
        color: {C.TEXT_PRIMARY};
    }}
    QDialog QLabel, QMessageBox QLabel {{
        color: {C.TEXT_PRIMARY};
        background: transparent;
    }}
    /* A message box sizes itself to its text and then squeezes the button
       row into that width, so a box with two long buttons clipped both of
       them to "existing database" and "a new empty databa". */
    QMessageBox QLabel#qt_msgbox_label {{
        min-width: 460px;
    }}
    QDialog QPushButton, QMessageBox QPushButton {{
        background-color: {C.BG_SURFACE_HOVER};
        color: {C.TEXT_PRIMARY};
        border: 1px solid {C.BORDER_DEFAULT};
        border-radius: 4px;
        padding: 6px 14px;
        min-width: 80px;
    }}
    QDialog QPushButton:hover, QMessageBox QPushButton:hover {{
        border-color: {C.BORDER_FOCUS};
    }}
    /* No bold here: Qt sizes the button for the regular weight and then
       draws it bold, which clipped the first and last letters of a long
       label. The accent fill marks the default button on its own. */
    QDialog QPushButton:default, QMessageBox QPushButton:default {{
        background-color: {C.ACCENT_PRIMARY};
        color: #07171B;
        border-color: {C.ACCENT_PRIMARY};
    }}
    QDialog QLineEdit, QDialog QTextEdit, QDialog QPlainTextEdit {{
        background-color: {C.BG_ROOT};
        color: {C.TEXT_PRIMARY};
        border: 1px solid {C.BORDER_DEFAULT};
        border-radius: 4px;
        padding: 4px 6px;
    }}
    QToolTip {{
        background-color: {C.BG_SURFACE_HOVER};
        color: {C.TEXT_PRIMARY};
        border: 1px solid {C.BORDER_DEFAULT};
        padding: 4px;
    }}

    /* Custom Scrollbars */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {C.BORDER_DEFAULT};
        min-height: 20px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {C.BORDER_FOCUS};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}
"""
