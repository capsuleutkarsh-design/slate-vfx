"""
The theme foundation: one palette per theme (Gate), one stylesheet (main.qss),
the shared controls, tables, cards and empty states built on them.

These are the pieces every screen leans on, so they are pinned here: the Light
theme really is a different palette, the stylesheet carries no colour of its
own, Enter in a dialog presses the primary action and never Cancel, an "&" in
a label is shown as an "&", a table's cell colours survive the stylesheet, and
no screen has gone back to typing the dark palette as hex.
"""

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from slate.core.infra.gate import Gate, normalise_theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def dark(app):
    """Run in the Dark theme, and leave the application in it afterwards."""
    from slate.core.infra.theme_manager import ThemeManager
    ThemeManager.apply_theme("Dark", persist=False)
    yield
    ThemeManager.apply_theme("Dark", persist=False)


@pytest.fixture
def light(app):
    from slate.core.infra.theme_manager import ThemeManager
    ThemeManager.apply_theme("Light", persist=False)
    yield
    ThemeManager.apply_theme("Dark", persist=False)


# ----------------------------------------------------------------- palette
def test_theme_names_normalise_and_slate_means_dark():
    assert normalise_theme("light") == "Light"
    assert normalise_theme(" LIGHT ") == "Light"
    assert normalise_theme("Slate") == "Dark"
    assert normalise_theme("Dark") == "Dark"
    assert normalise_theme("something else") == "Dark"
    assert normalise_theme(None) == "Dark"


def test_light_is_a_different_palette_and_colour_tokens_follow(dark):
    from slate.core.infra.design_tokens import ColorTokens as C

    assert Gate.PANEL == "#16161A" and C.BG_SURFACE == "#16161A"
    assert Gate.IS_DARK
    Gate.use("Light")
    try:
        assert not Gate.IS_DARK
        assert Gate.PANEL != "#16161A"
        # The aliases older code imports follow the switch too.
        assert C.BG_SURFACE == Gate.PANEL
        assert C.TEXT_PRIMARY == Gate.TEXT
        assert C.ERROR_DIM == Gate.BAD_SURFACE
        # Text on the Light ground is dark, and reads (contrast well over 4.5:1).
        assert _contrast(Gate.TEXT, Gate.PANEL) > 7
        assert _contrast(Gate.TEXT_DIM, Gate.PANEL) >= 4.5
        assert _contrast(Gate.TEXT_ON_ACCENT, Gate.ACCENT) >= 4.5
        assert Gate.status_color("done") == Gate.OK
    finally:
        Gate.use("Dark")
    assert C.BG_SURFACE == "#16161A"


def test_every_palette_defines_the_same_names():
    dark_names = set(Gate.palette("Dark"))
    assert dark_names == set(Gate.palette("Light"))
    for name in dark_names:
        assert re.fullmatch(r"#[0-9A-F]{6}", Gate.palette("Light")[name]), name


def test_translucent_helpers_are_rgba_not_eight_digit_hex(dark):
    assert Gate.tint("#D9635F", 0.13) == "rgba(217, 99, 95, 0.13)"
    assert Gate.overlay(0.05) == "rgba(255, 255, 255, 0.05)"
    Gate.use("Light")
    try:
        # A light "lift" is darker, not white on white.
        assert Gate.overlay(0.05) == "rgba(0, 0, 0, 0.05)"
    finally:
        Gate.use("Dark")
    assert Gate.mix("#000000", "#FFFFFF", 0.5) == "#808080"


def test_sheet_resolves_longest_token_first():
    text = Gate.sheet("a: @LINE_SOFT; b: @LINE; c: @TEXT_ON_ACCENT; d: @TEXT;")
    assert "@" not in text
    assert f"a: {Gate.LINE_SOFT};" in text and f"b: {Gate.LINE};" in text
    assert f"c: {Gate.TEXT_ON_ACCENT};" in text and f"d: {Gate.TEXT};" in text


# -------------------------------------------------------------- stylesheet
def test_main_qss_has_no_colours_of_its_own():
    qss = (ROOT / "slate" / "resources" / "styles" / "main.qss").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", qss, flags=re.S)
    assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", body)
    assert not re.search(r"rgba?\(", body)
    # No universal font rule: a widget's own setFont() must be able to win.
    for rule in re.findall(r"(?:^|\})\s*(\*|QWidget)\s*\{([^}]*)\}", body):
        assert "font-size" not in rule[1] and "font-family" not in rule[1]
    # A bare ::item never gets a border or a background (Qt then drops cell colours).
    for block in re.findall(r"(?:QTableView|QTreeView|QListView)::item\s*\{([^}]*)\}", body):
        assert "border" not in block and "background" not in block


