"""
Stylesheet Builder - Generate Qt stylesheets from design tokens

This module provides helper functions to build consistent Qt stylesheets
using the centralized design tokens. Instead of writing inline stylesheets
with hardcoded values, use these builders for consistency.

Usage:
    from ut_vfx.core.infra.style_builder import StyleBuilder
    button.setStyleSheet(StyleBuilder.primary_button())
"""

from .design_tokens import ColorTokens as C, SpacingTokens as S, TypographyTokens as T, RadiusTokens as R


class StyleBuilder:
    """Helper class to build consistent Qt stylesheets using design tokens"""
    
    # === CONTAINERS ===
    
    @staticmethod
    def card(background=C.BG_SURFACE, border=C.BORDER_DEFAULT, radius=R.LG, hover_border=None):
        """
        Standard card/panel style.
        
        Args:
            background: Background color (default: BG_SURFACE)
            border: Border color (default: BORDER_DEFAULT)
            radius: Border radius in px (default: R.LG)
            hover_border: Optional hover border color
            
        Returns:
            Qt stylesheet string
        """
        style = f"""
            QFrame {{
                background-color: {background};
                border: 1px solid {border};
                border-radius: {radius}px;
            }}
            QLabel {{
                border: none;
            }}
        """
        
        if hover_border:
            style += f"""
            QFrame:hover {{
                border-color: {hover_border};
            }}
            """
        
        return style
    
    @staticmethod
    def sidebar():
        """
        Sidebar list widget style (used in Admin Panel).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QListWidget {{ 
                background-color: {C.BG_SIDEBAR}; 
                border: none; 
                outline: none; 
                padding-top: {S.MD}px; 
            }} 
            QListWidget::item {{ 
                color: {C.TEXT_GRAY_LIGHT}; 
                padding: {S.MD}px {S.XL}px; 
                margin: {S.XS}px {S.MD}px; 
                border-radius: {R.MD}px; 
                font-size: {T.SIZE_MD}px; 
                font-weight: {T.WEIGHT_SEMIBOLD}; 
                border: 1px solid transparent;
            }} 
            QListWidget::item:hover {{ 
                background-color: {C.BG_HOVER}; 
                color: {C.TEXT_WHITE}; 
                border: 1px solid {C.BORDER_DEFAULT};
            }} 
            QListWidget::item:selected {{ 
                background-color: {C.BG_SURFACE}; 
                color: {C.ACCENT_PRIMARY}; 
                border: 1px solid {C.ACCENT_PRIMARY}; 
                font-weight: {T.WEIGHT_BOLD};
            }}
        """
    
    # === BUTTONS ===
    
    @staticmethod
    def primary_button():
        """
        Primary action button (cyan background).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: {C.TEXT_INVERSE};
                border: none;
                border-radius: {R.RADIUS_BUTTON}px;
                padding: {S.PADDING_BUTTON};
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {C.ACCENT_PRESSED};
            }}
            QPushButton:disabled {{
                background-color: {C.BG_ELEVATED};
                color: {C.TEXT_DISABLED};
            }}
        """
    
    @staticmethod
    def danger_button():
        """
        Destructive action button (red background).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QPushButton {{
                background-color: {C.ERROR};
                color: {C.TEXT_WHITE};
                border: none;
                border-radius: {R.RADIUS_BUTTON}px;
                padding: {S.PADDING_BUTTON};
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.ERROR_BRIGHT};
            }}
        """
    
    @staticmethod
    def success_button():
        """
        Success/approve button (green background).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QPushButton {{
                background-color: {C.SUCCESS};
                color: {C.TEXT_WHITE};
                border: none;
                border-radius: {R.RADIUS_BUTTON}px;
                padding: {S.SM}px;
            }}
            QPushButton:hover {{
                background-color: {C.SUCCESS_HOVER};
            }}
        """
    
    @staticmethod
    def reject_button():
        """
        Reject button (dim red background - specific to Shot Review).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QPushButton {{
                background-color: {C.ERROR_DIM};
                color: {C.TEXT_WHITE};
                border: none;
                border-radius: {R.RADIUS_BUTTON}px;
                padding: {S.SM}px;
            }}
            QPushButton:hover {{
                background-color: {C.ERROR};
            }}
        """
    
    @staticmethod
    def secondary_button():
        """
        Secondary action button (transparent with border).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {C.ACCENT_PRIMARY};
                border: 1px solid {C.ACCENT_PRIMARY};
                border-radius: {R.RADIUS_BUTTON}px;
                padding: {S.PADDING_BUTTON};
                font-weight: {T.WEIGHT_SEMIBOLD};
            }}
            QPushButton:hover {{
                background-color: {C.ACCENT_PRIMARY};
                color: {C.TEXT_INVERSE};
            }}
        """
    
    # === INPUTS ===
    
    @staticmethod
    def input_field():
        """
        Standard input field (QLineEdit, QTextEdit, QPlainTextEdit).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QLineEdit, QTextEdit, QPlainTextEdit {{
                background-color: {C.BG_ELEVATED};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: {R.RADIUS_INPUT}px;
                padding: {S.PADDING_INPUT}px;
                color: {C.TEXT_PRIMARY};
            }}
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
                border-color: {C.BORDER_FOCUS};
            }}
        """
    
    @staticmethod
    def combo_box():
        """
        Standard combo box (QComboBox).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QComboBox {{
                background-color: {C.BG_ELEVATED};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: {R.RADIUS_INPUT}px;
                padding: {S.PADDING_INPUT}px;
                color: {C.TEXT_PRIMARY};
            }}
            QComboBox:focus {{
                border-color: {C.BORDER_FOCUS};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid {C.TEXT_PRIMARY};
            }}
        """
    
    # === LABELS ===
    
    @staticmethod
    def label(color=C.TEXT_PRIMARY, size=T.SIZE_MD, weight=T.WEIGHT_NORMAL):
        """
        Standard label style.
        
        Args:
            color: Text color
            size: Font size in px
            weight: Font weight
            
        Returns:
            Qt stylesheet string
        """
        return f"color: {color}; font-size: {size}px; font-weight: {weight};"
    
    @staticmethod
    def title_label(size=T.SIZE_2XL):
        """
        Page title label.
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            font-size: {size}px; 
            font-weight: {T.WEIGHT_BOLD}; 
            color: {C.TEXT_WHITE}; 
            margin-bottom: {S.MD}px;
        """
    
    @staticmethod
    def section_label(size=T.SIZE_MD):
        """
        Section header label.
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            font-size: {size}px; 
            font-weight: {T.WEIGHT_BOLD}; 
            padding: {S.XS}px;
        """
    
    @staticmethod
    def secondary_label():
        """
        Secondary/de-emphasized label.
        
        Returns:
            Qt stylesheet string
        """
        return f"color: {C.TEXT_GRAY_LIGHT}; padding: {S.XS}px;"
    
    # === PROGRESS BARS ===
    
    @staticmethod
    def progress_bar():
        """
        Standard progress bar.
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            QProgressBar {{ 
                border: 1px solid {C.BORDER_LIGHT}; 
                border-radius: {R.SM}px; 
                text-align: center; 
                color: {C.TEXT_WHITE}; 
                background: {C.PROGRESS_BG}; 
                height: 16px; 
            }}
            QProgressBar::chunk {{ 
                background-color: {C.PROGRESS_FILL}; 
            }}
        """
    
    # === GROUP BOXES ===
    
    @staticmethod
    def group_box(border_color=C.SUCCESS_BRIGHT):
        """
        Standard group box with colored border.
        
        Args:
            border_color: Color for the border
            
        Returns:
            Qt stylesheet string
        """
        return f"""
            QGroupBox {{
                border: 2px solid {border_color};
                border-radius: {R.SM}px;
                margin-top: {S.MD}px;
                padding: {S.MD}px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: {S.MD}px;
                padding: 0 {S.XS}px;
            }}
        """
    
    # === COMPOSITE STYLES ===
    
    @staticmethod
    def info_panel():
        """
        Info panel style (used in Shot Review).
        
        Returns:
            Qt stylesheet string
        """
        return f"""
            padding: {S.MD}px; 
            background: {C.BG_HOVER}; 
            border-radius: {R.SM}px; 
            font-family: {T.FONT_MONO};
        """
    
    @staticmethod
    def dialog_background():
        """
        Dialog window background.
        
        Returns:
            Qt stylesheet string
        """
        return f"background-color: {C.BG_CARD}; color: {C.TEXT_WHITE};"


# Export the main class
__all__ = ['StyleBuilder']
