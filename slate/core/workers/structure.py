"""
Build & Ingest's worker: build the project folders and bring the plates in.

What is on the drive is worked out first by the survey
(slate/core/domain/ingest_survey.py) - one walk, shared with the stitch dialog
and the pre-flight summary - and this worker carries out exactly that plan:

* **Copy by default.** The client drive is left as it was; Move (copy, check,
  delete - or a plain rename on the same volume) is an option.
* **Stop and Pause act between files**, not between sequences, and a stopped
  run says it was stopped: nothing about it reads as a clean success.
* **A scan version exists only once a file lands in it.** A re-run over files
  that fail again no longer leaves an empty v002 behind and a "new scan" flag
  on the dashboard.
* **Sequences come from the one sequence detector** (group_frames): a still is
  not a sequence, and plate_1001 / plate.1002 / plate-1003 are three stills,
  not a sequence starting at frame -1003.
* **The log says one line per sequence**, not one per frame; files that fail or
  are skipped are named individually.
* **Bookkeeping is batched**: task rows go to the database 200 at a time, and
  registering the shots on the dashboard happens here, off the UI thread.
"""

import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

import psutil
from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from slate.utils.security import SecurityValidator
from slate.core.infra.database_manager import database_manager
from slate.core.infra.file_operations import SafeFileOperations, long_path, same_volume
from slate.core.services.path_template_manager import get_path_manager
from slate.core.domain.ingest_survey import (
    IGNORED_FILES, IGNORED_SUFFIXES, IngestSurvey, client_folder_for, is_junk_file, survey_drive,
)
from slate.core.domain.naming import name_problem, shot_name_problem
from slate.utils.sequence_utils import group_frames

__all__ = ["FolderCreationWorker", "ShotSubfoldersWorker", "is_junk_file",
           "IGNORED_FILES", "IGNORED_SUFFIXES", "DEFAULT_SHOT_FOLDERS", "COPY", "MOVE",
           "reels_root_for"]

# The shot folders every shot gets when a template names none. One list, so
# the preview, the pre-flight and the run agree about what will be created.
DEFAULT_SHOT_FOLDERS = ["01_Scan", "07_Comp", "08_Output"]
# What goes inside every scan version when a template does not say. A
# template that lists none gets none.
DEFAULT_VERSION_FOLDERS = ["Denoise"]

COPY = "copy"
MOVE = "move"

COMPLETED = "completed"
STOPPED = "stopped"
FAILED = "failed"

# Task rows are written in batches of this many.
DETAIL_BATCH = 200
# The ingest lock is refreshed this often while a run is on screen - paused
# or not - so it is never judged abandoned (Build & Ingest's timer does it).
LOCK_TOUCH_SECONDS = 300
# Progress is reported at most this often.
PROGRESS_SECONDS = 0.1


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


