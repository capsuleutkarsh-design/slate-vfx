from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt
from .advanced_player import AdvancedPlayer

class QuickLookDialog(QDialog):
    """
    MacOS-style Quick Look window.
    Popped up via Spacebar. closed via Spacebar or Esc.
    """
    def __init__(self, parent=None, asset_name="Asset", asset_path=None):
        super().__init__(parent)
        self.setWindowTitle(asset_name)
        self.resize(1280, 720) 
        self.setModal(True)
        # Frameless looks cooler but we lose dragging. Let's keep frame for now.
        # self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup) 
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0,0,0,0)
        
        # We reuse the robust AdvancedPlayer
        self.player = AdvancedPlayer(self)
        self.main_layout.addWidget(self.player)
        
        if asset_path:
            self.player.load(asset_path)

    def keyPressEvent(self, event):
        # Spacebar toggles close (if passed from parent)
        # But AdvancedPlayer consumes Spacebar for Play/Pause.
        # We need a way to differentiate.
        # "Space to Open, Space to Close" collision.
        # Decision: If playing, Space Pauses. If paused? 
        # Actually macOS QuickLook: Space opens, Space closes. It DOES NOT play/pause usually? 
        # Wait, macOS video quicklook: Space plays/pauses. You close with Top-Left X or CMD+W?
        # No, Space closes it too in Finder if it is just an image.
        # Let's simple logic: ESC closes. Space plays/pauses.
        # User asked for "Spacebar Quick Look... Spacebar again to close".
        
        if event.key() == Qt.Key.Key_Space:
            self.close()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.player.stop_media()
        super().closeEvent(event)
