import os
import random
import string
import shutil
import logging
import pandas as pd
from pathlib import Path
from typing import Optional

# Import DB for verification
from ..core.infra.database_manager import database_manager
from ..core.infra.global_config import GlobalConfig
from ..core.infra.app_context import AppContext

from PySide6.QtWidgets import (

    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTabWidget, QSpinBox, QComboBox, QCheckBox, QLineEdit, 
    QProgressBar, QFileDialog, QGroupBox, QFormLayout, QTextEdit,
    QMessageBox, QRadioButton, QButtonGroup, QTableWidget,
    QTableWidgetItem, QHeaderView, QDateEdit
)

from PySide6.QtCore import Qt, Signal, QThread, QObject, QDate, QTime, QDateTime
from ..core.domain.asset_ingestor import IngestWorker
from slate.gui.core.offline_notice import on_database_error

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


# --- UTILS ---
class QTextEditHandler(logging.Handler, QObject):
    """Custom Logging Handler to emit signals to a QTextEdit."""
    log_signal = Signal(str)
    
    def __init__(self, parent=None):
        logging.Handler.__init__(self)
        QObject.__init__(self, parent)
        
    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_signal.emit(msg)
        except Exception:
            self.handleError(record)


class FileGeneratorWorker(QThread):
    progress_signal = Signal(int)
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, target_path, count, size_strategy, file_types):
        super().__init__()
        self.target_path = Path(target_path)
        self.count = count
        self.size_strategy = size_strategy # "Empty", "1KB", "1MB", "Random"
        self.file_types = file_types # [".jpg", ".mov", ...]
        self._is_running = True

    def run(self):
        self.target_path.mkdir(parents=True, exist_ok=True)
        
        for i in range(self.count):
            if not self._is_running: break
            
            ext = random.choice(self.file_types)
            name = f"dummy_file_{i:04d}_{self._random_string(5)}{ext}"
            file_path = self.target_path / name
            
            try:
                self._create_file(file_path)
                self.log_signal.emit(f"Created: {name}")
            except Exception as e:
                self.log_signal.emit(f"Error creating {name}: {e}")
                
            progress = int((i + 1) / self.count * 100)
            self.progress_signal.emit(progress)
            
        self.finished_signal.emit()

    def stop(self):
        self._is_running = False

    def _random_string(self, length):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def _create_file(self, path):
        size = 0
        if self.size_strategy == "1KB": size = 1024
        elif self.size_strategy == "1MB": size = 1024 * 1024
        elif self.size_strategy == "50MB": size = 50 * 1024 * 1024
        elif self.size_strategy == "Random": size = random.randint(1024, 10 * 1024 * 1024)
        
        # Use sparse file creation for speed if system supports it, or just write zeros
        with open(path, "wb") as f:
            if size > 0:
                f.seek(size - 1)
                f.write(b"\0")
            else:
                pass # Empty file