@pytest.mark.parametrize("mode", ["Dark", "Light"])
def test_built_stylesheet_is_fully_resolved_with_icons_on_disk(app, mode):
    from slate.core.infra.theme_manager import ThemeManager
    try:
        sheet = ThemeManager.build_stylesheet(mode)
        assert sheet
        sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
        assert not re.search(r"@[A-Z_]+", sheet), re.findall(r"@[A-Z_]+", sheet)[:5]
        urls = re.findall(r"url\(([^)]+)\)", sheet)
        assert urls, "the sheet should draw its arrows and ticks from icon files"
        for url in set(urls):
            assert os.path.exists(url), url
        assert "chevron-down.svg" in sheet and "chevron-up.svg" in sheet
        assert Gate.PANEL in sheet
    finally:
        Gate.use("Dark")


def test_apply_theme_sets_palette_font_and_keeps_explicit_fonts(app, light):
    from PySide6.QtGui import QFont, QPalette
    from PySide6.QtWidgets import QLabel

    assert app.palette().color(QPalette.ColorRole.Window).name().upper() == Gate.GROUND
    assert app.palette().color(QPalette.ColorRole.Highlight).name().upper() == Gate.ACCENT
    label = QLabel("Title")
    label.setFont(QFont("Segoe UI", 20))
    label.ensurePolished()
    # The old "QWidget { font-size }" rule turned this into ~12 px.
    assert label.font().pointSize() == 20


def test_controls_are_one_height(app, dark):
    from PySide6.QtWidgets import QComboBox, QDateEdit, QSpinBox, QWidget
    from slate.gui.core.controls import make_button

    holder = QWidget()
    row = QHBoxLayout(holder)
    widgets = [make_button("Save", "primary"), make_button("Cancel"), QPushButton("Plain"),
               QLineEdit(), QComboBox(), QSpinBox(), QDateEdit()]
    for widget in widgets:
        row.addWidget(widget)
    holder.show()
    app.processEvents()
    heights = {type(w).__name__ + str(i): w.sizeHint().height() for i, w in enumerate(widgets)}
    assert set(heights.values()) == {Gate.CONTROL_HEIGHT}, sorted(heights.items())
    holder.close()


# ------------------------------------------------------------------- icons
def test_film_is_a_film_strip_not_a_grid():
    from slate.gui.core import icons
    film = icons._PATHS["film"]
    assert film != "M3 5h18v14H3z M3 9h18 M3 15h18 M8 5v14 M16 5v14"
    assert film.count("h1") >= 8        # sprocket holes along both edges


def test_icons_the_areas_asked_for_exist(app):
    from slate.gui.core.icons import has_icon, icon
    for name in ("close", "help", "sign-out", "eye", "eye-off", "filter", "bell", "copy",
                 "star", "star-filled", "grid", "list", "pause", "stop", "skip-previous",
                 "skip-next", "step-back", "step-forward", "expand", "collapse", "volume",
                 "volume-off", "image", "video", "sequence", "chevron-left", "chevron-right",
                 "chevron-up", "more", "edit", "archive", "undo"):
        assert has_icon(name), name
        assert not icon(name).isNull(), name
    assert icon("no-such-icon").isNull()


# ----------------------------------------------------------------- buttons
def test_make_button_shows_ampersands_as_written(app):
    from slate.gui.core.controls import make_button, plain, strip_mnemonic
    button = make_button("Save & close")
    assert button.text() == "Save && close"
    assert strip_mnemonic(button.text()) == "Save & close"
    assert plain("Stress & crash") == "Stress && crash"
    assert make_button("&Save", mnemonic=True).text() == "&Save"


def test_button_kinds_decide_enter(app):
    from slate.gui.core.controls import make_button
    primary = make_button("Save", "primary")
    assert primary.isDefault() and primary.autoDefault()
    assert primary.property("kind") == "primary"
    for kind in ("secondary", "ghost", "danger"):
        other = make_button("Other", kind)
        assert not other.autoDefault() and not other.isDefault()


def _dialog_with(buttons):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    field = QLineEdit()
    layout.addWidget(field)
    row = QHBoxLayout()
    for button in buttons:
        row.addWidget(button)
    layout.addLayout(row)
    return dialog, field


def _press_enter(app, dialog, field):
    from PySide6.QtTest import QTest
    dialog.show()
    app.processEvents()
    field.setFocus()
    app.processEvents()
    QTest.keyClick(field, Qt.Key.Key_Return)
    app.processEvents()


