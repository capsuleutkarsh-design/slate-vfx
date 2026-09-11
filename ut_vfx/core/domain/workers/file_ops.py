import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple

from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from ut_vfx.utils.error_handler import error_handler
from ut_vfx.utils.security import SecurityValidator
from ut_vfx.utils.asset_tracker import AssetTracker
from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.core.infra.file_operations import SafeFileOperations

# --- JUNK FILE FILTER LIST ---
IGNORED_FILES = {
    '.ds_store', 'thumbs.db', 'desktop.ini', 
    '._*', # AppleDouble resource forks
    '*.tmp', '*.bak', '*.swp', # Temp/Backup files
    '$recycle.bin', 'system volume information'
}

class MoveScanWorker(QThread):
    
    progress_signal = Signal(int, str)
    log_signal = Signal(str)
    stats_signal = Signal(int, int, int, int)
    finished_signal = Signal(bool, str)
    file_progress_signal = Signal(str, int, int)
    
    def __init__(self, parent, mode: str, **kwargs):
        super().__init__(parent)
        self.mode = mode
        self.kwargs = kwargs
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.pause_condition = QWaitCondition()
        
        self.files_moved = 0
        self.folders_created = 0
        self.files_skipped = 0
        self.errors = 0
        self.total_bytes_moved = 0
        
        self.security_validator = SecurityValidator()
        self.asset_tracker = AssetTracker()
        
        error_handler.register_operation(f"move_scan_{id(self)}", {'type': 'move_scan', 'mode': mode})
        
    def pause(self):
        """Pause the worker thread safely."""
        self.mutex.lock()
        self.is_paused = True
        self.mutex.unlock()
        self.log_signal.emit("[WAIT] Process Paused.")

    def resume(self):
        """Resume the worker thread."""
        self.mutex.lock()
        self.is_paused = False
        self.pause_condition.wakeAll()
        self.mutex.unlock()
        self.log_signal.emit("[RESUME] Process Resumed.")

    def check_pause(self):
        """Check if paused, and block if necessary."""
        self.mutex.lock()
        if self.is_paused:
            self.pause_condition.wait(self.mutex)
        self.mutex.unlock()

    def run(self):
        self.start_time = datetime.now()
        
        try:
            proj_name = f"Scan_Op_{datetime.now().strftime('%H%M')}"
            pid = database_manager.record_project(proj_name, "Scan_Ops", "Various")
            self.op_id = database_manager.start_operation(pid, f"Move Scan ({self.mode})")
        except Exception:
            self.op_id = 0

        try:
            self.log_signal.emit("[START] Starting production scan move operation...")
            
            if self.mode == "excel_based":
                success, message = self._run_excel_based_move()
            elif self.mode == "specific_shot":
                success, message = self._run_specific_shot_move()
            else:
                success, message = False, f"Unknown mode: {self.mode}"
            
            duration = (datetime.now() - self.start_time).total_seconds()
            database_manager.update_operation(self.op_id, duration, self.files_moved, self.errors, success)
            
            self._log_final_statistics(success, message)
            self.finished_signal.emit(success, message)
                
        except Exception as e:
            logging.exception(f"CRITICAL: {e}", exc_info=True)
            self.finished_signal.emit(False, str(e))
        finally:
            error_handler.cleanup_operation(f"move_scan_{id(self)}")
    
    def _run_excel_based_move(self) -> Tuple[bool, str]:
        scan_map = self.kwargs.get("scan_map", {})
        dest_root = Path(self.kwargs.get("dest_root", ""))
        dry_run = self.kwargs.get("dry_run", False)
        overwrite = self.kwargs.get("overwrite", False)
        format_mapping = self.kwargs.get("format_mapping", {})
        fast_mode = self.kwargs.get("fast_mode", False)
        
        if not scan_map: return False, "No scan map"
        if not dest_root.exists(): return False, "Destination root missing"
        
        total = len(scan_map)
        current = 0
        
        for source_str, info in scan_map.items():
            self.check_pause()
            if not self.is_running: return False, "Stopped by user"
            
            source = Path(source_str)
            reel_name = info.get('reel', 'Unknown')
            shot_name = info.get('shot', 'Unknown')
            
            if not source.exists():
                self.files_skipped += 1
                continue
            
            # 1. Determine Scan Root
            reel_clean = str(reel_name).strip().replace('/', '_')
            shot_clean = str(shot_name).strip().replace('/', '_')
            
            shot_path = dest_root / reel_clean / shot_clean
            scan_root_name = "01_Scan" # Default
            
            if shot_path.exists():
                for child in shot_path.iterdir():
                    if child.is_dir() and "scan" in child.name.lower():
                        scan_root_name = child.name
                        break
            
            base_dest = shot_path / scan_root_name
            
            if dry_run:
                self.files_moved += 1
                self.log_signal.emit(f"[DRY] {source.name} -> {base_dest}")
            else:
                success, bytes_moved = self._process_folder_recursive(source, base_dest, overwrite, format_mapping, fast_mode)
                if not success:
                    self.errors += 1
            
            current += 1
            self.progress_signal.emit(int((current/total)*100), f"Processing {current}/{total}")
        
        return self.errors == 0, "Completed"

    def _run_specific_shot_move(self) -> Tuple[bool, str]:
        source_dir = Path(self.kwargs.get("source_dir", ""))
        dest_root = Path(self.kwargs.get("dest_root", ""))
        reel_name = self.kwargs.get("reel_name", "")
        shot_name = self.kwargs.get("shot_name", "")
        dry_run = self.kwargs.get("dry_run", False)
        overwrite = self.kwargs.get("overwrite", False)
        format_mapping = self.kwargs.get("format_mapping", {})
        fast_mode = self.kwargs.get("fast_mode", False)
        
        self.log_signal.emit(f"[MOVE] Manual Move: {shot_name} -> {reel_name}")
        
        if not source_dir.exists(): return False, "Source missing"
        
        reel_clean = str(reel_name).strip().replace('/', '_')
        shot_clean = str(shot_name).strip().replace('/', '_')
        shot_path = dest_root / reel_clean / shot_clean
        scan_root_name = "01_Scan"
        if shot_path.exists():
            for child in shot_path.iterdir():
                if child.is_dir() and "scan" in child.name.lower():
                    scan_root_name = child.name
                    break
        
        base_dest = shot_path / scan_root_name
        
        if dry_run:
            self.log_signal.emit(f"[DRY] {source_dir} -> {base_dest}")
            return True, "Dry run ok"
            
        success, bytes_moved = self._process_folder_recursive(source_dir, base_dest, overwrite, format_mapping, fast_mode)
        
        if success:
            self.log_signal.emit(f"[OK] Created Version: {base_dest}")
            return True, "Success"
        return False, "Failed"

    def _process_folder_recursive(self, source_root: Path, base_dest: Path, overwrite: bool, format_mapping: dict, fast_mode: bool) -> Tuple[bool, int]:
        total_bytes = 0
        error_count = 0
        
        subfolders = [d for d in source_root.iterdir() if d.is_dir()]
        if len(subfolders) > 0:
            subnames = ", ".join([d.name for d in subfolders])
            self.log_signal.emit(f"[WARN] Multi-Scan Detected: [{subnames}]. Preserving structure.")

        for f in source_root.rglob('*'):
            self.check_pause()
            if not self.is_running: break
            
            if not f.is_file() or self._is_junk_file(f): continue
            
            ext = f.suffix.lower().lstrip('.')
            mapped_val = format_mapping.get(ext)
            format_folder = mapped_val.split('/')[-1] if mapped_val else ext.upper()
            
            rel_path = f.relative_to(source_root)
            sub_structure = rel_path.parent
            final_dest = base_dest / format_folder / sub_structure / f.name
            
            success, size = self._production_move_operation(f, final_dest, overwrite, fast_mode)
            if success:
                self.files_moved += 1
                total_bytes += size
                if self.files_moved % 10 == 0:
                    self.log_signal.emit(f"[OK] Moved: .../{sub_structure}/{f.name}")
            else:
                error_count += 1
                
        return error_count == 0, total_bytes

    def _production_move_operation(self, source: Path, destination: Path, overwrite: bool, fast_mode: bool) -> Tuple[bool, int]:
        t0 = time.perf_counter()
        
        try:
            if destination.exists() and not overwrite:
                duration = time.perf_counter() - t0
                self.files_skipped += 1
                database_manager.record_task_detail(self.op_id, source.name, source, destination, 0, duration, "Skipped", "File exists")
                self.log_signal.emit(f"[SKIP] {source.name} exists")
                return True, 0

            # Use SafeFileOperations
            verify = not fast_mode
            success, msg, moved_bytes = SafeFileOperations.safe_move_with_verification(
                source, destination, verify_checksum=verify
            )

            duration = time.perf_counter() - t0
            
            if success:
                database_manager.record_task_detail(self.op_id, source.name, source, destination, moved_bytes, duration, "Success")
                return True, moved_bytes
            else:
                database_manager.record_task_detail(self.op_id, source.name, source, destination, 0, duration, "Failed", msg)
                self.log_signal.emit(f"[ERR] Error: {msg}")
                return False, 0
            
        except Exception as e:
            self.log_signal.emit(f"[ERR] Move Error: {e}")
            return False, 0

    def _is_junk_file(self, path: Path) -> bool:
        name = path.name.lower()
        if name in IGNORED_FILES: return True
        if name.startswith("._"): return True 
        if name.startswith("~$"): return True 
        if path.suffix.lower() in {'.tmp', '.bak'}: return True
        return False

    def _log_final_statistics(self, success, message):
        self.log_signal.emit(f"[DONE] Moved: {self.files_moved}, Errors: {self.errors}")

    def stop(self):
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.resume()