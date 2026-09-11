import re
import hashlib
import logging
from pathlib import Path
from collections import defaultdict

from PySide6.QtCore import QThread, Signal, QMutex, QWaitCondition

from .metadata_engine import SmartMetadataManager
from ut_vfx.core.infra.database_manager import database_manager
from ut_vfx.core.domain.proxy_manager import proxy_manager 
from ut_vfx.core.domain.asset_api import create_asset_api
from ut_vfx.core.infra.task_registry import task_registry

# --- PROXY WORKER ---
def _normalise_path(path) -> str:
    """
    One spelling of a path, for comparing what we have with what we found.

    String handling rather than Path.resolve(): resolving asks the file system
    to canonicalise the path, and on a shared drive that is a network round
    trip for every asset in the library.
    """
    return str(path).replace("\\", "/").rstrip("/").lower()


class ProxyWorker(QThread):
    progress_signal = Signal(str, int) # status, percent (fake)
    proxy_ready_signal = Signal(str, str) # asset_id, proxy_path
    finished_signal = Signal()

    def __init__(self):
        super().__init__()
        self.queue = []
        self.is_running = True
        self.mutex = QMutex()
        self.wait_condition = QWaitCondition()
        self.task_info = task_registry.register_task(
            name="Proxy Generation (FFmpeg)",
            description="Waiting for jobs..."
        )
        self.task_info.cancel_hook = self.stop

    def add_job(self, asset_id, file_path):
        self.mutex.lock()
        self.queue.append((asset_id, file_path))
        self.wait_condition.wakeAll()
        self.mutex.unlock()
        task_registry.update_progress(self.task_info.task_id, 0, f"Queued {len(self.queue)} jobs")

    def run(self):
        while self.is_running:
            self.mutex.lock()
            while not self.queue and self.is_running:
                task_registry.update_progress(self.task_info.task_id, 100, "Idle")
                self.wait_condition.wait(self.mutex)
            
            if not self.is_running:
                self.mutex.unlock()
                break
                
            asset_id, f_path = self.queue.pop(0)
            self.mutex.unlock()
            
            # Process
            try:
                self.progress_signal.emit(f"Proxy: {Path(f_path).name}", 0)
                task_registry.update_progress(self.task_info.task_id, 50, f"Encoding {Path(f_path).name}...")
                success, path = proxy_manager.generate_proxy(Path(f_path))
                if success:
                    self.proxy_ready_signal.emit(asset_id, str(path))
            except Exception as e:
                logging.exception(f"Proxy Job Failed for {f_path}: {e}")

    def stop(self):
        self.is_running = False
        task_registry.update_progress(self.task_info.task_id, 100, "Cancelled")
        task_registry.finish_task(self.task_info.task_id)
        self.mutex.lock()
        self.wait_condition.wakeAll()
        self.mutex.unlock()
        self.wait(1000)  # Wait up to 1 second for thread to finish

