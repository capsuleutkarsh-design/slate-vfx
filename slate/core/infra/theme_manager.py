"""
Applies Slate's look to the running application.

There is one stylesheet - slate/resources/styles/main.qss - written against the
Gate tokens (@PANEL, @TEXT, @ACCENT ...), and one palette per theme in gate.py.
Applying a theme means: switch Gate to that palette, resolve main.qss against
it, write the handful of icons the sheet reaches through url(), and set the
application font and QPalette to match.

There used to be four more stylesheets in this file (Dark, Slate, Flutter and a
Light one that was never finished) plus a fifth, resources/styles.qss, that the
main window laid over the top at window level. Light swapped only the first of
those, so switching to it changed almost nothing; and because the window-level
sheet also carried "QWidget { font-size: ... }", every font a screen set in code
was overruled. Both are gone: the size now lives in the application font, where
a widget's own setFont() can still win.
"""

import logging
import os
import re
import tempfile

from PySide6.QtWidgets import QApplication
from .gate import Gate, normalise_theme, THEMES
from .global_config import GlobalConfig
from ..system.adaptation_engine import system_engine


class ThemeManager:
    """Switches between the Dark and Light themes ("Slate" is another name for Dark)."""

    # Set by the GUI layer (slate.gui.core.theme_manager), which owns the icon
    # set: core does not reach up into the GUI to draw icons itself.
    _icon_writer = None
    # Callables run with the QApplication each time a theme is applied - the
    # dialog default-button policy installs itself this way.
    _app_hooks = []
    # The theme the screens were first built in; inline styles keep it until
    # Slate restarts.
    _built_in = None

    # ------------------------------------------------------------- plumbing
    @classmethod
    def register_icon_writer(cls, writer):
        """writer(out_dir) -> bool: write the stylesheet icons for the active theme."""
        cls._icon_writer = writer

    @classmethod
    def register_app_hook(cls, hook):
        if hook not in cls._app_hooks:
            cls._app_hooks.append(hook)

    @classmethod
    def _ensure_gui_hooks(cls):
        # A process that never imported the GUI theme manager (a test, the
        # harness, a tool window) still gets the icons and the dialog policy.
        if cls._icon_writer is None:
            try:
                import slate.gui.core.theme_manager  # noqa: F401  (registers itself)
            except Exception as exc:
                logging.debug("ThemeManager: GUI hooks unavailable (%s)", exc)

    @staticmethod
    def _cache_root() -> str:
        """A folder this user can write to, for the generated stylesheet icons."""
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        return os.path.join(base, "Slate", "cache", "theme")

    @classmethod
    def icon_dir(cls) -> str:
        """
        The folder main.qss reaches through @ICONS, with the icons for the
        active theme written into it, or "" if they could not be written.

        It lives in the user's local app data rather than inside the install:
        an installed copy of Slate cannot write into its own folder, and when
        the icons were missing the sheet fell back to Qt's drawing - a filled
        accent block where every combo box arrow should be.
        """
        cls._ensure_gui_hooks()
        if cls._icon_writer is None:
            return ""
        for root in (cls._cache_root(), os.path.join(tempfile.gettempdir(), "Slate", "theme")):
            folder = os.path.join(root, Gate.MODE.lower())
            try:
                if cls._icon_writer(folder):
                    return folder.replace(os.sep, "/")
            except Exception as exc:
                logging.debug("ThemeManager: could not write icons to %s (%s)", folder, exc)
        return ""

    @staticmethod
    def _qss_path() -> str:
        here = os.path.dirname(os.path.abspath(__file__))
        package = os.path.dirname(os.path.dirname(here))       # -> slate/
        return os.path.join(package, "resources", "styles", "main.qss")

    @staticmethod
    def ui_scale() -> float:
        try:
            return float(system_engine.ui_scale or 1.0)
        except Exception:
            return 1.0

    @classmethod
    def base_font_px(cls) -> int:
        """The application's body text size: Gate.SIZE_MD, scaled for the screen."""
        return max(9, int(round(Gate.SIZE_MD * cls.ui_scale())))

    # ----------------------------------------------------------- stylesheet
    @classmethod
    def build_stylesheet(cls, mode=None) -> str:
        """main.qss resolved against the active (or given) theme."""
        if mode is not None:
            Gate.use(mode)
        try:
            with open(cls._qss_path(), "r", encoding="utf-8") as handle:
                sheet = handle.read()
        except OSError as exc:
            logging.error("ThemeManager: main.qss could not be read (%s)", exc)
            return ""

        icons = cls.icon_dir()
        if icons:
            sheet = sheet.replace("@ICONS", icons)
        else:
            # Without the files a url() draws nothing, which is better than the
            # broken image Qt would otherwise try to load.
            sheet = re.sub(r"[ \t]*image:\s*url\(@ICONS[^)]*\);", "", sheet)

        sheet = Gate.sheet(sheet)

        # The footer keeps exactly the text size it has always had: the licence
        # credit line lives there and must not change (licence section 5). It
        # used to get this size from the window-wide font rule removed above.
        footer_px = max(1, int(12 * cls.ui_scale()))
        sheet += "\n\nQWidget#footer QLabel { font-size: %dpx; }\n" % footer_px
        return sheet

    @staticmethod
    def build_palette():
        """A QPalette in the active theme, for everything the sheet does not reach."""
        from PySide6.QtGui import QColor, QPalette

        palette = QPalette()
        role = QPalette.ColorRole
        palette.setColor(role.Window, QColor(Gate.GROUND))
        palette.setColor(role.WindowText, QColor(Gate.TEXT))
        palette.setColor(role.Base, QColor(Gate.GROUND))
        palette.setColor(role.AlternateBase, QColor(Gate.PANEL))
        palette.setColor(role.ToolTipBase, QColor(Gate.PANEL))
        palette.setColor(role.ToolTipText, QColor(Gate.TEXT))
        palette.setColor(role.PlaceholderText, QColor(Gate.TEXT_DIM))
        palette.setColor(role.Text, QColor(Gate.TEXT))
        palette.setColor(role.Button, QColor(Gate.RAISED))
        palette.setColor(role.ButtonText, QColor(Gate.TEXT))
        palette.setColor(role.BrightText, QColor(Gate.TEXT_ON_BAD))
        palette.setColor(role.Link, QColor(Gate.ACCENT))
        palette.setColor(role.LinkVisited, QColor(Gate.ACCENT_DIM))
        # One highlight colour for the whole product. The window used to set
        # dark blue over an orange application highlight, so a selection was a
        # different colour depending on which widget drew it.
        palette.setColor(role.Highlight, QColor(Gate.ACCENT))
        palette.setColor(role.HighlightedText, QColor(Gate.TEXT_ON_ACCENT))
        palette.setColor(role.Light, QColor(Gate.RAISED_HI))
        palette.setColor(role.Midlight, QColor(Gate.RAISED))
        palette.setColor(role.Mid, QColor(Gate.LINE))
        palette.setColor(role.Dark, QColor(Gate.LINE_SOFT))
        palette.setColor(role.Shadow, QColor("#000000"))
        group = QPalette.ColorGroup.Disabled
        for disabled_role in (role.Text, role.ButtonText, role.WindowText):
            palette.setColor(group, disabled_role, QColor(Gate.IDLE))
        return palette

    @classmethod
    def apply_font(cls, app=None):
        """
        The base font, as the application font - not as a stylesheet rule.

        A "QWidget { font-size }" rule beats every setFont() in the product, so
        page titles asked for 16 pt and got 11 px. The application font is only
        a default: a widget that sets its own font keeps it.
        """
        app = app or QApplication.instance()
        if app is None:
            return
        from PySide6.QtGui import QFont, QFontDatabase

        installed = set(QFontDatabase.families())
        families = [f for f in Gate.FONT_UI_FAMILIES if f in installed] or list(Gate.FONT_UI_FAMILIES)
        font = QFont(app.font())
        font.setFamilies(families)
        font.setPixelSize(cls.base_font_px())
        font.setStyleHint(QFont.StyleHint.SansSerif)
        app.setFont(font)

    # ---------------------------------------------------------------- public
    @staticmethod
    def get_available_themes():
        """The themes a person can choose, in the order the Settings tab lists them."""
        return list(THEMES)

    @staticmethod
    def get_current_theme():
        return normalise_theme(GlobalConfig.get("THEME_MODE", "Slate"))

    @classmethod
    def apply_theme(cls, mode, persist: bool = True):
        """
        Switch to mode ('Dark' or 'Light'; 'Slate' means Dark) now.

        The application stylesheet, palette and font change at once; styles a
        screen built into itself earlier follow when Slate next starts. Returns
        the theme's proper name.
        """
        name = Gate.use(mode)
        app = QApplication.instance()
        if cls._built_in is None:
            cls._built_in = name
        if app is not None:
            app.setStyleSheet(cls.build_stylesheet())
            app.setPalette(cls.build_palette())
            cls.apply_font(app)
            for hook in list(cls._app_hooks):
                try:
                    hook(app)
                except Exception as exc:
                    logging.debug("ThemeManager: app hook failed (%s)", exc)
        if persist and cls.get_current_theme() != name:
            GlobalConfig.set("THEME_MODE", name)
        return name

    @classmethod
    def set_theme(cls, mode) -> bool:
        """
        Choose a theme from the Settings tab. True when it differs from the one
        the screens were built in, so the caller can say "restart to finish".
        """
        built_in = cls._built_in or Gate.MODE
        after = cls.apply_theme(mode, persist=True)
        return after != built_in

    @classmethod
    def refresh_scale(cls):
        """Re-apply the current theme after the UI scale has been worked out."""
        cls.apply_theme(Gate.MODE, persist=False)

    @classmethod
    def toggle_mode(cls):
        order = list(THEMES)
        current = Gate.MODE if Gate.MODE in order else order[0]
        return cls.apply_theme(order[(order.index(current) + 1) % len(order)])

    @staticmethod
    def is_dark_mode():
        return Gate.IS_DARK

    @classmethod
    def apply_saved_theme(cls):
        return cls.apply_theme(cls.get_current_theme(), persist=False)
