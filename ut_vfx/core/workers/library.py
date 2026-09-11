from PySide6.QtCore import QThread, Signal

class StockLoaderWorker(QThread):
    """Background worker to fetch library data without freezing UI."""
    finished_signal = Signal(list)
    
    def __init__(self, lib_manager, query=None, limit=None, offset=0, file_types=None, asset_ids=None):
        super().__init__()
        self.lib_manager = lib_manager
        self.query = query
        self.limit = limit
        self.offset = offset
        self.file_types = file_types
        self.asset_ids = asset_ids
        
    def run(self):
        # Heavy DB / IO operations
        # If parameters are default (None), search_library will behave like load_library() but without updating cache
        # For legacy compatibility or full reload, user can pass limit=None
        if self.limit is None and self.query is None and self.file_types is None and self.asset_ids is None:
             # Legacy full load
             self.lib_manager.load_library()
             assets = self.lib_manager.get_all_assets()
        else:
             # Search Query
             assets = self.lib_manager.search_library(
                 query=self.query,
                 limit=self.limit,
                 offset=self.offset,
                 file_types=self.file_types,
                 asset_ids=self.asset_ids
             )
        self.finished_signal.emit(assets)
    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