# --- INGEST WORKER ---
class IngestWorker(QThread):
    progress_signal = Signal(int, str)
    asset_processed_signal = Signal(dict)
    assets_batch_signal = Signal(list) # For initial discovery
    asset_update_signal = Signal(dict) # Legacy: For immediate UI feedback if needed
    assets_update_batch_signal = Signal(list) # NEW: For batched DB/JSON updates
    finished_signal = Signal(bool, str)

    def __init__(self, root_path=None, single_file=None, fast_mode=False):
        super().__init__()
        self.root_path = Path(root_path) if root_path else None
        self.single_file = Path(single_file) if single_file else None
        self.fast_mode = fast_mode
        self.is_running = True
        self.is_paused = False
        self.mutex = QMutex()
        self.wait_condition = QWaitCondition()
        self._buffer = [] 
        self._update_buffer = [] # NEW: Buffer for deep analysis updates
        self.lib_manager = create_asset_api(db_manager=database_manager)
        
        # Register with Task Manager
        target = self.root_path.name if self.root_path else (self.single_file.name if self.single_file else "Unknown")
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

    def run(self):
        all_files = []
        if self.single_file:
            all_files = [self.single_file]
        elif self.root_path:
            # EXPANDED EXTENSIONS (Removed 3D extensions per user request)
            valid_exts = {
                '.mov', '.mp4', '.mkv', '.avi', 
                '.exr', '.dpx', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp',
                '.r3d', '.ari'
            }
            # Use scandir or simpler walk? rglob is fine for now.
            try:
                for f in self.root_path.rglob("*"):
                    if not self.is_running: return
                    if f.suffix.lower() in valid_exts:
                        all_files.append(f)
            except Exception as e:
                 self.finished_signal.emit(False, f"Scan Error: {e}")
                 return
        
        if not all_files:
            self.finished_signal.emit(True, "No files found.")
            return

        from ut_vfx.utils.media_capabilities import is_video
        # BROADER REGEX: Captures "Name0001.ext" or "Name.0001.ext"
        re.compile(r'^(.*?)(_|-|\.)?(\d+)(\.[a-zA-Z0-9]+)$')
        sequences = defaultdict(list)
        standalone = []

        for f in all_files:
            if not self.is_running: return
            
            is_video_file = is_video(f.suffix.lower())
            
            # ROBUST SEQUENCE PARSING (Right-to-Left)
            # Instead of a complex regex, we simply look for trailing digits in the stem.
            parsed = None
            if not is_video_file:
                stem = f.stem
                # Find all trailing digits
                # \d+$ matches digits at end of string
                trailing_digits = re.search(r'(\d+)$', stem)
                if trailing_digits:
                    frame_str = trailing_digits.group(1)
                    # Base is everything up to the digits
                    base = stem[:-len(frame_str)]
                    
                    # Cleanup visible separator if present (e.g. Tank.001 -> Base: Tank)
                    # We strip . _ - from the right side of base
                    base = base.rstrip('._-')
                    
                    parsed = (base, frame_str, f.suffix.lower())

            if parsed:
                base, frame, ext = parsed
                # Key: (BaseName, Extension, ParentFolder)
                key = (base, ext, f.parent) 
                sequences[key].append(f)
            else:
                standalone.append(f)

        len(standalone) + len(sequences)

        # --- PHASE 0: LOAD EXISTING STATE FOR DEDUPLICATION ---
        self.mutex.lock()
        try:
            logging.info("Ingest: Fetching existing paths for deduplication...")
            # Paths only. Reading every column of every row - including the
            # similarity vectors - meant a large library had to be pulled across
            # the network in full before the first new file was looked at.
            existing_assets = self.lib_manager.list_known_paths()

            # Normalised in memory rather than through the file system.
            # Path.resolve() asks the file system to canonicalise every path,
            # which on a shared drive is a network round trip each - tens of
            # thousands of them before the scan even starts.
            self.existing_map = {}
            for a in existing_assets:
                p = a.get('file_path') or a.get('path')
                if p:
                    self.existing_map[_normalise_path(p)] = a
        except Exception as e:
            logging.exception(f"Ingest Dedupe Init Failed: {e}")
            self.existing_map = {}
        self.mutex.unlock()

        # --- PHASE 1: DISCOVERY & FAST EMIT ---
        # We process logical items (files/sequences), emit them as "pending", then analyze.
        
        pending_analysis = []
        skipped_count = 0
        
        for f in standalone:
            if not self.is_running: break
            
            # DEDUPLICATION CHECK
            norm_path = _normalise_path(f)
            if norm_path in self.existing_map:
                existing = self.existing_map[norm_path]
                # Check if healthy (has thumbnail)
                thumb_p = existing.get('thumb_path')
                if thumb_p and Path(thumb_p).exists():
                    skipped_count += 1
                    continue # SKIP HEALTHY ASSET
                else:
                    logging.info(f"Re-ingesting broken asset: {f.name}")
                    # Validate ID reuse to prevent visual duplicates?
                    # Ideally we update the EXISTING ID.
                    # For now, let's treat as new/update and rely on DB merge.
                    pass

            # Fast Emit
            asset = self._create_basic_asset(f, is_sequence=False)
            self._buffer.append(asset)
            pending_analysis.append((asset, f, False)) # asset_dict, path, is_seq
            
            if len(self._buffer) >= 100:
                self._flush_buffer()

        for (base, ext, parent), frames in sequences.items():
            if not self.is_running: break
            frames.sort()
            first_frame = frames[0]
            display_name = f"{base}[{len(frames)}]{ext}"
            
            # DEDUPLICATION CHECK (Sequence)
            # Same spelling as the map was built with. It used to resolve the
            # path here, which on Windows gives back backslashes while the map
            # holds forward slashes - so no sequence ever matched, and every
            # image sequence in the library was ingested again from scratch on
            # every run.
            norm_path = _normalise_path(first_frame)
            if norm_path in self.existing_map:
                existing = self.existing_map[norm_path]
                thumb_p = existing.get('thumb_path')
                if thumb_p and Path(thumb_p).exists():
                    skipped_count += 1
                    continue
                else:
                    logging.info(f"Re-ingesting broken sequence: {display_name}")

            asset = self._create_basic_asset(first_frame, is_sequence=True, display_name=display_name)
            self._buffer.append(asset)
            pending_analysis.append((asset, first_frame, True))

            if len(self._buffer) >= 100:
                self._flush_buffer()
        
        self._flush_buffer() # Emit remaining "Pending" assets
        
        if skipped_count > 0:
            logging.info(f"Smart Ingest: Skipped {skipped_count} existing healthy assets.")
            self.progress_signal.emit(0, f"Skipped {skipped_count} existing files...")
        
        # --- PHASE 2: DEEP ANALYSIS (Slow) ---
        total_analyze = len(pending_analysis)
        if total_analyze == 0:
             self.finished_signal.emit(True, f"Scan Complete. Skipped {skipped_count} existing.")
             return

        logging.info(f"Ingest Phase 2: Starting Deep Analysis for {total_analyze} items.")
        analyzed_count = 0
        
        for asset, f_path, is_seq in pending_analysis:
            self.mutex.lock()
            while self.is_paused:
                self.wait_condition.wait(self.mutex)
            self.mutex.unlock()
            if not self.is_running: break
            
            # Perform Analysis
            updated_asset = self._perform_deep_analysis(asset, f_path, is_seq=is_seq)
            
            # Collect, and hand them over in groups. One message per asset
            # with no pacing floods the interface on a large ingest, which looks
            # exactly like the scan having hung.
            self._update_buffer.append(updated_asset)
            if len(self._update_buffer) >= 25:
                self._flush_update_buffer()
            logging.debug("Ingest Phase 2: %s | %s",
                          asset.get('file_name'), updated_asset.get('status'))

            analyzed_count += 1
            if analyzed_count % 5 == 0:
                pct = int((analyzed_count / total_analyze) * 100)
                self.progress_signal.emit(pct, f"Analyzed: {asset['file_name']}")
                task_registry.update_progress(self.task_info.task_id, pct, f"Analyzed: {asset['file_name']}")

            # THROTTLING REMOVED: User requested full batch processing
            # We rely on the UI thread checking to keep app responsive
            pass

        self._flush_update_buffer()      # whatever is left over
        logging.info("Ingest Phase 2: All items processed. Emitting finished signal.")
        task_registry.update_progress(self.task_info.task_id, 100, "Ingest Complete")
        task_registry.finish_task(self.task_info.task_id)
        self.finished_signal.emit(True, "Ingest Complete")

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
        
        # Auto-Classify immediately
        category = SmartMetadataManager.classify_category(f)

        return {
            'id': asset_id,
            'name': display_name if display_name else f.name, # FIX: Add 'name' key for StockModel
            'file_name': display_name if display_name else f.name,
            'file_path': str(f),
            'path': str(f), # Legacy compatibility key for UI
            'thumb_path': None, # Pending
            'proxy_path': None,
            'tags': ["Pending"],
            'category': category,
            'metadata': {},
            'status': 'ingesting' # UI can use this to show spinner
        }

    def _flush_buffer(self):
        if self._buffer:
            # Batch add to DB via LibraryManager
            try:
                self.lib_manager.add_assets_batch(self._buffer)
                # Only emit signal if DB save succeeds
                self.assets_batch_signal.emit(self._buffer)
            except Exception as e:
                logging.exception(f"Failed to save batch to DB: {e}")
                # Don't emit signal if save failed
                
            self._buffer = []

    def _flush_update_buffer(self):
        if self._update_buffer:
            self.assets_update_batch_signal.emit(self._update_buffer)
            self._update_buffer = []

    def _perform_deep_analysis(self, asset, f_path, is_seq=False):
        try:
            # 1. Generate Thumb/Proxy
            # Capture errors individually
            thumb_success, thumb_path = False, None
            try:
                # Always generate thumbnail (it's fast-ish and needed)
                logging.debug(f"Generating Thumb for {f_path} (is_seq={is_seq})")
                thumb_success, thumb_path = proxy_manager.generate_thumbnail(f_path, is_seq=is_seq)
                logging.debug(f"Thumb Result: Success={thumb_success}, Path={thumb_path}")
            except Exception as e:
                 logging.exception(f"Thumb Gen Error {f_path}: {e}")
                 logging.exception(f"Thumb Gen EXCEPTION: {e}")

            proxy_success, proxy_path = False, None
            
            # ONLY Generate Proxy if NOT Fast Mode
            if not self.fast_mode:
                try:
                    proxy_success, proxy_path = proxy_manager.generate_proxy(f_path, is_seq=is_seq)
                except Exception as e:
                    logging.warning(f"Proxy generation failed for {f_path}: {e}") 
            
            # 2. Extract Metadata
            meta = {}
            try:
                meta = SmartMetadataManager.extract_tech_metadata(str(f_path))
            except Exception as e:
                logging.exception(f"Meta Error {f_path}: {e}")

            # 3. Tags
            primary_cat, tags = SmartMetadataManager.get_smart_tags(f_path)
            if asset.get('file_name', '').count('[') > 0: tags.append("Sequence")
            
            if thumb_success and thumb_path:
                # DISABLE VISUAL TAGS FOR STABILITY
                # visual_tags = SmartMetadataManager.extract_visual_tags(str(thumb_path))
                # tags.extend(visual_tags)
                pass
            
            # Update Asset Dict
            asset['thumb_path'] = str(thumb_path) if thumb_path else None
            asset['proxy_path'] = str(proxy_path) if proxy_path else None
            asset['metadata'] = meta
            asset['status'] = 'ready'
            
            # Merge tags logic (keep category)
            # We overwrite "Pending" tags
            asset['tags'] = tags
            
            # DB Update in background thread to prevent UI freezing
            try:
                self.lib_manager.update_asset(asset['id'], asset)
            except Exception as e:
                logging.error(f"DB Update error for {f_path}: {e}")
            
            return asset

        except Exception as e:
            logging.exception(f"Deep Analysis Critical Fail {f_path}: {e}")
            asset['status'] = 'corrupt'
            asset['tags'] = ['Corrupt']
            return asset


