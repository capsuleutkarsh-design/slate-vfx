import logging
import time
import psutil
import re
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from slate.utils.security import SecurityValidator
from slate.core.infra.database_manager import database_manager
from slate.core.infra.file_operations import SafeFileOperations
from slate.core.services.path_template_manager import get_path_manager

# --- JUNK FILE FILTER LIST ---
# Matched against the lowercased file name, exactly.
IGNORED_FILES = {
    '.ds_store', 'thumbs.db', 'desktop.ini',
    '$recycle.bin', 'system volume information'
}

# Matched against the lowercased file suffix. Temp/backup/swap files never
# belong in a project structure.
IGNORED_SUFFIXES = {'.tmp', '.bak', '.swp', '.crdownload', '.partial'}

# Import SequenceDetector for robust frame handling
from slate.utils.sequence_utils import SequenceDetector

def is_junk_file(path: Path) -> bool:
    """Files that must never be treated as delivered media."""
    name = path.name.lower()
    if name in IGNORED_FILES:
        return True
    if name.startswith("._"):
        return True
    if name.startswith("~$"):
        return True
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    return False


def has_media_files(folder: Path) -> bool:
    """True when the folder has at least one non-junk file directly inside it."""
    try:
        return any(item.is_file() and not is_junk_file(item)
                   for item in folder.iterdir())
    except (PermissionError, OSError):
        return False


def walk_and_collect_shots(folder: Path, source_root: Path,
                           max_depth: int = 6, current_depth: int = 0):
    """Recursively find folders that look like shots (they hold media files)."""
    if current_depth > max_depth:
        return []

    results = []
    is_shot = False

    try:
        for item in folder.iterdir():
            if item.is_dir():
                results.extend(walk_and_collect_shots(
                    item, source_root, max_depth, current_depth + 1))
            elif item.is_file() and not is_junk_file(item):
                is_shot = True

        # A folder holding media is a shot. The source root is excluded here:
        # it is normally a container of reels, and is handled separately.
        if is_shot and folder != source_root:
            results.append(folder)
    except PermissionError:
        pass

    return results


def fallback_collect_shots_from_tree(source_root: Path):
    """
    Folder-only detection, for when no media-based shots were found.

    1) source/reel/shot layout -> every shot dir under a reel dir
    2) source/shot layout      -> the first-level dirs
    """
    try:
        level1 = [d for d in source_root.iterdir() if d.is_dir()]
    except (PermissionError, OSError):
        return []

    if not level1:
        return []

    shot_dirs = []
    for reel_dir in level1:
        try:
            children = [d for d in reel_dir.iterdir() if d.is_dir()]
        except (PermissionError, OSError):
            continue
        if children:
            shot_dirs.extend(children)

    if shot_dirs:
        return shot_dirs

    return level1


def collect_shot_folders(source_path):
    """
    The folders an ingest of this source would treat as shots.

    The same walk and the same fallbacks the run itself uses, so anything shown
    to a person beforehand matches what will actually move.
    """
    source_path = Path(source_path)
    shots = walk_and_collect_shots(source_path, source_path)

    if not shots:
        fallback = fallback_collect_shots_from_tree(source_path)
        if fallback:
            shots = fallback
        elif has_media_files(source_path):
            shots = [source_path]
        else:
            return []

    # Loose media sitting directly at the source root is its own shot.
    if source_path not in shots and has_media_files(source_path):
        shots.append(source_path)

    return shots


def group_shots_by_reel(shots, source_path, target_reel_name: str = ""):
    """Which reel each shot folder belongs to."""
    source_path = Path(source_path)
    grouped = {}
    for shot_path in shots:
        if target_reel_name:
            reel = target_reel_name
        elif shot_path == source_path or shot_path.parent == source_path:
            reel = "Reel_Incoming"
        else:
            reel = shot_path.parent.name
        grouped.setdefault(reel, []).append(shot_path)
    return grouped


