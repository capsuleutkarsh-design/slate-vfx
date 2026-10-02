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

def _arrow_icons() -> str:
    """
    Write the spin-box chevrons the stylesheet below uses, and return their
    folder ("" if they cannot be written).

    A spin box given its own background loses Qt's native arrows, and the
    border-triangle trick used for them before does not render in Qt - so the
    Keep fields showed two grey bars. The chevrons are the same paths as the
    client's icon set (slate/gui/core/icons.py); they are written here rather
    than imported because importing the client's GUI package pulls in its
    whole main window.
    """
    import os
    import tempfile
    paths = {"chevron-up": "M6 14.5 12 8.5 18 14.5", "chevron-down": "M6 9.5 12 15.5 18 9.5"}
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    folder = os.path.join(base, "Slate", "cache", "server_theme")
    try:
        os.makedirs(folder, exist_ok=True)
        for name, path in paths.items():
            with open(os.path.join(folder, name + ".svg"), "w", encoding="utf-8") as handle:
                handle.write(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
                    'fill="none" stroke="%s" stroke-width="2.2" stroke-linecap="round" '
                    'stroke-linejoin="round"><path d="%s"/></svg>' % (C.TEXT_SECONDARY, path))
        return folder.replace(os.sep, "/")
    except OSError:
        return ""


_ICONS = _arrow_icons()
_SPIN_ARROWS = f"""
    QAbstractSpinBox {{{{
        padding-right: 22px;
    }}}}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{{{
        subcontrol-origin: border;
        width: 18px;
        border: none;
        border-left: 1px solid {C.BORDER_DEFAULT};
        background: transparent;
    }}}}
    QAbstractSpinBox::up-button {{{{ subcontrol-position: top right; }}}}
    QAbstractSpinBox::down-button {{{{ subcontrol-position: bottom right; }}}}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{{{
        background: {C.BG_SURFACE_HOVER};
    }}}}
    QAbstractSpinBox::up-arrow {{{{ image: url({_ICONS}/chevron-up.svg); width: 9px; height: 9px; }}}}
    QAbstractSpinBox::down-arrow {{{{ image: url({_ICONS}/chevron-down.svg); width: 9px; height: 9px; }}}}
""" if _ICONS else ""

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
""" + _SPIN_ARROWS.replace("{{", "{").replace("}}", "}")
