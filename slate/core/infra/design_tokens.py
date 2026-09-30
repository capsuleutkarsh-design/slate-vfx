"""
Design Tokens - Single Source of Truth for UI Values

This module defines all colors, spacing, typography, and other design constants
used throughout the Slate application. By centralizing these values,
we ensure visual consistency and make theme customization trivial.

Usage:
    from slate.core.infra.design_tokens import ColorTokens as C
    button.setStyleSheet(f"background-color: {C.ACCENT_PRIMARY};")
"""

from .gate import Gate as _G


class ColorTokens:
    """
    The product palette. Every value comes from Gate.

    This class used to hold the colours that had been scraped out of the inline
    styles, preserved exactly as found - which meant it centralised the mess
    rather than resolving it: five greys that were nearly the same grey, three
    unrelated accents, and pure #00FF00 next to #4CAF50 for the same idea.

    The names are all kept so nothing that imports them has to change. What they
    point at is now one palette, defined in slate/core/infra/gate.py, and they
    follow it when the theme changes (see _follow_gate below) - so C.BG_SURFACE
    is the panel colour of the Light theme when Light is on.
    """

    # name -> the Gate attribute it reads. Kept as data so the aliases are
    # refreshed together whenever Gate switches palette.
    _ALIASES = {
        # === BACKGROUNDS ===
        "BG_MAIN": "GROUND", "BG_PRIMARY": "GROUND", "BG_SURFACE": "PANEL",
        "BG_ELEVATED": "RAISED", "BG_HOVER": "RAISED_HI", "BG_SIDEBAR": "PANEL",
        "BG_INPUT": "RAISED", "BG_DARK": "PANEL", "BG_CARD": "PANEL",
        "BG_DARKER": "GROUND",
        # === ACCENTS ===
        # One accent, for the single primary action on a screen. The teal,
        # orange, blue and alternate cyan were each one tab's idea of an accent;
        # they resolve to the same one so the product reads as a product.
        "ACCENT_PRIMARY": "ACCENT", "ACCENT_HOVER": "ACCENT_HI",
        "ACCENT_PRESSED": "ACCENT_DIM", "ACCENT_DARK": "ACCENT_DIM",
        "ACCENT_BLUE": "ACCENT", "ACCENT_TEAL": "ACCENT", "ACCENT_CYAN_ALT": "ACCENT",
        # These two carried meaning rather than decoration, so they keep it.
        "ACCENT_ORANGE": "WARN", "ACCENT_WARNING": "WARN", "ACCENT_INFO": "INFO",
        # === TEXT ===
        "TEXT_PRIMARY": "TEXT", "TEXT_SECONDARY": "TEXT_2", "TEXT_TERTIARY": "TEXT_DIM",
        "TEXT_DISABLED": "IDLE", "TEXT_INVERSE": "TEXT_ON_ACCENT", "TEXT_WHITE": "TEXT",
        "TEXT_GRAY_LIGHT": "TEXT_DIM", "TEXT_GRAY_LIGHTER": "TEXT_2",
        "TEXT_BEIGE": "TEXT_DIM", "TEXT_MUTED": "TEXT_DIM",
        # === SEMANTIC COLORS ===
        "SUCCESS": "OK", "SUCCESS_DIM": "OK_SURFACE", "SUCCESS_BRIGHT": "OK",
        "SUCCESS_HOVER": "OK_HI",
        "ERROR": "BAD", "ERROR_BRIGHT": "BAD_HI", "ERROR_DIM": "BAD_SURFACE",
        "ERROR_LIGHT": "BAD_TEXT_SOFT",
        "WARNING": "WARN", "WARNING_ALT": "WARN",
        "INFO": "INFO", "INFO_CYAN": "ACCENT",
        # === BORDERS ===
        "BORDER_DEFAULT": "LINE", "BORDER_SUBTLE": "LINE_SOFT", "BORDER_FOCUS": "ACCENT",
        "BORDER_HOVER": "ACCENT_HI", "BORDER_LIGHT": "RAISED_HI",
        # === SPECIAL PURPOSE ===
        # Machine state on the fleet cards. Pure #00FF00 and #FF0000 were the
        # only fully saturated colours in the product and glowed against everything.
        "STATUS_ONLINE": "OK", "STATUS_OFFLINE": "BAD", "STATUS_IDLE": "WARN",
        "PROGRESS_BG": "RAISED", "PROGRESS_FILL": "ACCENT",
    }


