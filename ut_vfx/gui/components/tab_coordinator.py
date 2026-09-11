"""
Tab Coordinator Component.

Manages tab registration, initialization, visibility and navigation.

Extracted from main_window.py for better maintainability.
"""

from PySide6.QtWidgets import QListWidgetItem, QWidget, QHBoxLayout, QLabel, QFrame, QSizePolicy
from PySide6.QtCore import Qt, QSize, Signal, QObject
import logging

# A navigation entry and a category rule. They used to be the same height, 50px
# each, so twenty entries and five headers wanted 1250px of a window that is
# routinely 1000px tall - the last entries were clipped mid-word.
NAV_ROW_HEIGHT = 38
HEADER_ROW_HEIGHT = 30


def _apply_nav_icon(item, icon_name):
    """Put a drawn icon on a navigation entry, if we have one for it."""
    if not icon_name:
        return
    try:
        from ..core.icons import icon as draw_icon, has_icon
        if has_icon(icon_name):
            item.setIcon(draw_icon(icon_name))
    except Exception as exc:
        logging.debug("Nav icon skipped for %r: %s", icon_name, exc)


class CategoryHeaderWidget(QWidget):
    def __init__(self, label_text: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        # Remove vertical margins since QListWidgetItem already has 12px padding top/bottom
        layout.setContentsMargins(15, 0, 15, 0)
        layout.setSpacing(10)
        
        # Left line
        self.left_line = QFrame()
        self.left_line.setFixedHeight(1)
        self.left_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.left_line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(62, 168, 191, 0), stop:1 rgba(62, 168, 191, 0.4));")
        
        # Tells you the group can be folded away, and which way it is now.
        self.chevron = QLabel("▾")
        self.chevron.setStyleSheet(
            "color: #3EA8BF; font-size: 9px; background: transparent;")

        # Text label
        self.lbl = QLabel(label_text.upper())
        # Use a slightly bigger font and no margins
        self.lbl.setStyleSheet("color: #3EA8BF; font-size: 11px; font-weight: 900; letter-spacing: 2px; background: transparent; padding: 0px; margin: 0px;")
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Right line
        self.right_line = QFrame()
        self.right_line.setFixedHeight(1)
        self.right_line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.right_line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(62, 168, 191, 0.4), stop:1 rgba(62, 168, 191, 0));")
        
        layout.addWidget(self.left_line)
        layout.addWidget(self.lbl)
        layout.addWidget(self.chevron)
        layout.addWidget(self.right_line)

    def set_folded(self, folded: bool):
        self.chevron.setText("▸" if folded else "▾")
        
    def set_collapsed(self, collapsed: bool):
        if collapsed:
            self.chevron.hide()
            self.lbl.hide()
            self.left_line.setStyleSheet("background-color: rgba(62, 168, 191, 0.5);")
            self.right_line.hide()
        else:
            self.chevron.show()
            self.lbl.show()
            self.left_line.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(62, 168, 191, 0), stop:1 rgba(62, 168, 191, 0.4));")
            self.right_line.show()


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
        
        # Permission check
        if permission_key and user_role and allowed_tabs:
            is_dev = (user_role and user_role.lower() == "developer")
            has_perm = (permission_key in allowed_tabs) or ("ALL" in allowed_tabs)
            
            # DEBUG LOGGING
            logging.info(f"[SCAN] Tab Registration: '{label}'")
            logging.info(f"   Permission Key: '{permission_key}'")
            logging.info(f"   User Role: '{user_role}' | Is Developer: {is_dev}")
            logging.info(f"   Allowed Tabs: {allowed_tabs}")
            logging.info(f"   Has Permission: {has_perm}")
            
            # Skip if no permission (unless developer)
            if not (is_dev or has_perm):
                logging.warning(f"[SKIP] Tab '{label}' FILTERED - No permission")
                # Some tabs need to be added but hidden (for workflow switching)
                if not visible:
                    pass  # Continue to add but hidden
                else:
                    return False
            else:
                logging.info(f"[OK] Tab '{label}' ALLOWED")
        
        # Add to stack
        self.content_stack.addWidget(page_widget)
        
        # Add to sidebar
        is_collapsed = getattr(self, "sidebar_collapsed", False)
        
        # The icon is drawn, not typed. It used to be an emoji inside the
        # label text, which Windows rendered in whatever font it fancied - some
        # in colour, some not, on different baselines.
        item = QListWidgetItem(" " if is_collapsed else label)
        _apply_nav_icon(item, icon)
        item.setSizeHint(QSize(0, NAV_ROW_HEIGHT))
        
        if is_collapsed:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            
        self.sidebar_nav.addItem(item)
        
        # Set visibility
        item.setHidden(not visible)
        
        # Store mapping
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
        
        logging.info(f"Tab registered: {label}")
        return True
    
    def add_category_header(self, label: str):
        """
        Add a non-selectable category header to the sidebar.
        
        Args:
            label: Display label for the header
        """
        item = QListWidgetItem("")
        item.setSizeHint(QSize(0, HEADER_ROW_HEIGHT))
        
        # Make it non-selectable
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        
        self.sidebar_nav.addItem(item)
        
        # Set premium widget
        widget = CategoryHeaderWidget(label)
        self.sidebar_nav.setItemWidget(item, widget)
        
        # Keep track of header indices
        row_index = self.sidebar_nav.count() - 1
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
        
        logging.info(f"Category header added: {label}")
    
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
        for row in group['rows']:
            item = self.sidebar_nav.item(row)
            if item is None:
                continue
            entry = next((e for e in self.nav_items if e.get('item') is item), None)
            permitted = entry.get('permitted', True) if entry else True
            item.setHidden(group['folded'] or not permitted)

        widget = group.get('widget')
        if widget is not None and hasattr(widget, 'set_folded'):
            widget.set_folded(group['folded'])

    def set_group_folded(self, label, folded):
        for group in self.groups:
            if group['label'] == label:
                group['folded'] = bool(folded)
                self._apply_group(group)
                return True
        return False

    def toggle_group(self, row):
        group = self._group_for_row(row)
        if group is None:
            return False
        group['folded'] = not group['folded']
        self._apply_group(group)
        return True

    def content_height(self):
        """How much room the visible navigation entries want."""
        total = 0
        for row in range(self.sidebar_nav.count()):
            item = self.sidebar_nav.item(row)
            if item is not None and not item.isHidden():
                total += item.sizeHint().height()
        return total

    def fit_to_viewport(self):
        """
        Fold groups away only if the navigation does not fit.

        On a tall window nothing changes and every entry stays where the team
        expects it. On a short one the groups you are not working in fold up,
        rather than the last few entries being quietly cut off the bottom.
        """
        viewport = self.sidebar_nav.viewport().height()
        if viewport <= 0 or not self.groups:
            return False

        active = self._group_containing(self.sidebar_nav.currentRow())

        # Start from everything open, so widening the window brings them back.
        for group in self.groups:
            if group['folded']:
                group['folded'] = False
                self._apply_group(group)

        folded_any = False
        for group in self.groups:
            if self.content_height() <= viewport:
                break
            if group is active or not group['rows']:
                continue
            group['folded'] = True
            self._apply_group(group)
            folded_any = True

        return folded_any

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
        """Return currently active tab widget."""
        return self.content_stack.currentWidget()
    
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

        # Permission check
        if permission_key and user_role and allowed_tabs:
            is_dev = (user_role and user_role.lower() == "developer")
            has_perm = (permission_key in allowed_tabs) or ("ALL" in allowed_tabs)
            
            logging.info(f"[LAZY] Registering factory: '{label}'")
            logging.info(f"   Permission: '{permission_key}' | Role: '{user_role}'")
            logging.info(f"   Has Permission: {has_perm}")
            
            if not (is_dev or has_perm):
                # Not yours, so it is not there.
                #
                # These used to be registered as visible-but-locked, which left
                # an artist looking at Onboarding, Hardware, Licences,
                # Deployment, Users & Roles, Admin Panel and Tester Panel - seven
                # entries they can never open. A navigation full of doors that
                # do not unlock is worse than a short one: it makes the product
                # look broken and buries the tabs that do work.
                logging.info(f"[SKIP] Factory '{label}' - no '{permission_key}' permission")
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
            item.setToolTip(lock_tooltip)
        elif tooltip:
            item.setToolTip(tooltip)
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
        
        logging.info(f"[OK] Factory registered: {label}")
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
                
                # Replace placeholder at `index` in content_stack to keep 1:1 index alignment
                if 0 <= index < self.content_stack.count():
                    old_widget = self.content_stack.widget(index)
                    self.content_stack.insertWidget(index, widget)
                    if old_widget and old_widget != widget:
                        self.content_stack.removeWidget(old_widget)
                        old_widget.deleteLater()
                else:
                    self.content_stack.addWidget(widget)
                
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
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self.parent,
                "Tab Load Error",
                f"Failed to load tab '{label}'.\n\nError: {str(e)}\n\nPlease check logs."
            )
            return None
    
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

        # Fallback for eagerly-registered/plugin tabs that are already attached.
        if not widget:
            item = self.sidebar_nav.item(row)
            for entry in self.nav_items:
                if entry.get("item") is item:
                    widget = entry.get("page")
                    break
            if not widget and 0 <= row < self.content_stack.count():
                widget = self.content_stack.widget(row)

        if not widget:
            return

        self.content_stack.setCurrentWidget(widget)

        # UX Polish: Force layout calculation to prevent "broken layout on first load" bugs.
        # PySide6 sometimes delays layout math for complex widgets added to a QStackedWidget 
        # until the user resizes the window. We explicitly force it here.
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QResizeEvent
        from PySide6.QtCore import QCoreApplication
        
        QApplication.processEvents()
        if is_first_load:
            widget.updateGeometry()
            if widget.layout():
                widget.layout().activate()
            # Post a synthetic resize event to force deep child layout recalculations
            resize_event = QResizeEvent(widget.size(), widget.size())
            QCoreApplication.postEvent(widget, resize_event)

        # Which group has to stay open has just changed, so re-fit. Without
        # this, moving into a group that was folded leaves the navigation taller
        # than the window and the last entries clipped off the bottom again.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.fit_to_viewport)

        # Emit signal with tab name.
        try:
            item = self.sidebar_nav.item(row)
            if item:
                self.tab_switched.emit(item.text())
        except Exception as exc:
            logging.debug("Tab switch signal emit failed: %s", exc)

    
    def get_tab_count(self):
        """Return total number of registered tabs."""
        # Sidebar count includes lazy tabs (not yet created), eagerly created tabs,
        # and dynamically loaded plugins.
        return self.sidebar_nav.count()
    
    def get_visible_tab_count(self):
        """Return number of visible tabs."""
        return sum(1 for entry in self.nav_items if not entry['item'].isHidden())
