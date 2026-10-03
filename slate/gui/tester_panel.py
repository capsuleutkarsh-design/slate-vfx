"""
Tester Panel: tools for checking Slate itself.

Everything that writes works inside one shared test folder (chosen above the
tabs), and everything destructive - wiping a folder, changing file dates -
only acts on a folder this panel created: the generators leave a
'.slate_tester' marker in it. Before, Cancel on a Browse dialog left the target
empty, which Path() reads as '.', and Wipe then offered to delete the folder
Slate was running from. The destructive tools, the large generators and VACUUM
also need the tester_destructive ability (developers).
"""

import os
import random
import string
import shutil
import logging
import time
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import DB for verification
from ..core.infra.database_manager import database_manager
from ..core.infra.global_config import GlobalConfig
from ..core.infra.app_context import AppContext
from ..core.domain import access

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QSpinBox, QComboBox, QCheckBox, QLineEdit,
    QProgressBar, QFileDialog, QGroupBox, QFormLayout, QPlainTextEdit,
    QMessageBox, QRadioButton, QButtonGroup, QTableWidget,
    QTableWidgetItem, QDateEdit, QScrollArea, QSplitter, QFrame,
    QApplication
)

from PySide6.QtCore import Qt, Signal, QThread, QObject, QDate, QTime, QDateTime, QUrl
from PySide6.QtGui import QDesktopServices
from ..core.domain.asset_ingestor import IngestWorker
from slate.gui.core.offline_notice import on_database_error
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.table_style import style_table

# Let an outage reach the @on_database_error decorator rather than becoming an
# empty grid here. Everything else keeps the fallback it already had.
try:
    from slate.core.infra.postgres_manager import DatabaseUnavailableError
except ImportError:                                  # pragma: no cover
    class DatabaseUnavailableError(ConnectionError):
        """Fallback when the manager cannot be imported."""


MARKER = ".slate_tester"
WINDOWS_PATH_LIMIT = 259          # MAX_PATH less the terminating character
CONFIRM_ABOVE_BYTES = 5 * 1024 ** 3
KEEP_FREE_FRACTION = 0.10
# Above this a generator run counts as large: it needs tester_destructive.
LARGE_RUN_BYTES = 1024 ** 3
SIZES = {"Empty": 0, "1KB": 1024, "1MB": 1024 * 1024, "50MB": 50 * 1024 * 1024,
         "Random": 10 * 1024 * 1024}       # Random is up to 10 MB a file
PATH_TOO_LONG = ("The folder path became longer than Windows allows (260 characters). "
                 "Choose a shorter test folder, or fewer levels.")


def default_test_folder() -> Path:
    return Path.home() / "Downloads" / "TesterData"


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} TB"


def validate_test_folder(text) -> tuple:
    """
    (path, reason): a folder the panel may write into, or None and why not.
    An empty or relative path, a drive root, and Slate's own folder are
    refused.
    """
    raw = str(text or "").strip()
    if not raw:
        return None, "Choose a test folder first."
    path = Path(raw)
    if not path.is_absolute():
        return None, "The test folder must be a full path, such as C:\\Users\\you\\Downloads\\TesterData."
    resolved = Path(os.path.abspath(raw))
    if resolved.parent == resolved:
        return None, "A whole drive cannot be the test folder - choose a folder inside it."
    install = Path(os.path.abspath(Path(__file__).resolve().parents[2]))
    try:
        resolved.relative_to(install)
        return None, "The test folder cannot be inside Slate's own folder."
    except ValueError:
        pass
    try:
        install.relative_to(resolved)
        return None, "The test folder cannot contain Slate's own folder."
    except ValueError:
        pass
    if resolved == Path(os.path.abspath(Path.home())):
        return None, "Your home folder cannot be the test folder - choose a folder inside it."
    return resolved, ""


