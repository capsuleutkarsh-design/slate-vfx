"""
Gate - the one place UT_VFX decides what it looks like.

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

    from ut_vfx.core.infra.gate import Gate
    widget.setStyleSheet(f"color: {Gate.TEXT};")
"""

from __future__ import annotations


class Gate:
    """Colour, spacing and type for the whole product."""

    # -------------------------------------------------------------- surfaces
    # Neutral, not blue-tinted. Four steps is enough to build any panel; the
    # old palette had ten near-identical darks that read as accidents.
    GROUND = "#0D0D0F"      # the window itself
    PANEL = "#16161A"       # a panel sitting on the window
    RAISED = "#1D1D22"      # a control sitting on a panel
    RAISED_HI = "#26262D"   # that control, hovered
    LINE = "#2C2C34"        # borders
    LINE_SOFT = "#212128"   # separators inside a panel

    # ------------------------------------------------------------------ text
    TEXT = "#E8E6E1"        # primary - warm off-white, easier than pure white
    TEXT_2 = "#B4B1AA"      # secondary
    TEXT_DIM = "#87857F"    # labels, captions, placeholders
    TEXT_ON_ACCENT = "#07171B"

    # ---------------------------------------------------------------- accent
    # Descended from the studio cyan, roughly 40% less chroma so it stops
    # competing with the footage. One job: the single primary action on screen.
    ACCENT = "#3EA8BF"
    ACCENT_HI = "#5FC6DA"
    ACCENT_DIM = "#2A7A8C"

    # ------------------------------------------------------------- semantics
    # The only other saturation in the product. These mean something.
    OK = "#5FBF8F"          # done, approved, final, online
    WARN = "#D9A441"        # wip, pending, degraded
    BAD = "#D9635F"         # retake, failed, offline, destructive
    INFO = "#6BA4C9"        # neutral notice
    IDLE = "#6E6C67"        # omitted, not started, disabled

    # ------------------------------------------------------------------ type
    # Faces that are actually on the machines this runs on.
    #
    # The old stack asked for Inter and JetBrains Mono; neither is installed and
    # neither was ever bundled, so every workstation silently substituted Segoe
    # UI and the product never rendered as designed. These lead with what
    # Windows ships, so the type is the same on every desk - and still prefer
    # Inter and JetBrains Mono if a studio chooses to install them.
    FONT_UI = "Inter, 'Segoe UI', system-ui, sans-serif"
    # Bahnschrift is Windows' condensed technical face - the closest thing to
    # the way a slate or an edge code is set, and narrow enough to keep the
    # dashboard's eighteen columns legible without shrinking the text.
    FONT_LABEL = "'Bahnschrift SemiCondensed', 'Bahnschrift', 'Segoe UI', sans-serif"
    FONT_LABEL_STRONG = "'Bahnschrift SemiBold SemiCondensed', 'Bahnschrift', 'Segoe UI', sans-serif"
    FONT_MONO = "'JetBrains Mono', Consolas, 'Cascadia Mono', monospace"

    SIZE_XS = 11
    SIZE_SM = 12
    SIZE_MD = 13
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

    ROW_HEIGHT = 30
    CONTROL_HEIGHT = 30

    # ------------------------------------------------------------ shot status
    # The vocabulary the dashboard actually uses. One definition, so the table,
    # the counts and the board can never disagree about what colour DONE is.
    STATUS = {
        "APPROVED": OK,
        "DONE": OK,
        "FINAL": OK,
        "READY": OK,
        "WIP": WARN,
        "PENDING": WARN,
        "SENT FOR REVIEW": INFO,
        "REVIEW": INFO,
        "IN REVIEW": INFO,
        "YTS": IDLE,
        "NOT STARTED": IDLE,
        "OMIT": IDLE,
        "OMITTED": IDLE,
        "RETAKE": BAD,
        "SI": BAD,
        "FAILED": BAD,
    }

    @classmethod
    def status_color(cls, status: str) -> str:
        """The colour for a shot status, whatever case or spacing it arrives in."""
        if not status:
            return cls.TEXT_DIM
        return cls.STATUS.get(str(status).strip().upper(), cls.TEXT_DIM)

    # ------------------------------------------------------------------ tokens
    @classmethod
    def tokens(cls) -> dict:
        """The @NAME substitutions used by the stylesheet."""
        return {
            "@GROUND": cls.GROUND,
            "@PANEL": cls.PANEL,
            "@RAISED": cls.RAISED,
            "@RAISED_HI": cls.RAISED_HI,
            "@LINE_SOFT": cls.LINE_SOFT,
            "@LINE": cls.LINE,
            "@TEXT_ON_ACCENT": cls.TEXT_ON_ACCENT,
            "@TEXT_DIM": cls.TEXT_DIM,
            "@TEXT_2": cls.TEXT_2,
            "@TEXT": cls.TEXT,
            "@ACCENT_HI": cls.ACCENT_HI,
            "@ACCENT_DIM": cls.ACCENT_DIM,
            "@ACCENT": cls.ACCENT,
            "@OK": cls.OK,
            "@WARN": cls.WARN,
            "@BAD": cls.BAD,
            "@INFO": cls.INFO,
            "@IDLE": cls.IDLE,
            "@FONT_UI": cls.FONT_UI,
            "@FONT_LABEL_STRONG": cls.FONT_LABEL_STRONG,
            "@FONT_LABEL": cls.FONT_LABEL,
            "@FONT_MONO": cls.FONT_MONO,
            "@RADIUS_SM": f"{cls.RADIUS_SM}px",
            "@RADIUS_MD": f"{cls.RADIUS_MD}px",
            "@RADIUS_LG": f"{cls.RADIUS_LG}px",
            "@CONTROL_HEIGHT": f"{cls.CONTROL_HEIGHT}px",
        }
