import json
import logging
from PySide6.QtCore import QRunnable, QThreadPool

class MetadataAnalysisMixin:
    """
    Mixin for StockBrowserTab handling background metadata extraction.
    """

    def trigger_background_analysis(self, path, asset):
        """Run metadata extraction in background via QThreadPool (Qt-safe)."""
        asset_id = asset.get('id', '')
        asset_name = asset.get('name', '')
        parent_ref = self  # prevent closure over 'self' directly

        class AnalysisRunnable(QRunnable):
            def run(self_r):
                try:
                    from .....core.domain.metadata_engine import SmartMetadataManager
                    meta = SmartMetadataManager.extract_tech_metadata(path)
                    if meta and asset_id:
                        meta_str = json.dumps(meta)
                        parent_ref.lib_manager.update_asset(asset_id, {'metadata': meta_str, 'status': 'ready'})
                        parent_ref._analysis_done.emit(asset_id, meta_str, asset_name)
                except Exception as e:
                    logging.exception(f"Background analysis failed: {e}")

        runnable = AnalysisRunnable()
        runnable.setAutoDelete(True)
        QThreadPool.globalInstance().start(runnable)

    def _on_analysis_result(self, asset_id, meta_json, asset_name):
        """Slot: receives analysis result on the main thread - safe to update model."""
        self.model.update_item({
            'id': asset_id,
            'metadata': meta_json,
            'status': 'ready'
        })
        logging.info(f"Background analysis saved for {asset_name}")
