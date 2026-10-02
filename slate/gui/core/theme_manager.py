"""
The GUI half of the theme.

slate.core.infra.theme_manager does the work - one stylesheet (main.qss), one
palette per theme (gate.py). This module supplies the two things core may not
reach up for: the icon set the stylesheet draws its arrows and ticks from, and
the dialog default-button policy. It registers both with core on import, and
keeps the entry point the launcher has always called.
"""

import logging
import os

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from slate.core.infra.gate import Gate
from slate.core.infra.theme_manager import ThemeManager as _CoreTheme

from .icons import stylesheet_icons


def _write_icons(out_dir: str) -> bool:
    return stylesheet_icons(out_dir)


def _install_policy(app):
    from .controls import install_dialog_policy
    install_dialog_policy(app)


_CoreTheme.register_icon_writer(_write_icons)
_CoreTheme.register_app_hook(_install_policy)


class ThemeManager:
    """
    Centralized Theme Engine for Slate.
    Loads bundled fonts, then applies the saved theme through the core manager.
    """

    @classmethod
    def _gate_tokens(cls):
        # The palette lives in Gate; this is only the @NAME table main.qss uses.
        return Gate.tokens()

    @classmethod
    def apply_theme(cls, app: QApplication, slate_dir: str):
        """
        Applies the global QSS theme to the QApplication.
        Args:
            app: The QApplication instance.
            slate_dir: The path to the slate directory.
        """
        # Fonts shipped in resources (if a studio adds Inter, say) come first,
        # so the family check in apply_font can find them.
        fonts_dir = os.path.join(slate_dir, "resources", "fonts")
        if os.path.exists(fonts_dir):
            for font_file in os.listdir(fonts_dir):
                if font_file.endswith((".ttf", ".otf")):
                    QFontDatabase.addApplicationFont(os.path.join(fonts_dir, font_file))

        name = _CoreTheme.apply_saved_theme()
        logging.info("ThemeManager: %s theme applied.", name)
        return name


# Anything still reading ThemeManager.TOKENS gets the same palette.
ThemeManager.TOKENS = ThemeManager._gate_tokens()