def _follow_gate(_mode=None):
    """Point every ColorTokens name at the active Gate palette."""
    for alias, source in ColorTokens._ALIASES.items():
        setattr(ColorTokens, alias, getattr(_G, source))


_follow_gate()
_G.on_change(_follow_gate)


class SpacingTokens:
    """
    Spacing scale based on 4px increments.
    
    Use these instead of hardcoded pixel values to maintain
    consistent spacing throughout the application.
    """
    XS = 4      # Extra small spacing
    SM = 8      # Small spacing (most common for padding)
    MD = 12     # Medium spacing
    LG = 16     # Large spacing
    XL = 24     # Extra large spacing
    XXL = 32    # 2X large spacing
    
    # Common padding combinations
    PADDING_INPUT = SM          # Standard input padding (8px)
    PADDING_BUTTON = f"{SM}px {LG}px"  # Button padding (8px 16px)
    PADDING_CARD = MD           # Card padding (12px)


class TypographyTokens:
    """
    Typography definitions including fonts and sizes.
    """
    # Font families
    FONT_FAMILY = _G.FONT_UI
    FONT_UI = _G.FONT_UI
    FONT_LABEL = _G.FONT_LABEL
    FONT_MONO = _G.FONT_MONO
    
    # Font sizes (in pixels)
    SIZE_2XS = 8     # Tiny (8px)
    SIZE_XS = 9      # Extra small (9pt in some places)
    SIZE_SM = 10     # Small (10px)
    SIZE_BASE = 12   # Base size for most UI text
    SIZE_MD = 14     # Medium (default for many elements)
    SIZE_LG = 16     # Large
    SIZE_XL = 18     # Extra large
    SIZE_2XL = 24    # Page titles
    SIZE_3XL = 32    # Large headers
    
    # Font weights
    WEIGHT_NORMAL = 400
    WEIGHT_MEDIUM = 500
    WEIGHT_SEMIBOLD = 600
    WEIGHT_BOLD = 700
    
    # Common font-weight values used in code
    WEIGHT_STYLE_NORMAL = "normal"
    WEIGHT_STYLE_BOLD = "bold"


class RadiusTokens:
    """
    Border radius scale for rounded corners.
    """
    NONE = 0     # No rounding
    XS = 2       # Extra small (2px)
    SM = 4       # Small rounding (most common)
    MD = 8       # Medium rounding
    LG = 12      # Large rounding (cards)
    PILL = 18    # Pill-shaped (Flutter theme buttons)
    
    # Common values from existing code
    RADIUS_INPUT = SM      # Input fields
    RADIUS_BUTTON = SM     # Standard buttons
    RADIUS_CARD = LG       # Card containers


class ShadowTokens:
    """
    Box shadow definitions for depth and elevation.
    """
    NONE = "none"
    SM = "0 1px 3px rgba(0, 0, 0, 0.3)"
    MD = "0 4px 6px rgba(0, 0, 0, 0.3)"
    LG = "0 10px 20px rgba(0, 0, 0, 0.4)"
    
    # Elevation levels
    ELEVATION_1 = SM
    ELEVATION_2 = MD
    ELEVATION_3 = LG


class TransitionTokens:
    """
    Animation and transition timing.
    """
    FAST = "100ms"
    NORMAL = "200ms"
    SLOW = "300ms"
    
    # Easing functions
    EASE_IN_OUT = "ease-in-out"
    EASE_OUT = "ease-out"
    EASE_IN = "ease-in"


# Convenience aliases for shorter imports
C = ColorTokens
S = SpacingTokens
T = TypographyTokens
R = RadiusTokens


# Export all token classes
__all__ = [
    'ColorTokens', 'C',
    'SpacingTokens', 'S',
    'TypographyTokens', 'T',
    'RadiusTokens', 'R',
    'ShadowTokens',
    'TransitionTokens',
]
