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
same everywhere and nobody has to guess which button ends their afternoon. All
four are the same height (Gate.CONTROL_HEIGHT) with the same padding, radius
and weight as a text field; they differ by colour only.

Enter in a dialog
-----------------
make_button() also decides what Enter does. A primary button is the dialog's
default - Enter presses it - and no other kind is ever "auto default". Qt's own
rule makes the first auto-default button in focus order the Enter target, and
that was usually Cancel, placed first: pressing Enter in a half-filled form
threw it away. install_dialog_policy() (installed with the theme) applies the
same rule to dialogs built from plain QPushButtons: Cancel, Close and No are
never what Enter presses while the dialog has anything else to offer.

Ampersands
----------
Qt reads a single "&" in button, group-box, tab and menu text as "underline the
next letter and make Alt+letter press it", so "Save & Close" showed as
"Save _Close". make_button() escapes it; for anything else use plain():

    group = QGroupBox(plain("Stress & crash"))
    button.setText(plain(f"Update & ingest {n} files"))
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate


KINDS = ("primary", "secondary", "ghost", "danger")


# ----------------------------------------------------------------- ampersand
def plain(text) -> str:
    """
    Text for a button, check box, group box, tab or menu item, shown exactly as
    written. Doubles every "&" so Qt does not take it as a keyboard shortcut.
    """
    return str(text if text is not None else "").replace("&", "&&")


def strip_mnemonic(text) -> str:
    """What a label reads as on screen: "&&" -> "&", a lone "&" dropped."""
    text = str(text or "")
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


# ---------------------------------------------------------------- the kinds
def _sheet(background, colour, border, hover_bg, hover_border=None, hover_colour=None,
           pressed_bg=None, disabled=None):
    """
    One button's stylesheet. The metrics are the same for every kind, and the
    same as main.qss gives a plain QPushButton.
    """
    disabled_bg, disabled_colour, disabled_border = disabled or (Gate.PANEL, Gate.IDLE, Gate.LINE_SOFT)
    return f"""
        QPushButton {{
            background-color: {background};
            color: {colour};
            border: 1px solid {border};
            border-radius: {Gate.RADIUS_MD}px;
            padding: 6px 14px;
            font-weight: 600;
            min-height: 18px;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border-color: {hover_border or border};
            color: {hover_colour or colour};
        }}
        QPushButton:pressed {{
            background-color: {pressed_bg or Gate.PANEL};
        }}
        QPushButton:checked {{
            background-color: {Gate.tint(Gate.ACCENT, 0.16)};
            border-color: {Gate.ACCENT};
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_colour};
            border-color: {disabled_border};
        }}
        QPushButton::menu-indicator {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            right: 6px;
        }}
    """


def primary_style() -> str:
    """The single action a screen exists to perform."""
    return _sheet(Gate.ACCENT, Gate.TEXT_ON_ACCENT, Gate.ACCENT, Gate.ACCENT_HI,
                  pressed_bg=Gate.ACCENT_DIM,
                  # Disabled, it still reads as the primary action - just not a
                  # live one - rather than as another grey box beside Cancel.
                  disabled=(Gate.tint(Gate.ACCENT, 0.16), Gate.IDLE, Gate.tint(Gate.ACCENT, 0.16)))


def secondary_style() -> str:
    """An ordinary action."""
    return _sheet(Gate.RAISED, Gate.TEXT, Gate.LINE, Gate.RAISED_HI, Gate.TEXT_DIM)


def ghost_style() -> str:
    """Present, but not asking for attention."""
    return _sheet("transparent", Gate.TEXT_2, "transparent", Gate.HOVER, Gate.LINE,
                  hover_colour=Gate.TEXT,
                  disabled=("transparent", Gate.IDLE, "transparent"))


def danger_style() -> str:
    """
    Destroys something. Outlined rather than filled on purpose: a solid red
    block draws the eye and gets clicked, which is the opposite of what a
    destructive control should do.
    """
    return _sheet(Gate.tint(Gate.BAD, 0.12), Gate.BAD, Gate.tint(Gate.BAD, 0.5),
                  Gate.BAD, Gate.BAD, hover_colour=Gate.TEXT_ON_BAD,
                  pressed_bg=Gate.BAD_HI,
                  disabled=("transparent", Gate.IDLE, Gate.LINE_SOFT))


_STYLES = {
    "primary": primary_style,
    "secondary": secondary_style,
    "ghost": ghost_style,
    "danger": danger_style,
}


def style_button(button: QPushButton, kind: str = "secondary") -> QPushButton:
    """
    Apply one of the four kinds to a button that already exists - its look and
    its Enter behaviour (see set_default_button).
    """
    kind = kind if kind in _STYLES else "secondary"
    button.setProperty("kind", kind)
    sheet = _STYLES[kind]()
    if kind == "ghost":
        # A ghost button with a menu is a kebab or an icon; a chevron squeezed
        # in beside it is noise.
        sheet += " QPushButton::menu-indicator { image: none; width: 0px; }"
    button.setStyleSheet(sheet)
    if kind == "primary":
        button.setAutoDefault(True)
        button.setDefault(True)
    else:
        button.setAutoDefault(False)
        button.setDefault(False)
    if button.menu() is not None:
        button.setProperty("hasMenu", True)
    return button


