import logging
from PySide6.QtCore import QRect
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
        """Restore window coordinates from previous session."""
        geo = getattr(self, "global_settings", {}).get("window_geometry")
        if geo:
            try:
                x, y, w, h = map(int, geo.split(','))
                
                # Validation: Ensure window is visible on current screens
                rect = QRect(x, y, w, h)
                valid = False
                for screen in QApplication.screens():
                    if screen.availableGeometry().intersects(rect):
                        valid = True
                        break
                
                if valid:
                    self.setGeometry(x, y, w, h)
                else:
                    self.center_window()
            except Exception as e:
                logging.exception(f"Error restoring window geometry: {e}")
                self.center_window()

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
            for timer_name in ("cleanup_timer", "idle_timer"):
                timer_obj = getattr(self, timer_name, None)
                if timer_obj:
                    try:
                        timer_obj.stop()
                    except Exception as e:
                        logging.exception(f"Error stopping timer '{timer_name}': {e}")

            # Save geometry/user settings early in shutdown.
            try:
                self.global_settings['window_geometry'] = f"{self.x()},{self.y()},{self.width()},{self.height()}"
                self.config_manager.update_global_settings(self.global_settings)
            except Exception as e:
                logging.exception(f"Error saving window geometry: {e}")

            # Log user logout event.
            if getattr(self, "user_data", None):
                try:
                    if hasattr(self, "_is_sqlite_fallback_mode") and not self._is_sqlite_fallback_mode():
                        from ...core.domain.central_attendance import CentralAttendance
                        att = CentralAttendance()
                        logout_user = self.user_data.get('user_id', self.user_data.get('username'))
                        if logout_user:
                            att.log_action(logout_user, 'out')
                except Exception as e:
                    logging.debug(f"Attendance logout failed: {e}")
            
            # Cleanup all tabs that have cleanup_resources method
            if hasattr(self, 'content_stack'):
                for i in range(self.content_stack.count()):
                    page = self.content_stack.widget(i)
                    if hasattr(page, 'cleanup_resources'):
                        try:
                            page.cleanup_resources()
                        except Exception as e:
                            logging.exception(f"Error cleaning up page {i}: {e}")
            
            # Cleanup network manager
            if hasattr(self, 'network_manager') and self.network_manager:
                try:
                    self.network_manager.stop()
                except Exception as e:
                    logging.exception(f"Error stopping network manager: {e}")

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
                from ...core.infra.error_reporting import error_handler
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
