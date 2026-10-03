"""
Tab Coordinator Component.

Manages tab registration, initialization, visibility and navigation.

Extracted from main_window.py for better maintainability.
"""

from PySide6.QtWidgets import (
    QListWidgetItem, QWidget, QHBoxLayout, QLabel, QFrame, QSizePolicy, QScrollArea,
    QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QSize, Signal, QObject, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QIcon
import html
import logging
from slate.core.infra.gate import Gate

# A navigation entry and a category rule. They used to be the same height, 50px
# each, so twenty entries and five headers wanted 1250px of a window that is
# routinely 1000px tall - the last entries were clipped mid-word.
NAV_ROW_HEIGHT = 34
HEADER_ROW_HEIGHT = 26
RAIL_HEADER_HEIGHT = 16


# What a sidebar entry gets when it has no drawn icon (a plugin with a letter
# for an icon): a blank row there threw its label out of line with the rest.
DEFAULT_NAV_ICON = "package"


def nav_icon(icon_name):
    """
    The entry's drawn icon: dim at rest, accent when selected - the selected
    row's text turned accent while its icon stayed grey.
    """
    from ..core.icons import pixmap, has_icon
    name = icon_name if icon_name and has_icon(icon_name) else DEFAULT_NAV_ICON
    result = QIcon()
    for size in (18, 36):
        result.addPixmap(pixmap(name, Gate.TEXT_2, size), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, Gate.ACCENT, size), QIcon.Mode.Selected)
    return result


def _apply_nav_icon(item, icon_name):
    """Put a drawn icon on a navigation entry (a default one if we have none for it)."""
    try:
        item.setIcon(nav_icon(icon_name))
    except Exception as exc:
        logging.debug("Nav icon skipped for %r: %s", icon_name, exc)


def nav_tooltip(label, description=""):
    """
    The entry's name in bold, then what it is for. Folded to icons, the
    sidebar showed only the description and never said which screen it was.
    """
    name = f"<b>{html.escape(str(label))}</b>"
    return f"{name}<br>{html.escape(str(description))}" if description else name


# Where a sidebar entry keeps its unread / waiting count (see set_badge).
BADGE_ROLE = Qt.ItemDataRole.UserRole + 40