def mark_folder(folder: Path) -> None:
    """Leave the marker that says this panel created the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / MARKER
    if not marker.exists():
        marker.write_text("Created by Slate's Tester Panel. The panel's destructive tools "
                          "only act on folders holding this file.\n", encoding="utf-8")


def is_marked(folder: Path) -> bool:
    return (Path(folder) / MARKER).is_file()


def count_files(folder: Path) -> int:
    total = 0
    for _root, _dirs, files in os.walk(folder):
        total += sum(1 for name in files if name != MARKER)
    return total


def free_space_check(folder: Path, needed: int):
    """
    (ok, ask, message): refuse a run that would take the disk below 10 % free
    (or not fit at all), ask above 5 GB. A disk already under 10 % is not a
    reason to refuse a run that writes nothing, or one that fits without
    crossing anything further.
    """
    if needed <= 0:
        return True, False, ""
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return True, needed > CONFIRM_ABOVE_BYTES, ""
    limit = usage.total * KEEP_FREE_FRACTION
    crosses = usage.free >= limit and usage.free - needed < limit
    if needed > usage.free:
        return False, False, (f"This would write {human_size(needed)}, but the disk has only "
                              f"{human_size(usage.free)} free - it does not fit. Make it smaller.")
    if crosses:
        return False, False, (f"This would write {human_size(needed)}, leaving less than 10% of the "
                              f"disk free ({human_size(usage.free)} free now). Make it smaller.")
    return True, needed > CONFIRM_ABOVE_BYTES, ""


def max_nesting(root: Path, segment: str = "Deep_Level_100", tail: str = "") -> int:
    """How many nested folders fit under root before Windows' path limit."""
    room = WINDOWS_PATH_LIMIT - len(str(root)) - len(tail)
    return max(0, room // (len(segment) + 1))


def _is_path_too_long(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 206 or exc.errno in (36, 2) and \
        len(str(getattr(exc, "filename", "") or "")) >= WINDOWS_PATH_LIMIT - 10


# --- UTILS ---
class QTextEditHandler(logging.Handler, QObject):
    """Custom Logging Handler to emit signals to a text view."""
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

    def __init__(self, target_path, count, size_strategy, file_types, name_template=None):
        super().__init__()
        self.target_path = Path(target_path)
        self.count = count
        self.size_strategy = size_strategy # "Empty", "1KB", "1MB", "50MB", "Random"
        self.file_types = list(file_types or [])
        # e.g. "regression_{i:04d}_still{ext}" - names without trailing digits
        # are not grouped into sequences by an ingest.
        self.name_template = name_template
        self._is_running = True
        self.created = 0
        self.errors = []

    def run(self):
        try:
            mark_folder(self.target_path)
        except OSError as exc:
            self.errors.append(str(exc))
            self.finished_signal.emit()
            return

        for i in range(self.count):
            if not self._is_running or self.isInterruptionRequested():
                break

            ext = random.choice(self.file_types)
            if self.name_template:
                name = self.name_template.format(i=i, ext=ext)
            else:
                name = f"dummy_file_{i:04d}_{self._random_string(5)}{ext}"
            file_path = self.target_path / name

            try:
                self._create_file(file_path)
                self.created += 1
                self.log_signal.emit(f"Created: {name}")
            except Exception as e:
                self.errors.append(f"{name}: {e}")
                self.log_signal.emit(f"Could not create {name}: {e}")

            progress = int((i + 1) / self.count * 100)
            self.progress_signal.emit(progress)

        self.finished_signal.emit()

    def stop(self):
        self._is_running = False
        self.requestInterruption()

    def _random_string(self, length):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def _create_file(self, path):
        size = 0
        if self.size_strategy == "1KB": size = 1024
        elif self.size_strategy == "1MB": size = 1024 * 1024
        elif self.size_strategy == "50MB": size = 50 * 1024 * 1024
        elif self.size_strategy == "Random": size = random.randint(1024, 10 * 1024 * 1024)

        # Seeking to the end and writing one byte is quick, but it is not a
        # sparse file: NTFS reserves the whole size on disk.
        with open(path, "wb") as f:
            if size > 0:
                f.seek(size - 1)
                f.write(b"\0")


class StructureWorker(QThread):
    finished_signal = Signal(str)

    def __init__(self, target_path, nesting_level, folder_count):
        super().__init__()
        self.target_path = Path(target_path)
        self.nesting_level = nesting_level
        self.folder_count = folder_count

    def run(self):
        try:
            mark_folder(self.target_path)
            created = 0

            current = self.target_path
            for i in range(self.nesting_level):
                if self.isInterruptionRequested():
                    break
                current = current / f"Deep_Level_{i+1}"
                current.mkdir(exist_ok=True)
                created += 1

            for i in range(self.folder_count):
                if self.isInterruptionRequested():
                    break
                (self.target_path / f"Wide_Folder_{i+1:03d}").mkdir(exist_ok=True)
                created += 1

            stopped = " (stopped early)" if self.isInterruptionRequested() else ""
            self.finished_signal.emit(f"Created {created} folders in {self.target_path}{stopped}.")
        except OSError as e:
            if _is_path_too_long(e):
                self.finished_signal.emit(f"Error: {PATH_TOO_LONG}")
            else:
                self.finished_signal.emit(f"Error: {e}")
        except Exception as e:
            self.finished_signal.emit(f"Error: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()


def workflow_base(root: Path, now: datetime = None) -> Path:
    """
    Where a test environment goes: 'TEST', or 'TEST_<date>_<time>' when a TEST
    folder is already there. An existing one used to be deleted without asking.
    """
    base = Path(root) / "TEST"
    if not base.exists():
        return base
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return Path(root) / f"TEST_{stamp}"


def workflow_longest_path(base: Path, nesting: int, scans: int, sequences: bool, count: int) -> int:
    client = Path(base) / "For_move" / "From_Client"
    for i in range(nesting):
        client = client / f"Level_{i + 1}"
    folder = client / ("CAP_RL02_SH_0005" + ("_ScanE" if scans > 1 else ""))
    name = f"CAP_RL02_SH_0005_v01.{1000 + count:04d}.exr" if sequences else f"CAP_RL02_SH_0005_v{count:03d}.exr"
    return len(str(folder / name))


class WorkflowWorker(QThread):
    finished_signal = Signal(str)

    def __init__(self, root_path, count=10, size_strategy="Empty", file_types=None, nesting_level=0,
                 scans_per_shot=1, create_sequences=False, base=None):
        super().__init__()
        self.root_path = Path(root_path)
        self.count = count # Files per shot (or frames per sequence)
        self.size_strategy = size_strategy
        self.file_types = file_types or [".mov"]
        self.nesting_level = nesting_level
        self.scans_per_shot = scans_per_shot
        self.create_sequences = create_sequences
        self.base = Path(base) if base else workflow_base(self.root_path)
        self.files_created = 0

        # Helper for file creation
        self.gen_worker = FileGeneratorWorker(Path("."), count, size_strategy, file_types)

    def run(self):
        try:
            mark_folder(self.root_path)
            base = self.base
            base.mkdir(parents=True, exist_ok=True)
            mark_folder(base)

            path_move = base / "For_move"
            path_client = path_move / "From_Client"
            path_dest = path_move / "Destination"
            path_rename = base / "FOR_RENAME"

            current_client_root = path_client
            for i in range(self.nesting_level):
                current_client_root = current_client_root / f"Level_{i+1}"

            current_client_root.mkdir(parents=True, exist_ok=True)
            path_dest.mkdir(parents=True, exist_ok=True)
            path_rename.mkdir(parents=True, exist_ok=True)

            data = []
            files_created = 0

            for r in range(1, 3):
                reel_name = f"REEL_{r:02d}"
                for s in range(1, 6):
                    if self.isInterruptionRequested():
                        break
                    shot_base_name = f"CAP_RL{r:02d}_SH_{s:04d}"
                    frames = random.randint(50, 200)
                    data.append({"SR NO": len(data)+1, "REEL": reel_name, "SHOT NO": shot_base_name, "FRAMES": frames})

                    for scan_idx in range(self.scans_per_shot):
                        if self.scans_per_shot > 1:
                            shot_folder_name = f"{shot_base_name}_Scan{chr(65+scan_idx)}"
                        else:
                            shot_folder_name = shot_base_name

                        shot_dir = current_client_root / shot_folder_name
                        shot_dir.mkdir(parents=True, exist_ok=True)

                        if self.create_sequences:
                            ext = next((t for t in self.file_types if t in ['.exr', '.jpg', '.png', '.dpx']), '.exr')
                            seq_name = f"{shot_base_name}_v01"
                            for i in range(self.count):
                                frame_num = 1001 + i
                                self.gen_worker._create_file(shot_dir / f"{seq_name}.{frame_num:04d}{ext}")
                                files_created += 1
                        else:
                            for i in range(self.count):
                                ext = random.choice(self.file_types)
                                self.gen_worker._create_file(shot_dir / f"{shot_base_name}_v{i+1:03d}{ext}")
                                files_created += 1

            df = pd.DataFrame(data)
            excel_path = path_move / "EXCEL_TEMPLATE.xlsx"
            df.to_excel(excel_path, index=False)

            for i in range(5):
                (path_rename / f"DCIM_{random.randint(1000,9999)}.JPG").touch()

            self.files_created = files_created
            msg = (
                f"Test environment ready: {files_created} files.\n\n"
                f"Folder: {base}\n"
                f"Client files: {current_client_root}\n"
                f"Levels: {self.nesting_level} | Scans per shot: {self.scans_per_shot} | "
                f"Sequences: {'yes' if self.create_sequences else 'no'} | Size: {self.size_strategy}"
            )
            self.finished_signal.emit(msg)

        except OSError as e:
            self.finished_signal.emit(f"Error: {PATH_TOO_LONG if _is_path_too_long(e) else e}")
        except Exception as e:
            self.finished_signal.emit(f"Error: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def find_ghosts(scope: Path, db_paths) -> list:
    """
    Files under scope the database does not know, relative to scope. Both
    sides are normalised: 'Z:/Show/a.EXR' in the database is 'z:\\show\\a.exr'
    on disk, and used to be reported as a ghost.
    """
    known = {_norm(p) for p in db_paths if p}
    ghosts = []
    for root, _dirs, files in os.walk(str(scope)):
        for name in files:
            if name == MARKER:
                continue
            full = Path(root) / name
            if _norm(full) not in known:
                ghosts.append(os.path.relpath(full, scope))
    return sorted(ghosts)


class ValidationWorker(QThread):
    finished_signal = Signal(str)

    def __init__(self, src_path, dst_path, verify_db=True, mode="raw"):
        super().__init__()
        self.src_path = Path(src_path)
        self.dst_path = Path(dst_path)
        self.verify_db = verify_db
        self.mode = mode # "raw", "smart" or "ghost"

    def run(self):
        report = ["<b>Analysis report</b>"]
        try:
            if self.mode in ("smart", "ghost"):
                target_scope = self.dst_path
                if self.mode == "smart": report.append("<b>Smart path check</b>")
                report.append(f"Folder: {target_scope}")

                if not target_scope.exists(): raise Exception(f"The folder was not found: {target_scope}")

                # Paths are compared with forward slashes, whichever way they
                # were saved, and the scope is passed as a parameter with
                # LIKE's wildcards escaped.
                scope = str(target_scope).replace("\\", "/").rstrip("/")
                pattern = (scope.replace("!", "!!").replace("%", "!%")
                           .replace("_", "!_") + "/%")
                query = (
                    "SELECT item_name, dest_path, file_size FROM task_details "
                    "WHERE REPLACE(dest_path, '\\', '/') ILIKE %s ESCAPE '!'"
                )
                report.append("Looking up the database…")
                tasks = database_manager.execute_query(query, (pattern,)) or []

                if self.mode == "smart":
                     if not tasks: report.append("The database has no ingest records for this folder.")
                     else:
                         report.append(f"The database expects {len(tasks)} files here.")
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

                         if not missing and not size_mismatch: report.append(f"<font color='{Gate.OK}'>Every file is there, at the right size.</font>")
                         else:
                             report.append(f"<font color='{Gate.BAD}'>Problems found</font>")
                             if missing: report.append(f"Missing: {len(missing)}")
                             if size_mismatch: report.append(f"Wrong size: {len(size_mismatch)}")

                elif self.mode == "ghost":
                     report.append("<b>Files missing from the database</b>")
                     ghosts = find_ghosts(target_scope, [t['dest_path'] for t in tasks])
                     if not ghosts: report.append(f"<font color='{Gate.OK}'>Every file in the folder is in the database.</font>")
                     else:
                         report.append(f"<font color='{Gate.BAD}'>{len(ghosts)} files are on disk but not in the database:</font>")
                         for g in ghosts[:10]: report.append(f" - {g}")
                         if len(ghosts) > 10: report.append(f"… and {len(ghosts)-10} more.")

            else:
                if not self.src_path.exists(): raise Exception("The source folder does not exist")
                if not self.dst_path.exists(): raise Exception("The destination folder does not exist")

                report.append(f"<b>Folder comparison</b><br>Source: {self.src_path}<br>Destination: {self.dst_path}")

                src_files = {f.name: f.stat().st_size for f in self.src_path.rglob('*') if f.is_file()}
                dst_files = {f.name: f.stat().st_size for f in self.dst_path.rglob('*') if f.is_file()}

                report.append(f"<br>Source files: {len(src_files)} | Destination files: {len(dst_files)}")

                missing = []
                corrupted = []

                for name, size in src_files.items():
                    if name not in dst_files:
                        missing.append(name)
                    elif dst_files[name] != size:
                        corrupted.append(f"{name} ({size} bytes in source, {dst_files[name]} in destination)")

                if not missing and not corrupted:
                    report.append(f"<font color='{Gate.OK}'>Files: every source file arrived intact.</font>")
                else:
                    report.append(f"<font color='{Gate.BAD}'>Files: problems found</font>")
                    if missing: report.append(f"  - Missing: {len(missing)} (e.g. {', '.join(missing[:3])})")
                    if corrupted: report.append(f"  - Different size: {len(corrupted)} (e.g. {'; '.join(corrupted[:3])})")

                if self.verify_db:
                    report.append("<br><b>Database and report check</b>")
                    op = database_manager.execute_query(
                        "SELECT id, project_id, created_at FROM operations ORDER BY id DESC LIMIT 1",
                        fetch="one",
                    )
                    if op:
                        report.append(f"Latest operation: {op['id']} ({op['created_at']})")
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
                            report.append(f"Only the latest {verify_limit:,} task rows were checked.")

                        db_ghosts = [task['item_name'] for task in tasks
                                     if task['status'] == 'Success' and task['dest_path']
                                     and not Path(task['dest_path']).exists()]
                        if not db_ghosts:
                            report.append(f"<font color='{Gate.OK}'>Report: every file it lists as done exists.</font>")
                        else:
                            report.append(f"<font color='{Gate.BAD}'>Report: {len(db_ghosts)} files listed as done are not on disk.</font>")
                    else:
                        report.append("The database has no operations yet.")

            self.finished_signal.emit("<br>".join(report))

        except DatabaseUnavailableError:
            # Raised inside a thread it killed the worker without a word.
            self.finished_signal.emit("The analysis stopped: the database is not reachable.")
        except Exception as e:
            self.finished_signal.emit(f"The analysis stopped: {e}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()


class FolderJobWorker(QThread):
    """Deletes a folder, or sets the dates of its files, off the UI thread."""
    done = Signal(int, list)

    def __init__(self, folder, job, timestamp=None):
        super().__init__()
        self.folder = Path(folder)
        self.job = job
        self.timestamp = timestamp

    def run(self):
        errors = []
        count = 0
        if self.job == "wipe":
            count = count_files(self.folder)

            def on_error(_func, path, exc_info):
                errors.append(f"{path}: {exc_info[1]}")
            shutil.rmtree(self.folder, onerror=on_error)
        else:
            for root, _dirs, files in os.walk(self.folder):
                for name in files:
                    if name == MARKER:
                        continue
                    try:
                        os.utime(os.path.join(root, name), (self.timestamp, self.timestamp))
                        count += 1
                    except OSError as e:
                        errors.append(f"{name}: {e}")
        self.done.emit(count, errors)


class DatabaseHealthWorker(QThread):
    """VACUUM or an integrity check, on SQLite or PostgreSQL."""
    done = Signal(bool, str)

    def __init__(self, job, db=None):
        super().__init__()
        self.job = job
        self.db = db or database_manager

    def _is_sqlite(self):
        backend = getattr(self.db, "backend", self.db)
        return "sqlite" in backend.__class__.__name__.lower()

    def run(self):
        try:
            if self.job == "vacuum":
                self.done.emit(*self.vacuum())
            else:
                self.done.emit(*self.integrity())
        except DatabaseUnavailableError:
            self.done.emit(False, "The database is not reachable.")
        except Exception as exc:
            self.done.emit(False, str(exc))

    def vacuum(self):
        with self.db.get_connection() as conn:
            if self._is_sqlite():
                conn.execute("VACUUM")
                return True, "The database file was compacted (VACUUM)."
            # PostgreSQL refuses VACUUM inside a transaction block.
            previous = conn.autocommit
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute("VACUUM (ANALYZE)")
            finally:
                conn.autocommit = previous
        return True, "VACUUM (ANALYZE) finished: space reclaimed and statistics refreshed."

    def integrity(self):
        with self.db.get_connection() as conn:
            if self._is_sqlite():
                row = conn.execute("PRAGMA integrity_check").fetchone()
                status = row[0] if row else "no answer"
                return status == "ok", ("Integrity check: OK." if status == "ok"
                                        else f"Integrity check found problems: {status}")
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'amcheck'")
                has_amcheck = cur.fetchone() is not None
                problems = []
                checked = 0
                if has_amcheck:
                    cur.execute(
                        "SELECT c.oid::regclass::text FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indexrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_am am ON am.oid = c.relam "
                        "WHERE n.nspname = 'public' AND am.amname = 'btree'")
                    for (index_name,) in cur.fetchall():
                        try:
                            cur.execute("SELECT bt_index_check(%s::regclass)", (index_name,))
                            checked += 1
                        except Exception as exc:
                            problems.append(f"{index_name}: {exc}")
                            conn.rollback()
                cur.execute("SELECT COALESCE(SUM(checksum_failures), 0) FROM pg_stat_database "
                            "WHERE datname = current_database()")
                failures = int((cur.fetchone() or [0])[0] or 0)
            conn.rollback()
        if failures:
            problems.append(f"{failures} page checksum failures reported by PostgreSQL")
        if problems:
            return False, "Integrity check found problems:\n" + "\n".join(problems)
        if has_amcheck:
            return True, f"Integrity check: OK ({checked} indexes checked with amcheck, no checksum failures)."
        return True, ("No checksum failures reported. The amcheck extension is not installed, so "
                      "indexes were not checked one by one.")


class _LiveLogView(QWidget):
    """The live log, attached to the root logger only while it is on screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        self.text.setStyleSheet(f"font-family: {Gate.FONT_MONO}; font-size: 11px;")
        layout.addWidget(self.text)
        self.handler = None

    def attach(self):
        if self.handler is not None:
            return
        self.handler = QTextEditHandler()
        self.handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.handler.log_signal.connect(self.text.appendPlainText)
        logging.getLogger().addHandler(self.handler)

    def detach(self):
        if self.handler is None:
            return
        logging.getLogger().removeHandler(self.handler)
        try:
            self.handler.log_signal.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.handler = None

    def showEvent(self, event):
        super().showEvent(event)
        self.attach()

    def hideEvent(self, event):
        # A tab never gets a close event, so the handler used to stay on the
        # root logger for the rest of the session.
        self.detach()
        super().hideEvent(event)


class TesterPanel(QWidget):
    __test__ = False

    def __init__(self, user_manager=None, app_context=None, roles=None):
        super().__init__()
        self.app_context = app_context or AppContext()
        self.user_manager = user_manager or self.app_context.user_manager()
        if roles is None:
            try:
                roles = self.app_context.current_roles()
            except Exception:
                roles = []
        self.roles = [roles] if isinstance(roles, str) else list(roles or [])
        self.can_destroy = access.can(self.roles, "tester_destructive")
        self.generation_worker: Optional[QThread] = None
        self.structure_worker: Optional[QThread] = None
        self.workflow_worker: Optional[QThread] = None
        self.analysis_worker: Optional[QThread] = None
        self.stress_worker: Optional[QThread] = None
        self.reg_gen: Optional[QThread] = None
        self.reg_ingest: Optional[QThread] = None
        self.reg_valid: Optional[QThread] = None
        self.folder_job: Optional[QThread] = None
        self.db_job: Optional[QThread] = None
        self._regression = {}
        self.setup_ui()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)

        # One test folder for every tab - several tabs used to read another
        # tab's field without saying so.
        top = QHBoxLayout()
        top.addWidget(QLabel("Test folder"))
        self.test_root = QLineEdit(str(default_test_folder()))
        self.test_root.setToolTip("Every tool writes here. Destructive tools only act on a folder "
                                  "this panel created.")
        self.test_root.textChanged.connect(self._test_folder_changed)
        top.addWidget(self.test_root, 1)
        btn_browse = make_button("Browse…", "secondary", on_click=self.browse_test_folder)
        top.addWidget(btn_browse)
        layout.addLayout(top)
        self.gen_path = self.test_root      # older name, kept for callers

        self.lbl_rights = QLabel(
            "" if self.can_destroy else
            "Wipe, Set file dates, large generator runs and VACUUM are for developers.")
        self.lbl_rights.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        self.lbl_rights.setVisible(not self.can_destroy)
        layout.addWidget(self.lbl_rights)

        self.sandbox_banner = QFrame()
        self.sandbox_banner.setObjectName("SandboxBanner")
        self.sandbox_banner.setStyleSheet(f"QFrame#SandboxBanner {{ background: {Gate.WARN_SURFACE}; "
                                          f"border: 1px solid {Gate.WARN}; border-radius: 5px; }}")
        banner_row = QHBoxLayout(self.sandbox_banner)
        self.lbl_sandbox = QLabel("")
        self.lbl_sandbox.setWordWrap(True)
        banner_row.addWidget(self.lbl_sandbox, 1)
        banner_row.addWidget(make_button("Reset", "secondary", on_click=self.reset_config_sandbox))
        self.sandbox_banner.hide()
        layout.addWidget(self.sandbox_banner)

        self.tabs = QTabWidget()
        pages = (
            (self.create_generator_tab, "Data generator"),
            (self.create_workflow_tab, "Workflow sim"),
            (self.create_structure_tab, "Deep folders"),
            (self.create_analyzer_tab, "Analysis"),
            (self.create_diagnostics_tab, "Diagnostics"),
            (self.create_system_tab, "System"),
            (self.create_automation_tab, "Automation"),
            (self.create_utils_tab, "Utilities"),
        )
        for build, title in pages:
            # Each page scrolls: at 1280x720 the fields overlapped and the
            # permission matrix collapsed to its header.
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(build())
            self.tabs.addTab(scroll, title)

        # Results below the tabs, in a pane that keeps its height.
        results = QWidget()
        rl = QVBoxLayout(results)
        rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Results")
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(make_button("Copy", "ghost", on_click=self.copy_log))
        head.addWidget(make_button("Save…", "ghost", on_click=self.save_log))
        head.addWidget(make_button("Clear", "ghost", on_click=self.clear_log))
        rl.addLayout(head)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumBlockCount(5000)
        self.log_area.setStyleSheet(f"font-family: {Gate.FONT_MONO};")
        rl.addWidget(self.log_area)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(results)
        self.splitter.setStretchFactor(0, 7)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)

        self._update_generator_state()
        self._test_folder_changed()

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return form

    @staticmethod
    def _spin(low, high, value, step=1):
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.setFixedWidth(110)
        return spin

    @staticmethod
    def _end_row(*widgets):
        row = QHBoxLayout()
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def _destructive(self, button, what):
        """Developers only: disabled, with the reason, for everybody else."""
        if not self.can_destroy:
            button.setEnabled(False)
            button.setToolTip(f"Only developers can {what}.")
        return button

    def closeEvent(self, event):
        """Cleanup running testing threads when panel closes."""
        for attr_name in ("generation_worker", "structure_worker", "workflow_worker",
                          "analysis_worker", "stress_worker", "reg_gen", "reg_ingest",
                          "reg_valid", "folder_job", "db_job"):
            self._cleanup_worker_attr(attr_name)
        self.live_log.detach()
        super().closeEvent(event)

    def _cleanup_worker_attr(self, attr_name, timeout_ms=3000):
        """Stop a worker thread safely and clear its owning attribute."""
        worker = getattr(self, attr_name, None)
        if worker is None:
            return

        try:
            running = hasattr(worker, "isRunning") and worker.isRunning()
        except RuntimeError:
            running = False
        if running:
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

    # ---------------------------------------------------------- test folder
    def browse_test_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Choose the test folder", self.test_root.text())
        # Cancel returns ''; the field keeps what it had.
        if chosen:
            self.test_root.setText(chosen)

    def test_folder(self, quiet=False):
        """The validated test folder, or None after saying why."""
        path, reason = validate_test_folder(self.test_root.text())
        if path is None and not quiet:
            QMessageBox.warning(self, "Test folder", reason)
        return path

    def _test_folder_changed(self, *_):
        path, _reason = validate_test_folder(self.test_root.text())
        if path is None or not hasattr(self, "spin_nest"):
            return
        limit = max_nesting(path, tail="\\Wide_Folder_000")
        self.spin_nest.setMaximum(max(0, min(100, limit)))
        self.lbl_nest_limit.setText(f"Up to {self.spin_nest.maximum()} levels fit in this folder.")

    def _marked_test_folder(self, action):
        """The test folder, only if this panel created it."""
        folder = self.test_folder()
        if folder is None:
            return None
        if not folder.exists():
            QMessageBox.information(self, action, f"{folder} does not exist - there is nothing to do.")
            return None
        if not is_marked(folder):
            QMessageBox.warning(
                self, action,
                f"{folder} was not created by the Tester Panel, so it is left alone.\n\n"
                f"Only folders holding the '{MARKER}' file this panel writes can be changed here.")
            return None
        return folder

    def _offer_open_folder(self, title, text, folder):
        box = QMessageBox(QMessageBox.Icon.Information, title, text, QMessageBox.StandardButton.Ok, self)
        open_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --------------------------------------------------------- generator tab
    def create_generator_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        config_box = QGroupBox("What to create"); cl = self._form(); config_box.setLayout(cl)

        self.spin_count = self._spin(1, 10000, 10, 10)
        self.spin_count.valueChanged.connect(self._update_generator_state)
        cl.addRow("Files:", self.spin_count)

        self.combo_size = QComboBox(); self.combo_size.addItems(list(SIZES))
        self.combo_size.setFixedWidth(110)
        self.combo_size.currentTextChanged.connect(self._update_generator_state)
        cl.addRow("Size of each:", self.combo_size)

        type_box = QGroupBox("File types"); tl = QHBoxLayout(type_box)
        self.chk_jpg = QCheckBox(".jpg"); self.chk_jpg.setChecked(True); tl.addWidget(self.chk_jpg)
        self.chk_mov = QCheckBox(".mov"); tl.addWidget(self.chk_mov)
        self.chk_exr = QCheckBox(".exr"); tl.addWidget(self.chk_exr)
        self.chk_txt = QCheckBox(".txt"); tl.addWidget(self.chk_txt)
        tl.addStretch(1)
        for box in (self.chk_jpg, self.chk_mov, self.chk_exr, self.chk_txt):
            box.toggled.connect(self._update_generator_state)
        l.addWidget(config_box); l.addWidget(type_box)

        self.lbl_gen_total = QLabel("")
        self.lbl_gen_total.setStyleSheet(f"color: {Gate.TEXT_2};")
        l.addWidget(self.lbl_gen_total)

        self.btn_gen = make_button("Generate files", "primary", on_click=self.toggle_generation)
        l.addLayout(self._end_row(self.btn_gen))

        self.prog_bar = QProgressBar(); self.prog_bar.setVisible(False); l.addWidget(self.prog_bar)
        l.addStretch()
        return w

    def generator_types(self):
        types = []
        if self.chk_jpg.isChecked(): types.append(".jpg")
        if self.chk_mov.isChecked(): types.append(".mov")
        if self.chk_exr.isChecked(): types.append(".exr")
        if self.chk_txt.isChecked(): types.append(".txt")
        return types

    def generator_bytes(self) -> int:
        return self.spin_count.value() * SIZES.get(self.combo_size.currentText(), 0)

    def _update_generator_state(self, *_):
        if not hasattr(self, "btn_gen"):
            return
        types = self.generator_types()
        total = self.generator_bytes()
        upto = "up to " if self.combo_size.currentText() == "Random" else ""
        self.lbl_gen_total.setText(f"{self.spin_count.value():,} files, {upto}{human_size(total)} in all."
                                   + ("" if types else " Tick at least one file type."))
        running = self.generation_worker is not None
        if not running:
            self.btn_gen.setEnabled(bool(types))
            self.btn_gen.setToolTip("" if types else "Tick at least one file type.")
        if hasattr(self, "btn_sim") and self.workflow_worker is None:
            self.btn_sim.setEnabled(bool(self.workflow_types()))

    def toggle_generation(self):
        if self.generation_worker is not None:
            self.generation_worker.stop()
            self.btn_gen.setEnabled(False)
            self.btn_gen.setText("Stopping…")
            return
        self.start_generation()

    def start_generation(self):
        path = self.test_folder()
        if path is None:
            return
        types = self.generator_types()
        if not types:
            return
        count = self.spin_count.value()
        size = self.combo_size.currentText()
        total = self.generator_bytes()
        if total > LARGE_RUN_BYTES and not self.can_destroy:
            QMessageBox.warning(self, "Generate files",
                                f"{human_size(total)} is a large run; only developers can generate "
                                f"more than {human_size(LARGE_RUN_BYTES)} at once.")
            return
        ok, ask, message = free_space_check(path, total)
        if not ok:
            QMessageBox.warning(self, "Generate files", message)
            return
        if ask and QMessageBox.question(
                self, "Generate files",
                f"This writes {human_size(total)} into {path}. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        self.btn_gen.setText("Stop")
        self.prog_bar.setVisible(True); self.prog_bar.setValue(0)
        self.log(f"Creating {count} files in {path}…")
        self._cleanup_worker_attr("generation_worker")

        self.generation_worker = FileGeneratorWorker(path, count, size, types)
        self.generation_worker.progress_signal.connect(self._on_generation_progress)
        self.generation_worker.log_signal.connect(self.log)
        self.generation_worker.finished_signal.connect(self.on_gen_finished)
        self.generation_worker.start()
        return self.generation_worker

    def _on_generation_progress(self, value):
        if self.sender() is not self.generation_worker:
            return
        self.prog_bar.setValue(value)

    def on_gen_finished(self):
        worker = self.sender()
        if not self._release_finished_worker("generation_worker", worker):
            return

        self.btn_gen.setText("Generate files")
        self.btn_gen.setEnabled(bool(self.generator_types()))
        self.prog_bar.setVisible(False)
        text = f"Created {worker.created} files in {worker.target_path}."
        if worker.errors:
            text += f"\n{len(worker.errors)} could not be created: {worker.errors[0]}"
        self.log(text)
        self._offer_open_folder("Generate files", text, worker.target_path)

    # ---------------------------------------------------------- workflow tab
    def create_workflow_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        info = QLabel("<b>Workflow simulator</b><br>Builds a 'For_move' incoming structure with "
                      "an Excel sheet, in a new TEST folder inside the test folder.")
        info.setWordWrap(True)
        l.addWidget(info)

        config_box = QGroupBox("Files"); cl = self._form(); config_box.setLayout(cl)

        self.wf_count = self._spin(1, 1000, 5)
        cl.addRow("Files per shot:", self.wf_count)

        self.wf_size = QComboBox(); self.wf_size.addItems(["Empty", "1KB", "1MB", "Random"])
        self.wf_size.setCurrentText("1KB")
        self.wf_size.setFixedWidth(110)
        cl.addRow("Size of each:", self.wf_size)

        type_w = QWidget(); tl = QHBoxLayout(type_w); tl.setContentsMargins(0,0,0,0)
        self.wf_chk_exr = QCheckBox(".exr"); self.wf_chk_exr.setChecked(True); tl.addWidget(self.wf_chk_exr)
        self.wf_chk_mov = QCheckBox(".mov"); tl.addWidget(self.wf_chk_mov)
        self.wf_chk_jpg = QCheckBox(".jpg"); tl.addWidget(self.wf_chk_jpg)
        for box in (self.wf_chk_exr, self.wf_chk_mov, self.wf_chk_jpg):
            box.toggled.connect(self._update_generator_state)
        cl.addRow("Types:", type_w)

        l.addWidget(config_box)

        complex_box = QGroupBox("Complications"); cxl = self._form(); complex_box.setLayout(cxl)

        self.wf_nesting = self._spin(0, 5, 0)
        self.wf_nesting.setToolTip("Puts the shot folders this many levels deep (Level_1/Level_2/...)")
        cxl.addRow("Extra folder levels:", self.wf_nesting)

        self.wf_multiscan = self._spin(1, 5, 1)
        self.wf_multiscan.setToolTip("Several folders per shot (Shot_ScanA, Shot_ScanB...)")
        cxl.addRow("Scans per shot:", self.wf_multiscan)

        self.wf_seq = QCheckBox("Image sequences")
        self.wf_seq.setToolTip("Frames 1001-10xx instead of versioned files.")
        cxl.addRow("", self.wf_seq)

        l.addWidget(complex_box)

        self.btn_sim = make_button("Create test environment", "primary", on_click=self.toggle_workflow)
        l.addLayout(self._end_row(self.btn_sim))

        l.addStretch()
        return w

    def workflow_types(self):
        types = []
        if self.wf_chk_exr.isChecked(): types.append(".exr")
        if self.wf_chk_mov.isChecked(): types.append(".mov")
        if self.wf_chk_jpg.isChecked(): types.append(".jpg")
        return types

    def toggle_workflow(self):
        if self.workflow_worker is not None:
            self.workflow_worker.stop()
            self.btn_sim.setEnabled(False)
            self.btn_sim.setText("Stopping…")
            return
        self.start_workflow_sim()

    def start_workflow_sim(self):
        path = self.test_folder()
        if path is None:
            return
        types = self.workflow_types()
        if not types:
            return
        count = self.wf_count.value()
        size = self.wf_size.currentText()
        nesting = self.wf_nesting.value()
        scans = self.wf_multiscan.value()
        seq = self.wf_seq.isChecked()

        base = workflow_base(path)
        if workflow_longest_path(base, nesting, scans, seq, count) > WINDOWS_PATH_LIMIT:
            QMessageBox.warning(self, "Create test environment", PATH_TOO_LONG)
            return

        self.log(f"Building a test environment in {base}: {count} x {size}, levels {nesting}, "
                 f"scans {scans}, sequences {'yes' if seq else 'no'}…")
        self._cleanup_worker_attr("workflow_worker")

        self.workflow_worker = WorkflowWorker(path, count, size, types, nesting, scans, seq, base=base)
        self.workflow_worker.finished_signal.connect(self._on_workflow_finished)
        self.btn_sim.setText("Stop")
        self.workflow_worker.start()
        return self.workflow_worker

    def _on_workflow_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("workflow_worker", worker):
            return
        self.btn_sim.setText("Create test environment")
        self.btn_sim.setEnabled(bool(self.workflow_types()))
        self.log(msg)
        if msg.startswith("Error"):
            QMessageBox.warning(self, "Create test environment", msg[len("Error: "):])
        else:
            self._offer_open_folder("Create test environment", msg, worker.base)

    # ---------------------------------------------------------- analysis tab
    def create_analyzer_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        mode_box = QGroupBox("What to check"); ml = QHBoxLayout(mode_box)
        self.rb_smart = QRadioButton("Against the database (recommended)"); ml.addWidget(self.rb_smart)
        self.rb_raw = QRadioButton("Compare two folders"); ml.addWidget(self.rb_raw)
        self.rb_ghost = QRadioButton("Files missing from the database"); ml.addWidget(self.rb_ghost)
        ml.addStretch(1)
        self.rb_smart.setChecked(True)
        l.addWidget(mode_box)

        self.bg_mode = QButtonGroup(self)
        for rb in (self.rb_smart, self.rb_raw, self.rb_ghost):
            self.bg_mode.addButton(rb)

        input_box = QGroupBox("Folders"); il = QVBoxLayout(input_box)
        il.addWidget(QLabel("Folder to check:"))
        self.an_dst = QLineEdit()
        btn_dst = make_button("Browse…", "secondary", on_click=lambda: self._browse_into(self.an_dst))
        il.addLayout(self._row(self.an_dst, btn_dst))

        self.lbl_src = QLabel("Source folder (what it was copied from):")
        self.an_src = QLineEdit()
        self.btn_src = make_button("Browse…", "secondary", on_click=lambda: self._browse_into(self.an_src))
        il.addWidget(self.lbl_src)
        il.addLayout(self._row(self.an_src, self.btn_src))
        l.addWidget(input_box)

        self.bg_mode.buttonClicked.connect(self.update_analyzer_ui)
        self.update_analyzer_ui()

        btn_an = make_button("Analyse", "primary", on_click=self.start_analysis)
        l.addLayout(self._end_row(btn_an))

        db_box = QGroupBox("Database health"); dbl = QHBoxLayout(db_box)
        self.btn_vac = self._destructive(
            make_button("Run VACUUM", "secondary", on_click=self.run_db_vacuum,
                        tooltip="Reclaim space and refresh statistics - heavy on a busy database"),
            "run VACUUM")
        self.btn_chk = make_button("Integrity check", "secondary", on_click=self.run_db_integrity)
        dbl.addWidget(self.btn_vac); dbl.addWidget(self.btn_chk); dbl.addStretch(1)
        l.addWidget(db_box)

        l.addStretch()
        return w

    @staticmethod
    def _row(field, button):
        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(button)
        return row

    def _browse_into(self, field):
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", field.text())
        if chosen:
            field.setText(chosen)

    def _run_db_job(self, job):
        if self.db_job is not None:
            return None
        self.db_job = DatabaseHealthWorker(job)
        self.db_job.done.connect(self._on_db_job_done)
        self.btn_vac.setEnabled(False); self.btn_chk.setEnabled(False)
        self.log("Running VACUUM…" if job == "vacuum" else "Checking the database…")
        self.db_job.start()
        return self.db_job

    def run_db_vacuum(self):
        if not self.can_destroy:
            return None
        return self._run_db_job("vacuum")

    def run_db_integrity(self):
        return self._run_db_job("integrity")

    def _on_db_job_done(self, ok, message):
        worker = self.sender()
        if not self._release_finished_worker("db_job", worker):
            return
        self.btn_vac.setEnabled(self.can_destroy); self.btn_chk.setEnabled(True)
        self.log(message)
        title = "Database health"
        if ok:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def update_analyzer_ui(self, *_):
        is_raw = self.rb_raw.isChecked()
        self.lbl_src.setVisible(is_raw)
        self.an_src.setVisible(is_raw)
        self.btn_src.setVisible(is_raw)

        if is_raw:
            self.an_src.setPlaceholderText("Where the files were copied from…")
            self.an_dst.setPlaceholderText("Where the files were copied to…")
        else:
            self.an_dst.setPlaceholderText("Folder to check against the database (e.g. Z:/Show/Reel_01)…")

    # -------------------------------------------------------- structure tab
    def create_structure_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        info = QLabel("Deep and wide folder trees, to check that ingest copes with them.")
        info.setWordWrap(True)
        l.addWidget(info)
        form_box = QGroupBox("Folders"); fl = self._form(); form_box.setLayout(fl)
        self.spin_nest = self._spin(0, 100, 10)
        fl.addRow("Levels deep:", self.spin_nest)
        self.lbl_nest_limit = QLabel("")
        self.lbl_nest_limit.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        fl.addRow("", self.lbl_nest_limit)

        self.spin_folders = self._spin(0, 1000, 50)
        fl.addRow("Folders side by side:", self.spin_folders)
        l.addWidget(form_box)

        self.btn_structure = make_button("Create folders", "primary", on_click=self.toggle_structure)
        l.addLayout(self._end_row(self.btn_structure))
        l.addStretch()
        return w

    def toggle_structure(self):
        if self.structure_worker is not None:
            self.structure_worker.stop()
            return
        self.start_structure()

    def start_structure(self):
        path = self.test_folder()
        if path is None:
            return
        target = path / "Deep_folders"
        self.log(f"Creating folders in {target}…")
        self._cleanup_worker_attr("structure_worker")

        self.structure_worker = StructureWorker(target, self.spin_nest.value(), self.spin_folders.value())
        self.structure_worker.finished_signal.connect(self._on_structure_finished)
        self.btn_structure.setText("Stop")
        self.structure_worker.start()
        return self.structure_worker

    def _on_structure_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("structure_worker", worker):
            return
        self.btn_structure.setText("Create folders")
        self.log(msg)
        if msg.startswith("Error"):
            QMessageBox.warning(self, "Deep folders", msg[len("Error: "):])
        else:
            self._offer_open_folder("Deep folders", msg, worker.target_path)

    # -------------------------------------------------------- utilities tab
    def create_utils_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        info = QLabel("Deletes the test folder and everything in it - only when the Tester Panel "
                      f"created it (it holds a '{MARKER}' file).")
        info.setWordWrap(True)
        l.addWidget(info)
        self.btn_wipe = self._destructive(
            make_button("Delete test folder…", "danger", on_click=self.wipe_folder), "delete the test folder")
        l.addLayout(self._end_row(self.btn_wipe))
        l.addStretch()
        return w

    # ------------------------------------------------------ diagnostics tab
    def create_diagnostics_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        log_group = QGroupBox("Live log"); ll = QVBoxLayout(log_group)
        self.live_log = _LiveLogView()
        self.live_log_text = self.live_log.text
        ll.addWidget(self.live_log)
        l.addWidget(log_group, 2)

        action_group = QGroupBox("Load"); al = QVBoxLayout(action_group)
        al.addWidget(QLabel("Writes 1,000 small .jpg files (1 KB each) into the test folder - "
                            "for watching how browsing and ingest cope. It does not make thumbnails."))
        btn_thumb = make_button("Create 1,000 small files", "secondary", on_click=self.run_thumb_stress)
        al.addLayout(self._end_row(btn_thumb))
        l.addWidget(action_group)

        # Crashing the program on purpose is not a sibling of a harmless
        # button: its own group, developers only.
        self.crash_group = QGroupBox("Developer only"); cl = QVBoxLayout(self.crash_group)
        cl.addWidget(QLabel("Raises an error on purpose to test the crash reporter. Slate closes."))
        self.btn_crash = make_button("Simulate crash", "danger", on_click=self.simulate_crash)
        cl.addLayout(self._end_row(self.btn_crash))
        self.crash_group.setVisible(self.can_destroy)
        l.addWidget(self.crash_group)

        return w

    def simulate_crash(self):
        if not self.can_destroy:
            return
        if QMessageBox.warning(self, "Simulate crash",
                               "This closes Slate with an error, to test the crash reporter. Continue?",
                               QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                               QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            raise ValueError("Intentional Crash Triggered from Tester Panel.")

    def run_thumb_stress(self):
        path = self.test_folder()
        if path is None:
            return
        self.log("Creating 1,000 small files…")
        self._cleanup_worker_attr("stress_worker")
        self.stress_worker = FileGeneratorWorker(path / "Small_files", 1000, "1KB", [".jpg"])
        self.stress_worker.progress_signal.connect(self._on_stress_progress)
        self.stress_worker.finished_signal.connect(self._on_stress_finished)
        self.stress_worker.start()
        return self.stress_worker

    def _on_stress_progress(self, value):
        if self.sender() is not self.stress_worker:
            return
        if value % 25 == 0:
            self.log(f"Small files: {value}%")

    def _on_stress_finished(self):
        worker = self.sender()
        if not self._release_finished_worker("stress_worker", worker):
            return
        self.log(f"Created {worker.created} small files in {worker.target_path}.")

    # -------------------------------------------------------- automation tab
    def create_automation_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        info = QLabel("<b>Regression run</b><br>Creates 50 test images in a new folder inside the "
                      "test folder, ingests them into the stock library, checks that every one "
                      "arrived, then removes the test rows again.")
        info.setWordWrap(True)
        l.addWidget(info)

        self.btn_reg = make_button("Run regression", "primary", on_click=self.run_regression)
        l.addLayout(self._end_row(self.btn_reg))

        l.addStretch()
        return w

    REGRESSION_FILES = 50

    def run_regression(self):
        root = self.test_folder()
        if root is None:
            return None
        target = root / f"Regression_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._regression = {"target": target, "expected": self.REGRESSION_FILES}

        self.log(f"Regression: creating {self.REGRESSION_FILES} images in {target}…")
        self._cleanup_worker_attr("reg_gen")
        self._cleanup_worker_attr("reg_ingest")
        self._cleanup_worker_attr("reg_valid")
        self.btn_reg.setEnabled(False)

        # Names without trailing digits, so the ingest keeps them as stills.
        self.reg_gen = FileGeneratorWorker(target, self.REGRESSION_FILES, "1KB", [".jpg"],
                                           name_template="regression_{i:04d}_still{ext}")
        self.reg_gen.finished_signal.connect(self._reg_step_2_ingest)
        self.reg_gen.start()
        return self.reg_gen

    def _reg_step_2_ingest(self):
        worker = self.sender()
        if not self._release_finished_worker("reg_gen", worker):
            return
        self._regression["created"] = worker.created
        self.log(f"Step 1: {worker.created} images created. Ingesting…")
        self._cleanup_worker_attr("reg_ingest")
        # The ingest writes the stock library itself (the Stock Viewer's own
        # writer); the check below reads what it wrote.
        self.reg_ingest = IngestWorker(str(self._regression["target"]))
        self.reg_ingest.finished_signal.connect(self._reg_step_3_verify)
        self.reg_ingest.start()

    @staticmethod
    def _stock_pattern(target: Path) -> str:
        scope = str(target).replace("\\", "/").rstrip("/").lower()
        return scope.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "/%"

    def count_stock_rows(self, target: Path) -> int:
        row = database_manager.execute_query(
            "SELECT COUNT(*) AS n FROM stock_library "
            "WHERE LOWER(REPLACE(file_path, '\\', '/')) LIKE %s ESCAPE '!'",
            (self._stock_pattern(target),), fetch="one")
        if not row:
            return 0
        return int(row["n"] if isinstance(row, dict) else row[0])

    def remove_stock_rows(self, target: Path) -> None:
        database_manager.execute_update(
            "DELETE FROM stock_library WHERE LOWER(REPLACE(file_path, '\\', '/')) LIKE %s ESCAPE '!'",
            (self._stock_pattern(target),))

    def _reg_step_3_verify(self, success=True, message=""):
        worker = self.sender()
        if not self._release_finished_worker("reg_ingest", worker):
            return
        target = self._regression.get("target")
        expected = self._regression.get("created", 0)
        try:
            found = self.count_stock_rows(target) if success else 0
        except Exception as exc:
            found = 0
            success, message = False, f"the stock library could not be read: {exc}"
        try:
            self.remove_stock_rows(target)
        except Exception as exc:
            logging.warning("Regression rows were not removed: %s", exc)
        passed = bool(success) and expected == self.REGRESSION_FILES and found == expected
        self._regression["found"] = found
        self._reg_finish(passed, expected, found, message)

    def _reg_finish(self, passed, expected, found, message=""):
        self.btn_reg.setEnabled(True)
        if passed:
            text = (f"Regression passed: {found} of {expected} images reached the stock library. "
                    f"The test rows were removed again.")
            self.log(text)
            QMessageBox.information(self, "Regression passed", text)
        else:
            text = f"Regression failed: {found} of {expected} images reached the stock library."
            if message:
                text += f"\nThe ingest said: {message}"
            self.log(text)
            QMessageBox.warning(self, "Regression failed", text)
        self._regression["passed"] = passed

    # ------------------------------------------------------------ system tab
    def create_system_tab(self):
        w = QWidget(); l = QVBoxLayout(w)

        perm_box = QGroupBox("Permission matrix"); pl = QVBoxLayout(perm_box)
        self.perm_table = QTableWidget()
        self.perm_table.setMinimumHeight(240)
        pl.addWidget(self.perm_table)
        btn_refresh_perm = make_button("Refresh", "secondary", on_click=self.load_permissions)
        pl.addLayout(self._end_row(btn_refresh_perm))
        l.addWidget(perm_box, 1)
        self.load_permissions()

        conf_box = QGroupBox("Config sandbox (this session only)"); cl = self._form(); conf_box.setLayout(cl)
        self.conf_path = QLineEdit(str(GlobalConfig.server_root()))
        self.conf_path.setMinimumWidth(420)
        cl.addRow("Server root:", self.conf_path)
        self.conf_cache = QLineEdit(str(GlobalConfig.local_cache_dir()))
        self.conf_cache.setMinimumWidth(420)
        cl.addRow("Local cache:", self.conf_cache)
        cl.addRow("", self._end_row(
            make_button("Apply for this session", "secondary", on_click=self.apply_config_sandbox),
            make_button("Reset", "ghost", on_click=self.reset_config_sandbox)))
        l.addWidget(conf_box)

        time_box = QGroupBox("Set file dates"); tl = QHBoxLayout(time_box)
        tl.addWidget(QLabel("Set every file in the test folder to"))
        self.time_date = QDateEdit(); self.time_date.setCalendarPopup(True)
        self.time_date.setDisplayFormat("yyyy-MM-dd")
        self.time_date.setDate(QDate.currentDate().addDays(-365))
        tl.addWidget(self.time_date)
        self.btn_time = self._destructive(
            make_button("Set file dates…", "secondary", on_click=self.run_time_travel), "change file dates")
        tl.addWidget(self.btn_time)
        tl.addStretch(1)
        l.addWidget(time_box)

        return w

    @staticmethod
    def permission_rows(roles_config):
        """
        (role, {tab label: bool}, abilities text) per role. The matrix printed
        Python lists such as "['ALL', 'Admin Panel']" with internal 'can:' tokens.
        """
        from ..core.domain import permissions_catalog as pc
        labels = {a.key: a.label for a in pc.ABILITIES}
        rows = []
        for role, perms in sorted((roles_config or {}).items(), key=lambda kv: kv[0].lower()):
            perms = list(perms or [])
            full = pc.has_all(perms)
            tabs = {t.label: full or t.key in perms for t in pc.TABS}
            abilities = sorted(labels.get(a, a) for a in pc.abilities_in(perms))
            text = "Full access" if full else ", ".join(abilities)
            rows.append((role, tabs, text))
        return rows

    @on_database_error
    def load_permissions(self):
        from ..core.domain import permissions_catalog as pc
        rows = self.permission_rows(self.user_manager.roles_config)
        headers = ["Role"] + [t.label for t in pc.TABS] + ["Abilities"]
        self.perm_table.clear()
        self.perm_table.setColumnCount(len(headers))
        self.perm_table.setHorizontalHeaderLabels(headers)
        self.perm_table.setRowCount(len(rows))
        for i, (role, tabs, abilities) in enumerate(rows):
            self.perm_table.setItem(i, 0, QTableWidgetItem(role))
            for c, label in enumerate(headers[1:-1], start=1):
                item = QTableWidgetItem("✓" if tabs.get(label) else "")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.perm_table.setItem(i, c, item)
            item = QTableWidgetItem(abilities)
            item.setToolTip(abilities)
            self.perm_table.setItem(i, len(headers) - 1, item)
        style_table(self.perm_table, {"Role": "contents", "Abilities": ("interactive", 320)})

    def apply_config_sandbox(self):
        """Point this session at another server root / cache. Nothing is saved."""
        server = self.conf_path.text().strip()
        cache = self.conf_cache.text().strip()
        if server:
            GlobalConfig.set_runtime_override("SERVER_ROOT", server)
        if cache:
            GlobalConfig.set_runtime_override("LOCAL_CACHE_DIR", cache)
        self.log(f"Sandbox: server root {server or '(unchanged)'}, local cache {cache or '(unchanged)'}")
        self._show_sandbox_banner()

    def reset_config_sandbox(self):
        GlobalConfig.clear_runtime_overrides()
        self.conf_path.setText(str(GlobalConfig.server_root()))
        self.conf_cache.setText(str(GlobalConfig.local_cache_dir()))
        self.log("Sandbox: overrides cleared.")
        self._show_sandbox_banner()

    def _show_sandbox_banner(self):
        overrides = GlobalConfig.runtime_overrides()
        if not overrides:
            self.sandbox_banner.hide()
            return
        parts = []
        if "SERVER_ROOT" in overrides:
            parts.append(f"server root {overrides['SERVER_ROOT']}")
        if "LOCAL_CACHE_DIR" in overrides:
            parts.append(f"local cache {overrides['LOCAL_CACHE_DIR']}")
        self.lbl_sandbox.setText("Sandbox override active for this session: " + "; ".join(parts)
                                 + ". Live Ops, logs and commands use it until you reset or restart.")
        self.sandbox_banner.show()

    def run_time_travel(self):
        """Set the dates of the files in the test folder, after saying how many and to what."""
        if not self.can_destroy:
            return None
        folder = self._marked_test_folder("Set file dates")
        if folder is None:
            return None
        new_date = self.time_date.date()
        count = count_files(folder)
        if QMessageBox.question(
                self, "Set file dates",
                f"Set the date of {count:,} files in {folder} to {new_date.toString('yyyy-MM-dd')}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return None
        ts = QDateTime(new_date, QTime(12, 0, 0)).toSecsSinceEpoch()
        return self._start_folder_job(folder, "dates", ts)

    def wipe_folder(self):
        """Delete the test folder - only one this panel created."""
        if not self.can_destroy:
            return None
        folder = self._marked_test_folder("Delete test folder")
        if folder is None:
            return None
        if not self._confirm_wipe(folder, count_files(folder)):
            return None
        return self._start_folder_job(folder, "wipe")

    def _confirm_wipe(self, folder, count) -> bool:
        """Says the path and how many files; Cancel is the default."""
        from .components.feedback import confirm
        return confirm(self, "Delete test folder",
                       f"Delete {count:,} files in {folder}?\n\nThis cannot be undone.",
                       yes_label="Delete", destructive=True)

    def _start_folder_job(self, folder, job, timestamp=None):
        if self.folder_job is not None:
            return None
        self.folder_job = FolderJobWorker(folder, job, timestamp)
        self.folder_job.done.connect(self._on_folder_job_done)
        self.log(("Deleting " if job == "wipe" else "Setting file dates in ") + str(folder) + "…")
        self.folder_job.start()
        return self.folder_job

    def _on_folder_job_done(self, count, errors):
        worker = self.sender()
        if not self._release_finished_worker("folder_job", worker):
            return
        title = "Delete test folder" if worker.job == "wipe" else "Set file dates"
        done = (f"Deleted {count:,} files in {worker.folder}." if worker.job == "wipe"
                else f"Set the date of {count:,} files in {worker.folder}.")
        self.log(done)
        if errors:
            self.log("\n".join(errors[:50]))
            QMessageBox.warning(self, title, f"{done}\n\n{len(errors)} could not be changed, "
                                             f"for example:\n{errors[0]}")
        else:
            QMessageBox.information(self, title, done)

    # --------------------------------------------------------------- actions
    def start_analysis(self):
        dst = self.an_dst.text().strip()
        src = self.an_src.text().strip()

        if self.rb_smart.isChecked(): mode = "smart"
        elif self.rb_ghost.isChecked(): mode = "ghost"
        else: mode = "raw"

        if not dst:
             QMessageBox.warning(self, "Analyse", "Choose the folder to check.")
             return
        if mode == "raw" and not src:
             QMessageBox.warning(self, "Analyse", "Comparing two folders needs the source folder as well.")
             return

        self.log(f"Analysing {dst}…")
        self._cleanup_worker_attr("analysis_worker")

        self.analysis_worker = ValidationWorker(src, dst, verify_db=True, mode=mode)
        self.analysis_worker.finished_signal.connect(self._on_analysis_finished)
        self.analysis_worker.start()
        return self.analysis_worker

    def _on_analysis_finished(self, msg):
        worker = self.sender()
        if not self._release_finished_worker("analysis_worker", worker):
            return
        self.log(msg.replace("<br>", "\n"))

    # ------------------------------------------------------------- results
    def log(self, msg):
        import re
        self.log_area.appendPlainText(re.sub(r"<[^>]+>", "", str(msg)))

    def clear_log(self):
        self.log_area.clear()

    def copy_log(self):
        QApplication.clipboard().setText(self.log_area.toPlainText())

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save results", "tester_results.txt", "Text (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.log_area.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save results", f"The results could not be saved:\n{exc}")
