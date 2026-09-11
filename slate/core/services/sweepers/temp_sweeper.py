import os
import logging
import time
from pathlib import Path
from ..sweeper_engine import BaseSweeper

logger = logging.getLogger(__name__)

class TempFileSweeper(BaseSweeper):
    def __init__(self, name="TempFileSweeper", max_age_days=1):
        super().__init__(name)
        self.max_age_days = max_age_days
        
    def run(self, dry_run=False):
        freed = 0
        count = 0
        errors = []
        
        # 1. Slate Temp Dir via AppData
        # (Assuming standard location: %LOCALAPPDATA%/Slate/Temp)
        local_appdata = os.environ.get('LOCALAPPDATA')
        if not local_appdata:
            return {'freed_bytes': 0, 'files_deleted': 0}

        target_dirs = [
             Path(local_appdata) / "Slate" / "Temp",
             Path(local_appdata) / "Slate" / "Cache"
        ]
        
        current_time = time.time()
        age_seconds = self.max_age_days * 86400
        
        for folder in target_dirs:
            if not folder.exists(): 
                continue
                
            for root, dirs, files in os.walk(folder):
                for f in files:
                    try:
                        filepath = Path(root) / f
                        stat = filepath.stat()
                        
                        # Check Age
                        if (current_time - stat.st_mtime) > age_seconds:
                            size = stat.st_size
                            if not dry_run:
                                try:
                                    os.remove(filepath)
                                    freed += size
                                    count += 1
                                except Exception as e:
                                    errors.append(str(e))
                            else:
                                freed += size
                                count += 1
                    except Exception as exc:
                        logger.debug("Skipping temp file cleanup for %s: %s", filepath, exc)
                        
        return {
            'freed_bytes': freed,
            'files_deleted': count,
            'errors': errors
        }
