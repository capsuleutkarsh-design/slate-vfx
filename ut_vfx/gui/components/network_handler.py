import logging
import os
from pathlib import Path
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox
from .qt_safety import safe_single_shot

class NetworkHandler(QObject):
    """
    Handles incoming network commands for the Main Window.
    Separated from main_window.py to reduce bloat.
    """
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.overlay = main_window.overlay
        self.user_data = main_window.user_data
        self.user_display_name = main_window.user_display_name

    def _get_tab_instance(self, label: str, create: bool = False):
        tc = getattr(self.main_window, "tab_coordinator", None)
        if not tc:
            return None
        if label in tc.tab_instances:
            return tc.tab_instances[label]
        if not create:
            return None
        try:
            idx = tc.tab_labels.index(label)
        except ValueError:
            return None
        return tc.get_or_create_tab(idx)

    def on_network_message(self, command, target_user, payload):
        """
        Handle incoming network commands (Message, Shutdown, Clean, etc.)
        """
        # 1. Filter Target
        is_me = (target_user == 'all') or (target_user == self.user_display_name) or (target_user == self.user_data.get('username'))
        
        if not is_me:
             return
             
        logging.info(f"Network Command Received: [{command}] {payload}")

        if command == "MESSAGE":
            # Show Notification
            if self.overlay:
                self.overlay.show_message("Admin Message", payload, duration=5000)
            else:
                QMessageBox.information(self.main_window, "Admin Message", payload)
                
        elif command == "SHUTDOWN":
            if hasattr(self.main_window, 'status_bar'):
                self.main_window.status_bar.showMessage("Remote Shutdown Initiated...", 0)
            safe_single_shot(2000, self.main_window, self.main_window.close) # Graceful exit
            
        elif command == "RESTART":
             if hasattr(self.main_window, 'status_bar'):
                self.main_window.status_bar.showMessage("Remote Restart Initiated...", 0)
             safe_single_shot(2000, self.main_window, self.main_window.close)

        elif command == "WIPE_CACHE":
             self.perform_remote_wipe()
             
        elif command == "ASSET_LINK":
             # Payload is a path. Open it in Stock Browser or Quick Look?
             if self.overlay: 
                 self.overlay.show_message("Asset Received", f"Incoming: {Path(payload).name}", duration=4000)

    def perform_remote_wipe(self):
        """Execute the WIPE_CACHE command safely."""
        logging.warning("Received Remote Cache Wipe Command!")
        
        # 1. Notify User
        if self.overlay:
             self.overlay.show_message("Administrator Action", "Forcing Library Cache Refresh...", duration=3000)
        
        # 2. Perform Wipe
        try:
            # Helper to wipe
            from ...core.domain.library_manager import LibraryManager
            lib = LibraryManager()
            if lib.local_cache.exists():
                os.remove(lib.local_cache)
                logging.info("Deleted Local Cache File.")
            
            # 3. Reload
            stock_tab = self._get_tab_instance("Stock Viewer", create=False)
            if stock_tab and hasattr(stock_tab, "load_library_from_server"):
                stock_tab.load_library_from_server()
            elif hasattr(self.main_window, "stock_page"):
                # Legacy fallback (pre-lazy-tab architecture)
                self.main_window.stock_page.load_library_from_server()
                
            if hasattr(self.main_window, 'status_bar'):
                self.main_window.status_bar.showMessage("Cache Wiped & Reloaded by Admin", 5000)
            
        except Exception as e:
            logging.exception(f"Remote Wipe Failed: {e}")
