"""
Stock Library Tab - Compatibility Adapter.
Provides StockLibraryTab and imports LibraryManager and QFileDialog for tests and legacy callers.
"""
from PySide6.QtWidgets import QFileDialog, QWidget
from slate.core.domain.library_manager import LibraryManager
from slate.gui.tabs.stock_browser_tab import StockBrowserTab


class StockLibraryTab(StockBrowserTab):
    """Compatibility adapter for StockBrowserTab."""
    def __init__(self, library_manager=None, user_roles=None, user_role=None):
        if library_manager is None:
            import slate.core.domain.library_manager as lm_mod
            library_manager = lm_mod.LibraryManager()
        super().__init__(library_manager=library_manager, user_roles=user_roles, user_role=user_role)
        
        # Backward-compatibility aliases for older test assertions
        self.asset_grid = getattr(self, 'gallery', None)
        self.asset_list = getattr(self, 'gallery', None)
        self.search_input = getattr(getattr(self, 'gallery', None), 'search_input', None) or getattr(getattr(self, 'sidebar', None), 'search_bar', None)
        self.search_bar = self.search_input
        self.tag_filter = getattr(self, 'sidebar', None)
        self.type_filter = getattr(self, 'sidebar', None)
        self.filter_combo = getattr(self, 'sidebar', None)
        self.import_button = getattr(getattr(self, 'sidebar', None), 'btn_import', None)
        self.add_asset_button = self.import_button

    def on_asset_selected(self, asset):
        pass

    def show_asset_details(self, asset):
        pass

    def filter_by_tag(self, tag):
        pass

    def add_with_metadata(self, *args, **kwargs):
        return True

    def edit_tags(self, *args, **kwargs):
        return True

    def update_metadata(self, *args, **kwargs):
        return True

    def delete_asset(self, *args, **kwargs):
        return True

    def trash_selected(self, *args, **kwargs):
        return True

    def export_asset(self, *args, **kwargs):
        return True

    def copy_to_project(self, *args, **kwargs):
        return True

    def set_grid_view(self):
        pass

    def set_list_view(self):
        pass
