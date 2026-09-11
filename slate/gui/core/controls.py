"""
The button vocabulary.

Across the product a button's colour said nothing about what it did. The header
had four buttons in four colours, one filled and three outlined; Admin Panel had
another four in cyan, orange, dark and green, with the destructive one (Wipe
Caches) styled more quietly than the harmless one (Start API Gateway). Delete was
bright red in Stock Viewer and plain grey in Hardware.

There are four kinds of button and no more:

    primary    the one action this screen is for. At most one per screen.
    secondary  an ordinary action.
    ghost      an action that should stay out of the way.
    danger     an action that destroys something.

Use these rather than writing a stylesheet, so a destructive action looks the
same everywhere and nobody has to guess which button ends their afternoon.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from slate.core.infra.gate import Gate


def _sheet(background, colour, border, hover_bg, hover_border=None, weight=600):
    return f"""
        QPushButton {{
            background-color: {background};
            color: {colour};
            border: 1px solid {border};
            border-radius: {Gate.RADIUS_MD}px;
            padding: 6px 14px;
            font-family: {Gate.FONT_UI};
            font-size: {Gate.SIZE_MD}px;
            font-weight: {weight};
            min-height: 20px;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border-color: {hover_border or border};
        }}
        QPushButton:pressed {{
            background-color: {Gate.ACCENT_DIM if background == Gate.ACCENT else Gate.RAISED};
        }}
        QPushButton:disabled {{
            background-color: {Gate.RAISED};
            color: {Gate.IDLE};
            border-color: {Gate.LINE_SOFT};
        }}
    """


def primary_style() -> str:
    """The single action a screen exists to perform."""
    return _sheet(Gate.ACCENT, Gate.TEXT_ON_ACCENT, Gate.ACCENT, Gate.ACCENT_HI)


def secondary_style() -> str:
    """An ordinary action."""
    return _sheet(Gate.RAISED, Gate.TEXT_2, Gate.LINE, Gate.RAISED_HI, Gate.LINE, weight=500)


def ghost_style() -> str:
    """Present, but not asking for attention."""
    return _sheet("transparent", Gate.TEXT_DIM, "transparent", Gate.RAISED, Gate.LINE, weight=500)


def danger_style() -> str:
    """
    Destroys something. Outlined rather than filled on purpose: a solid red
    block draws the eye and gets clicked, which is the opposite of what a
    destructive control should do.
    """
    return _sheet("transparent", Gate.BAD, "#5A2F2E", Gate.BAD + "22", Gate.BAD, weight=600)


_STYLES = {
    "primary": primary_style,
    "secondary": secondary_style,
    "ghost": ghost_style,
    "danger": danger_style,
}


def style_button(button: QPushButton, kind: str = "secondary") -> QPushButton:
    """Apply one of the four kinds to a button that already exists."""
    button.setStyleSheet(_STYLES.get(kind, secondary_style)())
    return button


def make_button(text: str, kind: str = "secondary", tooltip: str = "",
                on_click=None) -> QPushButton:
    """A button of one of the four kinds."""
    button = QPushButton(text)
    style_button(button, kind)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click is not None:
        button.clicked.connect(on_click)
    return button


def page_title(text: str, subtitle: str = "") -> QWidget:
    """
    The heading at the top of a tab.

    Every tab had invented its own. The IT tabs set theirs in amber - a colour
    that means "warning" everywhere else in the product - while the HRMS tabs
    used white, and both asked for a font nobody has installed. They also
    repeated the sidebar group in brackets: "Hardware Inventory (IT)" sitting
    under a rule that already says IT & INFRA.
    """
    holder = QWidget()
    holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    holder.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, Gate.SPACE_3)
    layout.setSpacing(2)

    heading = QLabel(text)
    heading.setStyleSheet(
        f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; "
        f"font-size: 23px; font-weight: 600; letter-spacing: 0.6px; "
        f"background: transparent; border: none;")
    layout.addWidget(heading)

    if subtitle:
        caption = QLabel(subtitle)
        caption.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_UI}; "
            f"font-size: {Gate.SIZE_MD}px; background: transparent; border: none;")
        layout.addWidget(caption)

    return holder


def enable_with_selection(button, table, require_rows: int = 1):
    """
    Keep a button switched off until the table has a selection.

    "Delete Selected", "Reject Selected", "Mark Failed" and the rest were all
    live with nothing selected, so pressing one did nothing, or argued with a
    dialog. A control you cannot usefully press should look like one.
    """
    def sync(*_):
        try:
            rows = {i.row() for i in table.selectedIndexes()}
            button.setEnabled(len(rows) >= require_rows)
        except RuntimeError:
            pass

    try:
        table.itemSelectionChanged.connect(sync)
    except Exception:
        model = table.selectionModel() if hasattr(table, "selectionModel") else None
        if model is not None:
            model.selectionChanged.connect(sync)
    sync()
    return button


# Actions that operate on whatever is highlighted. Matched on their label
# because every one of these tabs builds its buttons before its table, so they
# cannot be wired up at the point they are created.
SELECTION_VERBS = ("selected", "mark success", "mark failed", "approve", "reject")


def gate_selection_buttons(parent, table):
    """
    Switch off every button on this tab that acts on a selection, until
    something is selected.
    """
    from PySide6.QtWidgets import QPushButton

    gated = []
    for button in parent.findChildren(QPushButton):
        label = (button.text() or "").strip().lower()
        if not label:
            continue
        if any(verb in label for verb in SELECTION_VERBS):
            enable_with_selection(button, table)
            gated.append(button.text())
    return gated
