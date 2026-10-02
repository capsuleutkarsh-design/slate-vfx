"""
Bringing folders of stock into the library.

One IngestWorker takes any number of folders (MED-008: a drop of several
folders used to ingest only the first), finds the stills, movies and image
sequences in them, stores each one at once as "being analysed" so the gallery
can show it, then analyses them a few at a time: thumbnail, proxy (unless Fast
mode), technical metadata, tags and visual tags. What each analysis finds is
written to the database by file path, in groups, as it goes (MED-001), and the
run ends with a summary the person is shown (MED-021).

Sequences follow the shared rules in slate.utils.sequence_utils, plus one of
the library's own: numbered stills only count as a sequence when they really
run on (MED-010). fire_burst_01/03/05/07/09.png are five textures, not one
clip, and IMG_2045.jpg is a photograph.
"""

import copy
import hashlib
import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from .metadata_engine import SmartMetadataManager
from slate.core.infra.database_manager import database_manager
from slate.core.domain.proxy_manager import proxy_manager
from slate.core.domain.asset_api import create_asset_api
from slate.core.infra.task_registry import task_registry


def current_memory_mb() -> float:
    """This process's working set, in MB; 0 when it cannot be read."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def memory_limit_mb() -> float:
    """
    How much this process may use before the ingest stops itself.

    Well above anything the program needs - it runs in a few hundred MB, and
    the largest picture it ever holds is a couple of GB - and well below the
    point where Windows starts paging everything else out. Forty percent of
    the machine's memory, never less than 6 GB.
    """
    try:
        import psutil
        total = psutil.virtual_memory().total / (1024 * 1024)
        return max(6144.0, total * 0.4)
    except Exception:
        return 6144.0


def describe_memory() -> str:
    """
    This process's memory, and what it is made of, for the log.

    A 17,000-asset ingest that ran for a day took the program to 100 GB and
    the machine to a standstill, and the log had nothing to say about when
    the growth began, how fast it went, or what it consisted of. It could
    not be reproduced on a test machine; a synthetic run stayed flat. So the
    real run has to report on itself. Written every few hundred assets this
    makes the next such report a graph with the suspects named: pictures
    held in memory, threads, child processes, open handles.
    """
    try:
        import gc
        import psutil
        proc = psutil.Process()
        info = proc.memory_info()
        kinds = {}
        for obj in gc.get_objects():
            name = type(obj).__name__
            if name in ("QImage", "QPixmap", "ndarray", "dict", "list"):
                kinds[name] = kinds.get(name, 0) + 1
        try:
            handles = proc.num_handles()
        except Exception:
            handles = -1
        try:
            children = len(proc.children())
        except Exception:
            children = -1
        return ("memory %.0f MB working / %.0f MB private; threads %d; "
                "children %d; handles %d; QImage %d; QPixmap %d; ndarray %d; "
                "dict %d; list %d" % (
                    info.rss / (1024 * 1024),
                    getattr(info, "private", info.vms) / (1024 * 1024),
                    threading.active_count(), children, handles,
                    kinds.get("QImage", 0), kinds.get("QPixmap", 0),
                    kinds.get("ndarray", 0), kinds.get("dict", 0),
                    kinds.get("list", 0)))
    except Exception:
        return "memory unknown"


def _normalise_path(path) -> str:
    """
    One spelling of a path, for comparing what we have with what we found.

    String handling rather than Path.resolve(): resolving asks the file system
    to canonicalise the path, and on a shared drive that is a network round
    trip for every asset in the library.
    """
    return str(path).replace("\\", "/").rstrip("/").lower()


# What the stock library takes. 3D files were removed on request.
VALID_EXTENSIONS = frozenset({
    '.mov', '.mp4', '.mkv', '.avi', '.m4v', '.webm', '.mxf',
    '.exr', '.dpx', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp', '.hdr', '.tga',
    '.r3d', '.ari',
})

# Camera raw: stored and searchable, but nothing in Slate can decode them.
RAW_EXTENSIONS = frozenset({'.r3d', '.ari'})

# A numbered group is a sequence when this much of its range is present.
MIN_COVERAGE = 0.9


def is_real_sequence(seq) -> bool:
    """
    Whether a group of numbered files is a clip rather than a set of stills.

    group_frames() already insists on two or more frames with the same name
    and padding. Stock libraries are full of numbered variants
    (fire_burst_01, _03, _05...; sparks_1, sparks_2), so the library also
    asks that the numbers run on - at least 90% of the range present - and
    that there are three frames, or the numbers are padded like frame numbers
    (0001, 1001), before it treats them as one clip.
    """
    count = len(seq.frames)
    span = seq.end - seq.start + 1
    if count < 2 or span <= 0:
        return False
    if count / span < MIN_COVERAGE:
        return False
    looks_like_frames = seq.padding >= 3 or seq.start >= 100
    return count >= 3 or looks_like_frames


def sequence_display_name(seq) -> str:
    """'muzzle_flash_A.[1001-1024].png' - name, range, extension."""
    return f"{seq.head}[{seq.start}-{seq.end}]{seq.tail}"


def group_media(files):
    """
    (sequences, stills) for the ingest: sequences only where is_real_sequence.
    """
    from slate.utils.sequence_utils import group_frames
    sequences, stills = group_frames(files)
    real = []
    for seq in sequences:
        if is_real_sequence(seq):
            real.append(seq)
        else:
            stills.extend(seq.files)
    stills = sorted(set(Path(s) for s in stills), key=lambda p: str(p).lower())
    return real, stills


class IngestWorker(QThread):
    progress_signal = Signal(int, str)          # percent (-1 = still scanning), text
    asset_processed_signal = Signal(dict)
    assets_batch_signal = Signal(list)          # new assets, shown as "being analysed"
    asset_update_signal = Signal(dict)          # legacy: one analysed asset
    assets_update_batch_signal = Signal(list)   # analysed assets, in groups
    finished_signal = Signal(bool, str)
    # What happened, for the person: added, refreshed, skipped, failed, stopped.
    summary_ready = Signal(dict)
    # The ingest has paused itself because the program's memory is far above
    # anything it should need. Carries a sentence for the person.
    memory_alarm = Signal(str)

    # How many files are analysed at once. Each one is mostly waiting on
    # ffmpeg or the network, so a few in parallel is several times faster;
    # more than this only competes for the same share.
    WORKERS = 3
    # Analysed assets are written and shown in groups of this many.
    BATCH = 25

    def __init__(self, root_path=None, single_file=None, fast_mode=False,
                 root_paths=None, username=""):
        super().__init__()
        self._memory_alarm_raised = False
        roots = list(root_paths or [])
        if root_path:
            roots.insert(0, root_path)
        seen = set()
        self.root_paths = []
        for r in roots:
            key = _normalise_path(r)
            if key not in seen:
                seen.add(key)
                self.root_paths.append(Path(r))
        self.root_path = self.root_paths[0] if self.root_paths else None
        self.single_file = Path(single_file) if single_file else None
        self.fast_mode = fast_mode
        self.username = str(username or "")
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.wait_condition = QWaitCondition()
        self._buffer = []
        self._update_buffer = []
        self._last_progress = 0.0
        self.summary = {"found": 0, "added": 0, "refreshed": 0, "skipped": 0,
                        "failed": 0, "failed_names": [], "stopped": False, "roots": []}
        self.lib_manager = create_asset_api(db_manager=database_manager)
        if self.username and hasattr(self.lib_manager, "set_user"):
            try:
                self.lib_manager.set_user(self.username)
            except Exception:
                pass

        # Register with Task Manager
        target = (", ".join(p.name for p in self.root_paths) if self.root_paths
                  else (self.single_file.name if self.single_file else "Unknown"))
        self.task_info = task_registry.register_task(
            name="Stock Asset Ingest",
            description=f"Scanning {target}"
        )
        self.task_info.cancel_hook = self.stop
        self.task_info.pause_hook = self.toggle_pause

    def pause(self):
        self.mutex.lock()
        self.is_paused = True
        self.mutex.unlock()

    def resume(self):
        self.mutex.lock()
        self.is_paused = False
        self.wait_condition.wakeAll()
        self.mutex.unlock()

    def set_fast_mode(self, enabled):
        self.fast_mode = enabled

    def toggle_pause(self):
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def stop(self):
        self.is_running = False
        self.resume()

    def _memory_guard(self, done, total) -> bool:
        """
        Pause rather than take the machine down.

        Returns True when the ingest has just paused itself. The limit is far
        above normal use, so tripping it means something is wrong; the log
        line says what the memory is made of, and the person is told to save
        their work and restart the program rather than finding the whole
        machine unresponsive an hour later. Raised once: resuming after it is
        the person's decision, and it will not nag.
        """
        if self._memory_alarm_raised:
            return False
        used = current_memory_mb()
        limit = memory_limit_mb()
        if not used or used < limit:
            return False
        self._memory_alarm_raised = True
        logging.critical(
            "Ingest paused itself: this program is using %.1f GB, over the %.1f GB "
            "limit, after %d of %d assets. %s",
            used / 1024, limit / 1024, done, total, describe_memory())
        message = ("Paused: Slate is using %.0f GB of memory. Save your work and "
                   "restart Slate, then run the ingest again - it continues "
                   "where it stopped." % (used / 1024))
        pct = int(done * 100 / max(1, total))
        self.progress_signal.emit(pct, message)
        task_registry.update_progress(self.task_info.task_id, pct, message)
        self.memory_alarm.emit(message)
        self.pause()
        return True

    # ------------------------------------------------------------- progress

    def _progress(self, pct, text, force=False):
        """At most five updates a second, plus the ones that matter (force)."""
        now = time.monotonic()
        if not force and now - self._last_progress < 0.2:
            return
        self._last_progress = now
        self.progress_signal.emit(int(pct), text)
        if pct >= 0:
            task_registry.update_progress(self.task_info.task_id, int(pct), text)

    def _wait_while_paused(self):
        self.mutex.lock()
        while self.is_paused and self.is_running:
            self.wait_condition.wait(self.mutex)
        self.mutex.unlock()

    # ------------------------------------------------------------------ scan

    def _discover(self):
        """Every file worth ingesting under the roots; None when stopped."""
        found = []
        if self.single_file:
            return [self.single_file]
        for root in self.root_paths:
            try:
                for f in root.rglob("*"):
                    if not self.is_running:
                        return None
                    if f.name.startswith("._"):
                        continue
                    if f.suffix.lower() in VALID_EXTENSIONS:
                        found.append(f)
                        if len(found) % 50 == 0:
                            # The bar has nothing to measure yet: say how many
                            # have been found so far instead of sitting at 0%.
                            self._progress(-1, f"Scanning… {len(found):,} files found")
            except OSError as e:
                logging.warning("Could not read %s: %s", root, e)
                self.summary["failed"] += 1
                self.summary["failed_names"].append(f"{root} (could not be read)")
        return found

    def _root_of(self, path: Path) -> str:
        key = _normalise_path(path)
        for root in self.root_paths:
            if key.startswith(_normalise_path(root) + "/"):
                return str(root)
        return str(self.root_paths[0]) if self.root_paths else ""

    def run(self):
        self._progress(-1, "Scanning…", force=True)
        all_files = self._discover()
        if all_files is None:
            return self._finish(False, "Stopped before anything was added.", stopped=True)
        if not all_files:
            return self._finish(True, "No media files found in that folder.")

        # Remember where the library comes from, so Rescan can look again.
        for root in self.root_paths:
            remember = getattr(self.lib_manager, "remember_root", None)
            if callable(remember):
                remember(str(root))
            self.summary["roots"].append(str(root))

        from slate.utils.media_capabilities import is_video
        movies = [f for f in all_files if is_video(f.suffix.lower())]
        others = [f for f in all_files if not is_video(f.suffix.lower())]
        sequences, stills = group_media(others)
        standalone = sorted(movies + stills, key=lambda p: str(p).lower())
        self.summary["found"] = len(standalone) + len(sequences)
        self._progress(-1, f"Found {self.summary['found']:,} items. Checking the library…",
                       force=True)

        # --- PHASE 0: what the library already has ---
        try:
            # Paths only. Reading every column of every row - including the
            # similarity vectors - meant a large library had to be pulled across
            # the network in full before the first new file was looked at.
            existing_assets = self.lib_manager.list_known_paths()
            # Normalised in memory rather than through the file system.
            self.existing_map = {}
            for a in existing_assets or []:
                p = a.get('file_path') or a.get('path')
                if p:
                    self.existing_map[_normalise_path(p)] = a
        except Exception as e:
            logging.exception(f"Ingest Dedupe Init Failed: {e}")
            self.existing_map = {}

        # --- PHASE 1: store everything new as "being analysed" ---
        pending = []
        for f in standalone:
            if not self.is_running:
                break
            known = self.existing_map.get(_normalise_path(f))
            if known is not None and self._healthy(known):
                self.summary["skipped"] += 1
                continue
            asset = self._create_basic_asset(f, is_sequence=False)
            asset['ingest_root'] = self._root_of(f)
            self._buffer.append(asset)
            pending.append((asset, f, None, known is not None))
            if len(self._buffer) >= 100:
                self._flush_buffer()

        for seq in sequences:
            if not self.is_running:
                break
            first_frame = seq.files[0]
            # Same spelling as the map was built with, or no sequence would
            # ever match and every one would be ingested again on every run.
            known = self.existing_map.get(_normalise_path(first_frame))
            if known is not None and self._healthy(known):
                self.summary["skipped"] += 1
                continue
            asset = self._create_basic_asset(first_frame, is_sequence=True,
                                             display_name=sequence_display_name(seq))
            asset.update({
                'is_sequence': True, 'frame_first': seq.start, 'frame_last': seq.end,
                'frame_count': seq.frame_count, 'pattern': seq.pattern,
                'ingest_root': self._root_of(first_frame),
            })
            self._buffer.append(asset)
            pending.append((asset, first_frame, seq, known is not None))
            if len(self._buffer) >= 100:
                self._flush_buffer()
        self._flush_buffer()

        if not self.is_running:
            return self._finish(False, "Stopped.", stopped=True)

        total = len(pending)
        if total == 0:
            return self._finish(True, "Nothing new: everything in that folder is already in the library.")

        logging.info("Ingest: analysing %d items with %d workers (%s).",
                     total, self.WORKERS, describe_memory())
        self._analyse(pending)
        self._flush_update_buffer()

        stopped = not self.is_running
        logging.info("Ingest finished (%s). %s", describe_memory(), self.summary)
        return self._finish(not stopped, "Stopped." if stopped else "Ingest complete.",
                            stopped=stopped)

    @staticmethod
    def _healthy(known) -> bool:
        """Already ingested properly: a thumbnail on disk and metadata stored."""
        thumb = known.get('thumb_path')
        meta = known.get('metadata')
        if isinstance(meta, str):
            has_meta = meta.strip() not in ("", "{}")
        else:
            has_meta = bool(meta)
        if not thumb or not has_meta:
            return False
        try:
            return Path(proxy_manager.long_path(str(thumb))).exists()
        except OSError:
            return False

    def _analyse(self, pending):
        """Run the analyses on a small pool; write and report in groups."""
        total = len(pending)
        done = 0
        queue = list(pending)
        with ThreadPoolExecutor(max_workers=self.WORKERS,
                                thread_name_prefix="slate-stock-ingest") as pool:
            running = {}
            while (queue or running) and self.is_running:
                self._wait_while_paused()
                while queue and len(running) < self.WORKERS * 2 and self.is_running:
                    asset, path, seq, refresh = queue.pop(0)
                    future = pool.submit(self._perform_deep_analysis, asset, path,
                                         seq is not None, seq)
                    running[future] = (asset, refresh)
                if not running:
                    break
                finished, _ = wait(list(running), timeout=0.5, return_when=FIRST_COMPLETED)
                for future in finished:
                    asset, refresh = running.pop(future)
                    try:
                        updated = future.result()
                    except Exception as exc:
                        logging.exception("Analysis failed for %s: %s", asset.get('file_name'), exc)
                        updated = dict(asset, status='corrupt')
                    if updated.get('status') == 'corrupt':
                        self.summary["failed"] += 1
                        self.summary["failed_names"].append(updated.get('file_name') or "")
                    elif refresh:
                        self.summary["refreshed"] += 1
                    else:
                        self.summary["added"] += 1
                    self._update_buffer.append(updated)
                    if len(self._update_buffer) >= self.BATCH:
                        self._flush_update_buffer()
                    done += 1
                    self._progress(done * 100 / total,
                                   f"Analysed {done:,} of {total:,}: {asset.get('file_name')}",
                                   force=(done == total))
                    if done % 250 == 0:
                        logging.info("Ingest: %d/%d analysed, %s", done, total, describe_memory())
                    if done % 25 == 0:
                        self._memory_guard(done, total)
            if not self.is_running:
                for future in running:
                    future.cancel()

    def _finish(self, ok, message, stopped=False):
        self._flush_buffer()
        self._flush_update_buffer()
        self.summary["stopped"] = bool(stopped)
        self.summary["message"] = message
        task_registry.update_progress(self.task_info.task_id, 100, message)
        task_registry.finish_task(self.task_info.task_id)
        self.summary_ready.emit(dict(self.summary))
        self.finished_signal.emit(bool(ok), message)

    def _create_basic_asset(self, f, is_sequence=False, display_name=None):
        # Deterministic, and unique. This is the key the interface uses to
        # match an asset while it is being ingested; the database assigns its
        # own identifiers separately.
        #
        # It used to be squeezed into the range of a small number column, which
        # threw most of the fingerprint away: at forty thousand assets there was
        # roughly a one in three chance of two files sharing an identifier, and
        # when that happened one asset's picture and details were written over
        # another's. The full fingerprint costs nothing and cannot collide.
        asset_id = hashlib.md5((str(f.name) + str(f)).encode('utf-8')).hexdigest()

        category = SmartMetadataManager.classify_category(f)

        return {
            'id': asset_id,
            'name': display_name if display_name else f.name,
            'display_name': display_name if display_name else f.name,
            'file_name': f.name,
            'file_path': str(f),
            'path': str(f),  # Legacy compatibility key for UI
            'file_type': f.suffix.lower(),
            'thumb_path': None,
            'proxy_path': None,
            # No tags until they are known. "Pending" used to be stored as a
            # tag, and stayed there whenever the analysis was not saved.
            'tags': [],
            'category': category,
            'metadata': {},
            'is_sequence': bool(is_sequence),
            'added_by': self.username,
            'status': 'ingesting',
        }

    @staticmethod
    def _handover(assets):
        """
        Copies for the interface; the worker keeps its own.

        The same dict objects used to be handed across: the gallery model
        stored them and painted from them while this thread went on writing
        thumbnail paths, metadata and tags into them during the deep analysis.
        Two threads on one object is a race whichever way it falls, and the
        interface reads these during paint, where nothing may go wrong.
        """
        return [copy.deepcopy(a) for a in assets]

    def _flush_buffer(self):
        if self._buffer:
            try:
                self.lib_manager.add_assets_batch(self._buffer)
                # Only shown once they are stored.
                self.assets_batch_signal.emit(self._handover(self._buffer))
            except Exception as e:
                logging.exception(f"Failed to save batch to DB: {e}")
                self.summary["failed"] += len(self._buffer)
                self.summary["failed_names"].extend(
                    a.get('file_name') or "" for a in self._buffer)
            self._buffer = []

    def _flush_update_buffer(self):
        if self._update_buffer:
            # Written first, in one transaction, then shown.
            writer = getattr(self.lib_manager, "update_assets_batch", None)
            try:
                if callable(writer):
                    writer(self._update_buffer)
                else:
                    for asset in self._update_buffer:
                        self.lib_manager.update_asset(asset.get('id'), asset)
            except Exception as e:
                logging.exception("Analysed assets were not saved: %s", e)
            self.assets_update_batch_signal.emit(self._handover(self._update_buffer))
            self._update_buffer = []

    def _perform_deep_analysis(self, asset, f_path, is_seq=False, seq=None):
        """Thumbnail, proxy, metadata and tags for one asset (runs on the pool)."""
        asset = dict(asset)
        f_path = Path(f_path)
        try:
            if f_path.suffix.lower() in RAW_EXTENSIONS:
                # Camera raw: kept and searchable; there is no decoder for a
                # picture, so none is attempted.
                primary_cat, tags = SmartMetadataManager.get_smart_tags(f_path)
                asset.update({'metadata': {"raw": True}, 'tags': tags + ["Camera raw"],
                              'status': 'ready'})
                return asset

            thumb_source = f_path
            if seq is not None and seq.frames:
                # A frame a little way in, past any slate or black lead-in.
                thumb_source = seq.frame_path(seq.frames[min(4, len(seq.frames) - 1)])
            thumb_success, thumb_path = False, None
            try:
                thumb_success, thumb_path = proxy_manager.generate_thumbnail(thumb_source)
            except Exception as e:
                logging.warning("Thumbnail failed for %s: %s", f_path.name, e)

            proxy_path = None
            if not self.fast_mode:
                try:
                    if seq is not None:
                        _ok, proxy_path = proxy_manager.generate_proxy(
                            f_path, is_seq=True, sequence=(seq.pattern, seq.start))
                    else:
                        _ok, proxy_path = proxy_manager.generate_proxy(f_path)
                except Exception as e:
                    logging.warning(f"Proxy generation failed for {f_path}: {e}")

            meta = {}
            try:
                meta = SmartMetadataManager.extract_tech_metadata(str(f_path))
            except Exception as e:
                logging.warning("Metadata failed for %s: %s", f_path.name, e)
            if seq is not None:
                meta.update({"is_still": False, "is_sequence": True,
                             "frame_count": seq.frame_count,
                             "frame_first": seq.start, "frame_last": seq.end,
                             # A sequence has no rate of its own; ffprobe's
                             # figure for one frame is meaningless.
                             "fps": 0.0, "duration_sec": 0.0})

            primary_cat, tags = SmartMetadataManager.get_smart_tags(f_path)
            if seq is not None:
                tags.append("Sequence")
            visual = []
            if thumb_success and thumb_path:
                visual = SmartMetadataManager.extract_visual_tags(str(thumb_path))

            asset.update({
                'thumb_path': str(thumb_path) if thumb_path else None,
                'proxy_path': str(proxy_path) if proxy_path else None,
                'metadata': meta,
                'tags': tags,
                'visual_tags': visual,
                'category': asset.get('category') or primary_cat,
                'status': 'ready' if (thumb_success or meta.get('width')) else 'corrupt',
            })
            return asset
        except Exception as e:
            logging.exception(f"Deep Analysis Critical Fail {f_path}: {e}")
            asset['status'] = 'corrupt'
            return asset


# --- IMPORT LIB WORKER ---
class ImportLibWorker(QThread):
    """
    Bring a library export back in, off the interface thread (MED-027).

    The file is checked first; anything that is not a Slate library export is
    refused with a sentence, never a Python error. Each entry is stored the
    same way the ingest stores it (the same path spelling, tags as a list),
    and an entry already in the library keeps what was learned about it.
    """
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)
    summary_ready = Signal(dict)

    def __init__(self, json_data, username=""):
        super().__init__()
        self.data = json_data
        self.username = username
        self.is_running = True
        self.lib_manager = create_asset_api(db_manager=database_manager)

    @staticmethod
    def validate(data):
        """(entries, error). entries are the dicts with a path; error is a sentence or ''."""
        if not isinstance(data, list):
            return [], ("This file is not a Slate library export. An export is a list of "
                        "assets; this file holds something else.")
        entries = [d for d in data if isinstance(d, dict)
                   and (d.get('file_path') or d.get('path'))]
        if data and not entries:
            return [], ("This file is not a Slate library export: none of its entries "
                        "has a file path.")
        return entries, ""

    def run(self):
        entries, error = self.validate(self.data)
        if error:
            self.summary_ready.emit({"imported": 0, "missing": 0, "error": error})
            self.finished_signal.emit(False, error)
            return
        total = len(entries)
        imported = missing = 0
        batch = []
        for i, entry in enumerate(entries, start=1):
            if not self.is_running:
                break
            path = Path(str(entry.get('file_path') or entry.get('path')))
            if not path.exists():
                missing += 1
            else:
                record = {k: v for k, v in entry.items()
                          if k in ('thumb_path', 'proxy_path', 'tags', 'metadata', 'category',
                                   'display_name', 'name', 'visual_tags', 'is_sequence',
                                   'frame_first', 'frame_last', 'frame_count', 'pattern')}
                record['file_path'] = str(path)
                record['added_by'] = entry.get('added_by') or self.username
                if not record.get('category'):
                    record['category'] = SmartMetadataManager.classify_category(path)
                batch.append(record)
                imported += 1
            if len(batch) >= 200:
                self.lib_manager.add_assets_batch(batch)
                batch = []
            if i % 25 == 0 or i == total:
                self.progress_signal.emit(int(i * 100 / max(1, total)), f"Importing {i:,} of {total:,}")
        if batch:
            self.lib_manager.add_assets_batch(batch)
        summary = {"imported": imported, "missing": missing, "error": "",
                   "stopped": not self.is_running}
        self.summary_ready.emit(summary)
        self.finished_signal.emit(True, f"Imported {imported:,}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.is_running = False
        self.requestInterruption()
