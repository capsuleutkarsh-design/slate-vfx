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
