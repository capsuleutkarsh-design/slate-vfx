import json
import logging
from PySide6.QtWidgets import QMessageBox, QFileDialog
from PySide6.QtCore import Qt

class LibraryActionMixin:
    """
    Mixin for StockBrowserTab handling library management operations:
    Deletion, Clearing, Importing, and Exporting logic.
    Assumes self.can_ingest, self.lib_manager, self.model, self.proxy_model, 
    and self.gallery are available.
    """

    def _get_selected_assets(self):
        """Collect currently selected assets from proxy view."""
        selection_model = self.gallery.asset_view.selectionModel()
        if not selection_model:
            return []

        assets = []
        seen_keys = set()
        for proxy_idx in selection_model.selectedIndexes():
            if not proxy_idx.isValid():
                continue

            source_idx = self.proxy_model.mapToSource(proxy_idx)
            if not source_idx.isValid():
                continue

            asset = self.model.data(source_idx, Qt.ItemDataRole.UserRole)
            if not isinstance(asset, dict):
                continue

            unique_key = str(asset.get("id") or asset.get("file_path") or asset.get("path") or "")
            if not unique_key or unique_key in seen_keys:
                continue

            seen_keys.add(unique_key)
            assets.append(asset)
        return assets

    def delete_selected_assets(self):
        """Developer-only: remove selected assets from DB + proxy cache."""
        if not self.can_ingest:
            self._notify("Delete is restricted to Developer Mode.", "warning")
            return

        selected_assets = self._get_selected_assets()
        if not selected_assets:
            self._notify("Select one or more assets to delete.", "info")
            return

        count = len(selected_assets)
        confirm_text = (
            f"Delete {count} selected asset(s) from stock library?\\n\\n"
            "This will remove database entries and generated proxy/thumbnail cache files.\\n"
            "Original source media files will NOT be deleted."
        )
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        failed = []
        for asset in selected_assets:
            try:
                if self.lib_manager.delete_asset(asset):
                    deleted += 1
                else:
                    failed.append(asset.get("name") or asset.get("file_name") or str(asset.get("id", "Unknown")))
            except Exception as e:
                logging.exception(f"Delete failed for asset {asset.get('id')}: {e}")
                failed.append(asset.get("name") or asset.get("file_name") or str(asset.get("id", "Unknown")))

        # Refresh local UI immediately
        successful_deletes = [a for a in selected_assets if a.get("name") not in failed and a.get("file_name") not in failed and str(a.get("id")) not in failed]
        if successful_deletes:
            self.model.remove_assets(successful_deletes)
            self.update_ui_counts()

        if failed:
            self._notify(
                f"Delete completed with errors. Deleted: {deleted}, failed: {len(failed)} (first: {failed[0]}).",
                "warning",
            )
        else:
            self._notify(f"Deleted {deleted} asset(s) from database and proxy cache.", "success")

    def clear_entire_library(self):
        if not self.can_ingest:
            return
        reply = QMessageBox.question(self, "Clear Library", "Are you sure? This clears the database and thumbnails.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.lib_manager.clear_all_assets()
            self.model.clear_assets()
            self.sidebar.update_categories([])
            self.update_ui_counts()

    def import_library_file(self):
        if not self.can_ingest:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import", "", "JSON (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.lib_manager.add_assets_batch(data)
                self.load_library_from_server()
            except Exception as e:
                self._notify("Failed to import library file.", "error", details=str(e))

    def export_library(self):
        if not self.can_ingest:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export", "Library.json", "JSON (*.json)")
        if path:
            try:
                data = self.lib_manager.get_all_assets()
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
            except Exception as e:
                self._notify("Failed to export library file.", "error", details=str(e))