def _size_text(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _eta_text(seconds: float) -> str:
    if seconds < 60:
        return "under a minute left"
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"about {minutes} min left"
    return f"about {minutes // 60} h {minutes % 60:02d} min left"


def reels_root_for(project_path, project_code: str, target_root=None) -> Path:
    """
    Where the reels go inside a project, from the studio's path template
    (normally <project>/05_Reels). Resolved relative to the project folder,
    so a project folder named differently from its code still works.
    """
    project_path = Path(project_path)
    target_root = Path(target_root) if target_root else project_path.parent
    try:
        formatted = Path(get_path_manager().format_path(
            'reels_root', project=project_code, root=str(target_root)))
        try:
            return project_path / formatted.relative_to(target_root / project_code)
        except ValueError:
            return formatted
    except Exception as exc:
        logging.debug("reels_root template not usable (%s); using 05_Reels", exc)
        return project_path / "05_Reels"


class FolderCreationWorker(QThread):

    log_signal = Signal(str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, int, int, int, int, str)
    error_signal = Signal(str)
    # "paused" / "running", so the screen can say Paused and mean it.
    state_signal = Signal(str)

    def __init__(self, target_dir=None, excel_df=None, source_scan_path=None, project_name="",
                 template_data=(), mode="full", template_type="", target_reel_name="",
                 overwrite=False, dry_run=False, format_mapping=None, fast_mode=False,
                 scan_version_folders=None, stitch_mapping=None, parent=None, root_dir=None,
                 operation=COPY, survey=None, project_dir=None, lock=None,
                 register_shots=False, client_folder="", register_existing=None, **kwargs):
        super().__init__(parent)
        self.target_dir = Path(target_dir or root_dir or ".")
        self.excel_df = excel_df
        self.source_scan_path = Path(source_scan_path) if source_scan_path else None
        self.project_name = project_name
        self.project_dir = Path(project_dir) if project_dir else None
        self.template_data = template_data
        self.mode = mode
        self.template_type = template_type
        self.target_reel_name = target_reel_name
        # "Overwrite Existing" is gone (every delivery gets its own scan
        # version, so it could never act - except to delete a project file
        # before its replacement was safely copied). Accepted and ignored.
        self.overwrite = False
        self.dry_run = dry_run
        self.format_mapping = format_mapping or {}
        self.fast_mode = fast_mode
        self.operation = MOVE if str(operation).lower() == MOVE else COPY
        self.survey = survey
        self.lock = lock
        self.register_shots = bool(register_shots)
        # Shots already in the project, unchanged, that are not on the
        # Dashboard yet: registered with this run's shots.
        self.register_existing = list(register_existing or [])
        self.client_folder = client_folder
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.pause_condition = QWaitCondition()

        self.folders_created = 0
        self.reels_count = 0
        self.shots_count = 0
        self.files_moved = 0          # files brought in (copied or moved)
        self.files_skipped = 0
        self.errors = 0
        self.bytes_done = 0
        self.security_validator = SecurityValidator()

        self.outcome = COMPLETED
        self.cancelled = False
        self.started_at = ""
        self.finished_at = ""
        self.registration = None
        self.registration_error = ""

        self._total_files = 0
        self._processed_files = 0
        self._total_bytes = 0
        self._started = 0.0
        self._last_progress = 0.0

        self.ingested_shots = []
        self._scan_versions = {}
        self.scan_version_folders = list(DEFAULT_VERSION_FOLDERS if scan_version_folders is None
                                         else scan_version_folders)
        self.stitch_mapping = dict(stitch_mapping or {})
        self._stitch_versions = {}
        self._stitch_split = {}
        self._entries = {}
        self._created_versions = set()
        self._planned_dirs = set()
        self._placed = set()
        self._reels_seen = set()
        self._shots_seen = set()
        self._details = []
        self._placement = {}
        self._attempted = set()
        self._moved_from = set()

        self.sequences_found = []
        self.incomplete_sequences = []
        self.skipped_files = []
        self.failed_files = []
        # Files a stopped run never reached, with where they were going: the
        # retry finishes the run into the same scan version.
        self.pending_files = []
        self.documents_filed = []
        self.skipped_shots = []

    # ------------------------------------------------------------ control
    def pause(self):
        self.mutex.lock()
        self.is_paused = True
        self.mutex.unlock()
        self.state_signal.emit("paused")
        self.log_signal.emit("[WAIT] Paused.")

    def resume(self):
        self.mutex.lock()
        self.is_paused = False
        self.pause_condition.wakeAll()
        self.mutex.unlock()
        self.state_signal.emit("running")
        self.log_signal.emit("[RESUME] Resumed.")

    def stop(self):
        self.mutex.lock()
        self.is_running = False
        self.is_paused = False
        self.pause_condition.wakeAll()
        self.mutex.unlock()
        self.log_signal.emit("[STOP] Stopping after the current file.")

    def check_pause(self):
        self.mutex.lock()
        if self.is_paused:
            self.pause_condition.wait(self.mutex)
        self.mutex.unlock()

    def _should_go_on(self) -> bool:
        """Pause here if asked; False once Stop was pressed."""
        self.check_pause()
        if not self.is_running:
            self.cancelled = True
            return False
        return True

    # ------------------------------------------------------------ folders
    def _mkdir(self, path: Path):
        """Create a folder (not in a dry run) and count it once if it is new."""
        path = Path(path)
        if self.dry_run:
            key = str(path).lower()
            if key not in self._planned_dirs and not SafeFileOperations.exists(path):
                self._planned_dirs.add(key)
                self.folders_created += 1
            return
        success, message = SafeFileOperations.safe_create_directory(path)
        if success and "already exists" not in str(message).lower():
            self.folders_created += 1

    def _create_subs(self, shot_path: Path, subs):
        for s in subs:
            self._mkdir(shot_path / s)

    # ------------------------------------------------------------ progress
    def _verb(self, past=False) -> str:
        if self.operation == MOVE:
            return "moved" if past else "Moving"
        return "copied" if past else "Copying"

    def _emit_progress(self, message="", force=False):
        """Progress across the whole ingest: files, speed and time left."""
        now = time.monotonic()
        if not force and now - self._last_progress < PROGRESS_SECONDS:
            return
        self._last_progress = now
        if self._total_files > 0:
            pct = min(int((self._processed_files / self._total_files) * 100), 99)
        else:
            pct = 0
        if not message:
            message = f"{self._verb()} {self._processed_files:,} of {self._total_files:,} files"
            elapsed = now - self._started if self._started else 0
            if elapsed > 1 and self.bytes_done:
                speed = self.bytes_done / elapsed
                message += f" · {_size_text(speed)}/s"
                remaining = max(self._total_bytes - self.bytes_done, 0)
                if speed > 0 and remaining:
                    message += f" · {_eta_text(remaining / speed)}"
        self.progress_signal.emit(pct, message)

    # ------------------------------------------------------------ the run
    def run(self):
        self._started = time.monotonic()
        self.started_at = datetime.now().isoformat(timespec="seconds")

        self.op_id = 0
        if not self.dry_run:
            # A simulation leaves no project row behind: that table feeds the
            # 'latest project' of reports and the Dashboard's fallback.
            try:
                pid = database_manager.record_project(self.project_name, self.template_type, str(self.target_dir))
                op_type = "Auto-Scan & Build" if self.source_scan_path else "Folder Creation"
                self.op_id = database_manager.start_operation(pid, op_type)
            except Exception:
                self.op_id = 0

        try:
            if not self.template_data or len(self.template_data) < 4:
                self.log_signal.emit("[WARN] The template is incomplete - using the default folders.")
                base_folders, prod_subs, outsource_subs = ["01_Scan", "05_Reels"], [], []
                shot_subs = list(DEFAULT_SHOT_FOLDERS)
            else:
                base_folders, prod_subs, outsource_subs, shot_subs = self.template_data
            if not isinstance(shot_subs, list) or not shot_subs:
                self.log_signal.emit("[WARN] The template has no shot folders - Slate adds "
                                     + ", ".join(DEFAULT_SHOT_FOLDERS) + ".")
                shot_subs = list(DEFAULT_SHOT_FOLDERS)

            project_path = self.project_dir or (self.target_dir / self.project_name)
            reels_path = reels_root_for(project_path, self.project_name, self.target_dir)
            if not self.client_folder:
                self.client_folder = client_folder_for({"base_folders": base_folders})

            if self.source_scan_path is not None and self.excel_df is None and self.survey is None:
                self._emit_progress("Looking at the client drive…", force=True)
                self.survey = survey_drive(self.source_scan_path, self.target_reel_name,
                                           should_stop=lambda: not self.is_running)
            if self.survey is not None and self.survey.cancelled:
                self.cancelled = True

            if self.survey is not None and not self.dry_run and not self.cancelled:
                need = self.survey.total_bytes
                if self.operation == MOVE and same_volume(self.source_scan_path, self.target_dir):
                    need = 0
                ok, why = self._enough_space(need, self.target_dir)
                if not ok:
                    self._finish(FAILED, why, success=False)
                    return

            if not self.cancelled:
                self._mkdir(project_path)
                if getattr(self.lock, "created_folder", False) and not self.dry_run:
                    # Taking the lock made the project folder a moment ago;
                    # it is still a folder this run created (the dry run
                    # counts it too).
                    self.folders_created += 1
                for f in base_folders:
                    self._mkdir(project_path / f)
                for s in list(prod_subs) + list(outsource_subs):
                    self._mkdir(project_path / "04_Production" / s)
                self._mkdir(reels_path)

                if self.excel_df is not None:
                    self._process_excel(reels_path, shot_subs)
                elif self.survey is not None:
                    self._total_files = self.survey.total_files
                    self._total_bytes = self.survey.total_bytes
                    self.log_signal.emit(
                        f"[INFO] {self._total_files:,} file(s), {_size_text(self._total_bytes)} "
                        f"to {'check' if self.dry_run else self.operation}")
                    self._emit_progress(force=True)
                    self._process_survey(reels_path, shot_subs, project_path)

            if self.cancelled and self.survey is not None and not self.dry_run:
                self._record_pending()
            self._tidy_drive()
            self._attach_frame_ranges()
            self._flush_details()

            if self.cancelled:
                self._finish(STOPPED, "Stopped by the user", success=False)
                return

            if self.register_shots and not self.dry_run and (self.ingested_shots or self.register_existing):
                self._register(project_path)

            self._finish(COMPLETED, self._summary(), success=True)

        except Exception as e:
            logging.exception(f"Worker Error: {e}", exc_info=True)
            self._flush_details()
            self._finish(FAILED, str(e), success=False)

    def _summary(self) -> str:
        verb = ("would be " + self._verb(past=True)) if self.dry_run else self._verb(past=True)
        summary = [f"{self.files_moved:,} file(s) {verb}"]
        if self.files_skipped:
            summary.append(f"skipped {self.files_skipped:,}")
        if self.errors:
            summary.append(f"failed {self.errors:,}")
        return " | ".join(summary)

    def _finish(self, outcome, message, success):
        self.outcome = outcome
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        self._record_operation_result(time.monotonic() - self._started, outcome == COMPLETED)
        if outcome == STOPPED:
            self.log_signal.emit(f"[STOP] Stopped: {self._summary()}.")
        self.finished_signal.emit(success, 1 if success else 0, self.reels_count, self.shots_count,
                                  self.folders_created, message)

    def _enough_space(self, size, dest):
        try:
            anchor = Path(dest).anchor or str(dest)
            free = psutil.disk_usage(anchor).free
        except Exception:
            return True, ""
        if free > size * 1.1:
            return True, ""
        return False, (f"Not enough free space on {anchor}: the delivery needs about "
                       f"{_size_text(size * 1.1)} and {_size_text(free)} is free.")

    def _register(self, project_path):
        """Dashboard records for the shots that landed - here, not on the UI thread."""
        try:
            from slate.core.domain.shot_registry import register_ingested_shots
            self.registration = register_ingested_shots(
                project_code=self.project_name, shots=self.ingested_shots + self.register_existing,
                project_name=self.project_name, folder_base=str(Path(project_path).parent),
            )
            self.log_signal.emit(f"[INFO] {self.registration.summary()}")
        except Exception as exc:
            logging.exception("Dashboard registration failed: %s", exc)
            self.registration_error = str(exc)

    def _record_operation_result(self, duration, success):
        """Best-effort bookkeeping - a database outage must not fail the ingest."""
        try:
            database_manager.update_operation(
                getattr(self, "op_id", 0), duration, self.files_moved, self.errors, success)
        except Exception as exc:
            logging.warning(f"Could not record operation result: {exc}")

    def _record_task_detail(self, *row):
        """Queue one task row; written in batches."""
        self._details.append(row)
        if len(self._details) >= DETAIL_BATCH:
            self._flush_details()

    def _flush_details(self):
        rows, self._details = self._details, []
        if not rows or not getattr(self, "op_id", 0):
            return
        try:
            repo = getattr(database_manager, "project_repo", None)
            batch = getattr(repo, "record_task_details", None)
            if callable(batch):
                batch(rows)
                return
            for row in rows:
                database_manager.record_task_detail(*row)
        except Exception as exc:
            logging.debug(f"Could not record task details: {exc}")

    # ------------------------------------------------------------ excel
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
            if not self._should_go_on():
                break
            reel_path = root / str(reel).strip()
            self._mkdir(reel_path)
            self.reels_count += 1
            for shot in self.excel_df[self.excel_df[reel_col] == reel][shot_col].dropna():
                shot_path = reel_path / str(shot).strip()
                self._mkdir(shot_path)
                self.shots_count += 1
                self._create_subs(shot_path, subs)

    # ------------------------------------------------------------ the scan
    def _process_scan(self, root, subs, oid=None):
        """Older entry point: survey the source and process it."""
        if self.survey is None and self.source_scan_path is not None:
            self.survey = survey_drive(self.source_scan_path, self.target_reel_name)
        if self.survey is not None:
            self._total_files = self.survey.total_files
            self._process_survey(Path(root), subs, Path(root).parent)

    def _plan_stitch_splits(self):
        """
        Decide up front which stitches keep their parts in sub-folders: all
        parts of a stitch whose file names collide go into <version>/<part>/,
        the first part included, so no part looks like the whole plate.
        """
        survey = self.survey
        names_by_dest = {}
        for shot in survey.active_shots():
            dest = survey.stitched_name(shot, self.stitch_mapping)
            if not dest:
                continue
            names_by_dest.setdefault((shot.reel, dest), []).append(
                {f.path.name.lower() for f in shot.files})
        for key, sets in names_by_dest.items():
            seen, clash = set(), False
            for names in sets:
                if seen & names:
                    clash = True
                    break
                seen |= names
            self._stitch_split[key] = clash

    def _part_folder(self, shot, dest) -> str:
        tail = shot.name[len(dest):].lstrip("_-. ") if shot.name.lower().startswith(dest.lower()) else ""
        label = tail or shot.source_name
        if shot.tail:
            label = f"{label}_{shot.tail}"
        return label

    def _process_survey(self, reels_path: Path, subs, project_path: Path):
        survey: IngestSurvey = self.survey
        oid = getattr(self, "op_id", 0)
        self._plan_stitch_splits()
        scan_root = next((s.split('/')[0] for s in subs if "scan" in s.lower()), "01_Scan")
        self._reels_path, self._subs, self._scan_root = reels_path, subs, scan_root
        self._docs_folder = Path(project_path) / self.client_folder / f"{date.today().isoformat()}_docs"

        if survey.documents:
            self._file_documents(project_path)

        for shot in survey.shots:
            if shot.skip:
                reason = (f"identical to {shot.unchanged_from} already in the project"
                          if shot.unchanged_from else "left out in the pre-flight")
                # Recorded under the shot it would have become (the merged
                # name for a stitch part), so it is counted like every shot.
                self.skipped_shots.append({"reel": shot.reel,
                                           "shot": survey.destination_of(shot, self.stitch_mapping),
                                           "source": shot.source_name, "reason": reason})
                self.log_signal.emit(f"[SKIP] {shot.source_name}: {reason}")
                continue
            if not self._should_go_on():
                break

            place = self._place(shot)
            if place.get("problem"):
                # Never built: a name like '../x' would put a shot tree
                # outside the project. The pre-flight refuses these too.
                problem = place["problem"]
                self.log_signal.emit(f"[ERR] {shot.source_name}: {problem}")
                for f in shot.files:
                    self.errors += 1
                    self.failed_files.append({"file": f.path.name, "source": str(f.path),
                                              "destination": "", "error": problem})
                continue

            dest_reel, dest_shot = place["dest_shot"].parent, place["dest_shot"]
            if shot.reel not in self._reels_seen:
                self._reels_seen.add(shot.reel)
                self.reels_count += 1
                self._mkdir(dest_reel)
            self._mkdir(dest_shot)
            for s in subs:
                self._mkdir(dest_shot / s)

            if survey.structure_only or not shot.files:
                # A folder skeleton: the shot is built, there is no scan yet.
                if survey.structure_only:
                    self._entry(shot, dest_shot, "", count=True)
                continue

            if place["stitched"] and place["scan_target"] != place["version"]:
                self.log_signal.emit(f"[STITCH] {shot.source_name} shares file names with another "
                                     f"part; it goes in {place['scan_target']}")
            elif shot.tail and not place["stitched"]:
                self.log_signal.emit(f"[SCAN] {shot.source_name} -> {dest_shot.name} {place['version']}")

            context = {"shot": shot, "dest_shot": dest_shot, "scan_root": scan_root,
                       "version": place["version"], "stitched": place["stitched"]}
            self._process_files(shot, dest_shot, subs, scan_root, place["scan_target"], oid, context)
            if self.cancelled:
                break

        self._emit_progress(force=True)

    def _place(self, shot) -> dict:
        """
        Where a shot goes - its folder, scan version and the folder inside
        the version - worked out once per shot, for the run and for the files
        a stopped run leaves to the retry.
        """
        if id(shot) in self._placement:
            return self._placement[id(shot)]
        survey, reels_path, scan_root = self.survey, self._reels_path, self._scan_root
        stitched = survey.stitched_name(shot, self.stitch_mapping)
        dest_name = stitched or shot.name
        problem = shot_name_problem(dest_name, "A shot name") or name_problem(shot.reel, "A reel name")
        dest_shot = reels_path / shot.reel / dest_name
        if not problem:
            try:
                dest_shot.resolve().relative_to(Path(reels_path).resolve())
            except ValueError:
                problem = "The shot would land outside the project."
        place = {"problem": problem, "dest_shot": dest_shot, "stitched": bool(stitched),
                 "version": "", "scan_target": ""}
        if not problem and shot.files and not survey.structure_only:
            if stitched:
                version = self._stitch_versions.get(str(dest_shot))
                if version is None:
                    version = self._next_scan_version(dest_shot, scan_root)
                    self._stitch_versions[str(dest_shot)] = version
            else:
                version = self._next_scan_version(dest_shot, scan_root)
            scan_target = version
            if stitched and self._stitch_split.get((shot.reel, dest_name)):
                scan_target = f"{version}/{self._part_folder(shot, dest_name)}"
            place.update(version=version, scan_target=scan_target)
        self._placement[id(shot)] = place
        return place

    def _target(self, shot, file_info, path: Path, target_sub: str) -> str:
        """The folder inside the shot a file lands in (its format folder, and its own sub-folder when names repeat)."""
        sub = getattr(file_info, "sub", "") if shot.keep_subfolders else ""
        parts = [p for p in sub.split("/") if p]
        if parts and parts[0].lower() == Path(target_sub).name.lower():
            parts = parts[1:]                    # EXR/4K inside .../EXR -> .../EXR/4K
        return "/".join([target_sub] + parts)

    def _file_documents(self, project_path: Path):
        """Client material (paperwork, LUTs, references): filed under the client folder, not as a shot."""
        folder = self._docs_folder
        for doc in self.survey.documents:
            if not self._should_go_on():
                return
            status = self._transfer(doc.path, self._doc_destination(doc), doc.size, getattr(self, "op_id", 0))
            if status == "ok":
                self.documents_filed.append({"file": str(self._doc_relative(doc)),
                                             "destination": str(self._doc_destination(doc))})
        if self.documents_filed:
            verb = "would be filed" if self.dry_run else "filed"
            self.log_signal.emit(f"[DOCS] {len(self.documents_filed)} document(s) {verb} in "
                                 f"{self.client_folder}/{folder.name}")

    def _doc_relative(self, doc) -> Path:
        try:
            return doc.path.relative_to(self.survey.source)
        except ValueError:
            return Path(doc.path.name)

    def _doc_destination(self, doc) -> Path:
        return self._docs_folder / self._doc_relative(doc)

    def _entry(self, shot, dest_shot, version, count=True):
        """The ingested-shot record, made when the first file of it lands."""
        stitched = self.survey.stitched_name(shot, self.stitch_mapping) if self.survey else None
        key = str(dest_shot).lower() if stitched else (str(dest_shot).lower(), shot.source_name, version)
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        entry = {
            "reel": shot.reel,
            "shot": dest_shot.name,
            "path": str(dest_shot),
            "scan_version": version,
            "source_folder": shot.source_name,
            "client_version": shot.client_version,
            "is_media": bool(shot.files),
        }
        self._entries[key] = entry
        self.ingested_shots.append(entry)
        # A shot is a destination (reel, shot): two deliveries of SH_050 in
        # one run are one shot with two scan versions, as on the dashboard.
        shot_key = (shot.reel.lower(), dest_shot.name.lower())
        if count and shot_key not in self._shots_seen:
            self._shots_seen.add(shot_key)
            self.shots_count += 1
        return entry

    def _ensure_version(self, dest_shot, scan_root, version):
        """Create the scan version (and Denoise...) the first time a file needs it."""
        key = (str(dest_shot).lower(), version)
        if key in self._created_versions:
            return
        self._created_versions.add(key)
        for derived in self.scan_version_folders:
            self._mkdir(Path(dest_shot) / scan_root / version / derived)

    def _prune_unused_version(self, context):
        """
        A failed copy creates the folders it was copying into. When nothing
        has landed in this scan version yet, take those empty folders away
        again, so a version exists only if it holds a plate.
        """
        key = (str(context["dest_shot"]).lower(), context["version"])
        if key in self._created_versions:
            return
        top = Path(context["dest_shot"]) / context["scan_root"] / context["version"]
        try:
            for root, dirs, files in os.walk(long_path(top), topdown=False):
                if not files and not os.listdir(root):
                    os.rmdir(root)
        except OSError as exc:
            logging.debug("Could not tidy the unused version %s: %s", top, exc)

    def _meta(self, context) -> dict:
        """What a retry needs to finish a file the way this run would have: its shot and scan version."""
        if not context:
            return {}
        shot = context["shot"]
        return {
            "reel": shot.reel, "shot": context["dest_shot"].name, "path": str(context["dest_shot"]),
            "scan_version": context["version"], "source_folder": shot.source_name,
            "client_version": shot.client_version,
            "version_dir": str(Path(context["dest_shot"]) / context["scan_root"] / context["version"]),
            "derived": list(self.scan_version_folders),
            "sequence": context.get("sequence"), "frame": context.get("frame"),
        }

    def _process_files(self, shot, dest_shot, subs, scan_root, scan_target, oid, context):
        sequences, stills = group_frames([f.path for f in shot.files])
        by_path = {str(f.path): f for f in shot.files}

        for seq in sequences:
            if not self._should_go_on():
                return
            ext = seq.tail.lstrip('.').lower()
            target_sub = self._resolve_target_sub(ext, subs, scan_root, scan_target)
            missing, display = self._sequence_display(seq)
            done = failed = skipped = 0
            landed = []
            for frame, frame_path in zip(seq.frames, seq.files):
                if not self._should_go_on():
                    break
                info = by_path.get(str(frame_path))
                context["sequence"], context["frame"] = display, frame
                status = self._transfer(frame_path,
                                        dest_shot / self._target(shot, info, frame_path, target_sub) / frame_path.name,
                                        getattr(info, "size", 0), oid, context)
                done += status == "ok"
                failed += status == "fail"
                skipped += status == "skip"
                if status != "fail":
                    landed.append(frame)
            context["sequence"] = context["frame"] = None
            self._record_sequence(seq, dest_shot, shot, display, missing, landed)
            self._log_group(display, seq.frame_count, target_sub, done, failed, skipped, missing,
                            landed=len(landed))
            if self.cancelled:
                return

        by_target = {}
        for still in stills:
            ext = still.suffix.lower().lstrip('.')
            by_target.setdefault(self._resolve_target_sub(ext, subs, scan_root, scan_target), []).append(still)
        for target_sub, files in by_target.items():
            done = failed = skipped = 0
            for path in files:
                if not self._should_go_on():
                    break
                info = by_path.get(str(path))
                status = self._transfer(path, dest_shot / self._target(shot, info, path, target_sub) / path.name,
                                        getattr(info, "size", 0), oid, context)
                if status != "fail":
                    self._record_still(path, dest_shot, shot)
                done += status == "ok"
                failed += status == "fail"
                skipped += status == "skip"
            label = files[0].name if len(files) == 1 else f"{len(files)} single file(s)"
            self._log_group(label, len(files), target_sub, done, failed, skipped, [],
                            landed=done + skipped)
            if self.cancelled:
                return

    def _log_group(self, label, count, target_sub, done, failed, skipped, missing, landed=None):
        if self.dry_run:
            line = f"[DRY] would {self.operation} {label} ({count} file(s)) -> {target_sub}"
        elif self.cancelled and (done + failed + skipped) < count:
            line = f"[STOP] {label} - {landed if landed is not None else done} of {count} file(s), stopped"
        elif failed:
            line = f"[ERR] {label}: {failed} of {count} file(s) failed -> {target_sub}"
        else:
            line = f"[OK] {label} ({done} file(s)) -> {target_sub}"
        if skipped:
            line += f"  ({skipped} skipped)"
        if missing:
            line += f"  MISSING {len(missing)}: {_frame_summary(missing)}"
        self.log_signal.emit(line)

    @staticmethod
    def _sequence_display(seq):
        """(frames the client did not deliver, 'plate [1001-1060].exr')."""
        base = seq.head.rstrip("._- ")
        missing = list(seq.missing_frames)
        # A numbering that jumps (1001 ... 9001) is not a short delivery of
        # 8,000 frames; it is irregular.
        if missing and seq.end - seq.start + 1 > 10 * seq.frame_count:
            missing = []
        return missing, f"{base} [{seq.start}-{seq.end}]{seq.tail}"

    def _record_sequence(self, seq, dest_shot: Path, shot, display=None, missing=None, landed=None):
        """
        Note a sequence for the delivery report, counting the frames that
        actually landed: a stopped or failed sequence is never reported whole.
        """
        if display is None:
            missing, display = self._sequence_display(seq)
        landed = list(seq.frames) if landed is None else list(landed)
        entry = {
            "shot": dest_shot.name,
            "reel": dest_shot.parent.name,
            "source": shot.source_name,
            "name": display,
            "kind": "sequence",
            "frames": len(landed),
            "start": seq.start,
            "end": seq.end,
            "missing": list(missing or []),
        }
        if seq.missing_frames and not missing:
            entry["irregular"] = True
        not_landed = sorted(set(seq.frames) - set(landed))
        if not_landed:
            entry["planned"] = seq.frame_count
            entry["not_landed"] = not_landed
            if self.cancelled:
                entry["stopped"] = True
        self.sequences_found.append(entry)
        if missing:
            self.incomplete_sequences.append(entry)
            logging.warning("Short delivery: %s is missing %d frame(s)", display, len(missing))
        return list(missing or []), display

    def _record_still(self, path: Path, dest_shot: Path, shot):
        self.sequences_found.append({
            "shot": dest_shot.name, "reel": dest_shot.parent.name, "source": shot.source_name,
            "name": path.name, "kind": "file", "frames": 1, "start": None, "end": None, "missing": [],
        })

    def _record_pending(self):
        """
        A stopped run: every file it did not reach, with the shot, scan
        version and folder it was going to - so 'Finish the stopped run'
        brings them into the same scan version instead of a new one.
        """
        survey = self.survey
        for doc in survey.documents:
            if str(doc.path) not in self._attempted:
                self.pending_files.append({
                    "file": doc.path.name, "source": str(doc.path),
                    "destination": str(self._doc_destination(doc)),
                    "error": "Not copied - the run was stopped", "pending": True})
        for shot in survey.active_shots():
            if not shot.files or survey.structure_only:
                continue
            todo = [f for f in shot.files if str(f.path) not in self._attempted]
            if not todo:
                continue
            place = self._place(shot)
            if place.get("problem"):
                continue
            context = {"shot": shot, "dest_shot": place["dest_shot"], "scan_root": self._scan_root,
                       "version": place["version"]}
            by_path = {str(f.path): f for f in shot.files}
            sequences, stills = group_frames([f.path for f in shot.files])
            recorded = {(e["shot"], e["name"]) for e in self.sequences_found}
            planned = []
            for seq in sequences:
                _missing, display = self._sequence_display(seq)
                if (place["dest_shot"].name, display) not in recorded:
                    # Never reached: in the report as a sequence none of whose frames landed.
                    self._record_sequence(seq, place["dest_shot"], shot, landed=[])
                target_sub = self._resolve_target_sub(seq.tail.lstrip('.').lower(), self._subs,
                                                      self._scan_root, place["scan_target"])
                planned += [(p, target_sub, display, frame) for frame, p in zip(seq.frames, seq.files)]
            for still in stills:
                target_sub = self._resolve_target_sub(still.suffix.lower().lstrip('.'), self._subs,
                                                      self._scan_root, place["scan_target"])
                planned.append((still, target_sub, None, None))
            for path, target_sub, display, frame in planned:
                if str(path) in self._attempted:
                    continue
                info = by_path.get(str(path))
                context.update(sequence=display, frame=frame)
                destination = place["dest_shot"] / self._target(shot, info, path, target_sub) / path.name
                self.pending_files.append({
                    "file": path.name, "source": str(path), "destination": str(destination),
                    "error": "Not copied - the run was stopped", "pending": True, **self._meta(context)})
        if self.pending_files:
            self.log_signal.emit(f"[STOP] {len(self.pending_files):,} file(s) not reached - "
                                 "'Finish the stopped run' brings them into the same scan version.")

    def _tidy_drive(self):
        """
        After a Move: take away the folders on the client drive this run
        emptied (bottom up, only empty ones), so the drive does not look like
        an undelivered one. Folders still holding something stay, and are said.
        """
        if self.operation != MOVE or self.dry_run or not self._moved_from or self.source_scan_path is None:
            return
        root = Path(self.source_scan_path)
        left = 0
        for folder in sorted(self._moved_from, key=lambda p: len(p.parts), reverse=True):
            current = folder
            while current != root and root in current.parents:
                try:
                    os.rmdir(long_path(current))
                except FileNotFoundError:
                    pass
                except OSError:
                    left += current == folder
                    break
                current = current.parent
        if left:
            self.log_signal.emit(f"[INFO] {left} folder(s) on the client drive still hold files Slate did "
                                 "not bring in (system files, or files left out) and were kept.")

    def _attach_frame_ranges(self):
        """
        Record each shot's first and last frame from the plates that landed.

        Only real sequences count: a lone movie or still has no frame range,
        and letting one in would collapse a 96-frame shot to one frame.
        """
        ranges = {}
        for seq in self.sequences_found:
            if seq.get("kind") != "sequence" or seq.get("start") is None:
                continue
            key = (seq.get("reel", ""), seq.get("shot", ""))
            first, last = ranges.get(key, (None, None))
            first = seq["start"] if first is None else min(first, seq["start"])
            last = seq["end"] if last is None else max(last, seq["end"])
            ranges[key] = (first, last)
        for entry in self.ingested_shots:
            found = ranges.get((entry.get("reel", ""), entry.get("shot", "")))
            if found:
                entry["first_frame"], entry["last_frame"] = int(found[0]), int(found[1])

    def _next_scan_version(self, dest_shot: Path, scan_root: str) -> str:
        """
        Next scan version for this shot, e.g. 'v001', 'v002'.

        Counts what is already in the project, and what this run has already
        handed out, so two deliveries of one shot never both claim v001. The
        folder itself is only created once a file lands in it.
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
                    match = re.fullmatch(r"v(\d+)", child.name, flags=re.IGNORECASE)
                    if child.is_dir() and match:
                        highest = max(highest, int(match.group(1)))
        except (PermissionError, OSError) as exc:
            logging.warning("Could not inspect %s for scan versions: %s", scan_dir, exc)
        self._scan_versions[key] = highest + 1
        return f"v{highest + 1:03d}"

    def _resolve_target_sub(self, ext, subs, scan_root, version_suffix=""):
        """
        Where a file of this format belongs inside the shot:
        <scan root>/<version>[/<part>]/<FORMAT>.

        A template folder named after the format inside the scan root wins;
        "08_Output/EXR" - the folder work goes OUT of - never matches.
        """
        target_sub = None
        scan_prefix = f"{scan_root.lower()}/"
        for s in subs:
            if not s.lower().startswith(scan_prefix):
                continue
            if Path(s).name.lower() == ext or Path(s).name.lower() == ext + "s":
                target_sub = s
                break
        if target_sub is None:
            mapped = self.format_mapping.get(ext)
            leaf = mapped.split('/')[-1] if mapped else (ext.upper() or "OTHER")
            target_sub = f"{scan_root}/{leaf}"
        if version_suffix and "scan" in target_sub.lower():
            head, _, tail = target_sub.partition('/')
            return f"{head}/{version_suffix}/{tail}" if tail else f"{head}/{version_suffix}"
        return target_sub

    def _transfer(self, source: Path, dest_file: Path, size: int, oid, context=None) -> str:
        """Bring one file in: 'ok', 'skip' or 'fail'."""
        self._processed_files += 1
        self._attempted.add(str(source))
        key = str(dest_file).lower()
        try:
            if key in self._placed or SafeFileOperations.exists(dest_file):
                self.files_skipped += 1
                self.skipped_files.append({"file": source.name, "reason": "already in the project",
                                           "destination": str(dest_file)})
                self._record_task_detail(oid, source.name, str(source), str(dest_file), 0, 0,
                                         "Skipped", "File exists")
                self.log_signal.emit(f"[SKIP] {source.name}: already in the project")
                return "skip"

            if not self.dry_run:
                verify = not self.fast_mode
                if self.operation == MOVE:
                    success, msg, _ = SafeFileOperations.safe_move_with_verification(
                        source, dest_file, verify_checksum=verify, allow_rename=True)
                else:
                    success, msg, _ = SafeFileOperations.safe_copy_with_verification(
                        source, dest_file, verify_checksum=verify)
                if not success:
                    raise RuntimeError(msg)
                self._record_task_detail(oid, source.name, str(source), str(dest_file), size, 0, "Success", "")
                if self.operation == MOVE:
                    self._moved_from.add(Path(source).parent)

            self._placed.add(key)
            self.files_moved += 1
            self.bytes_done += size
            if context is not None:
                # The version is real now that a file is in it: add its
                # Denoise (etc.) folders and the shot's record.
                self._ensure_version(context["dest_shot"], context["scan_root"], context["version"])
                self._entry(context["shot"], context["dest_shot"], context["version"])
            return "ok"

        except Exception as e:
            self.errors += 1
            self.failed_files.append({"file": source.name, "source": str(source),
                                      "destination": str(dest_file), "error": str(e), **self._meta(context)})
            self._record_task_detail(oid, source.name, str(source), str(dest_file), 0, 0, "Failed", str(e))
            self.log_signal.emit(f"[ERR] {source.name}: {e}")
            if context is not None and not self.dry_run:
                self._prune_unused_version(context)
            return "fail"
        finally:
            self._emit_progress()

    # kept for older callers
    def _move_file(self, f, dest_shot, target_sub, oid):
        size = f.stat().st_size if f.exists() else 0
        return self._transfer(Path(f), Path(dest_shot) / target_sub / Path(f).name, size, oid)

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
                if not self.is_running:
                    break
                try:
                    name_valid, sanitized_name, _ = self.security_validator.sanitize_filename(folder)
                    if not name_valid:
                        continue
                    path = self.target_dir / sanitized_name
                    success, msg = SafeFileOperations.safe_create_directory(path)
                    if success:
                        created += 1
                        self.progress_signal.emit(int(((i + 1) / total) * 100), f"Creating {sanitized_name}")
                        self.log_signal.emit(f"[OK] Created: {sanitized_name}")
                    else:
                        errors.append(msg)
                except Exception as e:
                    errors.append(str(e))
            self.finished_signal.emit(len(errors) == 0, created, errors)
        except Exception as e:
            self.finished_signal.emit(False, 0, [str(e)])

    def stop(self):
        self.is_running = False