def _frame_summary(frames, limit: int = 8) -> str:
    """Render missing frames as ranges: [1043, 1044, 1045, 1060] -> '1043-1045, 1060'."""
    if not frames:
        return ""

    ordered = sorted(set(frames))
    spans = []
    span_start = previous = ordered[0]
    for frame in ordered[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        spans.append((span_start, previous))
        span_start = previous = frame
    spans.append((span_start, previous))

    rendered = [str(a) if a == b else f"{a}-{b}" for a, b in spans]
    if len(rendered) > limit:
        return ", ".join(rendered[:limit]) + f" (+{len(rendered) - limit} more)"
    return ", ".join(rendered)


class FolderCreationWorker(QThread):
    
    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, int, int, int, int, str)
    error_signal = Signal(str)
    
    def __init__(self, target_dir=None, excel_df=None, source_scan_path=None, project_name="", template_data=(), mode="full", template_type="", target_reel_name="", overwrite=False, dry_run=False, format_mapping=None, fast_mode=False, scan_version_folders=None, stitch_mapping=None, parent=None, root_dir=None, **kwargs):
        super().__init__(parent)
        self.target_dir = Path(target_dir or root_dir or ".")
        self.excel_df = excel_df
        self.source_scan_path = Path(source_scan_path) if source_scan_path else None
        self.project_name = project_name
        self.template_data = template_data
        self.mode = mode
        self.template_type = template_type
        self.target_reel_name = target_reel_name
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.format_mapping = format_mapping or {}
        self.fast_mode = fast_mode
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.pause_condition = QWaitCondition()
        
        self.folders_created = 0
        self.reels_count = 0
        self.shots_count = 0
        self.files_moved = 0
        self.files_skipped = 0
        self.errors = 0
        self.security_validator = SecurityValidator()

        # Global progress tracking
        self._total_files = 0
        self._processed_files = 0

        # Reel/shot pairs discovered during the ingest, so the dashboard can be
        # populated from the delivery instead of by hand.
        self.ingested_shots = []
        # Scan version allocated per destination shot during this run.
        self._scan_versions = {}
        # Folders created inside each scan version for work derived from that
        # delivery - the degrained plate belongs to the scan it came from.
        self.scan_version_folders = list(scan_version_folders or ["Denoise"])
        # {source folder name: merged shot name} for confirmed stitches.
        self.stitch_mapping = dict(stitch_mapping or {})
        # Scan version shared by the parts of one stitch, per destination shot.
        self._stitch_versions = {}
        # Destination shots already created this run, so the parts of a stitch
        # are counted and registered once.
        self._seen_dest_shots = set()
        # File names already placed in a shared stitch scan version, so a
        # second part carrying the same names is not silently skipped.
        self._stitch_placed_names = {}

        # Everything the delivery report needs: what arrived, what was wrong
        # with it, and what could not be brought in.
        self.sequences_found = []
        self.incomplete_sequences = []
        self.skipped_files = []
        self.failed_files = []

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

    def stop(self):
        self.mutex.lock()
        self.is_running = False
        self.is_paused = False
        self.pause_condition.wakeAll()
        self.mutex.unlock()
        self.log_signal.emit("[STOP] Process Stopped.")

    def check_pause(self):
        self.mutex.lock()
        if self.is_paused:
            self.pause_condition.wait(self.mutex)
        self.mutex.unlock()

    def _mkdir(self, path: Path):
        """Create directory (unless dry-run) and count it for stats."""
        if self.dry_run:
            self.folders_created += 1
            return

        success, message = SafeFileOperations.safe_create_directory(path)
        if success and "already exists" not in str(message).lower():
            self.folders_created += 1

    def _emit_progress(self, message=""):
        """Report progress across the whole ingest, not per shot."""
        if self._total_files > 0:
            pct = min(int((self._processed_files / self._total_files) * 100), 99)
        else:
            pct = 0
        self.progress_signal.emit(
            pct, message or f"Processing {self._processed_files}/{self._total_files}"
        )

    def _count_source_files(self) -> int:
        """Count non-junk files in the source tree so progress means something."""
        if not self.source_scan_path:
            return 0
        try:
            return sum(1 for f in self.source_scan_path.rglob('*')
                       if f.is_file() and not self._is_junk_file(f))
        except (PermissionError, OSError) as exc:
            logging.warning(f"Could not pre-count source files: {exc}")
            return 0

    def _has_media_files(self, folder: Path) -> bool:
        """True when folder has at least one non-junk file directly inside it."""
        return has_media_files(folder)

    def run(self):
        start_time = time.time()
        
        try:
            pid = database_manager.record_project(self.project_name, self.template_type, str(self.target_dir))
            op_type = "Auto-Scan & Build" if self.source_scan_path else "Folder Creation"
            self.op_id = database_manager.start_operation(pid, op_type)
        except Exception:
            self.op_id = 0

        try:
            self.log_signal.emit("[START] Starting Process...")
            
            if self.source_scan_path and not self.dry_run:
                total_size = self._calculate_directory_size(self.source_scan_path)
                if not self._check_disk_space(total_size, self.target_dir):
                    self.finished_signal.emit(False, 0, 0, 0, 0, "Insufficient Disk Space")
                    return

            if not self.template_data or len(self.template_data) < 4:
                self.log_signal.emit("[WARN] Template data incomplete - using defaults")
                base_folders = ["01_Scan", "05_Reels"]
                prod_subs = []
                outsource_subs = []
                shot_subs = ["01_Scan", "07_Comp", "08_Output"]
            else:
                base_folders, prod_subs, outsource_subs, shot_subs = self.template_data
            project_path = self.target_dir / self.project_name

            if not isinstance(shot_subs, list) or not shot_subs:
                self.log_signal.emit("[WARN] shot_folders empty/invalid - using defaults")
                shot_subs = ["01_Scan", "07_Comp", "08_Output"]
            
            # Fix: Compute reels_path unconditionally so it's available for both branches
            mgr = get_path_manager()
            reels_path = Path(mgr.format_path('reels_root', project=self.project_name, root=str(project_path.parent)))

            self._mkdir(project_path)
            for f in base_folders:
                self._mkdir(project_path / f)
            if prod_subs:
                p = project_path / "04_Production"
                self._mkdir(p)
                for s in prod_subs:
                    self._mkdir(p / s)
            if outsource_subs:
                p = project_path / "04_Production"
                self._mkdir(p)
                for s in outsource_subs:
                    self._mkdir(p / s)
            # Create 05_Reels
            self._mkdir(reels_path)

            if self.excel_df is not None:
                self._process_excel(reels_path, shot_subs)
            elif self.source_scan_path:
                self._total_files = self._count_source_files()
                self.log_signal.emit(f"[INFO] {self._total_files} file(s) to process")
                self._emit_progress("Scanning source...")
                self._process_scan_improved(reels_path, shot_subs, self.op_id)

            dur = time.time() - start_time
            self._record_operation_result(dur, True)
            
            summary = [f"Moved {self.files_moved} files"]
            if self.files_skipped > 0: summary.append(f"Skipped {self.files_skipped}")
            if self.errors > 0: summary.append(f"Errors: {self.errors}")
            
            self.finished_signal.emit(True, 1, self.reels_count, self.shots_count, self.folders_created, " | ".join(summary))

        except Exception as e:
            logging.exception(f"Worker Error: {e}", exc_info=True)
            self._record_operation_result(time.time() - start_time, False)
            self.finished_signal.emit(False, 0, 0, 0, 0, str(e))

    def _record_operation_result(self, duration, success):
        """Best-effort bookkeeping - a database outage must not fail the ingest."""
        try:
            database_manager.update_operation(
                self.op_id, duration, self.files_moved,
                self.errors, success
            )
        except Exception as exc:
            logging.warning(f"Could not record operation result: {exc}")

    def _record_task_detail(self, *args, **kwargs):
        """Best-effort per-file bookkeeping."""
        try:
            database_manager.record_task_detail(*args, **kwargs)
        except Exception as exc:
            logging.debug(f"Could not record task detail: {exc}")

    def _process_excel(self, root, subs):
        self.log_signal.emit("[DATA] Processing Excel...")
        cols = [c.lower() for c in self.excel_df.columns]
        reel_col_idx = next((i for i, c in enumerate(cols) if 'reel' in c), None)
        shot_col_idx = next((i for i, c in enumerate(cols) if 'shot' in c), None)
        
        if reel_col_idx is None or shot_col_idx is None:
            raise ValueError("Excel must include both Reel and Shot columns")

        reel_col = self.excel_df.columns[reel_col_idx]
        shot_col = self.excel_df.columns[shot_col_idx]

        for reel in self.excel_df[reel_col].dropna().unique():
            self.check_pause()
            if not self.is_running: break
            
            reel_path = root / str(reel).strip()
            self._mkdir(reel_path)
            self.reels_count += 1
            
            for shot in self.excel_df[self.excel_df[reel_col] == reel][shot_col].dropna():
                shot_path = reel_path / str(shot).strip()
                self._mkdir(shot_path)
                self.shots_count += 1
                self._create_subs(shot_path, subs)

    def _create_subs(self, shot_path: Path, subs):
        """Create shot subfolders."""
        for s in subs:
            self._mkdir(shot_path / s)

    def _process_scan(self, root, subs, oid=None):
        """Process scan for backward compatibility with older tests and interfaces."""
        return self._process_scan_improved(root, subs, oid if oid is not None else getattr(self, 'op_id', 0))

    def _process_scan_improved(self, root, subs, oid):
        """Improved scan logic with recursion, sequences, and collision handling."""
        self.log_signal.emit(f"[SCAN] Analyzing source: {self.source_scan_path}")
        
        # 1. Collect all potential shots recursively
        detected_shots = self._walk_and_collect_shots(self.source_scan_path)
        self.log_signal.emit(f"[SCAN] Media-based detection found {len(detected_shots)} shot candidate(s)")

        # Fallback: if media-based detection finds nothing, infer from folder layout.
        # This helps when source tree has reel/shot folders that are empty (or files are deeper than scan heuristics).
        if not detected_shots:
            fallback_shots = self._fallback_collect_shots_from_tree(self.source_scan_path)
            if fallback_shots:
                self.log_signal.emit(f"[SCAN] Fallback folder-based detection found {len(fallback_shots)} shot candidate(s)")
                detected_shots = fallback_shots
            elif self._has_media_files(self.source_scan_path):
                self.log_signal.emit("[SCAN] Root-level media detected; using source folder as shot candidate")
                detected_shots = [self.source_scan_path]
            else:
                self.log_signal.emit("[WARN] No shot candidates found in source tree.")
                return

        # Loose media directly at the source root is its own shot candidate.
        # _walk_and_collect_shots deliberately skips the root (it is normally a
        # container of reels), which used to leave these files behind on the
        # client drive while the run still reported success.
        if (self.source_scan_path not in detected_shots
                and self._has_media_files(self.source_scan_path)):
            self.log_signal.emit("[SCAN] Root-level loose media detected; ingesting as its own shot")
            detected_shots.append(self.source_scan_path)

        # 2. Group by Reel. The same rule the pre-ingest survey uses, so the
        # stitch groups a coordinator confirmed line up with the real run.
        shots_by_reel = group_shots_by_reel(
            detected_shots, self.source_scan_path, self.target_reel_name)

        # 3. Process
        for reel_name, shots in shots_by_reel.items():
            dest_reel = root / reel_name
            self._mkdir(dest_reel)
            self.reels_count += 1
            
            for src_shot in shots:
                self.check_pause()
                if not self.is_running: break
                
                # Normalize shot name (merge multi-scans if needed)
                shot_name = src_shot.name
                normalized_name = re.sub(r'[_\-](?:scan[_]?[a-z0-9]*|rescan)$', '', shot_name, flags=re.IGNORECASE)
                normalized_name = re.sub(r'[_\-]v\d+$', '', normalized_name, flags=re.IGNORECASE)

                # A confirmed stitch: every part goes into the one shot.
                # Keyed by reel first, because SH010_A in one reel being a
                # stitch says nothing about SH010_A in another.
                stitched = (self.stitch_mapping.get((reel_name, shot_name))
                            or self.stitch_mapping.get(shot_name))
                if stitched:
                    normalized_name = stitched
                
                version_suffix = ""
                if normalized_name != shot_name and shot_name.lower().startswith(normalized_name.lower()):
                    version_suffix = shot_name[len(normalized_name):].lstrip('_-')
                
                dest_shot = dest_reel / normalized_name

                # Create Shot Structure
                self._mkdir(dest_shot)

                # The later parts of a stitch are not new shots. Counting them
                # again would report three shots for a three-part stitch and
                # register the same shot on the dashboard three times.
                #
                # Only stitches collapse. Two deliveries of the same shot in one
                # run (SH010_ScanA, SH010_ScanB) are two deliveries: each needs
                # its own entry so its own scan version is reported.
                first_time = (not stitched
                              or str(dest_shot) not in self._seen_dest_shots)
                self._seen_dest_shots.add(str(dest_shot))
                if first_time:
                    self.shots_count += 1
                    self.ingested_shots.append({
                        "reel": reel_name,
                        "shot": normalized_name,
                        "path": str(dest_shot),
                    })
                
                # Create Subfolders
                scan_root = "01_Scan"
                for s in subs:
                    self._mkdir(dest_shot / s)
                    if "scan" in s.lower(): scan_root = s.split('/')[0]

                # Every delivery of a shot gets its own scan version folder,
                # so a re-graded plate never collides with the one already in
                # the project.
                # Parts of one stitch are a single delivery, so they share a
                # scan version and sit side by side - exactly what a stitch
                # delivered as one folder already produces.
                if stitched:
                    scan_version = self._stitch_scan_version(dest_shot, scan_root)
                else:
                    scan_version = self._next_scan_version(dest_shot, scan_root)
                for derived in self.scan_version_folders:
                    self._mkdir(dest_shot / scan_root / scan_version / derived)
                if first_time:
                    self.ingested_shots[-1]["scan_version"] = scan_version
                    self.ingested_shots[-1]["source_folder"] = shot_name
                if version_suffix:
                    self.log_signal.emit(
                        f"[SCAN] {shot_name} -> {normalized_name} {scan_version}"
                    )

                # Parts of a stitch share one scan version, so two parts
                # using the same file names would collide and the second would
                # be skipped - losing half the shot. When that happens, keep
                # the parts apart inside the version instead.
                scan_target = scan_version
                if stitched:
                    part = version_suffix or shot_name
                    if self._stitch_part_collides(src_shot, dest_shot,
                                                  scan_version):
                        scan_target = f"{scan_version}/{part}"
                        self.log_signal.emit(
                            f"[STITCH] {shot_name} shares file names with an "
                            f"earlier part; keeping it in {scan_version}/{part}"
                        )

                # Process Files in Shot
                self._process_files_in_shot(src_shot, dest_shot, subs, scan_root,
                                            oid, scan_target)

        # The plate's real frame range, now that every sequence has been seen.
        self._attach_frame_ranges()

    def _attach_frame_ranges(self):
        """
        Record each shot's first and last frame from the plates that landed.

        The delivered frames are the truth about how long a shot is, and
        knowing it without going to the drive is what lets the dashboard show
        it and the timeline lay it out at the right length.

        Single-file "sequences" are ignored where real ones exist: fileseq
        reports a lone MOV as a one-frame sequence, and letting that in would
        collapse a 96-frame shot to one frame.
        """
        ranges = {}
        for seq in self.sequences_found:
            start, end = seq.get("start"), seq.get("end")
            if start is None or end is None:
                continue

            key = (seq.get("reel", ""), seq.get("shot", ""))
            real = int(seq.get("frames") or 0) > 1
            first, last, seen_real = ranges.get(key, (None, None, False))

            if seen_real and not real:
                continue
            if real and not seen_real:
                first, last = None, None

            first = start if first is None else min(first, start)
            last = end if last is None else max(last, end)
            ranges[key] = (first, last, seen_real or real)

        for entry in self.ingested_shots:
            found = ranges.get((entry.get("reel", ""), entry.get("shot", "")))
            if not found:
                continue
            first, last, _real = found
            entry["first_frame"] = int(first)
            entry["last_frame"] = int(last)

    def _walk_and_collect_shots(self, folder: Path, max_depth=6, current_depth=0):
        """Recursively find folders that look like shots (have media files)."""
        return walk_and_collect_shots(folder, self.source_scan_path, max_depth,
                                      current_depth)

    def _fallback_collect_shots_from_tree(self, source_root: Path):
        """Folder-only detection when no media-based shots are found."""
        return fallback_collect_shots_from_tree(source_root)

    def _process_files_in_shot(self, src_shot, dest_shot, subs, scan_root, oid, version_suffix=""):
        """Process files, including proper Sequence Detection and loose file handling."""
        handled_files = set()

        # 1. Sequence Detection (Grouped frame processing)
        if SequenceDetector.is_available():
            try:
                seqs = SequenceDetector.find_all_sequences(src_shot)
                for seq in seqs:
                    self.check_pause()
                    if not self.is_running:
                        return

                    ext = seq.extension().lower().lstrip('.')
                    target_sub = self._resolve_target_sub(ext, subs, scan_root, version_suffix)

                    # A short delivery has to be caught here, not by an artist
                    # opening the shot a week later.
                    missing = self._record_sequence(seq, src_shot, dest_shot)

                    msg = f"[SEQ] {seq.basename()} ({len(seq)} frames)"
                    if missing:
                        msg += f"  MISSING {len(missing)}: {_frame_summary(missing)}"
                    self.log_signal.emit(msg)

                    for frame_path_str in seq:
                        frame_path = Path(str(frame_path_str))
                        handled_files.add(frame_path.resolve())
                        # fileseq reports every standalone file as a one-frame
                        # sequence, so junk reaches this loop too.
                        if self._is_junk_file(frame_path):
                            continue
                        self._move_file(frame_path, dest_shot, target_sub, oid)
            except Exception as e:
                logging.warning(f"Sequence detection failed in {src_shot.name}, falling back to file-by-file: {e}")

        # 2. Process remaining loose files (not captured in sequences)
        try:
            files = [x for x in src_shot.iterdir() if x.is_file() and not self._is_junk_file(x) and x.resolve() not in handled_files]
        except (PermissionError, OSError):
            files = []

        for f in files:
            self.check_pause()
            if not self.is_running:
                break

            ext = f.suffix.lower().lstrip('.')
            target_sub = self._resolve_target_sub(ext, subs, scan_root, version_suffix)
            self._move_file(f, dest_shot, target_sub, oid)

    def _record_sequence(self, seq, src_shot: Path, dest_shot: Path):
        """
        Note a sequence for the delivery report and return its missing frames.

        A single loose file is reported as a one-frame sequence by fileseq and
        cannot have gaps, so it is recorded without a frame range.
        """
        try:
            missing = SequenceDetector.get_missing_frames(seq) or []
        except Exception as exc:
            logging.debug("Missing-frame check failed for %s: %s", seq, exc)
            missing = []

        try:
            start, end = seq.start(), seq.end()
        except Exception:
            start = end = None

        # A name a coordinator can read on a client-facing report:
        # "SH020 [1001-1100].exr" rather than "SH020..exr".
        base = str(seq.basename() or "").rstrip("._-")
        extension = str(seq.extension() or "")
        if start is not None and end is not None and start != end:
            display = f"{base} [{start}-{end}]{extension}"
        else:
            display = f"{base}{extension}"

        entry = {
            "shot": dest_shot.name,
            "reel": dest_shot.parent.name,
            "source": src_shot.name,
            "name": display,
            "frames": len(seq),
            "start": start,
            "end": end,
            "missing": missing,
        }
        self.sequences_found.append(entry)
        if missing:
            self.incomplete_sequences.append(entry)
            logging.warning("Short delivery: %s is missing %d frame(s)",
                            entry["name"], len(missing))
        return missing

    def _stitch_part_collides(self, src_shot: Path, dest_shot: Path,
                              scan_version: str) -> bool:
        """
        Whether this stitch part reuses file names an earlier part already used.

        Tracked in memory rather than by looking at the destination, so a dry
        run reports the same layout the real run would produce.
        """
        try:
            incoming = {f.name.lower() for f in src_shot.iterdir()
                        if f.is_file() and not self._is_junk_file(f)}
        except (PermissionError, OSError):
            return False

        key = f"{dest_shot}|{scan_version}"
        placed = self._stitch_placed_names.setdefault(key, set())
        collides = bool(placed & incoming)
        placed.update(incoming)
        return collides

    def _stitch_scan_version(self, dest_shot: Path, scan_root: str) -> str:
        """
        The scan version shared by every part of one stitch.

        The first part allocates it; the rest reuse it, so a two-part stitch
        lands as v001 with both plates in it rather than v001 and v002.
        """
        key = str(dest_shot)
        existing = self._stitch_versions.get(key)
        if existing:
            return existing

        version = self._next_scan_version(dest_shot, scan_root)
        self._stitch_versions[key] = version
        return version

    def _next_scan_version(self, dest_shot: Path, scan_root: str) -> str:
        """
        Next scan version folder for this shot, e.g. 'v001', 'v002'.

        Counts what is already in the project so a delivery that arrives weeks
        later still gets the next number, and tracks allocations made during
        this run so two source folders for one shot do not both claim v001.
        """
        key = str(dest_shot)
        if key in self._scan_versions:
            self._scan_versions[key] += 1
            return f"v{self._scan_versions[key]:03d}"

        highest = 0
        scan_dir = dest_shot / scan_root
        try:
            if scan_dir.is_dir():
                for child in scan_dir.iterdir():
                    if not child.is_dir():
                        continue
                    match = re.fullmatch(r"v(\d+)", child.name, flags=re.IGNORECASE)
                    if match:
                        highest = max(highest, int(match.group(1)))
        except (PermissionError, OSError) as exc:
            logging.warning("Could not inspect %s for scan versions: %s",
                            scan_dir, exc)

        self._scan_versions[key] = highest + 1
        return f"v{highest + 1:03d}"

    def _resolve_target_sub(self, ext, subs, scan_root, version_suffix=""):
        """
        Where a file of this format belongs inside the shot.

        When a shot is delivered more than once (SH010_ScanA, SH010_ScanB) the
        scans merge into one shot, so each delivery gets its own subfolder
        inside the scan root. Without that the second delivery collides with
        the first filename-for-filename and is skipped - which silently loses
        a re-graded plate.
        """
        target_sub = None
        scan_prefix = f"{scan_root.lower()}/"

        # A folder in the template named after the format, e.g. 01_Scan/EXR.
        # Restricted to the scan root: without that, "08_Output/EXR" - the
        # folder work goes OUT of - is just as good a match as "01_Scan/EXR",
        # and incoming plates land in the delivery folder.
        for s in subs:
            if not s.lower().startswith(scan_prefix):
                continue
            if Path(s).name.lower() == ext or Path(s).name.lower() == ext + "s":
                target_sub = s
                break

        if target_sub is None:
            mapped = self.format_mapping.get(ext)
            leaf = mapped.split('/')[-1] if mapped else ext.upper()
            target_sub = f"{scan_root}/{leaf}"

        # Keep separate deliveries of the same shot apart.
        if version_suffix and "scan" in target_sub.lower():
            head, _, tail = target_sub.partition('/')
            return f"{head}/{version_suffix}/{tail}" if tail else f"{head}/{version_suffix}"

        return target_sub

    def _move_file(self, f, dest_shot, target_sub, oid):
        dest_file = dest_shot / target_sub / f.name
        self._processed_files += 1
        if self._processed_files % 25 == 0:
            self._emit_progress(f"Processing {f.name}")
        
        try:
            # Race Condition Fix: Capture size BEFORE move
            file_size = f.stat().st_size if f.exists() else 0
            
            if not self.dry_run:
                # Collision Fix: Skip if exists unless overwrite
                if dest_file.exists():
                    if self.overwrite:
                        dest_file.unlink()
                    else:
                        self.files_skipped += 1
                        self.skipped_files.append({
                            "file": f.name, "reason": "already in the project",
                            "destination": str(dest_file),
                        })
                        self._record_task_detail(oid, f.name, f, dest_file, 0, 0,
                                                 "Skipped", "File exists")
                        self.log_signal.emit(f"[SKIP] {f.name} exists")
                        return

                if not dest_file.exists():
                    verify = not self.fast_mode
                    success, msg, _ = SafeFileOperations.safe_move_with_verification(
                        f, dest_file, verify_checksum=verify
                    )
                    if not success: raise Exception(msg)
            
            self.files_moved += 1
            if self.dry_run:
                self.log_signal.emit(f"[DRY] would move {f.name} -> {target_sub}")
            else:
                self._record_task_detail(oid, f.name, f, dest_file, file_size, 0, "Success")
                self.log_signal.emit(f"[OK] {f.name} -> {target_sub}")

        except Exception as e:
            self.errors += 1
            self.failed_files.append({
                "file": f.name,
                "source": str(f),
                "destination": str(dest_file),
                "error": str(e),
            })
            self._record_task_detail(oid, f.name, f, dest_file, 0, 0, "Failed", str(e))
            self.log_signal.emit(f"[ERR] {f.name}: {e}")

    def _calculate_directory_size(self, path):
        try:
            return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        except (PermissionError, OSError) as exc:
            logging.warning(f"Could not size {path}: {exc}")
            return 0

    def _check_disk_space(self, size, dest):
        try:
            free = psutil.disk_usage(str(dest.anchor)).free
            return free > (size * 1.1)
        except Exception: return True

    def _is_junk_file(self, path: Path) -> bool:
        return is_junk_file(path)

