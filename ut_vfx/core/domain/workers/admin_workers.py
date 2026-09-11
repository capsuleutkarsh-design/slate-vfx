from PySide6.QtCore import QThread, Signal
import json
from pathlib import Path
import logging

class LiveStatusWorker(QThread):
    """
    Worker to scan and load all workstation status JSON files in background.
    """
    # Emits list of loaded data dicts
    data_ready = Signal(list)
    
    def __init__(self, hub):
        super().__init__()
        self.hub = hub

    @staticmethod
    def _load_json_with_fallback(path: Path):
        """Load JSON with encoding fallbacks for mixed workstation environments."""
        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                with open(path, "r", encoding=encoding) as file:
                    return json.load(file)
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise ValueError(f"Could not read status file: {path}")
        
    def run(self):
        try:
            status_dir = self.hub.get_livestatus_dir()
            files = list(status_dir.glob("*.json"))
            loaded_data = []
            
            for f in files:
                try:
                    data = self._load_json_with_fallback(f)
                    if data.get('pc_name'):
                        # Pre-calculate delta here to save UI thread work? 
                        # No, let UI handle display logic. Just pass raw data.
                        loaded_data.append(data)
                except Exception as e:
                    logging.warning(f"Failed to load status file {f}: {e}")
                    continue
            
            self.data_ready.emit(loaded_data)
            
        except Exception as e:
            logging.exception(f"LiveStatusWorker failed: {e}")
            self.data_ready.emit([])

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

class UserDataWorker(QThread):
    """
    Worker to load user database in background.
    """
    # Emits dict of users {uid: data}
    users_loaded = Signal(dict)
    
    def __init__(self, user_manager):
        super().__init__()
        self.user_manager = user_manager
        
    def run(self):
        try:
            # User manager now uses SQL, so we can safely call get_all_users()
            users_dict = self.user_manager.get_all_users()
            self.users_loaded.emit(users_dict)
        except Exception as e:
            logging.exception(f"UserDataWorker failed: {e}")
            self.users_loaded.emit({})

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

