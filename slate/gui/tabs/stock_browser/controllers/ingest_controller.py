"""
Running an ingest from the Stock Viewer.

One ingest at a time. A second request while one runs is refused with a
sentence - it used to stop the running one and wait for it on the interface
thread (MED-022). Every dropped folder is ingested, and any that could not be
used are named (MED-008). When it ends the person is told what happened:
added, refreshed, skipped, failed, or stopped (MED-021). Rescan looks again in
the folders the library came from (MED-040).
"""

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QFileDialog

from .....core.domain.asset_ingestor import IngestWorker


def summary_sentence(summary: dict) -> tuple:
    """(message, level) for what an ingest did."""
    from .....core.domain.lineup import plural
    summary = summary or {}
    if summary.get("error"):
        return summary["error"], "error"
    added = int(summary.get("added") or 0)
    refreshed = int(summary.get("refreshed") or 0)
    skipped = int(summary.get("skipped") or 0)
    failed = int(summary.get("failed") or 0)
    removed = int(summary.get("removed") or 0)
    not_taken = len(summary.get("not_taken") or [])
    parts = []
    if added:
        parts.append(f"Added {added:,}")
    if refreshed:
        parts.append(f"re-analysed {refreshed:,}")
    if skipped:
        parts.append(f"{skipped:,} already in the library")
    if failed:
        parts.append(f"{failed:,} could not be read")
    if removed:
        # Deleted earlier: left out, and where to get them back (MED2-002).
        parts.append(f"{plural(removed, 'deleted asset')} left out (restore from Removed)")
    if not_taken:
        parts.append(f"{plural(not_taken, 'file')} left out: not a picture or movie")
    if summary.get("stopped"):
        text = "Ingest stopped. " + (", ".join(parts) + "." if parts else "Nothing was added.")
        return text, "warning"
    if not parts:
        return summary.get("message") or "Nothing new to add.", "info"
    text = ", ".join(parts)
    text = text[0].upper() + text[1:] + "."
    return text, ("warning" if failed or not_taken else "success")


