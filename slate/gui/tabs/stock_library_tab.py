"""
Stock Library Tab - Compatibility Adapter.

Older code and tests know the Stock Viewer by this name. Its methods used to
pretend to work - edit_tags, delete_asset and copy_to_project all returned
True and did nothing (MED-077). Each one now does the real thing through the
Stock Viewer, or says plainly that it does not exist.
"""
from PySide6.QtWidgets import QFileDialog, QWidget  # noqa: F401 (imported by older tests)
from slate.core.domain.library_manager import LibraryManager
from slate.gui.tabs.stock_browser_tab import StockBrowserTab


class StockLibraryTab(StockBrowserTab):
    """Compatibility adapter for StockBrowserTab."""

    def __init__(self, library_manager=None, user_roles=None, user_role=None, user_data=None):
        if library_manager is None:
            library_manager = LibraryManager()
        super().__init__(library_manager=library_manager, user_roles=user_roles,
                         user_role=user_role, user_data=user_data)
        self.asset_grid = self.gallery.asset_view
        self.asset_list = self.gallery.table_view
        self.search_input = self.gallery.search_bar
        self.search_bar = self.search_input
        self.import_button = getattr(self.sidebar, 'btn_import', None)

    def on_asset_selected(self, asset):
        self.inspector.update_asset(asset)

    def show_asset_details(self, asset):
        self.inspector.update_asset(asset)

    def edit_tags(self, asset=None):
        return self.edit_tags_of(asset)

    def delete_asset(self, *_args):
        return self.delete_selected_assets()

    def trash_selected(self, *_args):
        return self.delete_selected_assets()

    def set_grid_view(self):
        self.gallery.set_view_mode("grid")

    def set_list_view(self):
        self.gallery.set_view_mode("list")

    def _not_here(self, name):
        raise NotImplementedError(f"The Stock Viewer has no '{name}'.")

    def filter_by_tag(self, tag):
        """A tag is found by searching for it."""
        self.gallery.search_bar.setText(str(tag or ""))
        self.apply_filters()

    def add_with_metadata(self, *args, **kwargs):
        self._not_here("add with metadata - ingest a folder instead")

    def update_metadata(self, *args, **kwargs):
        self._not_here("update metadata - it is read from the file")

    def export_asset(self, *args, **kwargs):
        self._not_here("export asset")

    def copy_to_project(self, *args, **kwargs):
        self._not_here("copy to project")