def test_enter_presses_the_primary_button_not_cancel(app):
    from slate.gui.core.controls import make_button
    pressed = []
    cancel = make_button("Cancel", on_click=lambda: pressed.append("cancel"))
    save = make_button("Save", "primary", on_click=lambda: pressed.append("save"))
    dialog, field = _dialog_with([cancel, save])      # Cancel first, as the IT dialogs had it
    _press_enter(app, dialog, field)
    assert pressed == ["save"]
    dialog.close()


def test_dialog_policy_stops_plain_cancel_taking_enter(app):
    from slate.gui.core.controls import install_dialog_policy
    install_dialog_policy(app)
    pressed = []
    cancel = QPushButton("Cancel")                    # plain buttons: Qt makes both auto-default
    cancel.clicked.connect(lambda: pressed.append("cancel"))
    save = QPushButton("Save")
    save.clicked.connect(lambda: pressed.append("save"))
    dialog, field = _dialog_with([cancel, save])
    dialog.show()
    app.processEvents()
    assert not cancel.autoDefault()
    cancel.setFocus()                                 # even with focus on Cancel...
    app.processEvents()
    assert not cancel.isDefault()
    dialog.close()


def test_dialog_policy_leaves_a_lone_close_button_alone(app):
    from slate.gui.core.controls import apply_dialog_policy
    close = QPushButton("Close")
    dialog, _field = _dialog_with([close])
    apply_dialog_policy(dialog)
    assert close.autoDefault()


def test_button_box_reject_role_is_never_default(app):
    from slate.gui.core.controls import apply_dialog_policy
    dialog = QDialog()
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    QVBoxLayout(dialog).addWidget(box)
    apply_dialog_policy(dialog)
    assert not box.button(QDialogButtonBox.StandardButton.Cancel).autoDefault()
    assert box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_styled_buttons_are_the_same_kinds(app):
    from slate.gui.widgets.styled_buttons import DangerButton, GhostButton, PrimaryButton, DropdownButton
    assert PrimaryButton("Go").property("kind") == "primary"
    assert DangerButton("Delete").property("kind") == "danger"
    ghost = GhostButton("Refresh")
    assert ghost.property("kind") == "ghost" and not ghost.autoDefault()
    # GhostButton used to hard-code 16 px bold text.
    assert "16px" not in ghost.styleSheet()
    assert "padding-right: 32px" in DropdownButton("Manage Project").styleSheet()


# ------------------------------------------------------------------ tables
def test_style_table_shared_setup(app, dark):
    from slate.gui.core.table_style import style_table
    table = QTableWidget(3, 3)
    table.setHorizontalHeaderLabels(["Code", "Milestone", "Budget"])
    table.setStyleSheet("QTableWidget { color: #D9A441; }")
    style_table(table, {"Code": "contents", "Milestone": "stretch", "Budget": "numeric"})
    assert table.styleSheet() == ""
    assert not table.verticalHeader().isVisible()
    assert table.verticalHeader().defaultSectionSize() == Gate.ROW_HEIGHT
    assert table.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == header.ResizeMode.Stretch
    assert header.sectionResizeMode(0) == header.ResizeMode.ResizeToContents
    assert table.itemDelegateForColumn(2) is not None


def test_cell_status_colour_survives_the_stylesheet(app, dark):
    from slate.gui.core.table_style import set_cell_status, style_table
    table = QTableWidget(2, 1)
    table.setHorizontalHeaderLabels(["Status"])
    style_table(table)
    item = QTableWidgetItem("CONFLICT")
    set_cell_status(item, "bad")
    table.setItem(0, 0, item)
    table.setItem(1, 0, QTableWidgetItem("fine"))
    table.resize(300, 150)
    table.show()
    app.processEvents()
    image = table.viewport().grab().toImage()
    rect = table.visualItemRect(item)
    colour = image.pixelColor(rect.right() - 4, rect.center().y())
    plain = image.pixelColor(rect.right() - 4, table.visualItemRect(table.item(1, 0)).center().y())
    # The conflict cell is visibly redder than the plain one.
    assert colour.red() - colour.blue() > plain.red() - plain.blue() + 10
    table.close()


def test_numeric_items_sort_as_numbers(app):
    from slate.gui.core.table_style import numeric_item
    table = QTableWidget(3, 1)
    for row, value in enumerate((9, 100, 10)):
        table.setItem(row, 0, numeric_item(value, f"{value:,}"))
    table.sortItems(0)
    assert [table.item(r, 0).text() for r in range(3)] == ["9", "10", "100"]


