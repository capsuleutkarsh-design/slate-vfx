"""
The help window.

It used to be a horizontal strip of tabs. That works for five sections and
falls apart at twenty-one: the strip overflowed, Qt put small scroll arrows at
each end, and the only way to learn what help existed was to click an arrow
repeatedly. You cannot read a table of contents you have to scroll through one
item at a time.

So it is built the way the application itself is built - a list down the left,
grouped under headings, and the page on the right. Everything Slate can explain
is visible at once, and the search narrows that list rather than replacing
whatever you were reading.

    from ut_vfx.gui.help_dialog import show_help
    show_help(self, "leave", mode="ops")
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QStackedWidget, QTextBrowser, QVBoxLayout,
)

from ..core.help_content import HELP_CONTENT, get_all_tabs
from ..core.infra.gate import Gate
from .core.controls import make_button
from .core.icons import icon as drawn_icon


# The order sections appear in, and the headings they sit under. A section not
# named here still appears, under "More" - so adding one to the JSON is enough
# to make it show up, and forgetting to list it here is untidy rather than
# invisible.
GROUPS = [
    ("Start here", ["getting_started", "home"]),

    # The VFX client's own screens.
    ("Production", ["folder_creator", "rename_tool", "stock_browser",
                    "shot_review", "dashboard", "scheduling", "bidding"]),

    # The operations shell's. Split into the two teams that use them rather
    # than one long list, because almost nobody works across both.
    ("People",     ["attendance", "leave", "joining_leaving", "users_roles"]),
    ("IT",         ["it_support", "hardware", "licences", "deployment"]),

    ("System",     ["settings", "admin_panel", "tester", "workspace_info"]),
]

_ROLE_SECTION = Qt.ItemDataRole.UserRole
_ROLE_HEADING = Qt.ItemDataRole.UserRole + 1


class HelpDialog(QDialog):
    """Slate's help, as a browsable table of contents."""

    def __init__(self, parent=None, initial_tab="getting_started", mode=None):
        # Which application this is: "vfx", "ops", or None for both. The two
        # shells do not share a sidebar, so they do not share help.
        self.mode = mode
        super().__init__(parent)

        self.setWindowTitle("Slate Help")
        self.setMinimumSize(940, 620)
        self.resize(1180, 800)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QDialog {{ background: {Gate.GROUND}; }}")

        self._pages = {}      # section id -> index in the stack
        self._items = []      # (item, section id, searchable text)

        self._build()
        self._populate()
        self.set_active_tab(initial_tab)

    # ------------------------------------------------------------------ build
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar())
        body.addWidget(self._content(), 1)
        root.addLayout(body, 1)

        root.addWidget(self._footer())

    def _header(self):
        bar = QFrame()
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            f"QFrame {{ background: {Gate.PANEL}; border: none; "
            f"border-bottom: 1px solid {Gate.LINE}; }}")

        row = QHBoxLayout(bar)
        row.setContentsMargins(Gate.SPACE_4, Gate.SPACE_3, Gate.SPACE_4, Gate.SPACE_3)
        row.setSpacing(Gate.SPACE_3)

        # The same mark as the application header, from the same path data.
        try:
            from .core.icons_brand import slate_mark
            mark = QLabel()
            mark.setPixmap(slate_mark(Gate.ACCENT, 26).pixmap(26, 26))
            mark.setStyleSheet("background: transparent; border: none;")
            row.addWidget(mark)
        except Exception:
            logging.debug("help: brand mark unavailable", exc_info=True)

        title = QLabel("HELP")
        title.setStyleSheet(
            f"color: {Gate.TEXT}; font-family: {Gate.FONT_LABEL_STRONG}; "
            f"font-size: 18px; font-weight: 700; letter-spacing: 2.5px; "
            f"background: transparent; border: none;")
        row.addWidget(title)

        which = {"vfx": "VFX", "ops": "OPERATIONS"}.get(str(self.mode or "").lower())
        if which:
            badge = QLabel(which)
            badge.setStyleSheet(
                f"color: {Gate.ACCENT}; font-family: {Gate.FONT_LABEL}; font-size: 11px; "
                f"letter-spacing: 1.6px; background: transparent; "
                f"border: 1px solid {Gate.LINE}; border-radius: {Gate.RADIUS_SM}px; "
                f"padding: 3px 9px;")
            row.addWidget(badge)

        row.addStretch(1)

        self.crumb = QLabel("")
        self.crumb.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-size: 12px; "
            f"background: transparent; border: none;")
        row.addWidget(self.crumb)
        return bar

    def _sidebar(self):
        panel = QFrame()
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(268)
        panel.setStyleSheet(
            f"QFrame {{ background: {Gate.PANEL}; border: none; "
            f"border-right: 1px solid {Gate.LINE}; }}")

        box = QVBoxLayout(panel)
        box.setContentsMargins(Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_3, Gate.SPACE_3)
        box.setSpacing(Gate.SPACE_2)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search help...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.setStyleSheet(f"""
            QLineEdit {{
                background: {Gate.GROUND};
                border: 1px solid {Gate.LINE};
                border-radius: {Gate.RADIUS_SM}px;
                padding: 7px 10px;
                color: {Gate.TEXT};
            }}
            QLineEdit:focus {{ border: 1px solid {Gate.ACCENT}; }}
        """)
        box.addWidget(self.search)

        self.nav = QListWidget()
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.nav.currentItemChanged.connect(self._picked)
        self.nav.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{
                color: {Gate.TEXT_2};
                padding: 7px 9px;
                border-radius: {Gate.RADIUS_SM}px;
                margin: 1px 0;
            }}
            QListWidget::item:hover {{ background: {Gate.RAISED}; color: {Gate.TEXT}; }}
            QListWidget::item:selected {{
                background: rgba(62, 168, 191, 0.15);
                color: {Gate.ACCENT};
            }}
        """)
        box.addWidget(self.nav, 1)

        self.tally = QLabel("")
        self.tally.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-size: 11px; "
            f"background: transparent; border: none;")
        box.addWidget(self.tally)
        return panel

    def _content(self):
        holder = QFrame()
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        holder.setStyleSheet(f"QFrame {{ background: {Gate.GROUND}; border: none; }}")

        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        self.stack = QStackedWidget()
        box.addWidget(self.stack, 1)

        # Shown when a search matches nothing. A page rather than a dialog, so
        # the window does not jump about while somebody is still typing.
        self.nothing = QTextBrowser()
        self.nothing.setFrameShape(QFrame.Shape.NoFrame)
        self.nothing.setStyleSheet(self._browser_style())
        self.stack.addWidget(self.nothing)
        return holder

    def _footer(self):
        bar = QFrame()
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet(
            f"QFrame {{ background: {Gate.PANEL}; border: none; "
            f"border-top: 1px solid {Gate.LINE}; }}")

        row = QHBoxLayout(bar)
        row.setContentsMargins(Gate.SPACE_4, Gate.SPACE_3, Gate.SPACE_4, Gate.SPACE_3)

        hint = QLabel("F1 opens help for whatever screen you are on")
        hint.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-size: 12px; "
            f"background: transparent; border: none;")
        row.addWidget(hint)
        row.addStretch(1)
        row.addWidget(make_button("Close", "secondary", on_click=self.accept))
        return bar

    # --------------------------------------------------------------- populate
    def _populate(self):
        sections = get_all_tabs(self.mode)
        available = {s["id"]: s for s in sections}

        ordered = []
        for heading, ids in GROUPS:
            picked = [available.pop(i) for i in ids if i in available]
            if picked:
                ordered.append((heading, picked))
        if available:
            ordered.append(("More", list(available.values())))

        if not ordered:
            self._empty("No help available",
                        "The help content could not be loaded.")
            self.tally.setText("")
            return

        for heading, items in ordered:
            head = QListWidgetItem(heading.upper())
            head.setFlags(Qt.ItemFlag.NoItemFlags)      # a label, not a choice
            head.setData(_ROLE_HEADING, True)
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            head.setFont(font)
            head.setForeground(QColor(Gate.TEXT_DIM))
            self.nav.addItem(head)

            for section in items:
                body = HELP_CONTENT.get(section["id"], {})

                item = QListWidgetItem(section["title"])
                item.setData(_ROLE_SECTION, section["id"])
                glyph = section.get("icon") or ""
                if glyph:
                    item.setIcon(drawn_icon(glyph, Gate.TEXT_2, 16))
                self.nav.addItem(item)

                page = QTextBrowser()
                page.setOpenExternalLinks(True)
                page.setFrameShape(QFrame.Shape.NoFrame)
                page.setStyleSheet(self._browser_style())
                try:
                    page.setHtml(self.format_html(body.get("content", "")))
                except Exception:
                    logging.exception("help: could not render %s", section["id"])
                    page.setHtml(self.format_html(
                        "<h2>This page could not be shown</h2>"
                        "<p>The rest of the help still works.</p>"))
                self._pages[section["id"]] = self.stack.addWidget(page)

                self._items.append((
                    item, section["id"],
                    (section["title"] + " " + body.get("content", "")).lower()))

        self.tally.setText("%d pages" % len(self._items))

    def _empty(self, title, message):
        self.nothing.setHtml(self.format_html(
            f'<h1 style="color:{Gate.WARN};">{title}</h1><p>{message}</p>'))
        self.stack.setCurrentWidget(self.nothing)

    # ----------------------------------------------------------------- events
    def _picked(self, current, _previous=None):
        if current is None or current.data(_ROLE_HEADING):
            return
        section_id = current.data(_ROLE_SECTION)
        if section_id in self._pages:
            self.stack.setCurrentIndex(self._pages[section_id])
            self.crumb.setText(current.text())

    def _filter(self, query):
        """
        Narrow the list rather than replacing the page.

        The search used to overwrite whatever you were reading with a results
        page, so a typo lost your place. This hides what does not match and
        leaves the page alone.
        """
        query = (query or "").strip().lower()

        if not query:
            for i in range(self.nav.count()):
                self.nav.item(i).setHidden(False)
            self.tally.setText("%d pages" % len(self._items))
            if self.stack.currentWidget() is self.nothing:
                self.set_active_tab(None)
            return

        matched = 0
        for item, _section_id, haystack in self._items:
            hit = query in haystack
            item.setHidden(not hit)
            matched += int(hit)

        # A heading with nothing under it is noise.
        head, seen = None, 0
        for i in range(self.nav.count() + 1):
            item = self.nav.item(i) if i < self.nav.count() else None
            if item is None or item.data(_ROLE_HEADING):
                if head is not None:
                    head.setHidden(seen == 0)
                head, seen = item, 0
            elif not item.isHidden():
                seen += 1

        self.tally.setText("%d of %d pages match" % (matched, len(self._items)))
        if matched == 0:
            self._empty("Nothing matches &ldquo;%s&rdquo;" % query,
                        "Try another word &mdash; the search reads every page, "
                        "not just their titles.")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        elif (event.key() == Qt.Key.Key_F
              and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.search.setFocus()
            self.search.selectAll()
        else:
            super().keyPressEvent(event)

    # -------------------------------------------------------------------- api
    def set_active_tab(self, tab_id):
        """Open a section by id. Falls back to the first page."""
        for item, section_id, _haystack in self._items:
            if section_id == tab_id:
                self.nav.setCurrentItem(item)
                self._picked(item)
                return
        for item, _section_id, _haystack in self._items:
            if not item.isHidden():
                self.nav.setCurrentItem(item)
                self._picked(item)
                return

    # ----------------------------------------------------------------- render
    def _browser_style(self):
        return (f"QTextBrowser {{ background: {Gate.GROUND}; border: none; "
                f"padding: {Gate.SPACE_4}px {Gate.SPACE_5}px; }}")

    def format_html(self, content):
        """
        Wrap a section in the page styling.

        Colours come from Gate rather than being repeated here, so the help
        cannot drift away from the rest of the product's palette.
        """
        return f"""
        <html><head><style>
            body {{
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 14px;
                line-height: 1.6;
                color: {Gate.TEXT_2};
            }}
            h1 {{
                color: {Gate.TEXT};
                font-size: 25px;
                margin: 0 0 4px 0;
                padding-bottom: 10px;
                border-bottom: 1px solid {Gate.LINE};
            }}
            h2 {{ color: {Gate.ACCENT}; font-size: 17px; margin: 28px 0 4px 0; }}
            h3 {{ color: {Gate.TEXT}; font-size: 15px; margin: 20px 0 2px 0; }}
            p  {{ margin: 9px 0; }}
            b  {{ color: {Gate.TEXT}; }}
            ul {{ margin: 8px 0 8px 18px; }}
            li {{ margin: 5px 0; }}
            code {{
                background: {Gate.PANEL};
                color: {Gate.OK};
                padding: 1px 5px;
                border-radius: 3px;
                font-family: Consolas, monospace;
            }}
            pre {{
                background: {Gate.PANEL};
                border-left: 2px solid {Gate.ACCENT};
                padding: 13px 15px;
                margin: 14px 0;
                font-family: Consolas, monospace;
                font-size: 12.5px;
                color: {Gate.TEXT_2};
            }}
            table {{ border-collapse: collapse; margin: 14px 0; width: 100%; }}
            th {{
                background: {Gate.RAISED};
                color: {Gate.TEXT_DIM};
                font-size: 11px;
                letter-spacing: 1px;
                text-align: left;
                padding: 9px 12px;
                border: 1px solid {Gate.LINE};
            }}
            td {{
                padding: 9px 12px;
                border: 1px solid {Gate.LINE};
                vertical-align: top;
            }}
        </style></head>
        <body>{content}</body></html>
        """


def show_help(parent=None, tab_id="getting_started", mode=None):
    """
    Show the help window.

    Args:
        parent: Parent widget
        tab_id: Section to open on
        mode: "vfx", "ops", or None for everything. Decides which sections are
              offered, so each application's help matches its own sidebar.
    """
    dialog = HelpDialog(parent, initial_tab=tab_id, mode=mode)
    dialog.exec()