class PageScroll(QScrollArea):
    """
    The frame every tab sits in, so a page scrolls instead of forcing the
    window to be as tall as its tallest page.

    The main window used to refuse any height under 768 px - on a 1366x768
    laptop with a taskbar the footer and the bottom of every dialog anchor
    were off-screen. It can now be made much smaller; a page that genuinely
    needs more room than that shows a scroll bar rather than pushing the
    window past the screen. A page that fits looks exactly as before: the
    frame is invisible and the page is resized to fill it.
    """

    def __init__(self, page: QWidget, parent=None):
        super().__init__(parent)
        self.page = page
        self.setObjectName("PageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea#PageScroll { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setWidget(page)


def page_of(stack_widget):
    """The tab inside a PageScroll, or the widget itself."""
    if isinstance(stack_widget, PageScroll):
        return stack_widget.page
    return stack_widget


class NavBadgeDelegate(QStyledItemDelegate):
    """
    Draws a count on a sidebar entry: "3 waiting", "2 new replies".

    Painted over the normal item (so the sidebar stylesheet still decides how
    the entry itself looks): a small pill on the right when the sidebar is
    open, a dot on the icon when it is folded to icons.
    """

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        count = index.data(BADGE_ROLE)
        try:
            count = int(count or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return
        from slate.core.infra.gate import Gate
        text = str(count) if count <= 99 else "99+"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        collapsed = not str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(Gate.ACCENT))
        if collapsed:
            d = 9.0
            painter.drawEllipse(QRectF(rect.center().x() + 5, rect.top() + 7, d, d))
        else:
            font = QFont(painter.font())
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            w = max(20.0, painter.fontMetrics().horizontalAdvance(text) + 12.0)
            h = 18.0
            pill = QRectF(rect.right() - w - 10, rect.center().y() - h / 2, w, h)
            painter.drawRoundedRect(pill, h / 2, h / 2)
            painter.setPen(QColor(Gate.TEXT_ON_ACCENT))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class CategoryHeaderWidget(QWidget):
    """
    A group heading in the sidebar: a chevron that says whether the group is
    open, the name, and a rule. Clicking it folds the group away or brings it
    back. Folded to icons, a folded group shows a chevron and how many screens
    it holds - it used to be a bare 1 px dash, and its screens simply vanished.
    """

    clicked = Signal()

    def __init__(self, label_text: str, parent=None):
        super().__init__(parent)
        self.label_text = label_text
        self.count = 0
        self._folded = False
        self._collapsed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(6)
        self._layout = layout

        # The chevron, drawn and big enough to see (it was a 9 px text speck
        # that looked the same open or folded).
        self.chevron = QLabel()
        self.chevron.setFixedSize(14, 14)
        self.chevron.setStyleSheet("background: transparent;")

        self.lbl = QLabel(label_text.upper())
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.lbl.setMinimumWidth(0)

        # The rule shrinks first, to nothing, before the name gives way.
        self.right_line = QFrame()
        self.right_line.setFixedHeight(1)
        self.right_line.setMinimumWidth(0)
        self.right_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.right_line.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {Gate.tint(Gate.ACCENT, 0.4)}, stop:1 {Gate.tint(Gate.ACCENT, 0)});")

        # Kept for anything that reaches for it; the rule now sits on the right.
        self.left_line = QFrame()
        self.left_line.setFixedHeight(1)
        self.left_line.hide()

        # Folded to icons: how many screens are folded away here.
        self.count_label = QLabel("")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # An outline in the dim text colour: the filled teal pill was the
        # unread badge's look ("3 waiting").
        self.count_label.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; background: transparent; border: 1px solid {Gate.TEXT_DIM}; "
            "border-radius: 7px; font-size: 10px; font-weight: 700; padding: 0 4px;")
        self.count_label.setFixedHeight(14)
        self.count_label.hide()

        layout.addWidget(self.chevron)
        layout.addWidget(self.lbl)
        layout.addWidget(self.left_line)
        layout.addWidget(self.right_line, 1)
        layout.addWidget(self.count_label)
        self._restyle()

    def _label_sheet(self, tight: bool) -> str:
        spacing = "0.5px" if tight else "1.5px"
        return (f"color: {Gate.ACCENT}; font-size: 11px; font-weight: 800; letter-spacing: {spacing}; "
                "background: transparent; padding: 0px; margin: 0px;")

    def set_holds_current(self, holds: bool):
        """Folded over the screen that is open: the heading is marked, so where you are stays visible."""
        if bool(holds) != getattr(self, "_holds_current", False):
            self._holds_current = bool(holds)
            self.setStyleSheet(f"CategoryHeaderWidget {{ background: {Gate.SELECTION}; border-radius: 4px; }}"
                               if self._holds_current else "")

    def _restyle(self):
        from ..core.icons import pixmap
        name = "chevron-right" if self._folded else "chevron-down"
        self.chevron.setPixmap(pixmap(name, Gate.ACCENT, 12))
        self._fit_text()
        if self._collapsed:
            self.lbl.hide()
            self.right_line.hide()
            self.count_label.setText(str(self.count) if self._folded and self.count else "")
            self.count_label.setVisible(self._folded and bool(self.count))
            self.chevron.setVisible(self._folded)
            self._layout.setContentsMargins(6, 0, 6, 0)
            if not self._folded:
                # Open: a simple rule between groups, as before.
                self.right_line.show()
        else:
            self.lbl.show()
            self.right_line.show()
            self.chevron.show()
            self.count_label.hide()
        self._update_tooltip()

    def _fit_text(self):
        tight = 0 < self.width() < 230
        self.lbl.setStyleSheet(self._label_sheet(tight))
        if not self._collapsed:
            self._layout.setContentsMargins(8 if tight else 12, 0, 8 if tight else 12, 0)
        # The name is only shortened when it still does not fit.
        room = self.width() - self._layout.contentsMargins().left() - self._layout.contentsMargins().right() - 24
        text = self.label_text.upper()
        if room > 0:
            from PySide6.QtGui import QFontMetrics
            self.lbl.ensurePolished()
            text = QFontMetrics(self.lbl.font()).elidedText(text, Qt.TextElideMode.ElideRight, room)
        self.lbl.setText(text)

    def display_name(self) -> str:
        """'IT & INFRA' -> 'IT & Infra': short words (IT) keep their capitals."""
        return " ".join(w if len(w) <= 2 else w.capitalize() for w in self.label_text.split())

    def _update_tooltip(self):
        state = "click to show them" if self._folded else "click to fold them away"
        count = f"{self.count} screen{'s' if self.count != 1 else ''}"
        self.setToolTip(f"{self.display_name()} ({count}) - {state}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self.setStyleSheet(f"CategoryHeaderWidget {{ background: {Gate.overlay(0.05)}; border-radius: 4px; }}")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(f"CategoryHeaderWidget {{ background: {Gate.SELECTION}; border-radius: 4px; }}"
                           if getattr(self, "_holds_current", False) else "")
        super().leaveEvent(event)

    def set_folded(self, folded: bool, count=None):
        self._folded = bool(folded)
        if count is not None:
            self.count = int(count)
        self._restyle()

    def is_folded(self) -> bool:
        return self._folded

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._restyle()


