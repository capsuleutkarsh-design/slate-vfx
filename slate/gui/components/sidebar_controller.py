import logging
from PySide6.QtCore import QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QTimer

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

        # Fold navigation groups away if the entries no longer fit the height.
        # Without this the last entries are simply clipped off the bottom and
        # the tab you want can be unreachable without scrolling the nav.
        #
        # Deferred by one turn of the event loop on purpose: during a resize the
        # sidebar's viewport is still being laid out, and measuring it too early
        # reports a height far smaller than it settles at - which folds away far
        # more groups than the window actually needs.
        self._schedule_sidebar_fit()

        if hasattr(self, "header_builder") and self.header_builder:
            try:
                self.header_builder.update_responsive_layout(self.width())
            except Exception as exc:
                logging.debug("Header responsive update skipped: %s", exc)

    def _schedule_sidebar_fit(self):
        """Run the navigation fit once, after the layout has settled."""
        if getattr(self, "_sidebar_fit_pending", False):
            return
        self._sidebar_fit_pending = True

        def run():
            self._sidebar_fit_pending = False
            coordinator = getattr(self, "tab_coordinator", None)
            if coordinator is None or not hasattr(coordinator, "fit_to_viewport"):
                return
            try:
                coordinator.fit_to_viewport()
            except Exception as exc:
                logging.debug("Sidebar fit skipped: %s", exc)

        QTimer.singleShot(0, run)

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

    def toggle_sidebar(self):
        """Toggles the sidebar collapsed state."""
        self.sidebar_collapsed = not getattr(self, "sidebar_collapsed", False)
        
        if self.sidebar_collapsed:
            self.sidebar_toggle_btn.setText("⮞")
            self.sidebar_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #2C2C34;
                    border: none;
                    border-top: 1px solid #1D1D22;
                    font-size: 16px;
                    text-align: center;
                    padding: 0;
                }
                QPushButton:hover { color: #3EA8BF; background-color: rgba(62, 168, 191, 0.05); }
            """)
            self.sidebar_nav.setStyleSheet("""
                QListWidget { background: transparent; border: none; outline: none; padding: 2px; }
                QListWidget::item { 
                    color: #E8E6E1;
                    padding: 12px 0px; 
                    border-radius: 6px;
                    margin: 2px 4px;
                    font-size: 32px;
                }
                QListWidget::item:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                }
                QListWidget::item:selected {
                    background-color: rgba(62, 168, 191, 0.15);
                    color: #3EA8BF;
                    border-left: 3px solid #3EA8BF;
                    border-radius: 4px;
                }
            """)
        else:
            self.sidebar_toggle_btn.setText("⮜")
            self.sidebar_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #2C2C34;
                    border: none;
                    border-top: 1px solid #1D1D22;
                    font-size: 16px;
                    text-align: right;
                    padding-right: 20px;
                }
                QPushButton:hover { color: #3EA8BF; background-color: rgba(62, 168, 191, 0.05); }
            """)
            self.sidebar_nav.setStyleSheet("""
                QListWidget { background: transparent; border: none; outline: none; }
                QListWidget::item {
                    color: #E8E6E1;
                    padding: 8px 12px;
                    border-radius: 6px;
                    margin: 2px 8px;
                    font-size: 14px;
                }
                QListWidget::item:hover {
                    background-color: rgba(255, 255, 255, 0.05);
                }
                QListWidget::item:selected {
                    background-color: rgba(62, 168, 191, 0.15);
                    color: #3EA8BF;
                    border-left: 3px solid #3EA8BF;
                    border-radius: 4px;
                    font-weight: bold;
                }
            """)
            
        if self.sidebar_collapsed:
            if hasattr(self, 'tab_coordinator'):
                self.tab_coordinator.set_sidebar_collapsed(True)
            self._update_sidebar_responsive_width()
        else:
            self._update_sidebar_responsive_width()
            if hasattr(self, 'tab_coordinator'):
                QTimer.singleShot(250, lambda: self.tab_coordinator.set_sidebar_collapsed(False) if not getattr(self, "sidebar_collapsed", False) else None)
