"""
Gate - the one place Slate decides what it looks like.

The product had six competing stylesheets and 339 hardcoded colours spread over
698 inline setStyleSheet calls. Because a widget's own stylesheet beats the
application's in Qt, those inline calls quietly overruled the global theme
entirely: applying the whole 10,000-character sheet changed 0.02% of the pixels
on a typical tab.

The principle here comes from the room this software lives in. In a
colour-critical application the interface must never out-shout the picture -
which is why Nuke, Resolve and RV are all close to achromatic. So the chrome is
neutral, and saturation is reserved for meaning: shot status, and alerts.
Nothing else in the interface is allowed to be colourful.

Import the palette rather than typing a hex code:

    from slate.core.infra.gate import Gate
    widget.setStyleSheet(f"color: {Gate.TEXT};")

Themes
------
There are two looks: Dark (the near-black look Slate has always had) and
Light. "Slate" is kept as another name for Dark - saved settings and the
bundled config use both words, and they always drew the same stylesheet. Each
theme is a full palette under the same names, so code that asks for Gate.PANEL
gets the panel colour of whichever theme is active. That is the whole trick,
and it only works if nothing types a hex code: a literal "#16161A" is dark in
every theme.

The active theme is chosen once, when this module is first imported (from the
saved THEME_MODE setting), so every stylesheet a screen builds already carries
the right colours. Gate.use() switches it later; the application stylesheet
follows at once, but inline styles that screens built earlier keep the colours
they were built with until Slate restarts - which is why the Settings tab says
a theme change finishes on restart.

Long stylesheets can be written without f-string brace doubling:

    widget.setStyleSheet(Gate.sheet("QFrame { background: @PANEL; border: 1px solid @LINE; }"))

and translucent colours come from helpers rather than rgba() literals, because
"a little lighter" means white on a dark theme and black on a light one:

    Gate.tint(Gate.ACCENT, 0.18)    # the accent at 18%
    Gate.overlay(0.05)              # a hover wash that works in every theme
"""

from __future__ import annotations

import os


# ------------------------------------------------------------------ palettes
#
# Every theme defines every name. The Dark values are the ones the product has
# always used, so nothing changes for the default theme.
_PALETTES = {
    "Dark": {
        # surfaces - neutral, not blue-tinted
        "GROUND": "#0D0D0F",      # the window itself
        "PANEL": "#16161A",       # a panel sitting on the window
        "RAISED": "#1D1D22",      # a control sitting on a panel
        "RAISED_HI": "#26262D",   # that control, hovered
        "LINE": "#2C2C34",        # borders
        "LINE_SOFT": "#212128",   # separators inside a panel
        "INPUT": "#121214",       # the inset of a text field
        # text
        "TEXT": "#E8E6E1",        # primary - warm off-white, easier than pure white
        "TEXT_2": "#B4B1AA",      # secondary
        "TEXT_DIM": "#87857F",    # labels, captions, placeholders
        "TEXT_ON_ACCENT": "#07171B",
        # accent - descended from the studio cyan, roughly 40% less chroma so it
        # stops competing with the footage. One job: the primary action.
        "ACCENT": "#3EA8BF",
        "ACCENT_HI": "#5FC6DA",
        "ACCENT_DIM": "#2A7A8C",
        "ACCENT_SURFACE": "#16323A",   # a panel tinted with the accent
        # semantics - the only other saturation in the product
        "OK": "#5FBF8F",          # done, approved, final, online
        "WARN": "#D9A441",        # wip, pending, degraded
        "BAD": "#D9635F",         # retake, failed, offline, destructive
        "INFO": "#6BA4C9",        # neutral notice
        "IDLE": "#6E6C67",        # omitted, not started, disabled
        "OK_SURFACE": "#1B3A2C",
        "WARN_SURFACE": "#3A2F19",
        "BAD_SURFACE": "#3A1F1E",
        "BAD_HI": "#E4817E",
        "OK_HI": "#74CEA0",
        "BAD_TEXT_SOFT": "#EFC0BE",
    },
    "Light": {
        # Warm paper rather than pure white, which glares next to footage.
        # Accent and semantic colours are darker than their dark-theme twins so
        # that text in them still reads (4.5:1 or better on the panel).
        "GROUND": "#ECEBE7",
        "PANEL": "#F6F5F2",
        "RAISED": "#FFFFFF",
        "RAISED_HI": "#E6E5E0",
        "LINE": "#CFCDC6",
        "LINE_SOFT": "#E0DED8",
        "INPUT": "#FFFFFF",
        "TEXT": "#1C1C1F",
        "TEXT_2": "#44423D",
        "TEXT_DIM": "#6A6862",
        "TEXT_ON_ACCENT": "#FFFFFF",
        "ACCENT": "#1D7D92",
        "ACCENT_HI": "#16687A",
        "ACCENT_DIM": "#12596A",
        "ACCENT_SURFACE": "#D8ECF1",
        "OK": "#2B8558",
        "WARN": "#9A6A0E",
        "BAD": "#BF3F3A",
        "INFO": "#2D6C9B",
        "IDLE": "#8F8D87",
        "OK_SURFACE": "#DDEFE4",
        "WARN_SURFACE": "#F4E8CC",
        "BAD_SURFACE": "#F6DDDB",
        "BAD_HI": "#A8322E",
        "OK_HI": "#236E49",
        "BAD_TEXT_SOFT": "#7E2622",
    },
}

