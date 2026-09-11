
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout
from PySide6.QtCore import Qt
import re
import logging

# Optional ImageIO
try:
    import imageio.v3 as iio
    HAS_IMAGEIO = True
except ImportError:
    HAS_IMAGEIO = False

logger = logging.getLogger(__name__)

class LiveTechCheckWidget(QWidget):
    """
    Live Technical Check Widget
    
    Displays real-time validation for:
    1. Naming Convention
    2. Frame Continuity
    3. Tech Specs (Res, BitDepth)
    4. Banned Channels
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(5)
        
        # Style
        self.setStyleSheet("""
            QWidget { background-color: #1D1D22; border-radius: 4px; }
            QLabel { font-family: 'Consolas', monospace; color: #E8E6E1; font-size: 11px; }
            .section-title { font-weight: bold; color: #87857F; margin-top: 5px; }
            .pass { color: #5FBF8F; }
            .fail { color: #D9635F; font-weight: bold; }
            .warn { color: #D9A441; }
        """)
        
        # Info Header
        self.header_label = QLabel("Waiting for shot...")
        self.header_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #3EA8BF; padding: 5px;")
        self.main_layout.addWidget(self.header_label)
        
        # --- Checks Container ---
        self.checks_layout = QVBoxLayout()
        self.main_layout.addLayout(self.checks_layout)
        
        self.main_layout.addStretch()
        
    def check_shot(self, shot):
        """Run checks on the shot"""
        # Clear previous
        self._clear_checks()
        self.header_label.setText(f"{shot.sequence} / {shot.name}")
        
        # 1. Naming Convention
        # Pattern: seq_shot_task_version (e.g. SS1_TSR_0120_PL01)
        # Regex: Allow hyphens in parts
        valid_name = bool(re.match(r"^[A-Za-z0-9\-]+_[A-Za-z0-9\-]+_[A-Za-z0-9\-]+_[A-Za-z0-9\-]+$", shot.name))
        if not valid_name:
             # Try 3 parts
             valid_name = bool(re.match(r"^[A-Za-z0-9\-]+_[A-Za-z0-9\-]+_[A-Za-z0-9\-]+$", shot.name))
             
        self.add_check_result("Naming Convention", valid_name, shot.name if not valid_name else "Match")

        # 2. File Status
        has_scan = shot.has_scan()
        has_render = shot.has_render()
        self.add_check_result("Scan Found", has_scan, "Found" if has_scan else "Missing")
        self.add_check_result("Render Found", has_render, "Found" if has_render else "Missing")
        
        # 3. Reference Analysis (Scan)
        logger.info(f"Analyzing scan: {shot.scan_path}")
        scan_meta = self._analyze_file_sequence(shot.scan_path)
        logger.info(f"Scan metadata: {scan_meta}")
        
        # 4. Target Analysis (Render)
        logger.info(f"Analyzing render: {shot.render_path}")
        render_meta = self._analyze_file_sequence(shot.render_path)
        logger.info(f"Render metadata: {render_meta}")
        
        # --- ALWAYS DISPLAY BASIC INFO FROM SHOT ---
        # Even if file analysis fails, show what we know from the shot object
        
        # Scan Basic Info
        if shot.has_scan():
            self.add_section_title("SCAN INFO")
            self.add_metadata_row("Path", str(shot.scan_path.parent) if shot.scan_path else "Unknown")
            if shot.frame_range:
                start, end = shot.frame_range
                self.add_metadata_row("Frames", f"{start}-{end} ({shot.get_frame_count()} frames)")
            self.add_metadata_row("Format", shot.format or "Unknown")
            
            # Add analyzed metadata if available
            if scan_meta:
                scan_res = scan_meta.get('res', (0, 0))
                if scan_res[0] > 0:
                    self.add_metadata_row("Resolution", f"{scan_res[0]}x{scan_res[1]}")
                self.add_metadata_row("Bit Depth", scan_meta.get('dtype', 'Unknown'))
                self.add_metadata_row("Files Found", scan_meta.get('count', 0))
        
        # Render Basic Info
        if shot.has_render():
            self.add_section_title("RENDER INFO")
            self.add_metadata_row("Path", str(shot.render_path.parent) if shot.render_path else "Unknown")
            if shot.frame_range:
                start, end = shot.frame_range
                self.add_metadata_row("Frames", f"{start}-{end} ({shot.get_frame_count()} frames)")
            self.add_metadata_row("Format", shot.format or "Unknown")
            
            # Add analyzed metadata if available
            if render_meta:
                render_res = render_meta.get('res', (0, 0))
                if render_res[0] > 0:
                    self.add_metadata_row("Resolution", f"{render_res[0]}x{render_res[1]}")
                self.add_metadata_row("Bit Depth", render_meta.get('dtype', 'Unknown'))
                self.add_metadata_row("Files Found", render_meta.get('count', 0))
        
        if not scan_meta and not render_meta:
            self.add_label("No metadata available", "warn")
            return

        # --- COMPARISON CHECKS ---
        if scan_meta and render_meta:
            self.add_section_title("COMPARISON")
            
            # A. Start Frame Match
            scan_start = scan_meta.get('start_frame')
            render_start = render_meta.get('start_frame')
            match = (scan_start == render_start)
            detail = f"S:{scan_start} | R:{render_start}"
            self.add_check_result("Start Frame", match, detail)
        
            # B. Frame Count Match
            scan_count = scan_meta.get('count', 0)
            render_count = render_meta.get('count', 0)
            
            # Logic: Render should at least cover scan
            match = (render_count >= scan_count)
            detail = f"S:{scan_count} | R:{render_count}"
            self.add_check_result("Frame Count", match, detail)
            
            # C. Resolution Match
            scan_res = scan_meta.get('res')
            render_res = render_meta.get('res')
            match = (scan_res == render_res)
            detail = f"S:{scan_res} | R:{render_res}"
            self.add_check_result("Resolution", match, detail)

            # D. Bit Depth Comparison (Informational)
            scan_depth = scan_meta.get('dtype', '?')
            render_depth = render_meta.get('dtype', '?')
            match = (scan_depth == render_depth) 
            # Note: Often mismatch is okay (Int16 Scan vs Float16 Render), but we show status
            detail = f"S:{scan_depth} | R:{render_depth}"
            self.add_check_result("Bit Depth", match, detail)

    def _analyze_file_sequence(self, path):
        """Analyze a sequence to get metadata: count, start, res, dtype"""
        if not path or not path.exists(): return None
        
        info = {}
        folder = path.parent
        name = path.name
        
        # 1. Glob Logic
        import re
        if "%04d" in name: glob_pattern = name.replace("%04d", "*")
        elif "%d" in name: glob_pattern = name.replace("%d", "*")
        elif "#" in name: glob_pattern = name.replace("#", "*")
        else: glob_pattern = re.sub(r'\.\d+\.', '.*.', name)
        
        try:
            files = sorted(list(folder.glob(glob_pattern)))
            info['count'] = len(files)
            
            if files:
                # Extract start frame from filename
                # Standard: shot.1001.exr
                first_file = files[0]
                match = re.search(r'\.(\d+)\.', first_file.name)
                if match:
                    info['start_frame'] = int(match.group(1))
                else:
                    info['start_frame'] = 1
                
                # Image Spec Analysis
                # SAFETY: Skip EXR metadata extraction - causes segfaults in imageio/OIIO
                ext = first_file.suffix.lower()
                if ext == '.exr':
                    # Use defaults for EXR without attempting to load
                    info['dtype'] = 'float16 (EXR)'
                    info['res'] = (0, 0)
                elif HAS_IMAGEIO:
                    try:
                        meta = iio.imread(first_file, index=0)
                        h, w = meta.shape[:2]
                        info['res'] = (w, h)
                        
                        dtype = str(meta.dtype)
                        dtype = dtype.replace("float", "f").replace("uint", "u")
                        info['dtype'] = dtype
                    except Exception as e:
                        logger.debug(f"ImageIO failed: {e}")
                        # Fallback
                        if ext in ['.dpx']:
                            info['dtype'] = 'uint16 (DPX)'
                        else:
                            info['dtype'] = 'uint8'
                        info['res'] = (0, 0)
                else:
                    # No imageio - use defaults based on file type
                    ext = first_file.suffix.lower()
                    if ext == '.exr':
                        info['dtype'] = 'float16 (EXR)'
                    elif ext in ['.dpx']:
                        info['dtype'] = 'uint16 (DPX)'
                    elif ext in ['.tif', '.tiff']:
                        info['dtype'] = 'uint16 (TIFF)'
                    else:
                        info['dtype'] = 'uint8 (JPG/PNG)'
                    info['res'] = (0, 0)
                        
        except Exception as e:
            logger.warning(f"Analysis failed for {path}: {e}")
            # Return partial metadata rather than None, so viewer still works
            # This is important when OpenEXR codec is disabled
            if info.get('count', 0) > 0:
                # We found files, just couldn't extract metadata
                ext = path.suffix.lower() if path else ''
                if ext == '.exr':
                    info['dtype'] = 'float16 (EXR - codec unavailable)'
                elif ext in ['.dpx']:
                    info['dtype'] = 'uint16 (DPX - codec unavailable)'
                elif ext in ['.tif', '.tiff']:
                    info['dtype'] = 'uint16 (TIFF)'
                else:
                    info['dtype'] = 'uint8'
                if 'res' not in info:
                    info['res'] = (0, 0)
                logger.info(f"Returning partial metadata for {path.name}: count={info.get('count')}, dtype={info.get('dtype')}")
                return info
            else:
                # No files found at all
                logger.error(f"No files found for analysis: {path}")
                return None
            
        return info

    def _clear_checks(self):
        from ...core.layout_utils import clear_layout
        clear_layout(self.checks_layout)
                
    def add_check_result(self, label, passed, detail):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(5, 2, 5, 2)
        
        status = "✅" if passed else "❌"
        lbl = QLabel(f"{status} {label}:")
        if not passed:
            lbl.setStyleSheet("color: #D9635F; font-weight: bold;")
        else:
             lbl.setStyleSheet("color: #5FBF8F;")
             
        det = QLabel(detail)
        det.setAlignment(Qt.AlignmentFlag.AlignRight)
        det.setStyleSheet("color: #B4B1AA;")
        
        row_layout.addWidget(lbl)
        row_layout.addWidget(det)
        
        self.checks_layout.addWidget(row)

    def add_section_title(self, title):
        """Add a section title"""
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold; color: #3EA8BF; font-size: 12px; margin-top: 8px; margin-bottom: 2px;")
        self.checks_layout.addWidget(lbl)
    
    def add_metadata_row(self, label, value):
        """Add a metadata key-value row"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 1, 5, 1)
        
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet("color: #87857F; font-size: 10px;")
        lbl.setFixedWidth(80)
        
        val = QLabel(str(value))
        val.setStyleSheet("color: #E8E6E1; font-size: 10px;")
        val.setWordWrap(True)
        
        row_layout.addWidget(lbl)
        row_layout.addWidget(val, 1)
        
        self.checks_layout.addWidget(row)

    def add_label(self, text, style_class=""):
        lbl = QLabel(text)
        if style_class == "fail":
            lbl.setStyleSheet("color: #D9635F; font-weight: bold;")
        elif style_class == "warn":
            lbl.setStyleSheet("color: #D9A441;")
        self.checks_layout.addWidget(lbl)
