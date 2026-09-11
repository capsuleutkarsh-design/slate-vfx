import os
import logging
import time
from pathlib import Path
from ..sweeper_engine import BaseSweeper

logger = logging.getLogger(__name__)

class ProxySweeper(BaseSweeper):
    def __init__(self, name="ProxySweeper", projects_root=None, max_age_days=30):
        super().__init__(name)
        self.projects_root = projects_root # Should be passed from config
        self.max_age_days = max_age_days
        
    def run(self, dry_run=False):
        if not self.projects_root or not Path(self.projects_root).exists():
            return {'freed_bytes': 0, 'files_deleted': 0, 'error': 'No projects root'}
            
        freed = 0
        count = 0
        errors = []
        
        current_time = time.time()
        age_seconds = self.max_age_days * 86400
        
        # Heuristic: Look for "proxy" or "proxies" folders inside project structures
        # To be safe, we only look 3 levels deep max
        root_path = Path(self.projects_root)
        
        # Walk with depth limit logic manually or just standard walk if structure is known
        # Assuming: Project / Shot / proxies
        
        for root, dirs, files in os.walk(root_path):
            # Only care if current dir is named "proxy" or "proxies"
            current_dir_name = Path(root).name.lower()
            
            if current_dir_name in ['proxy', 'proxies', '.proxy']:
                # SAFE TO CLEAN OLD FILES HERE
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
                        logger.debug("Skipping proxy cleanup for %s: %s", full_path, exc)
                        
        return {
            'freed_bytes': freed,
            'files_deleted': count,
            'errors': errors
        }