# --------------------------------------------------------------- stat card
def test_stat_card_value_is_large_and_accent_is_a_strip(app, dark):
    from slate.gui.core.stat_card import StatCard, StatStrip
    card = StatCard("Overdue", 12, tone="bad")
    assert card.value_text() == "12"
    assert f"font-size: {Gate.SIZE_DISPLAY}px" in card._value.styleSheet()
    assert Gate.BAD in card._strip.styleSheet()
    assert "border-left" not in card.styleSheet()
    card.set_value("14 (2 late)")
    assert card.value_text() == "14 (2 late)"
    clicks = []
    strip = StatStrip(compact=True)
    late = strip.add("Late", 3, tone="warn", on_click=lambda: clicks.append(1))
    late.clicked.emit()
    assert clicks == [1]
    assert f"font-size: {Gate.SIZE_XL}px" in late._value.styleSheet()


# ------------------------------------------------------------- empty state
def test_empty_state_overlay_and_no_match_variant(app, dark):
    from slate.gui.core.empty_state import EmptyState
    table = QTableWidget(0, 2)
    table.resize(400, 300)
    table.show()
    state = EmptyState.over(table, "Nothing to rename yet", "Load files to preview new names.")
    app.processEvents()
    assert state.isVisible()
    assert state.parent() is table.viewport()
    table.setRowCount(1)
    app.processEvents()
    assert not state.isVisible()
    table.setRowCount(0)
    app.processEvents()
    assert state.isVisible()

    cleared = []
    state.set_filtered(True, on_clear=lambda: cleared.append(1), noun="tickets")
    assert state._heading.text() == "No tickets match"
    assert not state._clear_button.isHidden()
    state._clear_button.click()
    assert cleared == [1]
    state.set_filtered(False)
    assert state._heading.text() == "Nothing to rename yet"
    assert state._clear_button.isHidden()
    table.close()


def test_empty_state_glyph_is_visible_not_border_grey(app, dark):
    import inspect
    from slate.gui.core import empty_state
    source = inspect.getsource(empty_state.EmptyState.__init__)
    assert "Gate.LINE," not in source and "TEXT_DIM" in source


# ------------------------------------------------------------- the codemod
def test_codemod_rewrites_palette_colours_and_keeps_the_text_identical():
    from theme_codemod import rewrite_source
    source = (
        '"""Module doc #16161A stays."""\n'
        'from PySide6.QtGui import QColor\n'
        'def f(w):\n'
        '    w.setStyleSheet("QFrame { background: #16161A; color: white; }")\n'
        '    w.setStyleSheet("QPushButton { background: #3EA8BF; color: white; }")\n'
        '    return QColor("#D9635F"), "{x} #2C2C34".format(x=1), "rgba(255,255,255,0.05)"\n'
    )
    new, changes, report = rewrite_source(source, "x.py")
    assert changes == 5 and not report
    assert "from slate.core.infra.gate import Gate" in new
    assert '"""Module doc #16161A stays."""' in new
    namespace = {}
    exec(compile(new, "x.py", "exec"), namespace)

    class Sink:
        sheets = []

        def setStyleSheet(self, text):
            self.sheets.append(text)

    sink = Sink()
    colour, formatted, wash = namespace["f"](sink)
    assert sink.sheets[0] == "QFrame { background: #16161A; color: #E8E6E1; }"
    # White on the accent becomes the on-accent text colour.
    assert sink.sheets[1] == f"QPushButton {{ background: #3EA8BF; color: {Gate.TEXT_ON_ACCENT}; }}"
    assert colour.name().upper() == "#D9635F"
    assert formatted == "1 #2C2C34"
    assert wash == "rgba(255, 255, 255, 0.05)"


# Files that are documents or other programs, not themed screens.
_NOT_THEMED = re.compile(
    r"^slate/(licence\.py|core/infra/(gate|design_tokens|theme_manager|style_builder)\.py|api/|"
    r"core/domain/(delivery_report|ingest/reporter)\.py|utils/reporting\.py|scripts/|"
    r"gui/tabs/vfx_dashboard_pro/ui/qss_generator\.py)")


def test_no_screen_types_the_dark_palette_as_hex():
    """
    The Light theme only reaches a screen whose colours come from Gate. A hex
    code from the dark palette typed back into a screen is dark in Light too;
    run tools/theme_codemod.py on the file (or use a Gate token by meaning).
    """
    from theme_codemod import rewrite_source
    offenders = []
    for path in sorted((ROOT / "slate").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if _NOT_THEMED.match(rel):
            continue
        source = path.read_text(encoding="utf-8")
        if not re.search(r"#[0-9A-Fa-f]{6}|color: ?(white|black)|rgba\(", source):
            continue
        _new, changes, _report = rewrite_source(source, rel)
        if changes:
            offenders.append(f"{rel} ({changes})")
    assert not offenders, "hard-coded palette colours in: " + ", ".join(offenders)


def _contrast(a, b):
    def lum(hex_colour):
        c = QColor(hex_colour)
        parts = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            parts.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
