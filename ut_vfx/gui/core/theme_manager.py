import logging
import os
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication

class ThemeManager:
    """
    Centralized Theme Engine for UTCAP.
    Loads global styles, parses tokens, and applies custom typography.
    """
    
    # Global Theme Tokens
    #
    # These are not defined here any more. Gate is the product's palette, and
    # having a second copy of it in this file is exactly how the six competing
    # stylesheets came about. The names below are the ones main.qss uses.
    @classmethod
    def _gate_tokens(cls):
        from ut_vfx.core.infra.gate import Gate
        tokens = {
            "@BG_MAIN": Gate.GROUND,
            "@BG_PANEL": Gate.PANEL,
            "@BG_ELEVATED": Gate.RAISED,
            "@ACCENT_HOVER": Gate.ACCENT_HI,
            "@ACCENT": Gate.ACCENT,
            "@TEXT_PRIMARY": Gate.TEXT,
            "@TEXT_MUTED": Gate.TEXT_DIM,
            "@BORDER_FOCUS": Gate.ACCENT,
            "@BORDER": Gate.LINE,
            "@DANGER": Gate.BAD,
            "@SUCCESS": Gate.OK,
            "@WARNING": Gate.WARN,
            "@RADIUS_SM": f"{Gate.RADIUS_SM}px",
            "@RADIUS_MD": f"{Gate.RADIUS_MD}px",
            "@RADIUS_LG": f"{Gate.RADIUS_LG}px",
            "@FONT_UI": Gate.FONT_UI,
            "@FONT_LABEL": Gate.FONT_LABEL,
            "@FONT_MONO": Gate.FONT_MONO,
        }
        return tokens

    @classmethod
    def _icon_dir(cls, ut_vfx_dir: str) -> str:
        """Write the stylesheet's icons and return the folder QSS should use."""
        from ut_vfx.core.infra.gate import Gate
        from .icons import svg_file

        out = os.path.join(ut_vfx_dir, "resources", "icons")
        svg_file("chevron-down", Gate.TEXT_DIM, out)
        # The tick sits on the accent fill, so it is drawn in the ground colour
        # rather than in text grey.
        svg_file("check", Gate.GROUND, out, stroke=2.6)
        return out.replace("\\", "/")

    @classmethod
    def apply_theme(cls, app: QApplication, ut_vfx_dir: str):
        """
        Applies the global QSS theme to the QApplication.
        Args:
            app: The QApplication instance.
            ut_vfx_dir: The path to the ut_vfx directory.
        """
        # 1. Load Custom Fonts (if available in resources)
        fonts_dir = os.path.join(ut_vfx_dir, "resources", "fonts")
        if os.path.exists(fonts_dir):
            for font_file in os.listdir(fonts_dir):
                if font_file.endswith((".ttf", ".otf")):
                    QFontDatabase.addApplicationFont(os.path.join(fonts_dir, font_file))
        
        # 2. Set Default Font (Check for Inter, fallback to Segoe UI on Windows)
        available_families = QFontDatabase.families()
        chosen_family = "Inter" if "Inter" in available_families else "Segoe UI"
        default_font = QFont(chosen_family, 10)
        default_font.setStyleHint(QFont.SansSerif)
        app.setFont(default_font)
        
        # 3. Load QSS File
        qss_path = os.path.join(ut_vfx_dir, "resources", "styles", "main.qss")
        fallback_qss = os.path.join(ut_vfx_dir, "resources", "styles.qss")
        
        stylesheet = ""
        
        if os.path.exists(qss_path):
            with open(qss_path, "r", encoding="utf-8") as f:
                stylesheet = f.read()
        elif os.path.exists(fallback_qss):
             with open(fallback_qss, "r", encoding="utf-8") as f:
                stylesheet = f.read()
                
        if stylesheet:
            # 3b. Materialise the icons the sheet reaches through url().
            #
            # Qt cannot call into the icon module from a stylesheet, so the two
            # it needs are written out here from the same paths and the same
            # palette everything else uses. @ICONS is an absolute, forward-slash
            # folder - a relative url() resolves against the working directory,
            # which is wherever the launcher happened to be run from.
            stylesheet = stylesheet.replace("@ICONS", cls._icon_dir(ut_vfx_dir))

            # 4. Resolve Tokens
            # Replace longest tokens first to avoid partial collisions:
            # e.g. @BORDER before @BORDER_FOCUS -> "#333333_FOCUS" (invalid color).
            for token, value in sorted(cls._gate_tokens().items(), key=lambda kv: len(kv[0]), reverse=True):
                stylesheet = stylesheet.replace(token, value)
                
            # 5. Apply
            app.setStyleSheet(stylesheet)
            logging.info("ThemeManager: Global theme applied successfully.")
        else:
            logging.info(f"ThemeManager: Could not find stylesheet at {qss_path}")


# Anything still reading ThemeManager.TOKENS gets the same palette.
ThemeManager.TOKENS = ThemeManager._gate_tokens()