class StructureWorker(QThread):
    finished_signal = Signal(str)
    
    def __init__(self, target_path, nesting_level, folder_count):
        super().__init__()
        self.target_path = Path(target_path)
        self.nesting_level = nesting_level
        self.folder_count = folder_count

    def run(self):
        try:
            self.target_path.mkdir(parents=True, exist_ok=True)
            created = 0
            
            # 1. Deep Nesting
            current = self.target_path
            for i in range(self.nesting_level):
                current = current / f"Deep_Level_{i+1}"
                current.mkdir(exist_ok=True)
                created += 1
            
            # 2. Wide Folders
            for i in range(self.folder_count):
                (self.target_path / f"Wide_Folder_{i+1:03d}").mkdir(exist_ok=True)
                created += 1
                
            self.finished_signal.emit(f"Successfully created {created} folders.")
        except Exception as e:
            self.finished_signal.emit(f"Error: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

class WorkflowWorker(QThread):
    finished_signal = Signal(str)
    
    def __init__(self, root_path, count=10, size_strategy="Empty", file_types=None, nesting_level=0, scans_per_shot=1, create_sequences=False):
        super().__init__()
        self.root_path = Path(root_path)
        self.count = count # Files per shot (or frames per sequence)
        self.size_strategy = size_strategy
        self.file_types = file_types or [".mov"]
        self.nesting_level = nesting_level
        self.scans_per_shot = scans_per_shot
        self.create_sequences = create_sequences
        
        # Helper for file creation
        self.gen_worker = FileGeneratorWorker(Path("."), count, size_strategy, file_types) 

    def run(self):
        try:
            base = self.root_path / "TEST"
            if base.exists():
                shutil.rmtree(base)
            base.mkdir(parents=True, exist_ok=True)
            
            # 1. Structure
            path_move = base / "For_move"
            path_client = path_move / "From_Client"
            path_dest = path_move / "Destination"
            path_rename = base / "FOR_RENAME"
            
            # Create potentially nested client root
            current_client_root = path_client
            for i in range(self.nesting_level):
                current_client_root = current_client_root / f"Level_{i+1}"
            
            current_client_root.mkdir(parents=True, exist_ok=True)
            path_dest.mkdir(parents=True, exist_ok=True)
            path_rename.mkdir(parents=True, exist_ok=True)
            
            # 2. Generate Excel Template & Content
            data = []
            files_created = 0
            
            # Create 2 Reels with 5 Shots each
            for r in range(1, 3): 
                reel_name = f"REEL_{r:02d}"
                for s in range(1, 6): 
                    shot_base_name = f"CAP_RL{r:02d}_SH_{s:04d}"
                    frames = random.randint(50, 200)
                    data.append({"SR NO": len(data)+1, "REEL": reel_name, "SHOT NO": shot_base_name, "FRAMES": frames})
                    
                    # Create Multiple Scans per Shot (if requested)
                    for scan_idx in range(self.scans_per_shot):
                        # Construct Scan Folder Name
                        if self.scans_per_shot > 1:
                            # Mimic received naming: ShotName_ScanA, ShotName_v01, etc.
                            suffix = f"_Scan{chr(65+scan_idx)}" # _ScanA, _ScanB
                            shot_folder_name = f"{shot_base_name}{suffix}"
                        else:
                            shot_folder_name = shot_base_name
                            
                        shot_dir = current_client_root / shot_folder_name
                        shot_dir.mkdir(parents=True, exist_ok=True)
                        
                        # 3. Create Smart Dummy Files
                        if self.create_sequences:
                            # Generate IMAGE SEQUENCE
                            # Use first selected type or default to .exr
                            ext = next((t for t in self.file_types if t in ['.exr', '.jpg', '.png', '.dpx']), '.exr')
                            seq_name = f"{shot_base_name}_v01"
                            
                            for i in range(self.count): # count = frames
                                frame_num = 1001 + i
                                f_name = f"{seq_name}.{frame_num:04d}{ext}"
                                f_path = shot_dir / f_name
                                self.gen_worker._create_file(f_path)
                                files_created += 1
                        else:
                            # Generate Random Files
                            for i in range(self.count):
                                ext = random.choice(self.file_types)
                                f_name = f"{shot_base_name}_v{i+1:03d}{ext}"
                                f_path = shot_dir / f_name
                                self.gen_worker._create_file(f_path)
                                files_created += 1

            df = pd.DataFrame(data)
            excel_path = path_move / "EXCEL_TEMPLETE.xlsx"
            df.to_excel(excel_path, index=False)
            
            # 4. Populate Rename
            for i in range(5):
                (path_rename / f"DCIM_{random.randint(1000,9999)}.JPG").touch()
            
            msg = (
                f"Smart Simulation Ready!\n\n"
                f"Generated {files_created} files in structure.\n"
                f"Root: {base}\n"
                f"Client Path: {current_client_root}\n"
                f"Nesting Level: {self.nesting_level}\n"
                f"Scans per Shot: {self.scans_per_shot}\n"
                f"Sequences: {'Yes' if self.create_sequences else 'No'}\n"
                f"Size Strategy: {self.size_strategy}"
            )
            self.finished_signal.emit(msg)

        except Exception as e:
            self.finished_signal.emit(f"Error: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()


class ValidationWorker(QThread):
    finished_signal = Signal(str)
    
    
    def __init__(self, src_path, dst_path, verify_db=True, mode="raw"):
        super().__init__()
        self.src_path = Path(src_path)
        self.dst_path = Path(dst_path)
        self.verify_db = verify_db
        self.mode = mode # "raw" or "smart"

    def run(self):
        report = ["**ANALYSIS REPORT**", "-"*30]
        try:
            # --- MODE A: SMART DATABASE CHECK (Target Scope) ---
            if self.mode == "smart":
                target_scope = self.dst_path # In smart mode, dst is the target scope
                report.append("<b>Smart Verification Mode</b>")
                report.append(f"Target Scope: {target_scope}")
                
                if not target_scope.exists(): raise Exception(f"Target scope not found: {target_scope}")
                
                with database_manager.get_connection() as conn:
                    # Find items in DB that SHOULD be in this path
                    # We normalize path separators for SQL LIKE query
                    str(target_scope).replace("\\", "/")
                    report.append("Querying Database for matches...")
                    tasks = conn.execute(query).fetchall()
                    
                    if self.mode == "smart":
                         # CHECK A: DB -> DISK (Missing Files)
                         if not tasks: report.append("No database records found for this path.")
                         else:
                             report.append(f"Found {len(tasks)} expected files in DB.")
                             missing = []; size_mismatch = []
                             for task in tasks:
                                  f_path = Path(task['dest_path'])
                                  if not f_path.exists(): missing.append(task['item_name'])
                                  elif task['file_size']:
                                      try:
                                          if f_path.stat().st_size != int(task['file_size']):
                                              size_mismatch.append(f"{task['item_name']}")
                                      except (OSError, ValueError, TypeError) as e:
                                          logging.debug(f"Size check skipped for {task.get('item_name')}: {e}")
                             
                             if not missing and not size_mismatch: report.append("<font color='#5FBF8F'>INTEGRITY PASS</font>")
                             else:
                                 report.append("<font color='#D9635F'>FAIL</font>")
                                 if missing: report.append(f"Missing: {len(missing)}")
                                 if size_mismatch: report.append(f"Size Mismatch: {len(size_mismatch)}")

                    elif self.mode == "ghost":
                         # CHECK B: DISK -> DB (Ghost/Untracked Files)
                         report.append("<b>Ghost Hunter Mode</b>")
                         # 1. Get all DB paths for this scope
                         db_paths = set()
                         for t in tasks:
                             p = t['dest_path']
                             if p: db_paths.add(str(Path(p).absolute()))
                             
                         # 2. Walk Disk
                         ghosts = []
                         report.append("Scanning Disk...")
                         for root, dirs, files in os.walk(str(target_scope)):
                             for name in files:
                                 f_path = Path(root) / name
                                 abs_path = str(f_path.absolute())
                                 # Normalize? Windows paths can be tricky.
                                 # Let's try direct match first
                                 if abs_path not in db_paths:
                                     ghosts.append(abs_path)
                         
                         if not ghosts: report.append("<font color='#5FBF8F'>CLEAN: no ghost files found.</font>")
                         else:
                             report.append(f"<font color='#D9635F'>FOUND {len(ghosts)} GHOST FILES</font>")
                             report.append("(Files on disk but NOT in Database)")
                             for g in ghosts[:10]: report.append(f" - {g}")
                             if len(ghosts) > 10: report.append(f"... and {len(ghosts)-10} more.")


            # --- MODE B: RAW COMPARISON (Source vs Dest) ---
            else:
                if not self.src_path.exists(): raise Exception("Source path does not exist")
                if not self.dst_path.exists(): raise Exception("Dest path does not exist")
    
                # 1. FILESYSTEM CHECK (Source vs Dest)
                report.append(f"<b>Raw Comparison Mode</b><br>Src: {self.src_path}<br>Dst: {self.dst_path}")
                
                src_files = {f.name: f.stat().st_size for f in self.src_path.rglob('*') if f.is_file()}
                dst_files = {f.name: f.stat().st_size for f in self.dst_path.rglob('*') if f.is_file()}
                
                report.append(f"<br>Source Files: {len(src_files)} | Dest Files: {len(dst_files)}")
                
                missing = []
                corrupted = []
                
                for name, size in src_files.items():
                    if name not in dst_files:
                        missing.append(name)
                    elif dst_files[name] != size:
                        corrupted.append(f"{name} (Src: {size}B != Dst: {dst_files[name]}B)")
                
                if not missing and not corrupted:
                    report.append("<font color='#5FBF8F'>FILESYSTEM INTEGRITY: PASS</font>")
                else:
                    report.append("<font color='#D9635F'>FILESYSTEM INTEGRITY: FAIL</font>")
                    if missing: report.append(f"  - Missing: {len(missing)} files (e.g. {missing[:3]})")
                    if corrupted: report.append(f"  - Corrupted: {len(corrupted)} files (e.g. {corrupted[:3]})")

                # 2. DATABASE / REPORT CHECK (Previous Logic)
                if self.verify_db:
                    report.append("<br><b>Database &amp; Report Verification</b>")
                    with database_manager.get_connection() as conn:
                        # Get last operation
                        op = database_manager.execute_query(
                            "SELECT id, project_id, created_at FROM operations ORDER BY id DESC LIMIT 1",
                            fetch="one",
                        )
                        if op:
                            report.append(f"Latest Op ID: {op['id']} ({op['created_at']})")
                            verify_limit = 5000
                            tasks = database_manager.execute_query(
                                """
                                SELECT item_name, dest_path, status
                                FROM task_details
                                WHERE operation_id = %s
                                ORDER BY id DESC
                                LIMIT %s
                                """,
                                (int(op["id"]), verify_limit),
                                fetch="all",
                            ) or []
                            if len(tasks) >= verify_limit:
                                report.append(
                                    f"WARNING: Verification sampled latest {verify_limit:,} task rows for performance."
                                )
                            
                            db_ghosts = [] 
                            for task in tasks:
                                if task['status'] == 'Success' and task['dest_path']:
                                    if not Path(task['dest_path']).exists():
                                        db_ghosts.append(task['item_name'])
                            
                            if not db_ghosts:
                                report.append("<font color='#5FBF8F'>REPORT ACCURACY: PASS</font> (All DB 'Success' files exist)")
                            else:
                                report.append("<font color='#D9635F'>REPORT ACCURACY: FAIL</font>")
                                report.append(f"  - Ghost Files (In Report but NOT on Disk): {len(db_ghosts)}")
                        else:
                            report.append("No operations found in Database.")

            self.finished_signal.emit("<br>".join(report))

        except DatabaseUnavailableError:
            raise
        except Exception as e:
            self.finished_signal.emit(f"Analysis Error: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

class TesterPanel(QWidget):
    __test__ = False

    def __init__(self, user_manager=None, app_context=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.user_manager = user_manager or self.app_context.user_manager()
        self.generation_worker: Optional[QThread] = None
        self.structure_worker: Optional[QThread] = None
        self.workflow_worker: Optional[QThread] = None
        self.analysis_worker: Optional[QThread] = None
        self.stress_worker: Optional[QThread] = None
        self.reg_gen: Optional[QThread] = None
        self.reg_ingest: Optional[QThread] = None
        self.reg_valid: Optional[QThread] = None
        self.log_handler: Optional[QTextEditHandler] = None
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("TESTER PANEL"); header.setAlignment(Qt.AlignmentFlag.AlignCenter); header.setStyleSheet("font-size: 18px; font-weight: bold; color: #D9A441; background: transparent; border: none;")
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_generator_tab(), "Data Generator")
        self.tabs.addTab(self.create_workflow_tab(), "Workflow Sim")
        self.tabs.addTab(self.create_structure_tab(), "Structure (Chaos)")
        self.tabs.addTab(self.create_analyzer_tab(), "Analysis")
        self.tabs.addTab(self.create_diagnostics_tab(), "Diagnostics")
        self.tabs.addTab(self.create_system_tab(), "System")
        self.tabs.addTab(self.create_automation_tab(), "Automation")
        self.tabs.addTab(self.create_utils_tab(), "Utilities")



        
        layout.addWidget(self.tabs)
        
        # Log Area
        self.log_area = QTextEdit(); self.log_area.setReadOnly(True); self.log_area.setStyleSheet("background: #1D1D22; color: #5FBF8F; font-family: Consolas;")
        layout.addWidget(self.log_area)

    def closeEvent(self, event):
        """Cleanup running testing threads when panel closes."""
        worker_attrs = [
            "generation_worker",
            "structure_worker",
            "workflow_worker",
            "analysis_worker",
            "stress_worker",
            "reg_gen",
            "reg_ingest",
            "reg_valid",
        ]
        for attr_name in worker_attrs:
            self._cleanup_worker_attr(attr_name)

        if self.log_handler:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None

        super().closeEvent(event)

    def _cleanup_worker_attr(self, attr_name, timeout_ms=3000):
        """Stop a worker thread safely and clear its owning attribute."""
        worker = getattr(self, attr_name, None)
        if worker is None:
            return

        if hasattr(worker, "isRunning") and worker.isRunning():
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
            elif hasattr(worker, "requestInterruption"):
                worker.requestInterruption()
            worker.wait(timeout_ms)

        if hasattr(worker, "deleteLater"):
            worker.deleteLater()

        setattr(self, attr_name, None)

    def _release_finished_worker(self, attr_name, worker):
        """Clear a finished worker only if it is still the active instance."""
        if worker is not getattr(self, attr_name, None):
            return False
        setattr(self, attr_name, None)
        if hasattr(worker, "deleteLater"):
            worker.deleteLater()
        return True

    def create_generator_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        # Path
        path_box = QGroupBox("Target Location"); ph = QHBoxLayout(path_box)
        self.gen_path = QLineEdit(str(Path.home() / "Downloads" / "TesterData")); ph.addWidget(self.gen_path)
        btn_browse = QPushButton("Browse"); btn_browse.clicked.connect(lambda: self.gen_path.setText(QFileDialog.getExistingDirectory(self, "Select Target")))
        ph.addWidget(btn_browse); l.addWidget(path_box)
        
        # Config
        config_box = QGroupBox("Configuration"); cl = QFormLayout(config_box)
        
        self.spin_count = QSpinBox(); self.spin_count.setRange(1, 10000); self.spin_count.setValue(10); self.spin_count.setSingleStep(10)
        cl.addRow("File Count:", self.spin_count)
        
        self.combo_size = QComboBox(); self.combo_size.addItems(["Empty", "1KB", "1MB", "50MB", "Random"])
        cl.addRow("Size Strategy:", self.combo_size)
        
        # Types
        type_box = QGroupBox("File Types"); tl = QHBoxLayout(type_box)
        self.chk_jpg = QCheckBox(".jpg"); self.chk_jpg.setChecked(True); tl.addWidget(self.chk_jpg)
        self.chk_mov = QCheckBox(".mov"); tl.addWidget(self.chk_mov)
        self.chk_exr = QCheckBox(".exr"); tl.addWidget(self.chk_exr)
        self.chk_txt = QCheckBox(".txt"); tl.addWidget(self.chk_txt)
        l.addWidget(config_box); l.addWidget(type_box)
        
        # Action
        self.btn_gen = QPushButton("▶ GENERATE DUMMY FILES"); self.btn_gen.setStyleSheet("background: #3EA8BF; font-weight: bold; padding: 10px;")
        self.btn_gen.clicked.connect(self.start_generation)
        l.addWidget(self.btn_gen)
        
        self.prog_bar = QProgressBar(); self.prog_bar.setVisible(False); l.addWidget(self.prog_bar)
        l.addStretch()
        l.addStretch()
        return w

    def create_workflow_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        info = QLabel("<b>Workflow Simulator</b><br>Autogenerates 'For_move' structure and Excel Template.")
        info.setStyleSheet("color: #E8E6E1; font-size: 14px;")
        l.addWidget(info)
        
        # --- TEST DATASET CONFIG ---
        config_box = QGroupBox("Test Dataset Config"); cl = QFormLayout(config_box)
        
        self.wf_count = QSpinBox(); self.wf_count.setRange(1, 1000); self.wf_count.setValue(5)
        cl.addRow("Count per Shot:", self.wf_count)
        
        self.wf_size = QComboBox(); self.wf_size.addItems(["Empty", "1KB", "1MB", "Random"])
        self.wf_size.setCurrentText("1KB")
        cl.addRow("File Size:", self.wf_size)
        
        type_w = QWidget(); tl = QHBoxLayout(type_w); tl.setContentsMargins(0,0,0,0)
        self.wf_chk_exr = QCheckBox(".exr"); self.wf_chk_exr.setChecked(True); tl.addWidget(self.wf_chk_exr)
        self.wf_chk_mov = QCheckBox(".mov"); tl.addWidget(self.wf_chk_mov)
        self.wf_chk_jpg = QCheckBox(".jpg"); tl.addWidget(self.wf_chk_jpg)
        cl.addRow("Types:", type_w)
        
        l.addWidget(config_box)
        
        # --- COMPLEXITY CONFIG ---
        complex_box = QGroupBox("Complexity Generator"); cxl = QFormLayout(complex_box)
        
        self.wf_nesting = QSpinBox(); self.wf_nesting.setRange(0, 5); self.wf_nesting.setValue(0)
        self.wf_nesting.setToolTip("Adds random intermediate folders (Level_1/Level_2/...)")
        cxl.addRow("Nesting Depth:", self.wf_nesting)
        
        self.wf_multiscan = QSpinBox(); self.wf_multiscan.setRange(1, 5); self.wf_multiscan.setValue(1)
        self.wf_multiscan.setToolTip("Creates duplicate folders per shot (Shot_ScanA, Shot_ScanB...)")
        cxl.addRow("Multi-Scan Copies:", self.wf_multiscan)
        
        self.wf_seq = QCheckBox("Generate Image Sequences")
        self.wf_seq.setToolTip("Creates frames (1001-10XX) instead of random versions.")
        cxl.addRow("Sequences:", self.wf_seq)
        
        l.addWidget(complex_box)

        btn_sim = QPushButton("CREATE TEST ENVIRONMENT"); btn_sim.setStyleSheet("background: #5FBF8F; font-weight: bold; padding: 15px; font-size: 14px;")
        btn_sim.clicked.connect(self.start_workflow_sim)
        l.addWidget(btn_sim)
        
        l.addStretch()
        return w

    def create_analyzer_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        # Mode Selection
        mode_box = QGroupBox("Analysis Mode"); ml = QHBoxLayout(mode_box)
        self.rb_smart = QRadioButton("Smart Path Check (Recommended)"); ml.addWidget(self.rb_smart)
        self.rb_raw = QRadioButton("Raw Folder Compare"); ml.addWidget(self.rb_raw)
        self.rb_smart.setChecked(True)
        l.addWidget(mode_box)
        
        self.bg_mode = QButtonGroup(self); self.bg_mode.addButton(self.rb_smart); self.bg_mode.addButton(self.rb_raw)
        
        # New Ghost Mode
        self.rb_ghost = QRadioButton("Ghost Asset Hunter"); ml.addWidget(self.rb_ghost)
        self.bg_mode.addButton(self.rb_ghost)

        
        # Inputs (Stack)
        input_box = QGroupBox("Target Configuration"); il = QVBoxLayout(input_box)
        
        # Common Input
        il.addWidget(QLabel("<b>Target Scope / Destination:</b>"))
        self.an_dst = QLineEdit(); il.addWidget(self.an_dst)
        btn_dst = QPushButton("Select Folder"); btn_dst.clicked.connect(lambda: self.an_dst.setText(QFileDialog.getExistingDirectory(self)))
        il.addWidget(btn_dst)
        
        # Source Input (Comparison Mode Only)
        self.lbl_src = QLabel("<b>Reference Source (For Comparison):</b>")
        self.an_src = QLineEdit()
        self.btn_src = QPushButton("Select Source"); self.btn_src.clicked.connect(lambda: self.an_src.setText(QFileDialog.getExistingDirectory(self)))
        
        il.addWidget(self.lbl_src); il.addWidget(self.an_src); il.addWidget(self.btn_src)
        l.addWidget(input_box)
        
        # Logic to hide/show source based on mode
        self.bg_mode.buttonClicked.connect(self.update_analyzer_ui)
        self.update_analyzer_ui()
        
        btn_an = QPushButton("ANALYZE RESULTS"); btn_an.setStyleSheet("background: #3EA8BF; font-weight: bold; padding: 15px;")
        btn_an.clicked.connect(self.start_analysis)
        l.addWidget(btn_an)
        
        # DATABASE TOOLS
        db_box = QGroupBox("Database Health"); dbl = QHBoxLayout(db_box)
        btn_vac = QPushButton("Run VACUUM (Optimize DB)"); btn_vac.clicked.connect(self.run_db_vacuum)
        btn_chk = QPushButton("Integrity Check"); btn_chk.clicked.connect(self.run_db_integrity)
        dbl.addWidget(btn_vac); dbl.addWidget(btn_chk)
        l.addWidget(db_box)
        
        l.addStretch()
        return w

    def run_db_vacuum(self):
        try:
             with database_manager.get_connection() as conn:
                 conn.execute("VACUUM")
             QMessageBox.information(self, "Success", "Database Optimized (VACUUM Complete)")
             self.log("Database VACUUM executed successfully.")
        except DatabaseUnavailableError:
            raise
        except Exception as e:
             QMessageBox.critical(self, "Error", f"VACUUM Failed: {e}")

    def run_db_integrity(self):
        try:
             with database_manager.get_connection() as conn:
                 res = conn.execute("PRAGMA integrity_check").fetchone()
             
             status = res[0] if res else "Unknown"
             if status == "ok":
                 QMessageBox.information(self, "Healthy", "Database Integrity: OK")
                 self.log("DB Integrity Check: OK")
             else:
                 QMessageBox.warning(self, "Warning", f"Integrity Issues Found: {status}")
                 self.log(f"DB Integrity Issues: {status}")
        except DatabaseUnavailableError:
            raise
        except Exception as e:
             QMessageBox.critical(self, "Error", f"Check Failed: {e}")


    def update_analyzer_ui(self):
        is_raw = self.rb_raw.isChecked()
        self.lbl_src.setVisible(is_raw)
        self.an_src.setVisible(is_raw)
        self.btn_src.setVisible(is_raw)
        
        if is_raw:
            self.an_src.setPlaceholderText("Original Files Location...")
            self.an_dst.setPlaceholderText("Where files where moved to...")
        else:
            self.an_dst.setPlaceholderText("Folder to verify against Database (e.g. Z:/Show/Reel_01)...")

    def create_structure_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        form_box = QGroupBox("Chaos Configuration"); fl = QFormLayout(form_box)
        self.spin_nest = QSpinBox(); self.spin_nest.setRange(0, 100); self.spin_nest.setValue(10)
        fl.addRow("Nesting Level:", self.spin_nest)
        
        self.spin_folders = QSpinBox(); self.spin_folders.setRange(0, 1000); self.spin_folders.setValue(50)
        fl.addRow("Sibling Folders:", self.spin_folders)
        l.addWidget(form_box)
        
        btn_create = QPushButton("CREATE STRUCTURE"); btn_create.setStyleSheet("background: #D9A441; font-weight: bold; padding: 10px;")
        btn_create.clicked.connect(self.start_structure)
        l.addWidget(btn_create)
        l.addStretch()
        return w

    def create_utils_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        btn_wipe = QPushButton("WIPE TESTER FOLDER"); btn_wipe.setStyleSheet("background: #D9635F; padding: 10px;")
        btn_wipe.clicked.connect(self.wipe_folder)
        l.addWidget(btn_wipe)
        l.addStretch()
        return w

    def create_diagnostics_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        # 1. LIVE LOG VIEWER
        log_group = QGroupBox("Live Log Viewer (System)"); ll = QVBoxLayout(log_group)
        self.live_log_text = QTextEdit(); self.live_log_text.setReadOnly(True)
        self.live_log_text.setStyleSheet("background: #1D1D22; color: #B4B1AA; font-family: Consolas; font-size: 11px;")
        ll.addWidget(self.live_log_text)
        
        # Attach Handler
        self.log_handler = QTextEditHandler()
        self.log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.log_handler.log_signal.connect(self.live_log_text.append)
        logging.getLogger().addHandler(self.log_handler) # Attach to Root Logger
        
        l.addWidget(log_group, 2) # Stretch factor 2
        
        # 2. ACTIONS
        action_group = QGroupBox("Stress & Crash"); al = QHBoxLayout(action_group)
        
        btn_crash = QPushButton("Simulator Crash"); btn_crash.setStyleSheet("background: #D9635F")
        btn_crash.clicked.connect(self.simulate_crash)
        al.addWidget(btn_crash)
        
        btn_thumb = QPushButton("Thumbnail Stress"); btn_thumb.clicked.connect(self.run_thumb_stress)
        al.addWidget(btn_thumb)
        
        l.addWidget(action_group)
        
        return w

    def simulate_crash(self):
        # Feature #8: Crash Simulator
        if QMessageBox.warning(self, "Warning", "This will CRASH the application to test the Error Reporter.\nProceed?", QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            raise ValueError("Intentional Crash Triggered from Tester Panel.")

    def run_thumb_stress(self):
        # Feature #4: Thumbnail Stress
        self.log("Stress Test Started (Check CPU/RAM)...")
        self._cleanup_worker_attr("stress_worker")
        path = Path(self.gen_path.text()) / "Stress_Test"
        self.stress_worker = FileGeneratorWorker(path, 1000, "1KB", [".jpg"])
        self.stress_worker.progress_signal.connect(self._on_stress_progress)
        self.stress_worker.finished_signal.connect(self._on_stress_finished)
        self.stress_worker.start()

    def _on_stress_progress(self, value):
        if self.sender() is not self.stress_worker:
            return
        self.log(f"Stress Gen: {value}%")

    def _on_stress_finished(self):
        worker = self.sender()
        if not self._release_finished_worker("stress_worker", worker):
            return
        self.log("Stress Test Done. (Check Memory Profile)")

    def create_automation_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        info = QLabel("<b>Regression Suite (One-Click Verify)</b><br>Runs: Generate -> Ingest -> Verify.")
        info.setStyleSheet("color: #B4B1AA;")
        l.addWidget(info)
        
        btn_reg = QPushButton("▶ RUN FULL REGRESSION TEST"); btn_reg.setStyleSheet("background: #D9A441; font-weight: bold; padding: 20px; font-size: 16px;")
        btn_reg.clicked.connect(self.run_regression)
        l.addWidget(btn_reg)
        
        l.addStretch()
        return w

    def run_regression(self):
        target = self.gen_path.text()
        if not target:
            return
        
        self.log("STARTING REGRESSION SUITE...")
        
        self._cleanup_worker_attr("reg_gen")
        self._cleanup_worker_attr("reg_ingest")
        self._cleanup_worker_attr("reg_valid")

        # CHAIN EXECUTION
        # 1. GENERATE
        self.reg_gen = FileGeneratorWorker(target, 50, "1KB", [".jpg", ".txt"])
        self.reg_gen.finished_signal.connect(self._reg_step_2_ingest)
        self.reg_gen.start()
        
    def _reg_step_2_ingest(self):
        worker = self.sender()
        if not self._release_finished_worker("reg_gen", worker):
            return
        self.log("Step 1: Generation Complete. Starting Ingest...")
        target = self.gen_path.text()
        self._cleanup_worker_attr("reg_ingest")
        self.reg_ingest = IngestWorker(target)
        self.reg_ingest.finished_signal.connect(self._reg_step_3_verify)
        self.reg_ingest.start()
        
    def _reg_step_3_verify(self, success=True, message=""):
        worker = self.sender()
        if not self._release_finished_worker("reg_ingest", worker):
            return

        if not success:
            self.log(f"Step 2 failed: {message}")
            QMessageBox.warning(self, "Regression", f"Step 2 failed:\n{message}")
            return
        self.log("Step 2: Ingest Complete. Starting Verification...")
        target = self.gen_path.text()
        self._cleanup_worker_attr("reg_valid")
        # Smart Verify against DB
        self.reg_valid = ValidationWorker(target, target, verify_db=True, mode="smart")
        self.reg_valid.finished_signal.connect(self._reg_finish)
        self.reg_valid.start()
        
    def _reg_finish(self, report):
        worker = self.sender()
        if not self._release_finished_worker("reg_valid", worker):
            return
        self.log("Step 3: Verification Complete.")
        self.log(report)
        QMessageBox.information(self, "Regression", "Suite Completed! Check Logs.")


    def create_system_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        
        # 1. PERMISSION MATRIX
        perm_box = QGroupBox("Permission Matrix (Live View)"); pl = QVBoxLayout(perm_box)
        self.perm_table = QTableWidget()
        pl.addWidget(self.perm_table)
        l.addWidget(perm_box)
        
        # Load Permission Logic
        btn_refresh_perm = QPushButton("Refresh Permissions"); btn_refresh_perm.clicked.connect(self.load_permissions)
        pl.addWidget(btn_refresh_perm)
        self.load_permissions()
        
        # 2. CONFIG SANDBOX
        conf_box = QGroupBox("Config Sandbox (Temp Overrides)"); cl = QFormLayout(conf_box)
        self.conf_path = QLineEdit(str(GlobalConfig.server_root())); cl.addRow("Server Root:", self.conf_path)
        self.conf_cache = QLineEdit(str(GlobalConfig.local_cache_dir())); cl.addRow("Local Cache:", self.conf_cache)
        
        btn_apply_conf = QPushButton("Apply Changes (In-Memory Only)"); btn_apply_conf.clicked.connect(self.apply_config_sandbox)
        cl.addRow(btn_apply_conf)
        l.addWidget(conf_box)

        
        # 3. TIME TRAVEL
        time_box = QGroupBox("⏳ Time Travel Simulator"); tl = QHBoxLayout(time_box)
        tl.addWidget(QLabel("Modify Dates in Target:"))
        self.time_date = QDateEdit(); self.time_date.setCalendarPopup(True); self.time_date.setDate(QDate.currentDate().addDays(-365))
        tl.addWidget(self.time_date)
        
        btn_time = QPushButton("Warp Time"); btn_time.clicked.connect(self.run_time_travel)
        tl.addWidget(btn_time)
        l.addWidget(time_box)
        
        l.addStretch()
        return w

    @on_database_error
    def load_permissions(self):
        roles = self.user_manager.roles_config
        
        self.perm_table.setRowCount(len(roles))
        self.perm_table.setColumnCount(2)
        self.perm_table.setHorizontalHeaderLabels(["Role", "Allowed Tabs"])
        self.perm_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        
        for i, (role, perms) in enumerate(roles.items()):
            self.perm_table.setItem(i, 0, QTableWidgetItem(role))
            self.perm_table.setItem(i, 1, QTableWidgetItem(str(perms)))

    def apply_config_sandbox(self):
        # Unsafe but fun for testing
        # We need to access the singleton instance's data
        if GlobalConfig._instance is None: GlobalConfig.get("DUMMY_INIT") 
        
        GlobalConfig._instance.data["SERVER_ROOT"] = self.conf_path.text()
        
        self.log(f"Sandbox: Server Root set to {self.conf_path.text()}")
        QMessageBox.information(self, "Sandbox", "Configuration Updated in Memory!\n(Restart app to reset)")


    def run_time_travel(self):
        target = self.an_dst.text() # Use Analyzer Target
        if not target or not Path(target).exists():
            QMessageBox.warning(self, "Error", "Set a valid Target in 'Analysis' tab first!")
            return
            
        new_date = self.time_date.date()
        # Convert QDate to timestamp
        dt = QDateTime(new_date, QTime(12, 0, 0))
        ts = dt.toSecsSinceEpoch()
        
        count = 0
        for root, dirs, files in os.walk(target):
             for name in files:
                 try:
                     p = os.path.join(root, name)
                     os.utime(p, (ts, ts))
                     count += 1
                 except OSError as e:
                     logging.debug(f"Failed to set timestamp for {name}: {e}")
        
        self.log(f"Time Travel: Warped {count} files to {new_date.toString()}")
        QMessageBox.information(self, "Success", f"Warped {count} files to the past/future!")



    # --- ACTIONS ---
    def start_generation(self):
        path = self.gen_path.text()
        count = self.spin_count.value()
        size = self.combo_size.currentText()
        types = []
        if self.chk_jpg.isChecked(): types.append(".jpg")
        if self.chk_mov.isChecked(): types.append(".mov")
        if self.chk_exr.isChecked(): types.append(".exr")
        if self.chk_txt.isChecked(): types.append(".txt")
        
        if not types: types = [".dat"]
        
        self.btn_gen.setEnabled(False)
        self.prog_bar.setVisible(True); self.prog_bar.setValue(0)
        self.log(f"Starting generation of {count} files in {path}...")
        self._cleanup_worker_attr("generation_worker")

        self.generation_worker = FileGeneratorWorker(path, count, size, types)
        self.generation_worker.progress_signal.connect(self._on_generation_progress)
        self.generation_worker.log_signal.connect(self.log)
        self.generation_worker.finished_signal.connect(self.on_gen_finished)
        self.generation_worker.start()

    def _on_generation_progress(self, value):
        if self.sender() is not self.generation_worker:
            return
        self.prog_bar.setValue(value)

    def on_gen_finished(self):
        worker = self.sender()
        if not self._release_finished_worker("generation_worker", worker):
            return

        self.btn_gen.setEnabled(True)
        self.prog_bar.setVisible(False)
        self.log("Generation Complete.")
        QMessageBox.information(self, "Success", "Dummy files created.")

    def start_structure(self):
        path = self.gen_path.text()
        self.log(f"Creating structure in {path}...")
        self._cleanup_worker_attr("structure_worker")

        self.structure_worker = StructureWorker(path, self.spin_nest.value(), self.spin_folders.value())
        self.structure_worker.finished_signal.connect(self._on_structure_finished)
        self.structure_worker.start()

    def _on_structure_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("structure_worker", worker):
            return
        self.log(msg)
        QMessageBox.information(self, "Result", msg)

    def wipe_folder(self):
        path = Path(self.gen_path.text())
        if not path.exists(): return
        
        ret = QMessageBox.warning(self, "Confirm Wipe", f"Are you sure you want to DELETE:\n{path}\n\nThis cannot be undone.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(path)
                self.log(f"Wiped: {path}")
                QMessageBox.information(self, "Wiped", "Folder deleted.")
            except Exception as e:
                self.log(f"Error wiping: {e}")

    def start_workflow_sim(self):
        path = self.gen_path.text()
        
        # Get Config from local workflow tab controls
        count = self.wf_count.value()
        size = self.wf_size.currentText()
        types = []
        if self.wf_chk_exr.isChecked(): types.append(".exr")
        if self.wf_chk_mov.isChecked(): types.append(".mov")
        if self.wf_chk_jpg.isChecked(): types.append(".jpg")
        if not types: types = [".dat"]
        
        nesting = self.wf_nesting.value()
        scans = self.wf_multiscan.value()
        seq = self.wf_seq.isChecked()

        self.log(f"Initializing Smart Simulation: {count} x {size} | Nesting:{nesting} | Scans:{scans} | Seq:{seq}...")
        self._cleanup_worker_attr("workflow_worker")

        # Pass config to worker
        self.workflow_worker = WorkflowWorker(path, count, size, types, nesting, scans, seq)
        self.workflow_worker.finished_signal.connect(self._on_workflow_finished)
        self.workflow_worker.start()

    def _on_workflow_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("workflow_worker", worker):
            return
        self.log(msg)
        QMessageBox.information(self, "Simulation", msg)

    def start_analysis(self):
        dst = self.an_dst.text()
        src = self.an_src.text()
        
        if self.rb_smart.isChecked(): mode = "smart"
        elif self.rb_ghost.isChecked(): mode = "ghost"
        else: mode = "raw"

        
        if not dst: 
             QMessageBox.warning(self, "Missing Path", "Please select a Target/Destination folder.")
             return
             
        if mode == "raw" and not src:
             QMessageBox.warning(self, "Missing Path", "Compare Mode requires a Source folder.")
             return

        self.log(f"Starting Analysis (Mode: {mode})...")
        self._cleanup_worker_attr("analysis_worker")

        self.analysis_worker = ValidationWorker(src, dst, verify_db=True, mode=mode)
        self.analysis_worker.finished_signal.connect(self._on_analysis_finished)
        self.analysis_worker.start()

    def _on_analysis_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("analysis_worker", worker):
            return
        self.log(msg)
        QMessageBox.information(self, "Analysis Result", "Analysis Complete. Check Log.")

    def log(self, msg):
        self.log_area.append(msg)