# --- IMPORT LIB WORKER ---
class ImportLibWorker(QThread):
    progress_signal = Signal(int, str)
    asset_imported_signal = Signal(dict)
    finished_signal = Signal(bool, str)

    def __init__(self, json_data):
        super().__init__()
        self.data = json_data
        self.is_running = True

    def run(self):
        total = len(self.data)
        for i, asset in enumerate(self.data):
            if not self.is_running: break
            
            f_path = Path(asset.get('file_path', ''))
            if f_path.exists():
                thumb_path = asset.get('thumb_path')
                proxy_path = asset.get('proxy_path')
                tags = asset.get('tags', [])
                asset.get('metadata', {})
                category = asset.get('category')
                if not category:
                     category, _ = SmartMetadataManager.get_smart_tags(f_path)

                new_id = database_manager.add_stock_asset(
                    f_path, 
                    thumb_path=Path(thumb_path) if thumb_path else None, 
                    proxy_path=Path(proxy_path) if proxy_path else None, 
                    tags=tags
                )
                asset['id'] = new_id
                asset['category'] = category
                self.asset_imported_signal.emit(asset)
            
            if i % 10 == 0:
                self.progress_signal.emit(int(((i+1)/total)*100), f"Importing {i+1}/{total}")
        
        self.finished_signal.emit(True, "Library Import Complete")
    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
