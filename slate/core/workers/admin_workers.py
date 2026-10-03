from PySide6.QtCore import QThread, Signal
import json
import os
from pathlib import Path
import logging

def load_json_with_fallback(path: Path):
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


def load_report(path) -> dict:
    """
    One LiveStatus report, with '_file_mtime': when the file was last written,
    by the file server's clock. Freshness is judged from that rather than the
    client's own last_seen, so a workstation whose clock runs fast no longer
    stays 'Online' after it is switched off (fleet_status.last_seen_of).
    """
    path = Path(path)
    data = load_json_with_fallback(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a status report")
    try:
        data["_file_mtime"] = path.stat().st_mtime
    except OSError:
        pass
    return data


def list_reports(status_dir):
    """
    The report files in the LiveStatus folder. Raises OSError when the folder
    cannot be read: Path.glob returned [] for a dropped share, which looked
    exactly like a fleet with no machines.
    """
    with os.scandir(status_dir) as entries:
        return sorted(Path(e.path) for e in entries
                      if e.name.lower().endswith(".json") and e.is_file())


class LiveStatusWorker(QThread):
    """
    Worker to scan and load all workstation status JSON files in background.

    data_ready gets one dict per report; a report that could not be read is
    {'pc_name': <file name>, 'unreadable': True} so the machine stays on the
    grid instead of vanishing. failed gets the reason when the folder itself
    could not be read - the last good read stays on screen then.
    """
    data_ready = Signal(list)
    failed = Signal(str)

    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        # file -> mtime of the last warning, so a broken file is logged once
        # per change rather than every 30 s.
        self._warned = {}

    def run(self):
        try:
            files = list_reports(self.hub.get_livestatus_dir())
        except Exception as e:
            logging.warning("Live Ops could not read the status folder: %s", e)
            self.failed.emit(str(e))
            return

        loaded_data = []
        for f in files:
            if self.isInterruptionRequested():
                return
            try:
                data = load_report(f)
            except Exception as e:
                try:
                    stamp = f.stat().st_mtime
                except OSError:
                    stamp = None
                if self._warned.get(f) != stamp:
                    self._warned[f] = stamp
                    logging.warning(f"Failed to load status file {f}: {e}")
                loaded_data.append({"pc_name": f.stem, "unreadable": True})
                continue
            self._warned.pop(f, None)
            if data.get('pc_name'):
                loaded_data.append(data)
        self.data_ready.emit(loaded_data)

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

