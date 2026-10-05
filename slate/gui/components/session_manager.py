import logging
from PySide6.QtCore import QRect, QByteArray
from PySide6.QtWidgets import QApplication

class SessionManagerMixin:
    """
    Mixin for VFXFolderCreatorApp that handles session state,
    geometry restoration, and safe shutdown processes.
    """

    def restore_last_paths(self):
        """Restore session paths on already-loaded tabs when enabled."""
        if not getattr(self, "global_settings", {}).get("restore_last_paths", True):
            logging.info("Session path restore disabled by user settings.")
            return

        # Keep legacy and newer key names aligned.
        try:
            last_project_dir = self.config_manager.settings.get("last_project_dir", "")
            if last_project_dir and not self.config_manager.settings.get("last_project_directory"):
                self.config_manager.settings["last_project_directory"] = last_project_dir
        except Exception as e:
            logging.debug(f"Could not normalize legacy path keys: {e}")

        restored_tabs = []
        for label in ("Build & Ingest",):
            if hasattr(self, "_get_tab_instance"):
                tab = self._get_tab_instance(label, create=False)
                if tab and hasattr(tab, "restore_last_paths"):
                    try:
                        tab.restore_last_paths()
                        restored_tabs.append(label)
                    except Exception as e:
                        logging.debug(f"Path restore skipped for {label}: {e}")

        if restored_tabs:
            logging.info("Restored paths for tabs: %s", ", ".join(restored_tabs))

    def restore_window_geometry(self):
        """
        Put the window back where it was, maximised or not.

        The size used to be saved as x,y,w,h - a maximised window's size saved
        as a normal one - and then thrown away, because start-up always called
        showMaximized(). Qt's own saveGeometry keeps the maximised state too.
        The old "window_geometry" value is still read once, for an upgrade.
        """
        self._geometry_restored = False
        settings = getattr(self, "global_settings", {}) or {}
        state = settings.get("window_state")
        if state:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(str(state).encode("ascii"))):
                    if self._on_some_screen(self.geometry()):
                        self._geometry_restored = True
                        return
                    self.center_window()
            except Exception as e:
                logging.warning("Could not restore the window position: %s", e)

        geo = settings.get("window_geometry")
        if geo:
            try:
                x, y, w, h = map(int, str(geo).split(','))
                rect = QRect(x, y, w, h)
                if self._on_some_screen(rect):
                    self.setGeometry(x, y, w, h)
                    self._geometry_restored = True
                else:
                    self.center_window()
            except Exception as e:
                logging.warning("Could not restore the old window position: %s", e)
                self.center_window()

    @staticmethod
    def _on_some_screen(rect) -> bool:
        return any(screen.availableGeometry().intersects(rect) for screen in QApplication.screens())

    def show_restored(self):
        """Show the window as it was left; maximised the very first time."""
        if getattr(self, "_geometry_restored", False):
            self.show()
        else:
            self.showMaximized()

    def save_window_geometry(self):
        """Remember position, size and the maximised state for next time."""
        try:
            self.global_settings['window_state'] = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.global_settings.pop('window_geometry', None)
            self.config_manager.update_global_settings(self.global_settings)
        except Exception as e:
            logging.exception(f"Error saving window geometry: {e}")

    def closeEvent(self, event):
        """Cleanup all resources before closing the application."""
        try:
            if not getattr(self, "_init_complete", False):
                logging.debug("closeEvent skipped (init not complete).")
                event.accept()
                return
            self._is_closing = True
            logging.info("Application closing - cleaning up resources...")

            # Stop periodic timers first.
            for timer_name in ("cleanup_timer",):
                timer_obj = getattr(self, timer_name, None)
                if timer_obj:
                    try:
                        timer_obj.stop()
                    except Exception as e:
                        logging.exception(f"Error stopping timer '{timer_name}': {e}")

            # Save geometry/user settings early in shutdown.
            self.save_window_geometry()

            # Closing Slate or signing out never punches anybody out (studio
            # decision). It used to: somebody restarting Slate at lunch was
            # punched out at 13:00. A forgotten punch-out is closed by the
            # end-of-day auto-logout instead.
            
            # Cleanup all tabs that have cleanup_resources method
            if hasattr(self, 'content_stack'):
                from .tab_coordinator import page_of
                for i in range(self.content_stack.count()):
                    # Each tab sits in a scrolling frame; clean up the tab.
                    page = page_of(self.content_stack.widget(i))
                    if hasattr(page, 'cleanup_resources'):
                        try:
                            page.cleanup_resources()
                        except Exception as e:
                            logging.exception(f"Error cleaning up page {i}: {e}")
            
            # Cleanup DB Monitor (Fixes Zombie Process)
            if hasattr(self, 'db_monitor') and self.db_monitor:
                try:
                    self.db_monitor.stop()
                except Exception as e:
                    logging.exception(f"Error stopping DB monitor: {e}")

            # Cancel pending DB init worker.
            if hasattr(self, "_db_init_worker") and self._db_init_worker and hasattr(self._db_init_worker, "cancel"):
                try:
                    self._db_init_worker.cancel()
                except Exception as e:
                    logging.exception(f"Error cancelling DB init worker: {e}")
                finally:
                    self._db_init_worker = None

            # Stop update checker thread if still active.
            if hasattr(self, 'update_checker') and self.update_checker:
                try:
                    if self.update_checker.isRunning():
                        if hasattr(self.update_checker, "stop"):
                            self.update_checker.stop()
                        if not self.update_checker.wait(2000):
                            logging.warning("Update checker did not stop in time during shutdown.")
                except Exception as e:
                    logging.exception(f"Error stopping update checker: {e}")
                finally:
                    self.update_checker = None

            # FORCE DB SHUTDOWN to prevent orphan DB sessions.
            try:
                from ...core.infra.database_manager import database_manager
                database_manager.force_shutdown()
            except Exception as e:
                logging.exception(f"Error shutting down DB: {e}")
            
            # Save settings
            if hasattr(self, "config_manager"):
                try:
                    self.config_manager.save_settings(self.config_manager.settings)
                except Exception as e:
                    logging.exception(f"Error saving settings: {e}")

            # Shutdown background utility writers/processors.
            try:
                from ...core.infra.telemetry import telemetry
                telemetry.shutdown()
            except Exception as e:
                logging.debug(f"Telemetry shutdown skipped: {e}")
            try:
                from ...utils.error_handler import error_handler
                error_handler.cleanup()
            except Exception as e:
                logging.debug(f"Error handler cleanup skipped: {e}")
            
            logging.info("Cleanup complete")
            event.accept()
            app = QApplication.instance()
            if getattr(self, "_logout_requested", False):
                self._logout_requested = False
                if hasattr(self, "_reopen_login_after_logout"):
                    self._reopen_login_after_logout()
            elif app:
                app.quit()
            
        except Exception as e:
            logging.exception(f"Error in closeEvent: {e}")
            event.accept()
