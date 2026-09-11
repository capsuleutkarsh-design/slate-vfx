import logging
import time
import psutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.core.infra.file_operations import SafeFileOperations
from ut_vfx.utils.security import SecurityValidator
from ut_vfx.core.services.path_template_manager import get_path_manager
from ..ingest.analyzer import SmartIngestAnalyzer
from ut_vfx.core.infra.config_manager import ConfigManager

# --- JUNK FILE FILTER LIST ---
IGNORED_FILES = {
    '.ds_store', 'thumbs.db', 'desktop.ini', 
    '._*', # AppleDouble resource forks
    '*.tmp', '*.bak', '*.swp', # Temp/Backup files
    '$recycle.bin', 'system volume information'
}

class BetaSmartInternalWorker(QThread):
    """
    BETA WORKER: Unified Project Builder & Smart Files Mover.
    Integrates Smart Ingest Intelligence into the standard Auto-Scan structure.
    """
    
    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, int, int, int, int, str)
    
    def __init__(self, target_dir, source_scan_path=None, project_name="", template_data=(), 
                 target_reel_name="", overwrite=False, dry_run=False, fast_mode=False):
        super().__init__()
        self.target_dir = target_dir
        self.source_scan_path = source_scan_path
        self.project_name = project_name
        self.template_data = template_data
        self.target_reel_name = target_reel_name
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.fast_mode = fast_mode
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.pause_condition = QWaitCondition()
        
        self.folders_created = 0
        self.reels_count = 0
        self.shots_count = 0
        self.files_moved = 0
        self.errors = 0
        self.security_validator = SecurityValidator()
        
        # Init Analyzer
        cm = ConfigManager()
        self.analyzer = SmartIngestAnalyzer(cm.ingest_rules)

    def pause(self):
        self.mutex.lock()
        self.is_paused = True
        self.mutex.unlock()
        self.log_signal.emit("[WAIT] Process Paused.")

    def resume(self):
        self.mutex.lock()
        self.is_paused = False
        self.pause_condition.wakeAll()
        self.mutex.unlock()
        self.log_signal.emit("[RESUME] Process Resumed.")

    def check_pause(self):
        self.mutex.lock()
        if self.is_paused:
            self.pause_condition.wait(self.mutex)
        self.mutex.unlock()

    def run(self):
        start_time = time.time()
        
        try:
            pid = database_manager.record_project(self.project_name, "beta_smart", str(self.target_dir))
            op_type = "Beta Smart Ingest"
            self.op_id = database_manager.start_operation(pid, op_type)
        except Exception:
            self.op_id = 0

        try:
            self.log_signal.emit("[START] Starting Beta Smart Ingest Process...")
            
            if self.source_scan_path and not self.dry_run:
                total_size = self._calculate_directory_size(self.source_scan_path)
                if not self._check_disk_space(total_size, self.target_dir):
                    self.finished_signal.emit(False, 0, 0, 0, 0, "Insufficient Disk Space")
                    return

            base_folders, prod_subs, _, shot_subs = self.template_data
            project_path = self.target_dir / self.project_name
            
            # 1. Structure Creation
            self.log_signal.emit("[BUILD] Building Project Structure...")
            if not self.dry_run:
                SafeFileOperations.safe_create_directory(project_path)
                for f in base_folders: SafeFileOperations.safe_create_directory(project_path / f)
                if prod_subs:
                    p = project_path / "04_Production"
                    SafeFileOperations.safe_create_directory(p)
                    for s in prod_subs: SafeFileOperations.safe_create_directory(p / s)
                # Create 05_Reels using template
                mgr = get_path_manager()
                reels_path = Path(mgr.format_path('reels_root', project=self.project_name, root=str(project_path.parent)))
                SafeFileOperations.safe_create_directory(reels_path)

            # 2. Beta SMART Phase
            if self.source_scan_path:
                self._process_beta_smart_ingest(reels_path, shot_subs, self.op_id)

            dur = time.time() - start_time
            database_manager.update_operation(self.op_id, dur, self.files_moved, self.errors, True)
            self.finished_signal.emit(True, 1, self.reels_count, self.shots_count, self.folders_created, f"Done. Moved {self.files_moved} files.")

        except Exception as e:
            logging.exception(f"Beta Worker Error: {e}", exc_info=True)
            database_manager.update_operation(self.op_id, time.time()-start_time, 0, 1, False)
            self.finished_signal.emit(False, 0,0,0,0, str(e))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

    def _process_beta_smart_ingest(self, root, subs, oid):
        self.log_signal.emit(f"[SCAN] Smart Scanning: {self.source_scan_path}")
        
        # Determine Destination Reel
        if self.target_reel_name:
            dest_reel = root / self.target_reel_name
            if not self.dry_run: SafeFileOperations.safe_create_directory(dest_reel)
            self.reels_count += 1
            
            # Treat Client Drive as Shot List directly or Reel? 
            # Consistent with Auto-Scan: Iterate immediate children as potential shots
            for shot_dir in [d for d in self.source_scan_path.iterdir() if d.is_dir()]:
                self.check_pause()
                if not self.is_running: break
                self._process_single_smart_shot(shot_dir, dest_reel, subs, oid)
        else:
            # Auto-Detect Reel (Client_Drive / Reel_X / Shot_X)
            for reel_dir in [d for d in self.source_scan_path.iterdir() if d.is_dir()]:
                dest_reel = root / reel_dir.name
                if not self.dry_run: SafeFileOperations.safe_create_directory(dest_reel)
                self.reels_count += 1
                for shot_dir in [d for d in reel_dir.iterdir() if d.is_dir()]:
                    self.check_pause()
                    if not self.is_running: break
                    self._process_single_smart_shot(shot_dir, dest_reel, subs, oid)

    def _process_single_smart_shot(self, src_shot, dest_reel, subs, oid):
        """
        Analyzes files in the shot folder and sorts them using Smart Analyzer.
        """
        shot_name = src_shot.name
        dest_shot = dest_reel / shot_name
        
        if not self.dry_run: SafeFileOperations.safe_create_directory(dest_shot)
        self.shots_count += 1
        self.folders_created += 1
        
        # Create standard subs first
        for s in subs:
            if not self.dry_run: SafeFileOperations.safe_create_directory(dest_shot / s)
            self.folders_created += 1

        self.log_signal.emit(f"  [SHOT] Analysing Shot: {shot_name}")

        for f in [x for x in src_shot.rglob('*') if x.is_file()]:
            self.check_pause()
            if not self.is_running: break
            
            if self._is_junk_file(f): continue

            # --- SMART ANALYSIS ---
            category, score, reason = self.analyzer.analyze_item(f)
            
            # Determine Target Subfolder
            # If analyzer found a category (e.g., "01_Plates"), try to match it with `subs`
            # or fallback to "01_Scan/Unknown".
            
            target_sub = "01_Scan/Unknown" # Default
            
            if category:
                # Find best matching folder in 'subs' list to maintain structure consistency
                # e.g. category="Plates", sub="01_Plates" -> match!
                for s in subs:
                    if category.lower() in s.lower():
                        target_sub = s
                        break
                else:
                    # If no direct match in template, use the raw category
                    # e.g. "Documents" might not be in template, but we create it?
                    # Ideally we stick to template. 
                    if score > 0.8: # High confidence, create folder?
                        target_sub = category
            
            rel_parent = f.relative_to(src_shot).parent
            dest_file = dest_shot / target_sub / rel_parent / f.name
            
            # Ensure target parent exists (for deep dynamic moves)
            if not self.dry_run:
                 SafeFileOperations.safe_create_directory(dest_file.parent)

            # Move Logic
            try:
                if not self.dry_run:
                    if dest_file.exists() and self.overwrite: dest_file.unlink()
                    if not dest_file.exists():
                        verify = not self.fast_mode
                        success, msg, moved_bytes = SafeFileOperations.safe_move_with_verification(
                            f, dest_file, verify_checksum=verify
                        )
                        if not success: raise Exception(msg)
                
                size = f.stat().st_size if f.exists() else 0
                self.files_moved += 1
                self.log_signal.emit(f"    [MATCH] [{int(score*100)}%] {f.name} -> {target_sub}")
                database_manager.record_task_detail(oid, f.name, f, dest_file, size, 0, "Success")
            except Exception as e:
                self.errors += 1
                self.log_signal.emit(f"    [ERR] Error: {e}")

    def _calculate_directory_size(self, path):
        return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())

    def _check_disk_space(self, size, dest):
        try:
            free = psutil.disk_usage(str(dest.anchor)).free
            return free > (size * 1.1)
        except Exception: return True

    def _is_junk_file(self, path: Path) -> bool:
        name = path.name.lower()
        if name in IGNORED_FILES: return True
        if name.startswith("._"): return True
        if name.startswith("~$"): return True
        return False