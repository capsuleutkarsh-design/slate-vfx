"""
Design Tokens - Single Source of Truth for UI Values

This module defines all colors, spacing, typography, and other design constants
used throughout the UT_VFX application. By centralizing these values,
we ensure visual consistency and make theme customization trivial.

Usage:
    from ut_vfx.core.infra.design_tokens import ColorTokens as C
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
    point at is now one palette, defined in ut_vfx/core/infra/gate.py.
    """

    # === BACKGROUNDS ===
    BG_MAIN = _G.GROUND
    BG_PRIMARY = _G.GROUND
    BG_SURFACE = _G.PANEL
    BG_ELEVATED = _G.RAISED
    BG_HOVER = _G.RAISED_HI
    BG_SIDEBAR = _G.PANEL
    BG_INPUT = _G.RAISED
    BG_DARK = _G.PANEL
    BG_CARD = _G.PANEL
    BG_DARKER = _G.GROUND

    # === ACCENTS ===
    # One accent, for the single primary action on a screen. The teal, orange,
    # blue and alternate cyan below were each one tab's idea of an accent; they
    # now resolve to the same one so the product reads as a product.
    ACCENT_PRIMARY = _G.ACCENT
    ACCENT_HOVER = _G.ACCENT_HI
    ACCENT_PRESSED = _G.ACCENT_DIM
    ACCENT_DARK = _G.ACCENT_DIM
    ACCENT_BLUE = _G.ACCENT
    ACCENT_TEAL = _G.ACCENT
    ACCENT_CYAN_ALT = _G.ACCENT
    # These two carried meaning rather than decoration, so they keep it.
    ACCENT_ORANGE = _G.WARN
    ACCENT_WARNING = _G.WARN
    ACCENT_INFO = _G.INFO

    # === TEXT ===
    TEXT_PRIMARY = _G.TEXT
    TEXT_SECONDARY = _G.TEXT_2
    TEXT_TERTIARY = _G.TEXT_DIM
    TEXT_DISABLED = _G.IDLE
    TEXT_INVERSE = _G.TEXT_ON_ACCENT
    TEXT_WHITE = _G.TEXT
    TEXT_GRAY_LIGHT = _G.TEXT_DIM
    TEXT_GRAY_LIGHTER = _G.TEXT_2
    TEXT_BEIGE = _G.TEXT_DIM
    TEXT_MUTED = _G.TEXT_DIM

    # === SEMANTIC COLORS ===
    SUCCESS = _G.OK
    SUCCESS_DIM = "#1B3A2C"
    SUCCESS_BRIGHT = _G.OK
    SUCCESS_HOVER = "#74CEA0"

    ERROR = _G.BAD
    ERROR_BRIGHT = "#E4817E"
    ERROR_DIM = "#3A1F1E"
    ERROR_LIGHT = "#EFC0BE"

    WARNING = _G.WARN
    WARNING_ALT = _G.WARN

    INFO = _G.INFO
    INFO_CYAN = _G.ACCENT

    # === BORDERS ===
    BORDER_DEFAULT = _G.LINE
    BORDER_SUBTLE = _G.LINE_SOFT
    BORDER_FOCUS = _G.ACCENT
    BORDER_HOVER = _G.ACCENT_HI
    BORDER_LIGHT = _G.RAISED_HI

    # === SPECIAL PURPOSE ===
    # Machine state on the fleet cards. Pure #00FF00 and #FF0000 were the only
    # fully saturated colours in the product and they glowed against everything.
    STATUS_ONLINE = _G.OK
    STATUS_OFFLINE = _G.BAD
    STATUS_IDLE = _G.WARN

    PROGRESS_BG = _G.RAISED
    PROGRESS_FILL = _G.ACCENT


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
