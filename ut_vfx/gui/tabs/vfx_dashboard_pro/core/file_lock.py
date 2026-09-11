import os
import time
import socket
import logging
import psutil
from datetime import datetime

class FileLock:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lock_file = filepath + '.lock'
        self.locked = False
        self.hostname = socket.gethostname()
        
    def acquire(self) -> bool:
        if os.path.exists(self.lock_file):
            try:
                # Read lock info
                with open(self.lock_file, 'r') as f:
                    content = f.read().splitlines()
                    
                # Format: PID, Hostname, Timestamp
                # Support legacy format (just PID or PID\nTime)
                file_pid = int(content[0]) if content else 0
                file_host = content[1] if len(content) > 1 and not content[1][0].isdigit() else None
                
                # Check 1: Same Machine Crash Detection
                if file_host == self.hostname:
                    # Robust process existence check (prevents false positives from PID recycling on Windows)
                    if psutil.pid_exists(file_pid):
                        # The PID exists. For bulletproof checking, we could check process name,
                        # but just using psutil is significantly better than os.kill on Windows.
                        pass # Process exists, so it IS locked by another instance
                    else:
                        # Process does NOT exist, stale lock!
                        logging.warning(f"Removing stale lock from crashed process {file_pid}")
                        try:
                            os.remove(self.lock_file)
                        except Exception:
                            return False # Cannot remove, so cannot acquire
                            
                # Check 2: Time Timeout (10 minutes)
                mtime = os.path.getmtime(self.lock_file)
                if time.time() - mtime > 600: # 10 mins
                    logging.warning("Removing old stale lock (timeout)")
                    try:
                        os.remove(self.lock_file)
                    except Exception:
                        return False
                        
                # If we are here and file still exists, it is truly locked
                if os.path.exists(self.lock_file):
                    return False
                    
            except (OSError, ValueError, IndexError):
                # Corrupt lock file or read error, try to remove
                try:
                    os.remove(self.lock_file)
                except Exception:
                    return False
                
        try:
            with open(self.lock_file, 'w') as f:
                f.write(f"{os.getpid()}\n{self.hostname}\n{datetime.now()}")
            self.locked = True
            return True
        except IOError:
            return False
            
    def release(self):
        if self.locked and os.path.exists(self.lock_file):
            try:
                # Verify we own it before deleting (optional but safe)
                # But self.locked=True implies we created it.
                os.remove(self.lock_file)
                self.locked = False
            except OSError:
                pass
                
    def __del__(self):
        self.release()