class ShotSubfoldersWorker(QThread):
    progress_signal = Signal(int, str)
    log_signal = Signal(str)
    finished_signal = Signal(bool, int, list)
    
    def __init__(self, target_dir, shot_folders):
        super().__init__()
        self.target_dir = target_dir
        self.shot_folders = shot_folders
        self.is_running = True
        self.security_validator = SecurityValidator()
    
    def run(self):
        try:
            created = 0
            errors = []
            total = len(self.shot_folders)
            for i, folder in enumerate(self.shot_folders):
                if not self.is_running: break
                try:
                    name_valid, sanitized_name, _ = self.security_validator.sanitize_filename(folder)
                    if not name_valid: continue
                    
                    path = self.target_dir / sanitized_name
                    success, msg = SafeFileOperations.safe_create_directory(path)
                    
                    if success:
                        created += 1
                        self.progress_signal.emit(int(((i+1)/total)*100), f"Creating {sanitized_name}")
                        self.log_signal.emit(f"[OK] Created: {sanitized_name}")
                    else:
                        errors.append(msg)
                except Exception as e:
                    errors.append(str(e))
            self.finished_signal.emit(len(errors)==0, created, errors)
        except Exception as e:
            self.finished_signal.emit(False, 0, [str(e)])
    
    def stop(self):
        self.is_running = False