class TabCoordinator(QObject):
    """
    Coordinates tab management for the main window.
    
    Handles:
    - Tab registration and initialization
    - Tab visibility management
    - Navigation between tabs
    - Permission-based tab access
    """
    
    tab_switched = Signal(str)  # tab_name
    folds_changed = Signal(list)  # labels of the folded groups, to remember
    
    def __init__(self, parent_window, sidebar_nav, content_stack):
        """
        Initialize the coordinator.
        
        Args:
            parent_window: The main window
            sidebar_nav: QListWidget for sidebar navigation
            content_stack: QStackedWidget for tab content
        """
        super().__init__(parent_window)
        self.parent = parent_window
        self.sidebar_nav = sidebar_nav
        self.content_stack = content_stack
        self.nav_items = []  # List of {page, item} dicts
        
        # Lazy loading infrastructure (Improvement #4)
        self.tab_factories = {}  # Factory functions for each tab
        self.tab_instances = {}  # Cached instances
        self.tab_labels = []  # Ordered list of tab labels
        self.header_items = set()  # Set of row indices that are category headers

        # Category groups, so the navigation can be folded away. Every entry and
        # every rule used to be on screen at once, which needs more height than
        # the window has - the last entries were simply cut off.
        self.groups = []  # [{'label', 'header_row', 'widget', 'rows': [], 'folded': bool}]

        # Counts shown on sidebar entries (set_badge).
        self._badges = {}
        try:
            self.sidebar_nav.setItemDelegate(NavBadgeDelegate(self.sidebar_nav))
        except Exception as exc:
            logging.debug("Sidebar badge delegate not installed: %s", exc)

        # Connect navigation signal
        self.sidebar_nav.currentRowChanged.connect(self._on_nav_changed)
        # Headers are not selectable, so currentRowChanged never fires for them.
        self.sidebar_nav.itemClicked.connect(self._on_item_clicked)
    
    def register_tab(self, page_widget, label, icon="", permission_key=None, 
                     visible=True, user_role=None, allowed_tabs=None):
        """
        Register a tab for management.
        
        Args:
            page_widget: The tab widget to add
            label: Display label for the tab
            icon: Optional icon emoji/text
            permission_key: Permission key to check
            visible: Whether tab should be visible by default
            user_role: Current user's role
            allowed_tabs: List of allowed tab permissions
        
        Returns:
            bool: True if tab was added, False if filtered by permissions
        """
        if not page_widget:
            return False
        
        # Check if already added
        for entry in self.nav_items:
            if entry['page'] == page_widget:
                logging.warning(f"Tab {label} already registered")
                return False
        
        # Permission check. A tab with a key needs that key. A person whose
        # roles grant nothing (a deleted, misspelt or not-yet-created role) gets
        # nothing - this used to skip the check when the list was empty, which
        # opened every tab, Admin Panel included, to exactly those people.
        if permission_key:
            allowed_tabs = allowed_tabs or []
            is_dev = str(user_role or "").strip().lower() == "developer"
            has_perm = (permission_key in allowed_tabs) or ("ALL" in allowed_tabs)

            # DEBUG LOGGING
            logging.debug(f"[SCAN] Tab Registration: '{label}'")
            logging.debug(f"   Permission Key: '{permission_key}'")
            logging.debug(f"   User Role: '{user_role}' | Is Developer: {is_dev}")
            logging.debug(f"   Allowed Tabs: {allowed_tabs}")
            logging.debug(f"   Has Permission: {has_perm}")
            
            # Skip if no permission (unless developer)
            if not (is_dev or has_perm):
                logging.warning(f"[SKIP] Tab '{label}' FILTERED - No permission")
                # Some tabs need to be added but hidden (for workflow switching)
                if not visible:
                    pass  # Continue to add but hidden
                else:
                    return False
            else:
                logging.debug(f"[OK] Tab '{label}' ALLOWED")
        
        # Add to stack, in the scrolling frame every page sits in.
        self.content_stack.addWidget(PageScroll(page_widget))
        
        # Add to sidebar
        is_collapsed = getattr(self, "sidebar_collapsed", False)
        
        # The icon is drawn, not typed. It used to be an emoji inside the
        # label text, which Windows rendered in whatever font it fancied - some
        # in colour, some not, on different baselines.
        item = QListWidgetItem(" " if is_collapsed else label)
        _apply_nav_icon(item, icon)
        item.setSizeHint(QSize(0, NAV_ROW_HEIGHT))
        item.setToolTip(nav_tooltip(label, getattr(page_widget, "plugin_description", "")))
        
        if is_collapsed:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
        self.sidebar_nav.addItem(item)
        
        # Set visibility
        item.setHidden(not visible)
        
        # Store mapping
        self.tab_factories.setdefault(label, {'factory': None, 'icon': icon,
                                              'permission': permission_key,
                                              'visible': visible, 'locked': False})
        self.nav_items.append({
            'page': page_widget,
            'item': item,
            'label': label,
            'permission': permission_key,
            # Whether this entry is allowed to be seen at all. Folding a group
            # must not reveal a tab the person has no permission for.
            'permitted': bool(visible),
        })
        if self.groups:
            self.groups[-1]['rows'].append(self.sidebar_nav.count() - 1)
        if label not in self.tab_labels:
            self.tab_labels.append(label)
        self.tab_instances[label] = page_widget
        
        logging.debug(f"Tab registered: {label}")
        return True
    
    def add_category_header(self, label: str):
        """
        Add a non-selectable category header to the sidebar.
        
        Args:
            label: Display label for the header
        """
        item = QListWidgetItem("")
        item.setSizeHint(QSize(0, RAIL_HEADER_HEIGHT if getattr(self, "sidebar_collapsed", False)
                               else HEADER_ROW_HEIGHT))
        
        # Not an entry: not selectable, and "disabled" so the sidebar sheet
        # can give it no item padding (QListWidget::item:disabled). With the
        # entries' 12 px padding the heading had 6 px of height to draw in.
        # The heading widget takes the clicks itself.
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        
        self.sidebar_nav.addItem(item)
        
        # Set premium widget
        widget = CategoryHeaderWidget(label)
        self.sidebar_nav.setItemWidget(item, widget)
        
        # Keep track of header indices
        row_index = self.sidebar_nav.count() - 1
        widget.clicked.connect(lambda row=row_index: self.toggle_group(row))
        self.header_items.add(row_index)
        self.groups.append({
            'label': label,
            'header_row': row_index,
            'widget': widget,
            'rows': [],
            'folded': False,
        })
        
        # Add a placeholder to tab_labels so indices stay aligned
        self.tab_labels.append(f"__HEADER__{label}")
        
        # Keep content_stack index in 1:1 sync with sidebar_nav
        header_placeholder = QWidget()
        self.content_stack.addWidget(header_placeholder)
        
        logging.debug(f"Category header added: {label}")
    
    def set_tab_visible(self, page_widget, visible, rename_to=None):
        """
        Set visibility of a tab.
        
        Args:
            page_widget: The tab widget
            visible: True to show, False to hide
            rename_to: Optional new label text
        """
        if not page_widget:
            return
        
        for entry in self.nav_items:
            if entry['page'] == page_widget:
                entry['item'].setHidden(not visible)
                if rename_to:
                    entry['item'].setText(rename_to)
                return

    def set_sidebar_collapsed(self, collapsed: bool):
        """Update the text of sidebar items based on collapse state."""
        self.sidebar_collapsed = collapsed
        
        # We need to match the items in self.sidebar_nav with self.tab_factories 
        # to know their original icon and label.
        # The QListWidget items are in the same order as self.tab_labels
        for i in range(self.sidebar_nav.count()):
            item = self.sidebar_nav.item(i)
            if i < len(self.tab_labels):
                label = self.tab_labels[i]
                if label.startswith("__HEADER__"):
                    widget = self.sidebar_nav.itemWidget(item)
                    if widget and hasattr(widget, 'set_collapsed'):
                        widget.set_collapsed(collapsed)
                    # In the icon rail a heading is only a rule (or a chevron
                    # when folded): 16 px, so an admin's list fits the height.
                    item.setSizeHint(QSize(0, RAIL_HEADER_HEIGHT if collapsed else HEADER_ROW_HEIGHT))
                    continue # Do not modify headers
                
                factory_data = self.tab_factories.get(label, {})
                icon = factory_data.get('icon', '')
                is_locked = factory_data.get('locked', False)
                
                # Format text. The icon is drawn on the item rather than typed
                # into the label, so it never appears twice and never depends on
                # which emoji font Windows happens to choose.
                if collapsed:
                    display_text = " "
                elif is_locked:
                    display_text = f"[LOCKED] {label}"
                else:
                    display_text = label

                item.setText(display_text)
                _apply_nav_icon(item, icon)
                if collapsed:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------ folding
    def _group_for_row(self, row):
        for group in self.groups:
            if group['header_row'] == row:
                return group
        return None

    def _group_containing(self, row):
        for group in self.groups:
            if row in group['rows']:
                return group
        return None

    def _apply_group(self, group):
        """Show or hide a group's entries, without overriding permissions."""
        permitted_rows = 0
        for row in group['rows']:
            item = self.sidebar_nav.item(row)
            if item is None:
                continue
            entry = next((e for e in self.nav_items if e.get('item') is item), None)
            permitted = entry.get('permitted', True) if entry else True
            permitted_rows += int(bool(permitted))
            item.setHidden(group['folded'] or not permitted)

        widget = group.get('widget')
        if widget is not None and hasattr(widget, 'set_folded'):
            widget.set_folded(group['folded'], permitted_rows)
        if widget is not None and hasattr(widget, 'set_holds_current'):
            widget.set_holds_current(group['folded'] and self.sidebar_nav.currentRow() in group['rows'])

    def set_group_folded(self, label, folded):
        for group in self.groups:
            if group['label'] == label:
                group['folded'] = bool(folded)
                self._apply_group(group)
                return True
        return False

    def toggle_group(self, row):
        """A click on a heading: fold the group away or bring it back, and remember it."""
        group = self._group_for_row(row)
        if group is None:
            return False
        group['folded'] = not group['folded']
        self._apply_group(group)
        self.folds_changed.emit(self.folded_groups())
        return True

    def folded_groups(self):
        """The labels of the groups the person has folded away."""
        return [g['label'] for g in self.groups if g['folded']]

    def restore_folds(self, labels):
        """Fold the groups the person had folded last time (nothing folds by itself)."""
        wanted = {str(label) for label in (labels or [])}
        for group in self.groups:
            group['folded'] = group['label'] in wanted
            self._apply_group(group)

    def content_height(self):
        """How much room the visible navigation entries want, margins included."""
        total = 0
        for row in range(self.sidebar_nav.count()):
            item = self.sidebar_nav.item(row)
            if item is not None and not item.isHidden():
                # Each entry has a 2 px margin above and below (sidebar sheet).
                total += item.sizeHint().height() + 4
        return total

    def tab_rows(self):
        """Sidebar rows of the screens this person has, in order (no headings)."""
        rows = []
        for entry in self.nav_items:
            item = entry.get('item')
            if item is None or not entry.get('permitted', True):
                continue
            row = self.sidebar_nav.row(item)
            if row >= 0 and row not in self.header_items:
                rows.append(row)
        return sorted(rows)

    def _on_item_clicked(self, item):
        """A click on a category rule folds that group away, or brings it back."""
        row = self.sidebar_nav.row(item)
        if row in self.header_items:
            self.toggle_group(row)

    def _reveal_row(self, row):
        """Make sure a row is reachable before we navigate to it."""
        group = self._group_containing(row)
        if group is not None and group['folded']:
            group['folded'] = False
            self._apply_group(group)

    def select_tab(self, page_widget):
        """
        Switch to specified tab.
        
        Args:
            page_widget: The tab widget to switch to
        """
        if not page_widget:
            return
        
        for entry in self.nav_items:
            if entry.get('page') == page_widget and entry.get('item'):
                row = self.sidebar_nav.row(entry['item'])
                if row >= 0:
                    self.sidebar_nav.setCurrentRow(row)
                return
    
    def get_current_tab(self):
        """Return currently active tab widget (the page, not its scrolling frame)."""
        return page_of(self.content_stack.currentWidget())

    def stack_widget_for(self, page_widget):
        """The widget in the stack that holds this page (its PageScroll)."""
        for i in range(self.content_stack.count()):
            candidate = self.content_stack.widget(i)
            if candidate is page_widget or page_of(candidate) is page_widget:
                return candidate
        return page_widget

    # ------------------------------------------------------------ badges
    def set_badge(self, label, count, tooltip=None):
        """
        Show a count on a sidebar entry ("IT Support 3"). 0 or None clears it.

        The count is the screen's to decide - tickets waiting, replies unread,
        requests to approve. Drawn by NavBadgeDelegate; the entry's text and
        its label are untouched, so nothing that looks tabs up by name breaks.
        """
        try:
            count = int(count or 0)
        except (TypeError, ValueError):
            count = 0
        self._badges[label] = count
        for entry in self.nav_items:
            if entry.get('label') == label and entry.get('item') is not None:
                item = entry['item']
                item.setData(BADGE_ROLE, count)
                if 'base_tooltip' not in entry:
                    entry['base_tooltip'] = item.toolTip()
                if count and tooltip:
                    item.setToolTip(f"{entry['base_tooltip']}<br>{html.escape(str(tooltip))}")
                else:
                    item.setToolTip(entry['base_tooltip'])
                return True
        return False

    def badge(self, label):
        return int(self._badges.get(label, 0) or 0)
    
    def get_current_tab_name(self):
        """Return name of currently active tab."""
        current_widget = self.get_current_tab()
        for entry in self.nav_items:
            if entry['page'] == current_widget:
                return entry['label']
        return "Unknown"
    
    def find_tab_by_page(self, page_widget):
        """
        Find tab entry by page widget.
        
        Returns:
            dict or None: Tab entry dict if found
        """
        for entry in self.nav_items:
            if entry['page'] == page_widget:
                return entry
        return None
    
    def register_tab_factory(self, label, factory_fn, icon="", permission_key=None,
                            visible=True, user_role=None, allowed_tabs=None, tooltip=""):
        """
        Register a tab factory for lazy initialization (Improvement #4).
        
        Args:
            label: Display label for the tab
            factory_fn: Callable that creates the tab widget
            icon: Optional icon emoji/text
            permission_key: Permission key to check
            visible: Whether tab should be visible by default
            user_role: Current user's role
            allowed_tabs: List of allowed tab permissions
            tooltip: Optional tooltip shown when hovering the sidebar item
        
        Returns:
            bool: True if tab was registered
        """
        is_locked = False
        lock_tooltip = tooltip

        # Permission check. No permissions means no keyed tabs - see register_tab.
        if permission_key:
            allowed_tabs = allowed_tabs or []
            is_dev = str(user_role or "").strip().lower() == "developer"
            has_perm = (permission_key in allowed_tabs) or ("ALL" in allowed_tabs)

            logging.debug(f"[LAZY] Registering factory: '{label}'")
            logging.debug(f"   Permission: '{permission_key}' | Role: '{user_role}'")
            logging.debug(f"   Has Permission: {has_perm}")
            
            if not (is_dev or has_perm):
                # Not yours, so it is not there.
                #
                # These used to be registered as visible-but-locked, which left
                # an artist looking at Onboarding, Hardware, Licences,
                # Deployment, Users & Roles, Admin Panel and Tester Panel - seven
                # entries they can never open. A navigation full of doors that
                # do not unlock is worse than a short one: it makes the product
                # look broken and buries the tabs that do work.
                logging.debug(f"[SKIP] Factory '{label}' - no '{permission_key}' permission")
                return False
        
        # Store factory
        self.tab_factories[label] = {
            'factory': None if is_locked else factory_fn,
            'icon': icon,
            'permission': permission_key,
            'visible': visible,
            'locked': is_locked,
        }
        self.tab_labels.append(label)
        
        # Add to sidebar immediately (for navigation)
        is_collapsed = getattr(self, "sidebar_collapsed", False)
        
        if is_collapsed:
            display_text = " "
        else:
            if is_locked:
                display_text = f"[LOCKED] {label}"
            else:
                display_text = label

        item = QListWidgetItem(display_text)
        _apply_nav_icon(item, icon)
        item.setSizeHint(QSize(0, NAV_ROW_HEIGHT))
        item.setHidden(not visible)
        
        if is_collapsed:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
        if is_locked:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip(nav_tooltip(label, lock_tooltip if is_locked else tooltip))
        self.sidebar_nav.addItem(item)

        # Keep content_stack index in 1:1 sync with sidebar_nav
        tab_placeholder = QWidget()
        self.content_stack.addWidget(tab_placeholder)

        # Store in nav_items for compatibility & early query
        self.nav_items.append({
            'page': None,
            'item': item,
            'label': label,
            'permission': permission_key,
            # Folding a group must never reveal a tab this person may not open.
            'permitted': (not item.isHidden()),
        })
        if self.groups:
            self.groups[-1]['rows'].append(self.sidebar_nav.count() - 1)

        if self.sidebar_nav.currentRow() < 0 and not item.isHidden() and bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
            self.sidebar_nav.setCurrentRow(self.sidebar_nav.count() - 1)
        
        logging.debug(f"[OK] Factory registered: {label}")
        return True

    
    def get_or_create_tab(self, index):
        """
        Lazily create tab when first accessed (Improvement #4).
        
        Args:
            index: Tab index (row number) or Tab label (string)
        
        Returns:
            QWidget or None: The tab widget
        """
        if isinstance(index, str):
            if index in self.tab_labels:
                index = self.tab_labels.index(index)
            else:
                return None

        if index < 0 or index >= len(self.tab_labels):
            return None
        
        label = self.tab_labels[index]
        
        # Return cached if exists
        if label in self.tab_instances:
            logging.debug(f"[LAZY] Using cached tab: {label}")
            return self.tab_instances[label]
        
        # Create new instance
        if label not in self.tab_factories:
            logging.error(f"[LAZY] No factory for tab: {label}")
            return None
        
        factory_info = self.tab_factories[label]
        if factory_info.get("locked"):
            return None

        try:
            logging.info(f"[LAZY] Creating tab: {label}")
            factory = factory_info.get('factory')
            if factory is None:
                logging.error(f"[LAZY] Missing factory for tab: {label}")
                return None
            widget = factory()

            if widget:
                self.tab_instances[label] = widget
                holder = PageScroll(widget)

                # Replace placeholder at `index` in content_stack to keep 1:1 index alignment
                if 0 <= index < self.content_stack.count():
                    old_widget = self.content_stack.widget(index)
                    self.content_stack.insertWidget(index, holder)
                    if old_widget and old_widget != holder:
                        self.content_stack.removeWidget(old_widget)
                        old_widget.deleteLater()
                else:
                    self.content_stack.addWidget(holder)

                # Update or add to nav_items
                item = self.sidebar_nav.item(index)
                for entry in self.nav_items:
                    if entry.get('item') is item or entry.get('label') == label:
                        entry['page'] = widget
                        break
                else:
                    if item:
                        self.nav_items.append({
                            'page': widget,
                            'item': item,
                            'label': label,
                            'permission': factory_info['permission']
                        })

                logging.info(f"[OK] Tab created: {label}")
                return widget
            else:
                logging.error(f"[LAZY] Factory returned None for: {label}")
                return None

        except Exception as e:
            logging.exception(f"[LAZY] Failed to create tab '{label}': {e}", exc_info=True)
            self._report_load_failure(label, index, e)
            return None

    def _report_load_failure(self, label, index, exc):
        """
        A tab could not be built. Say so plainly and offer to try again.

        This was a box reading "Failed to load tab X. Error: <exception>
        Please check logs." - with no way to retry, and artists do not have
        the logs. The exception now goes behind "Copy details for IT".
        """
        from .feedback import show_error

        def retry():
            row = self.tab_labels.index(label) if label in self.tab_labels else index
            if self.sidebar_nav.currentRow() == row:
                self._on_nav_changed(row)
            else:
                self.sidebar_nav.setCurrentRow(row)

        show_error(
            self.parent,
            f"Could not open {label}.",
            exc=exc,
            retry=retry,
            title=f"Open {label}",
            hint=("Something went wrong while this screen was starting. Try again - "
                  "if it keeps happening, copy the details and send them to IT."),
        )

    def _on_nav_changed(self, row):
        """Handle sidebar navigation change with lazy loading and fade-in."""
        if row < 0 or row in self.header_items:
            return

        # Reached from a shortcut or from code, the group may be folded away.
        self._reveal_row(row)

        is_first_load = (
            row < len(self.tab_labels)
            and self.tab_labels[row] not in self.tab_instances
        )

        # Lazy load tab on demand.
        widget = self.get_or_create_tab(row)
        label = self.tab_labels[row] if row < len(self.tab_labels) else ""
        if widget is None and label in self.tab_factories and label not in self.tab_instances:
            # It failed (the box with Try again has been shown). The sidebar
            # goes back to the screen that is actually showing: it stayed on
            # the failed one, and clicking it again did nothing.
            showing = self.content_stack.currentIndex()
            self.sidebar_nav.blockSignals(True)
            self.sidebar_nav.setCurrentRow(showing if showing != row else -1)
            self.sidebar_nav.blockSignals(False)
            return

        # Fallback for eagerly-registered/plugin tabs that are already attached.
        if not widget:
            item = self.sidebar_nav.item(row)
            for entry in self.nav_items:
                if entry.get("item") is item:
                    widget = entry.get("page")
                    break
            if not widget and 0 <= row < self.content_stack.count():
                widget = page_of(self.content_stack.widget(row))

        if not widget:
            return

        self.content_stack.setCurrentWidget(self.stack_widget_for(widget))

        # The first time a page shows, its layout is worked out now and a resize
        # posted, so it does not wait for the window to be resized. No
        # processEvents here: it let a second click (or a timer) re-enter
        # navigation while the first screen was still being built.
        from PySide6.QtGui import QResizeEvent
        from PySide6.QtCore import QCoreApplication

        if is_first_load:
            widget.updateGeometry()
            if widget.layout():
                widget.layout().activate()
            # Post a synthetic resize event to force deep child layout recalculations
            resize_event = QResizeEvent(widget.size(), widget.size())
            QCoreApplication.postEvent(widget, resize_event)

        # Nothing folds by itself any more: the sidebar used to fold whole
        # groups away on every click so the list fitted the window, and the
        # navigation jumped about. A list taller than the window scrolls.

        for group in self.groups:
            widget_ = group.get('widget')
            if widget_ is not None and hasattr(widget_, 'set_holds_current'):
                widget_.set_holds_current(group['folded'] and row in group['rows'])

        # The screen's name - the item's text is blank when folded to icons.
        if 0 <= row < len(self.tab_labels):
            self.tab_switched.emit(self.tab_labels[row])

    
    def open_current(self):
        """Build and show the selected tab (after every tab is registered)."""
        row = self.sidebar_nav.currentRow()
        if row >= 0:
            self._on_nav_changed(row)

    def get_tab_count(self):
        """Return total number of registered tabs."""
        # Sidebar count includes lazy tabs (not yet created), eagerly created tabs,
        # and dynamically loaded plugins.
        return self.sidebar_nav.count()
    
    def get_visible_tab_count(self):
        """Return number of visible tabs."""
        return sum(1 for entry in self.nav_items if not entry['item'].isHidden())