THEMES = ("Dark", "Light")
DEFAULT_THEME = "Dark"
# Other names a saved setting may carry, and the theme each one means.
_ALIASES = {"slate": "Dark", "default": "Dark", "night": "Dark", "day": "Light"}


def normalise_theme(mode) -> str:
    """'light', 'LIGHT ' and 'Light' are the same theme; 'Slate' is Dark; anything unknown is Dark."""
    text = str(mode or "").strip().lower()
    for name in THEMES:
        if name.lower() == text:
            return name
    return _ALIASES.get(text, DEFAULT_THEME)


def _saved_theme() -> str:
    """
    The theme to start in: SLATE_THEME in the environment, else the saved
    THEME_MODE setting, else Dark. Never raises - a broken config must not
    stop the palette from importing.
    """
    env = os.environ.get("SLATE_THEME")
    if env:
        return normalise_theme(env)
    try:
        from .global_config import GlobalConfig
        return normalise_theme(GlobalConfig.get("THEME_MODE", DEFAULT_THEME))
    except Exception:
        return DEFAULT_THEME


def _rgb(colour: str):
    text = str(colour).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) == 8:          # #AARRGGBB, Qt's order
        text = text[2:]
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


class Gate:
    """Colour, spacing and type for the whole product."""

    MODE = DEFAULT_THEME
    IS_DARK = True

    # The palette names are filled in by use() below; they are listed here so
    # editors and readers can see what exists.
    GROUND = PANEL = RAISED = RAISED_HI = LINE = LINE_SOFT = INPUT = ""
    TEXT = TEXT_2 = TEXT_DIM = TEXT_ON_ACCENT = ""
    ACCENT = ACCENT_HI = ACCENT_DIM = ACCENT_SURFACE = ""
    OK = WARN = BAD = INFO = IDLE = ""
    OK_SURFACE = WARN_SURFACE = BAD_SURFACE = BAD_HI = OK_HI = BAD_TEXT_SOFT = ""
    # Always white: text on a solid red or green fill, in every theme.
    TEXT_ON_BAD = "#FFFFFF"

    # Derived translucent colours, rebuilt by use().
    SELECTION = ""          # a selected row
    ALT_ROW = ""            # every other row of a table
    HOVER = ""              # a hovered row or ghost button
    SCRIM = ""              # the dim behind a modal overlay

    # ------------------------------------------------------------------ type
    # Faces that are actually on the machines this runs on.
    #
    # The old stack asked for Inter and JetBrains Mono; neither is installed and
    # neither was ever bundled, so every workstation silently substituted Segoe
    # UI and the product never rendered as designed. These lead with what
    # Windows ships, so the type is the same on every desk - and still prefer
    # Inter and JetBrains Mono if a studio chooses to install them.
    FONT_UI = "Inter, 'Segoe UI', system-ui, sans-serif"
    FONT_UI_FAMILIES = ("Inter", "Segoe UI")
    # Bahnschrift is Windows' condensed technical face - the closest thing to
    # the way a slate or an edge code is set, and narrow enough to keep the
    # dashboard's eighteen columns legible without shrinking the text.
    FONT_LABEL = "'Bahnschrift SemiCondensed', 'Bahnschrift', 'Segoe UI', sans-serif"
    FONT_LABEL_STRONG = "'Bahnschrift SemiBold SemiCondensed', 'Bahnschrift', 'Segoe UI', sans-serif"
    FONT_MONO = "'JetBrains Mono', Consolas, 'Cascadia Mono', monospace"

    SIZE_XS = 11
    SIZE_SM = 12
    SIZE_MD = 13            # body text, and the application's base font
    SIZE_LG = 15
    SIZE_XL = 19
    SIZE_DISPLAY = 26

    # --------------------------------------------------------------- metrics
    RADIUS_SM = 3
    RADIUS_MD = 5
    RADIUS_LG = 8

    SPACE_1 = 4
    SPACE_2 = 8
    SPACE_3 = 12
    SPACE_4 = 16
    SPACE_5 = 24
    SPACE_6 = 32

    # One height for every control that sits in a row - buttons, fields,
    # combos, date edits - so toolbar edges line up.
    CONTROL_HEIGHT = 32
    ROW_HEIGHT = 30
    CELL_PADDING = 8

    STATUS = {}

    _listeners = []

    # ------------------------------------------------------------- switching
    @classmethod
    def use(cls, mode) -> str:
        """
        Make `mode` the active palette and return its proper name.

        Everything that reads Gate.X afterwards gets the new colour. Listeners
        (ColorTokens, the icon cache) are told so they can follow.
        """
        name = normalise_theme(mode)
        palette = _PALETTES[name]
        for key, value in palette.items():
            setattr(cls, key, value)
        cls.MODE = name
        cls.IS_DARK = name != "Light"

        # Opaque on purpose: Qt paints a selected row's fill twice (once for
        # the row, once for the cell), so a translucent selection came out at
        # nearly double the strength it was asked for.
        cls.SELECTION = cls.mix(cls.GROUND, cls.ACCENT, 0.26 if cls.IS_DARK else 0.2)
        cls.ALT_ROW = cls.mix(cls.GROUND, cls.TEXT, 0.025)
        cls.HOVER = cls.overlay(0.05)
        cls.SCRIM = "rgba(0, 0, 0, 0.55)" if cls.IS_DARK else "rgba(28, 28, 31, 0.35)"

        # The vocabulary the dashboard actually uses. One definition, so the
        # table, the counts and the board can never disagree about what colour
        # DONE is. Neutral statuses (not started, omitted) take the secondary
        # text colour in Light: IDLE there is too pale to read on a white pill.
        neutral = cls.IDLE if cls.IS_DARK else cls.TEXT_2
        cls.STATUS = {
            "APPROVED": cls.OK,
            "DONE": cls.OK,
            "FINAL": cls.OK,
            "READY": cls.OK,
            "WIP": cls.WARN,
            "PENDING": cls.WARN,
            "SENT FOR REVIEW": cls.INFO,
            "REVIEW": cls.INFO,
            "IN REVIEW": cls.INFO,
            "YTS": neutral,
            "NOT STARTED": neutral,
            "OMIT": neutral,
            "OMITTED": neutral,
            "N/A": neutral,
            "RETAKE": cls.BAD,
            "SI": cls.BAD,
            "FAILED": cls.BAD,
        }

        for listener in list(cls._listeners):
            try:
                listener(name)
            except Exception:
                pass
        return name

    @classmethod
    def on_change(cls, callback):
        """Call callback(mode) whenever the palette switches."""
        if callback not in cls._listeners:
            cls._listeners.append(callback)
        return callback

    @staticmethod
    def themes():
        return list(THEMES)

    @staticmethod
    def palette(mode) -> dict:
        """A copy of one theme's colours, without switching to it."""
        return dict(_PALETTES[normalise_theme(mode)])

    # --------------------------------------------------------------- helpers
    @staticmethod
    def tint(colour: str, alpha: float) -> str:
        """
        A palette colour at some opacity, as rgba() for a stylesheet.

        Never append two hex digits to a colour for this: Qt reads an 8-digit
        hex as #AARRGGBB, so "#D9635F22" is not the red at 13% but a different
        colour entirely.
        """
        r, g, b = _rgb(colour)
        alpha = max(0.0, min(1.0, float(alpha)))
        return "rgba(%d, %d, %d, %s)" % (r, g, b, ("%.3f" % alpha).rstrip("0").rstrip("."))

    @staticmethod
    def mix(base: str, over: str, amount: float) -> str:
        """`over` laid on `base` at `amount` (0-1), as an opaque hex colour."""
        amount = max(0.0, min(1.0, float(amount)))
        b, o = _rgb(base), _rgb(over)
        return "#%02X%02X%02X" % tuple(int(round(b[i] + (o[i] - b[i]) * amount)) for i in range(3))

    @classmethod
    def overlay(cls, alpha: float) -> str:
        """
        A wash that lifts a surface: white on the dark themes, black on Light.

        Hover and zebra stripes used rgba(255,255,255,...) everywhere, which is
        invisible on a light ground.
        """
        base = "#FFFFFF" if cls.IS_DARK else "#000000"
        return cls.tint(base, alpha)

    @staticmethod
    def qcolor(colour: str, alpha=None):
        """A QColor for painters, optionally with alpha 0-255 or 0.0-1.0."""
        from PySide6.QtGui import QColor
        result = QColor(colour)
        if alpha is not None:
            if isinstance(alpha, float) and alpha <= 1.0:
                result.setAlphaF(max(0.0, min(1.0, alpha)))
            else:
                result.setAlpha(max(0, min(255, int(alpha))))
        return result

    @classmethod
    def status_color(cls, status: str) -> str:
        """The colour for a shot status, whatever case or spacing it arrives in."""
        if not status:
            return cls.TEXT_DIM
        return cls.STATUS.get(str(status).strip().upper(), cls.TEXT_DIM)

    # ------------------------------------------------------------------ tokens
    @classmethod
    def tokens(cls) -> dict:
        """
        The @NAME substitutions used by stylesheets (main.qss and Gate.sheet).

        The @BG_* / @TEXT_PRIMARY names are the ones main.qss used before it was
        rewritten against Gate; they are kept so an older sheet still resolves.
        """
        tokens = {
            "@GROUND": cls.GROUND,
            "@PANEL": cls.PANEL,
            "@RAISED": cls.RAISED,
            "@RAISED_HI": cls.RAISED_HI,
            "@LINE_SOFT": cls.LINE_SOFT,
            "@LINE": cls.LINE,
            "@INPUT": cls.INPUT,
            "@TEXT_ON_ACCENT": cls.TEXT_ON_ACCENT,
            "@TEXT_ON_BAD": cls.TEXT_ON_BAD,
            "@TEXT_DIM": cls.TEXT_DIM,
            "@TEXT_2": cls.TEXT_2,
            "@TEXT": cls.TEXT,
            "@ACCENT_HI": cls.ACCENT_HI,
            "@ACCENT_DIM": cls.ACCENT_DIM,
            "@ACCENT_SURFACE": cls.ACCENT_SURFACE,
            "@ACCENT": cls.ACCENT,
            "@OK_SURFACE": cls.OK_SURFACE,
            "@WARN_SURFACE": cls.WARN_SURFACE,
            "@BAD_SURFACE": cls.BAD_SURFACE,
            "@BAD_HI": cls.BAD_HI,
            "@OK_HI": cls.OK_HI,
            "@OK": cls.OK,
            "@WARN": cls.WARN,
            "@BAD": cls.BAD,
            "@INFO": cls.INFO,
            "@IDLE": cls.IDLE,
            "@SELECTION": cls.SELECTION,
            "@ALT_ROW": cls.ALT_ROW,
            "@HOVER": cls.HOVER,
            "@SCRIM": cls.SCRIM,
            # translucent washes, for states that must not hide what is under them
            "@ACCENT_WASH": cls.tint(cls.ACCENT, 0.16),
            "@BAD_WASH": cls.tint(cls.BAD, 0.12),
            "@BAD_EDGE": cls.tint(cls.BAD, 0.5),
            "@OK_WASH": cls.tint(cls.OK, 0.14),
            "@WARN_WASH": cls.tint(cls.WARN, 0.14),
            "@INFO_WASH": cls.tint(cls.INFO, 0.14),
            "@OVERLAY_1": cls.overlay(0.03),
            "@OVERLAY_2": cls.overlay(0.06),
            "@OVERLAY_3": cls.overlay(0.10),
            "@FONT_UI": cls.FONT_UI,
            "@FONT_LABEL_STRONG": cls.FONT_LABEL_STRONG,
            "@FONT_LABEL": cls.FONT_LABEL,
            "@FONT_MONO": cls.FONT_MONO,
            "@RADIUS_SM": f"{cls.RADIUS_SM}px",
            "@RADIUS_MD": f"{cls.RADIUS_MD}px",
            "@RADIUS_LG": f"{cls.RADIUS_LG}px",
            "@CONTROL_HEIGHT": f"{cls.CONTROL_HEIGHT}px",
            "@ROW_HEIGHT": f"{cls.ROW_HEIGHT}px",
            "@SIZE_XS": f"{cls.SIZE_XS}px",
            "@SIZE_SM": f"{cls.SIZE_SM}px",
            "@SIZE_MD": f"{cls.SIZE_MD}px",
            "@SIZE_LG": f"{cls.SIZE_LG}px",
            "@SIZE_XL": f"{cls.SIZE_XL}px",
            # the names main.qss used before
            "@BG_MAIN": cls.GROUND,
            "@BG_PANEL": cls.PANEL,
            "@BG_ELEVATED": cls.RAISED,
            "@ACCENT_HOVER": cls.ACCENT_HI,
            "@TEXT_PRIMARY": cls.TEXT,
            "@TEXT_MUTED": cls.TEXT_DIM,
            "@BORDER_FOCUS": cls.ACCENT,
            "@BORDER": cls.LINE,
            "@DANGER": cls.BAD,
            "@SUCCESS": cls.OK,
            "@WARNING": cls.WARN,
        }
        return tokens

    @classmethod
    def sheet(cls, template: str, extra: dict = None) -> str:
        """
        A stylesheet with its @TOKENS resolved against the active theme.

        Longest names are replaced first, so @LINE_SOFT is never read as @LINE
        followed by "_SOFT".
        """
        values = cls.tokens()
        if extra:
            values.update(extra)
        text = str(template)
        for name, value in sorted(values.items(), key=lambda kv: -len(kv[0])):
            if name in text:
                text = text.replace(name, str(value))
        return text


Gate.use(_saved_theme())
