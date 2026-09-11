"""
VFX Dashboard Pro - QSS Generator
Generates QSS stylesheet dynamically from design tokens for single source of truth.
"""

from .....core.infra.design_tokens import ColorTokens as C, TypographyTokens as T, SpacingTokens as S, RadiusTokens as R


def generate_dashboard_qss() -> str:
    """
    Generate complete QSS stylesheet from design tokens.
    This replaces the static styles.qss file with a dynamically generated version.
    
    Returns:
        str: Complete QSS stylesheet using design tokens
    """
    return f"""
/* === GLOBAL THEME === */
/* Generated from Design Tokens - Single Source of Truth */

QMainWindow, QWidget {{
    background-color: {C.BG_SURFACE};
    color: {C.TEXT_PRIMARY};
    font-family: "{T.FONT_FAMILY}";
}}

/* === APP BAR === */
#appBar {{
    background-color: {C.BG_ELEVATED};
    border-bottom: 1px solid {C.BORDER_DEFAULT};
}}

/* === BUTTONS === */
/* Base Button */
QPushButton {{
    background-color: {C.BG_HOVER};
    color: {C.TEXT_PRIMARY};
    border: 1px solid {C.BORDER_LIGHT};
    padding: {S.SM}px {S.LG}px;
    border-radius: {R.SM}px;
    font-size: {T.SIZE_SM}px;
    font-weight: {T.WEIGHT_MEDIUM};
}}

QPushButton:hover {{
    background-color: {C.BORDER_LIGHT};
    border-color: {C.ACCENT_PRIMARY};
}}

QPushButton:pressed {{
    background-color: {C.ACCENT_PRIMARY};
    border-color: {C.ACCENT_PRIMARY};
}}

QPushButton:disabled {{
    background-color: {C.BG_ELEVATED};
    color: {C.TEXT_MUTED};
    border-color: {C.BORDER_DEFAULT};
}}

/* Primary Action (Save, etc.) */
#headerBtnPrimary, #saveBtn {{
    background-color: {C.ACCENT_DARK};
    border: 1px solid {C.ACCENT_DARK};
    font-weight: {T.WEIGHT_STYLE_BOLD};
}}

#headerBtnPrimary:hover, #saveBtn:hover {{
    background-color: {C.ACCENT_PRIMARY};
    border-color: {C.ACCENT_PRIMARY};
    color: white;
}}

/* Header Specific Buttons (Set Root, Search) */
#headerBtn {{
    background-color: {C.BORDER_DEFAULT};
    border: 1px solid {C.BORDER_LIGHT};
    color: {C.TEXT_PRIMARY};
}}

#headerBtn:hover {{
    background-color: {C.BORDER_LIGHT};
    border-color: {C.ACCENT_PRIMARY};
    color: white;
}}

/* Close / Destructive */
#closeBtn, #logoutBtn {{
    background-color: transparent;
    border: none;
    color: {C.TEXT_GRAY_LIGHT};
    font-weight: {T.WEIGHT_STYLE_BOLD};
    border-radius: {R.SM}px;
}}

#closeBtn:hover, #logoutBtn:hover {{
    background-color: {C.ERROR};
    color: white;
}}

/* Quick Filter Pills */
#folderBtn {{
    background-color: {C.BG_ELEVATED};
    border: 1px solid {C.BORDER_DEFAULT};
    border-radius: {R.LG}px;
    padding: {S.XS}px {S.MD}px;
    color: {C.TEXT_SECONDARY};
    font-size: {T.SIZE_XS}px;
}}

#folderBtn:hover {{
    background-color: {C.BG_HOVER};
    border-color: {C.ACCENT_PRIMARY};
    color: {C.ACCENT_PRIMARY};
}}

#folderBtn:checked {{
    background-color: {C.ACCENT_PRIMARY};
    color: white;
    border-color: {C.ACCENT_PRIMARY};
}}

/* === INPUTS === */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: rgba(0, 0, 0, 0.2);
    border: 1px solid {C.BORDER_DEFAULT};
    border-radius: {R.SM}px;
    padding: {S.SM}px;
    color: {C.TEXT_PRIMARY};
    font-size: {T.SIZE_SM}px;
    selection-background-color: {C.ACCENT_BLUE};
}}

QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {C.ACCENT_PRIMARY};
    background-color: {C.BG_SURFACE};
}}

/* === COMBOBOX === */
QComboBox {{
    background-color: {C.BG_HOVER};
    border: 1px solid {C.BORDER_DEFAULT};
    border-radius: {R.SM}px;
    padding: {S.SM}px;
    color: {C.TEXT_PRIMARY};
    font-size: {T.SIZE_SM}px;
    min-width: 60px;
}}

QComboBox:hover {{
    border-color: {C.BORDER_LIGHT};
}}

QComboBox:on {{
    border-color: {C.ACCENT_PRIMARY};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left-width: 0px;
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid {C.TEXT_GRAY_LIGHT};
    margin-right: 8px;
}}

QComboBox::down-arrow:on {{
    border-top-color: {C.ACCENT_PRIMARY};
}}

QComboBox QAbstractItemView {{
    background-color: {C.BG_ELEVATED};
    border: 1px solid {C.BORDER_LIGHT};
    selection-background-color: {C.ACCENT_BLUE};
    color: {C.TEXT_PRIMARY};
    outline: none;
}}

/* === TABLE VIEW === */
QTableView {{
    background-color: {C.BG_SURFACE};
    alternate-background-color: {C.BG_HOVER};
    gridline-color: {C.BORDER_DEFAULT};
    selection-background-color: {C.ACCENT_DARK};
    selection-color: white;
    border: none;
    font-size: {T.SIZE_MD}px;
}}

QHeaderView::section {{
    background-color: {C.BG_ELEVATED};
    color: {C.TEXT_SECONDARY};
    padding: {S.XS}px;
    border: none;
    border-right: 1px solid {C.BORDER_DEFAULT};
    border-bottom: 2px solid {C.BORDER_LIGHT};
    font-weight: {T.WEIGHT_SEMIBOLD};
    font-size: {T.SIZE_XS}px;
    text-transform: uppercase;
}}

QHeaderView::section:hover {{
    background-color: {C.BG_HOVER};
}}

QTableView::item {{
    min-height: 32px;
    padding: 2px;
}}

/* === SCROLLBARS === */
QScrollBar:vertical {{
    border: none;
    background: {C.BG_SURFACE};
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: {C.BORDER_LIGHT};
    min-height: 20px;
    border-radius: 6px;
    border: 2px solid {C.BG_SURFACE};
}}

QScrollBar::handle:vertical:hover {{
    background-color: {C.BORDER_LIGHT};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* === DETAIL PANEL HEADERS (Flat Style) === */
QGroupBox {{
    border: none;
    border-top: 1px solid {C.BORDER_LIGHT};
    margin-top: 24px;
    padding-top: 16px;
    font-weight: {T.WEIGHT_STYLE_BOLD};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 {S.XS}px;
    left: 0px;
    top: 0px;
    color: {C.ACCENT_PRIMARY};
    background-color: transparent;
    font-size: {T.SIZE_MD}px;
    text-transform: uppercase;
    font-weight: {T.WEIGHT_STYLE_BOLD};
}}

#detailHeader, #detailFooter {{
    background-color: {C.BG_ELEVATED};
    border-color: {C.BORDER_DEFAULT};
}}

#detailContent {{
    background-color: {C.BG_ELEVATED};
}}

#thumbnail {{
    background-color: {C.BG_DARKER};
    border: 1px solid {C.BORDER_LIGHT};
    border-radius: {R.SM}px;
}}

#detailTitle {{
    color: {C.TEXT_PRIMARY};
    font-size: {T.SIZE_3XL}px;
    font-weight: normal;
}}

#versionLabelPrev, #dateLabel {{
    color: {C.TEXT_MUTED};
    font-size: {T.SIZE_SM}px;
}}

#gridHeader {{
    color: {C.ACCENT_PRIMARY};
    font-weight: {T.WEIGHT_STYLE_BOLD};
    font-size: {T.SIZE_XS}px;
    border-bottom: 2px solid {C.BORDER_DEFAULT};
    margin-bottom: {S.SM}px;
}}

#deptName {{
    color: {C.TEXT_SECONDARY};
    font-size: {T.SIZE_MD}px;
    font-weight: {T.WEIGHT_MEDIUM};
}}

/* === FEEDBACK HEADERS === */
#feedbackLabel {{
    color: {C.ACCENT_PRIMARY};
    font-size: {T.SIZE_SM}px;
    font-weight: {T.WEIGHT_STYLE_BOLD};
    text-transform: uppercase;
    margin-bottom: {S.XS}px;
}}

/* === BADGES === */
#statusBadge {{
    padding: 2px {S.MD}px;
    border-radius: {R.MD}px;
    font-weight: {T.WEIGHT_STYLE_BOLD};
}}

/* === CHECKBOX & RADIO === */
QCheckBox, QRadioButton {{
    spacing: {S.SM}px;
    color: {C.TEXT_PRIMARY};
    font-size: {T.SIZE_SM}px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    background-color: {C.BG_HOVER};
    border: 1px solid {C.BORDER_LIGHT};
    border-radius: 3px;
}}

QCheckBox::indicator:hover {{
    border-color: {C.ACCENT_PRIMARY};
}}

QCheckBox::indicator:checked {{
    background-color: {C.ACCENT_PRIMARY};
    border-color: {C.ACCENT_PRIMARY};
    image: url(none);
}}

/* Fix specific widgets from main window */
#headerCombo, #headerInput {{
    background-color: {C.BG_ELEVATED};
    border: 1px solid {C.BORDER_LIGHT};
}}

/* === PROJECT SELECTOR === */
#projectCombo {{
    background-color: {C.BG_DARKER};
    border: 1px solid {C.ACCENT_PRIMARY};
    border-radius: {R.SM}px;
    color: {C.TEXT_PRIMARY};
    font-weight: {T.WEIGHT_STYLE_BOLD};
    padding: {S.SM}px {S.MD}px;
    min-width: 160px;
}}

#projectCombo:hover {{
    background-color: {C.BG_SURFACE};
    border-color: {C.ACCENT_INFO};
}}

#projectCombo::drop-down {{
    border: none;
    background: transparent;
    width: 30px;
}}

#projectCombo::down-arrow {{
    image: none;
    border: none;
    border-top: 6px solid {C.ACCENT_PRIMARY};
    border-left: 6px solid transparent;
    border-right: 6px solid transparent;
    margin-right: {S.MD}px;
}}

#projectCombo QAbstractItemView {{
    background-color: {C.BG_SURFACE};
    border: 1px solid {C.ACCENT_PRIMARY};
    selection-background-color: {C.ACCENT_PRIMARY};
    selection-color: black;
}}

/* === DELETE BUTTON === */
#deleteBtn {{
    background-color: {C.ERROR_DIM};
    border: 1px solid {C.ERROR};
    color: {C.ERROR_LIGHT};
    font-weight: {T.WEIGHT_STYLE_BOLD};
}}

#deleteBtn:hover {{
    background-color: {C.ERROR};
    border-color: {C.ERROR};
    color: {C.TEXT_PRIMARY};
}}
"""
