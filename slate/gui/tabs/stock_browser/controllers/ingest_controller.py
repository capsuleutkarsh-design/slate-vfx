import logging
from pathlib import Path
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QFileDialog

from .....core.domain.asset_ingestor import IngestWorker

class StockIngestController(QObject):
    """
    Controller for File Ingestion in the Stock Browser.
    Manages IngestWorker, Drag & Drop, and Batch Updates.
    """
    progress_updated = Signal(int, str)  # percent, status_text
    status_updated = Signal(str, bool) # status text, is_visible
    ingest_finished = Signal()
    assets_ready = Signal(list) # Emitted when batch is ready for model
    
    def __init__(self, parent_widget, model, proxy_model, library_manager):
        super().__init__()
        self.parent = parent_widget
        self.model = model
        self.proxy_model = proxy_model
        self.lib_manager = library_manager
        self.worker = None

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

    def on_folders_dropped(self, folders, fast_mode=False):
        """Handle drag-dropped folders with validation."""
        if not folders: return
        
        # Validate first folder
        folder_path = Path(folders[0])
        if not folder_path.exists():
            # Use a non-blocking UI alert if needed, or simply return.
            logging.error(f"Folder does not exist: {folder_path}")
            return
        
        if not folder_path.is_dir():
            logging.error(f"Path is not a directory: {folder_path}")
            return
        
        # Proceed with ingest
        self.start_ingest(str(folder_path), fast_mode)

    def start_ingest(self, folder_path=None, fast_mode=False):
        folder = folder_path if folder_path else QFileDialog.getExistingDirectory(self.parent, "Select Stock Root") 
        if not folder: return
        
        self.status_updated.emit("Ingesting...", True)
        self._cleanup_worker()
        
        try: 
            self.worker = IngestWorker(root_path=folder, fast_mode=fast_mode)
        except TypeError: 
            self.worker = IngestWorker(root_path=folder)
        
        self.worker.progress_signal.connect(self.progress_updated.emit)
        self.worker.assets_batch_signal.connect(self.on_batch_ingested)
        self.worker.asset_update_signal.connect(self.on_asset_update)
        self.worker.assets_update_batch_signal.connect(self.on_assets_update_batch)
        self.worker.finished_signal.connect(self.on_worker_finished)
        
        self.worker.start()
        return True

    def toggle_pause(self): 
        if self.worker: 
            if self.worker.is_paused:
                 self.worker.resume()
                 return False # is_paused = False
            else:
                 self.worker.pause()
                 return True # is_paused = True
        return False

    def stop_ingest(self): 
        if self.worker: 
            self.worker.stop()
            # UI update should happen via status signal in parent or logic here?
            # We'll just return True to indicate stop requested
            return True
        return False
    
    def on_worker_finished(self, success, message): 
        worker = self.sender() or self.worker
        if worker and not self._release_finished_worker(worker):
            return
        if worker is None:
            self.worker = None

        self.status_updated.emit(f"Ingest {'Finished' if success else 'Failed'}", False)
        
        if success:
            self.ingest_finished.emit()
        else:
            logging.error(f"Ingest Error: {message}")

    def on_batch_ingested(self, assets_list):
        """Handle batch of ingested assets - add to model incrementally."""
        # Add assets to model (triggers view update)
        self.model.add_assets(assets_list)
        
        # Invalidate proxy to apply filters to new items
        self.proxy_model.invalidateFilter()
        
        # Notify parent to update categories/counts if needed
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
        # Update Model (DB is already updated by worker)
        path = asset_dict.get('file_path')
        if not path: return
        
        # Straight to the model, which keeps an index of its rows. This used to
        # walk the whole list looking for a match - once per asset, so the work
        # grew with the square of the library size.
        updater = getattr(self.model, "update_item", None)
        if callable(updater):
            updater(asset_dict)
            return

        asset_id = asset_dict.get('id')
        for i, existing in enumerate(self.model.assets):
            if existing.get('id') == asset_id:
                existing.update(asset_dict)
                idx = self.model.index(i, 0)
                self.model.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
                break

    def cleanup(self):
        if self.worker:
            try:
                self._cleanup_worker(timeout_ms=3000)
            except Exception as e:
                logging.exception(f"Error cleaning up IngestWorker: {e}")