class StockIngestController(QObject):
    """
    Controller for File Ingestion in the Stock Browser.
    Manages IngestWorker, Drag & Drop, and Batch Updates.
    """
    progress_updated = Signal(int, str)  # percent (-1 while scanning), status_text
    status_updated = Signal(str, bool)   # status text, is_visible
    ingest_started = Signal()
    ingest_finished = Signal()
    ingest_summary = Signal(dict)
    assets_ready = Signal(list)
    notice = Signal(str, str)            # message, level

    def __init__(self, parent_widget, model, proxy_model, library_manager):
        super().__init__()
        self.parent = parent_widget
        self.model = model
        self.proxy_model = proxy_model
        self.lib_manager = library_manager
        self.worker = None
        self.username = ""

    # ------------------------------------------------------------ state
    def is_running(self) -> bool:
        return bool(self.worker is not None and self.worker.isRunning())

    def busy_reason(self):
        if self.is_running():
            return "The Stock Viewer is still ingesting files into the library."
        return None

    def _cleanup_worker(self, timeout_ms=3000):
        worker = self.worker
        if not worker:
            return
        if worker.isRunning():
            stop = getattr(worker, "stop", None)
            if callable(stop):
                stop()
            else:
                worker.requestInterruption()
            worker.wait(timeout_ms)
        if not worker.isRunning():
            worker.deleteLater()
        else:
            logging.warning("StockIngestController: worker did not stop in time. Abandoning.")
            try:
                worker.finished_signal.disconnect()
            except Exception:
                pass
            worker.finished.connect(worker.deleteLater)
        if self.worker is worker:
            self.worker = None

    def _release_finished_worker(self, worker):
        if worker is not self.worker:
            return False
        self.worker = None
        worker.deleteLater()
        return True

    # ------------------------------------------------------------ starting
    def on_folders_dropped(self, folders, fast_mode=False):
        """Every dropped folder; the ones that cannot be used are named."""
        good, bad = [], []
        for folder in folders or []:
            path = Path(folder)
            (good if path.is_dir() else bad).append(str(path))
        if bad:
            names = ", ".join(Path(b).name or b for b in bad[:3])
            more = f" and {len(bad) - 3} more" if len(bad) > 3 else ""
            self.notice.emit(f"Not a folder that can be read, so left out: {names}{more}.", "warning")
        if good:
            return self.start_ingest(good, fast_mode)
        return False

    def start_ingest(self, folder_path=None, fast_mode=False):
        """Start an ingest of one folder or several. Returns True when it started."""
        if self.is_running():
            self.notice.emit("An ingest is already running. Wait for it to finish, or stop it "
                             "first.", "warning")
            return False
        if folder_path is None:
            folder_path = QFileDialog.getExistingDirectory(self.parent, "Choose a folder to ingest")
        folders = [folder_path] if isinstance(folder_path, (str, Path)) else list(folder_path or [])
        folders = [str(f) for f in folders if f]
        if not folders:
            return False

        self._cleanup_worker()
        self.worker = IngestWorker(root_paths=folders, fast_mode=fast_mode, username=self.username)
        self.worker.progress_signal.connect(self.progress_updated.emit)
        self.worker.assets_batch_signal.connect(self.on_batch_ingested)
        self.worker.asset_update_signal.connect(self.on_asset_update)
        self.worker.assets_update_batch_signal.connect(self.on_assets_update_batch)
        self.worker.summary_ready.connect(self.ingest_summary.emit)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.memory_alarm.connect(lambda message: self.status_updated.emit(message, True))

        label = Path(folders[0]).name or folders[0]
        if len(folders) > 1:
            label += f" and {len(folders) - 1} more"
        self.status_updated.emit(f"Scanning {label}…", True)
        from .....core.domain.proxy_manager import proxy_manager
        if proxy_manager.local_only:
            self.notice.emit("The server's Cache folder could not be written, so thumbnails "
                             "are kept on this computer only and other computers will not see "
                             "them. Ask IT to check the server share, then Rescan.", "warning")
        self.ingest_started.emit()
        self.worker.start()
        return True

    def rescan(self, fast_mode=False):
        """Ingest the remembered folders again; only new or changed files are added."""
        roots = []
        getter = getattr(self.lib_manager, "ingest_roots", None)
        if callable(getter):
            roots = getter()
        if not roots:
            self.notice.emit("No folders are recorded yet. Ingest a folder once and Rescan will "
                             "look in it again.", "info")
            return False
        reachable = [r for r in roots if Path(r).is_dir()]
        unreachable = [r for r in roots if r not in reachable]
        if unreachable:
            from .....core.domain.lineup import plural
            self.notice.emit(f"{plural(len(unreachable), 'ingest folder')} could not be reached "
                             "and left out.", "warning")
        if not reachable:
            return False
        return self.start_ingest(reachable, fast_mode)

    def toggle_pause(self):
        if self.worker:
            if self.worker.is_paused:
                self.worker.resume()
                return False
            self.worker.pause()
            return True
        return False

    def stop_ingest(self):
        if self.worker:
            self.worker.stop()
            return True
        return False

    def on_worker_finished(self, success, message):
        worker = self.sender() or self.worker
        if worker and not self._release_finished_worker(worker):
            return
        if worker is None:
            self.worker = None
        if not success:
            logging.info("Ingest ended: %s", message)
        self.ingest_finished.emit()

    # ------------------------------------------------------------ results
    def on_batch_ingested(self, assets_list):
        """New assets: added, or refreshed when already listed (MED-023)."""
        upsert = getattr(self.model, "upsert_assets", None)
        if callable(upsert):
            upsert(assets_list)
        else:
            self.model.add_assets(assets_list)
        self.proxy_model.invalidateFilter()
        self.assets_ready.emit(self.model.assets)

    def on_assets_update_batch(self, assets):
        """
        A group of analysed assets at once.

        The scan sends these in batches rather than one message per file: with
        tens of thousands of assets, a message each floods the interface and
        looks exactly like the scan having hung.
        """
        for asset_dict in assets or []:
            self.on_asset_update(asset_dict)

    def on_asset_update(self, asset_dict):
        """Called when deep analysis finishes for an asset."""
        path = asset_dict.get('file_path')
        if not path:
            return
        # Straight to the model, which keeps an index of its rows. This used to
        # walk the whole list looking for a match - once per asset, so the work
        # grew with the square of the library size.
        updater = getattr(self.model, "update_item", None)
        if callable(updater):
            updater(asset_dict)
            return
        for i, existing in enumerate(self.model.assets):
            if existing.get('file_path') == path:
                existing.update(asset_dict)
                idx = self.model.index(i, 0)
                self.model.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                break

    def cleanup(self):
        if self.worker:
            try:
                self._cleanup_worker(timeout_ms=3000)
            except Exception as e:
                logging.exception(f"Error cleaning up IngestWorker: {e}")
