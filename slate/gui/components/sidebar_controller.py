import logging
from PySide6.QtCore import QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QTimer
from slate.core.infra.gate import Gate


def toggle_button_sheet(collapsed: bool) -> str:
    """
    The expand / collapse arrow at the foot of the sidebar. It rested in the
    divider colour (about 1.3:1 against the rail) and only showed on hover.
    """
    align = "text-align: center; padding: 0;" if collapsed else "text-align: right; padding-right: 20px;"
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {Gate.TEXT_DIM};
            border: none;
            border-top: 1px solid {Gate.RAISED};
            font-size: 16px;
            {align}
        }}
        QPushButton:hover {{ color: {Gate.ACCENT}; background-color: {Gate.tint(Gate.ACCENT, 0.05)}; }}
    """


def nav_sheet(collapsed: bool) -> str:
    """
    The sidebar list. Every entry reserves the 3 px selection edge, so the
    selected entry's icon and text no longer shift right when it is chosen.
    """
    if collapsed:
        item = "padding: 0px; margin: 2px 4px;"
        outer = "padding: 2px 2px 8px 2px;"
    else:
        item = "padding: 0px 12px; margin: 2px 8px;"
        outer = "padding: 0px 0px 8px 0px;"
    return f"""
        QListWidget {{ background: transparent; border: none; outline: none; {outer} }}
        QListWidget::item {{
            color: {Gate.TEXT};
            {item}
            border-radius: 6px;
            border-left: 3px solid transparent;
        }}
        QListWidget::item:hover {{
            background-color: {Gate.overlay(0.05)};
        }}
        QListWidget::item:selected {{
            background-color: {Gate.tint(Gate.ACCENT, 0.15)};
            color: {Gate.ACCENT};
            border-left: 3px solid {Gate.ACCENT};
        }}
        QListWidget::item:disabled {{
            padding: 0px; margin: 0px; border: none; background: transparent;
        }}
    """


class SidebarControllerMixin:
    """
    Mixin for VFXFolderCreatorApp that handles sidebar
    animations, responsive resizing, and state toggling.
    """

    def resizeEvent(self, event):
        """Keep header controls readable when window is resized."""
        # Note: We must call the superclass resizeEvent to maintain QMainWindow behavior
        super().resizeEvent(event)
        self._update_sidebar_responsive_width()

        # The navigation no longer folds groups to fit the height: it scrolls.

        if hasattr(self, "header_builder") and self.header_builder:
            try:
                self.header_builder.update_responsive_layout(self.width())
            except Exception as exc:
                logging.debug("Header responsive update skipped: %s", exc)

    def _update_sidebar_responsive_width(self):
        """Reduce sidebar pressure on narrow windows to prevent tab overlap and animate transition smoothly."""
        if not hasattr(self, "sidebar_container") or self.sidebar_container is None:
            return

        if getattr(self, "sidebar_collapsed", False):
            target = 64
        else:
            width = self.width()
            if width < 1280:
                target = 180
            elif width < 1500:
                target = 205
            else:
                target = 240

        if self.sidebar_container.width() != target:
            # Stop existing animation if running
            if hasattr(self, "_sidebar_anim") and self._sidebar_anim.state() == QParallelAnimationGroup.Running:
                self._sidebar_anim.stop()

            self._sidebar_anim = QParallelAnimationGroup(self)

            anim_min = QPropertyAnimation(self.sidebar_container, b"minimumWidth")
            anim_min.setDuration(250)
            anim_min.setEasingCurve(QEasingCurve.InOutQuad)
            anim_min.setStartValue(self.sidebar_container.minimumWidth())
            anim_min.setEndValue(target)

            anim_max = QPropertyAnimation(self.sidebar_container, b"maximumWidth")
            anim_max.setDuration(250)
            anim_max.setEasingCurve(QEasingCurve.InOutQuad)
            anim_max.setStartValue(self.sidebar_container.maximumWidth())
            anim_max.setEndValue(target)

            self._sidebar_anim.addAnimation(anim_min)
            self._sidebar_anim.addAnimation(anim_max)
            self._sidebar_anim.start()

    def apply_sidebar_look(self, collapsed: bool):
        """The toggle arrow, its tooltip and the list's sheet for one state."""
        from ..core.icons import icon as draw_icon
        button = getattr(self, "sidebar_toggle_btn", None)
        if button is not None:
            button.setText("")
            button.setIcon(draw_icon("chevron-right" if collapsed else "chevron-left", Gate.TEXT_DIM, 16))
            button.setStyleSheet(toggle_button_sheet(collapsed))
            button.setToolTip("Expand sidebar" if collapsed else "Collapse sidebar")
        nav = getattr(self, "sidebar_nav", None)
        if nav is not None:
            nav.setStyleSheet(nav_sheet(collapsed))

    def _remember_sidebar(self, key, value):
        """Keep a sidebar choice (folded to icons, folded groups) for next time."""
        settings = getattr(self, "global_settings", None)
        manager = getattr(self, "config_manager", None)
        if not isinstance(settings, dict) or manager is None:
            return
        try:
            settings[key] = value
            manager.update_global_settings(settings)
        except Exception as exc:
            logging.debug("Sidebar state not saved: %s", exc)

    def toggle_sidebar(self):
        """Toggles the sidebar collapsed state (and remembers it)."""
        self.sidebar_collapsed = not getattr(self, "sidebar_collapsed", False)
        self.apply_sidebar_look(self.sidebar_collapsed)
        self._remember_sidebar("sidebar_collapsed", bool(self.sidebar_collapsed))

        if self.sidebar_collapsed:
            if hasattr(self, 'tab_coordinator'):
                self.tab_coordinator.set_sidebar_collapsed(True)
            self._update_sidebar_responsive_width()
        else:
            self._update_sidebar_responsive_width()
            if hasattr(self, 'tab_coordinator'):
                QTimer.singleShot(250, lambda: self.tab_coordinator.set_sidebar_collapsed(False) if not getattr(self, "sidebar_collapsed", False) else None)