def make_button(text: str, kind: str = "secondary", tooltip: str = "",
                on_click=None, icon=None, mnemonic: bool = False) -> QPushButton:
    """
    A button of one of the four kinds.

    text is shown exactly as written ("Save & close" keeps its "&"); pass
    mnemonic=True only when the text carries a deliberate "&" shortcut marker.
    icon is a name from slate.gui.core.icons, drawn in the kind's text colour.
    """
    button = QPushButton(str(text) if mnemonic else plain(text))
    style_button(button, kind)
    if icon:
        from .icons import icon as draw_icon
        colour = {"primary": Gate.TEXT_ON_ACCENT, "danger": Gate.BAD,
                  "ghost": Gate.TEXT_2}.get(kind, Gate.TEXT)
        button.setIcon(draw_icon(icon, colour, 16))
    if tooltip:
        button.setToolTip(tooltip)
    if on_click is not None:
        button.clicked.connect(on_click)
    return button


def set_default_button(dialog: QWidget, button: QPushButton) -> QPushButton:
    """
    Make `button` the one Enter presses in `dialog`, and stop every other push
    button there from taking Enter when it happens to have focus.
    """
    for other in dialog.findChildren(QPushButton):
        if other is not button:
            other.setAutoDefault(False)
            other.setDefault(False)
    button.setAutoDefault(True)
    button.setDefault(True)
    return button


# ---------------------------------------------------------- dialog policy
# What a button that backs out of a dialog is called. Matched on the visible
# text, without the "&" markers.
_BACK_OUT = {
    "cancel", "close", "no", "discard", "back", "not now", "later", "skip",
    "don't save", "dont save", "keep editing", "dismiss",
}


def _backs_out(button: QPushButton) -> bool:
    box = button.parent()
    while box is not None and not isinstance(box, QDialogButtonBox):
        box = box.parent() if not isinstance(box, QDialog) else None
    if isinstance(box, QDialogButtonBox):
        role = box.buttonRole(button)
        if role in (QDialogButtonBox.ButtonRole.RejectRole,
                    QDialogButtonBox.ButtonRole.DestructiveRole,
                    QDialogButtonBox.ButtonRole.NoRole):
            return True
    label = strip_mnemonic(button.text()).strip().lower().rstrip(".…").strip()
    return label in _BACK_OUT


def apply_dialog_policy(dialog: QDialog) -> None:
    """
    Enter presses the dialog's primary action, never Cancel.

    - a button made with make_button(kind="primary") is the default;
    - Cancel / Close / No (by role or by label) are never auto-default while
      the dialog has another enabled button, so focus landing on them does not
      make Enter throw the form away.
    Message boxes are left alone: their default is chosen by whoever asks.
    """
    if isinstance(dialog, QMessageBox):
        return
    buttons = [b for b in dialog.findChildren(QPushButton) if b.window() is dialog]
    if not buttons:
        return

    primary = [b for b in buttons if b.property("kind") == "primary"]
    backing_out = [b for b in buttons if _backs_out(b)]
    others = [b for b in buttons if b not in backing_out and b.isEnabled()]

    if others:
        for button in backing_out:
            button.setAutoDefault(False)
            if button.isDefault():
                button.setDefault(False)
    for button in buttons:
        if button.property("kind") in ("secondary", "ghost", "danger"):
            button.setAutoDefault(False)
    if primary and not any(b.isDefault() for b in buttons if b not in backing_out):
        primary[0].setAutoDefault(True)
        primary[0].setDefault(True)


class _DialogPolicy(QObject):
    """Runs apply_dialog_policy on every dialog as it is shown."""

    def eventFilter(self, watched, event):
        try:
            if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
                apply_dialog_policy(watched)
        except Exception:
            # A policy about Enter must never be the reason a dialog fails.
            pass
        return False


_policy = None


def install_dialog_policy(app) -> None:
    """Install the Enter policy for every dialog the application shows. Idempotent."""
    global _policy
    if app is None or _policy is not None:
        return
    _policy = _DialogPolicy(app)
    app.installEventFilter(_policy)


# --------------------------------------------------------------- page parts
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
    heading.setObjectName("pageTitle")
    heading.setStyleSheet(
        f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; "
        f"font-size: 23px; font-weight: 600; letter-spacing: 0.6px; "
        f"background: transparent; border: none;")
    layout.addWidget(heading)

    if subtitle:
        caption = QLabel(subtitle)
        caption.setObjectName("pageSubtitle")
        caption.setWordWrap(True)
        caption.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-family: {Gate.FONT_UI}; "
            f"font-size: {Gate.SIZE_MD}px; background: transparent; border: none;")
        layout.addWidget(caption)

    return holder


def form_layout(parent=None) -> QFormLayout:
    """
    A form: labels right-aligned and vertically centred on their fields.

    QFormLayout's default puts a label at the top of its row, which next to a
    32 px field reads as belonging to the row above.
    """
    form = QFormLayout(parent) if parent is not None else QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setHorizontalSpacing(Gate.SPACE_3)
    form.setVerticalSpacing(Gate.SPACE_2)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return form


def tidy_form(form: QFormLayout) -> QFormLayout:
    """Give a QFormLayout that already exists the same alignment as form_layout()."""
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(Gate.SPACE_3)
    form.setVerticalSpacing(Gate.SPACE_2)
    return form


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
    gated = []
    for button in parent.findChildren(QPushButton):
        label = (button.text() or "").strip().lower()
        if not label:
            continue
        if any(verb in label for verb in SELECTION_VERBS):
            enable_with_selection(button, table)
            gated.append(button.text())
    return gated
