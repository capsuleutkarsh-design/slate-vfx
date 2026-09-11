"""
GUI modules for Slate Production tool.
"""
from .main_window import VFXFolderCreatorApp
from .tabs.folder_creator_tab import FolderCreatorTab
from .cap_rename_tab import CapRenameTab
from .tabs.stock_browser_tab import StockBrowserTab

# Import widgets folder content (needed for internal references)
from .widgets.advanced_player import AdvancedPlayer 

__all__ = [
    'VFXFolderCreatorApp',
    'FolderCreatorTab',
    'CapRenameTab',
    'StockBrowserTab',
    'AdvancedPlayer'
]