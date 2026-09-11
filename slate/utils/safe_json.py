import json
import time
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

class SafeJsonIO:
    """
    Robust JSON File I/O with Native OS Locking (Zero-Delete Protocol).
    Designed specifically for network shares without Delete/Rename permissions.
    """

    @staticmethod
    def _lock_file(f) -> bool:
        """Acquire an exclusive, non-blocking lock on the file descriptor."""
        try:
            if _HAS_MSVCRT:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            elif _HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            else:
                return True # Fallback if no OS locks supported
        except OSError:
            return False

    @staticmethod
    def _unlock_file(f):
        """Release the exclusive lock."""
        try:
            if _HAS_MSVCRT:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            elif _HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    @staticmethod
    def _ensure_file_exists(file_path: Path):
        """Safely ensure the file exists so we can use r+ mode without truncation."""
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Exclusive creation, fails safely if another thread beats us to it
                with open(file_path, 'x', encoding='utf-8') as f:
                    f.write("{}")
            except FileExistsError:
                pass

    @staticmethod
    def load_json(file_path: Path, retries: int = 15, wait_time: float = 0.5) -> Dict[str, Any]:
        """Safely load JSON data with OS-level read locking."""
        if not file_path.exists():
            return {}

        for i in range(retries):
            if not file_path.exists(): return {}
            f = None
            try:
                f = open(file_path, 'r', encoding='utf-8')
                if not SafeJsonIO._lock_file(f):
                    raise PermissionError("File is locked by another process.")
                
                f.seek(0)
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
                
            except json.JSONDecodeError as e:
                logging.error(f"CORRUPT JSON: {file_path} | {e}")
                return {}
            except (OSError, PermissionError) as e:
                logging.debug(f"File locked (Read): {file_path}. Retrying {i+1}/{retries}...")
                time.sleep(wait_time)
            finally:
                if f:
                    SafeJsonIO._unlock_file(f)
                    f.close()
                    
        logging.error(f"TIMEOUT: Could not acquire read lock for {file_path}")
        return {}

    @staticmethod
    def save_json(file_path: Path, data: Dict[str, Any], indent: int = 4, retries: int = 15, wait_time: float = 0.5) -> bool:
        """Safely save JSON data with in-place truncation (Zero-Delete Protocol)."""
        SafeJsonIO._ensure_file_exists(file_path)
        
        for i in range(retries):
            f = None
            try:
                f = open(file_path, 'r+', encoding='utf-8')
                if not SafeJsonIO._lock_file(f):
                    raise PermissionError("File is locked by another process.")
                
                f.truncate(0)
                f.seek(0)
                json.dump(data, f, indent=indent)
                f.flush()
                os.fsync(f.fileno())
                return True
                
            except (OSError, PermissionError) as e:
                logging.debug(f"File locked (Write): {file_path}. Retrying {i+1}/{retries}...")
                time.sleep(wait_time)
            finally:
                if f:
                    SafeJsonIO._unlock_file(f)
                    f.close()
                    
        logging.error(f"TIMEOUT: Could not acquire write lock for {file_path}")
        return False

    @staticmethod
    def update_json(file_path: Path, update_func, indent: int = 4, retries: int = 20, wait_time: float = 0.5) -> bool:
        """Atomic Read-Modify-Write operation using OS locks."""
        SafeJsonIO._ensure_file_exists(file_path)
        
        for i in range(retries):
            f = None
            try:
                f = open(file_path, 'r+', encoding='utf-8')
                if not SafeJsonIO._lock_file(f):
                    raise PermissionError("File is locked by another process.")
                
                # Load
                f.seek(0)
                content = f.read().strip()
                data = json.loads(content) if content else {}
                
                # Modify
                update_func(data)
                
                # Save
                f.truncate(0)
                f.seek(0)
                json.dump(data, f, indent=indent)
                f.flush()
                os.fsync(f.fileno())
                return True
                
            except json.JSONDecodeError as e:
                logging.error(f"Read error during atomic update: {e}")
                return False
            except Exception as e:
                # If update_func fails or we hit a generic error
                if isinstance(e, (TypeError, ValueError, KeyError, AttributeError)):
                    logging.error(f"Update function failed: {e}")
                    return False
                logging.debug(f"File locked (Atomic Update): {file_path}. Retrying {i+1}/{retries}...")
                time.sleep(wait_time)
            finally:
                if f:
                    SafeJsonIO._unlock_file(f)
                    f.close()
                    
        logging.error(f"TIMEOUT: Could not acquire lock for atomic update: {file_path}")
        return False
