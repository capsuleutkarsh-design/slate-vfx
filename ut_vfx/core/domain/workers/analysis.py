import json
import logging
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from ut_vfx.utils.media_capabilities import is_video

# Import SmartMetadataManager from core.metadata_engine
# File is in ut_vfx/core/workers/analysis.py
# Parent is ut_vfx/core
from ..metadata_engine import SmartMetadataManager

class BrokenAssetWorker(QThread):
    found_broken_signal = Signal(list)
    
    def __init__(self, assets):
        super().__init__()
        self.assets = assets
        self.running = True
        
    def run(self):
        to_heal = []
        # Chunk process to avoid holding GIL too long if massive list
        for i, asset in enumerate(self.assets):
            if not self.running: break
            
            # Lightweight checks
            p = asset.get('path') or asset.get('file_path') or ''
            
            # FIX: Ignore macOS resource fork files
            if Path(p).name.startswith("._"):
                 continue

            is_video_file = is_video(Path(p).suffix.lower()) if p else False
            
            # JSON parse is slightly heavy in tight loop
            try:
                meta = json.loads(asset.get('metadata', '{}')) if isinstance(asset.get('metadata'), str) else asset.get('metadata', {})
            except Exception as e:
                logging.warning(f"Failed to parse metadata for asset: {e}")
                meta = {}
                
            duration = meta.get('duration_sec', 0)
            
            if is_video_file and duration == 0:
                to_heal.append(asset)
                continue
                
                
            # Allow assets without tags (User preference: don't auto-heal perfectly valid assets just for tags)
            # tags = asset.get('tags', [])
            # if not tags:
            #     to_heal.append(asset)
                
        if self.running and to_heal:
            self.found_broken_signal.emit(to_heal)
            
    def stop(self):
        self.running = False
        self.wait(1000)  # Wait up to 1 second for thread to finish


class MetadataHealerWorker(QThread):
    progress_signal = Signal(int, int) # current, total
    file_healed_signal = Signal(str, dict, list) # KEPT FOR COMPATIBILITY (Optional)
    finished_signal = Signal()

    def __init__(self, items_to_heal, library_manager):
        super().__init__()
        self.items = items_to_heal 
        self.lib_manager = library_manager
        self.is_running = True

    def run(self):
        total = len(self.items)
        for i, item in enumerate(self.items):
            if not self.is_running: break
            
            # Handle both string paths and asset dicts
            if isinstance(item, dict):
                path_str = item.get('path') or item.get('file_path')
            else:
                path_str = str(item)
            
            if not path_str:
                continue
            
            # FIX: Ignore macOS resource fork files
            if Path(path_str).name.startswith("._"):
                continue

            path = Path(path_str)
            if not path.exists(): continue
            
            try:
                # 1. Tech Meta (Heavy I/O)
                meta = SmartMetadataManager.extract_tech_metadata(str(path))
                
                # 2. Smart Tags
                primary, tags = SmartMetadataManager.get_smart_tags(path)
                
                # 3. DIRECT UPDATE (Background Thread)
                # This prevents blocking the Main Thread with DB writes
                self.lib_manager.update_asset_metadata(str(path), meta, tags)
                
                self.progress_signal.emit(i + 1, total)
                
            except Exception as e:
                logging.exception(f"Healer failed for {path}: {e}")
                
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False
        self.wait(1000)  # Wait up to 1 second for thread to finish