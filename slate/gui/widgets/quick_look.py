"""
Quick Look: one asset large, on Space.

  * Sized to the screen it opens on - 80% of the space there is - instead of a
    fixed 1280x720 taller than a 1366x768 laptop's desk (MED-120).
  * Previous and Next walk the caller's list when it hands over a navigator;
    without one the buttons are hidden instead of doing nothing (MED-121).
  * Space plays and pauses here, Esc closes, and a line under the picture
    says so. The player's other keys work too (MED-117).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from slate.core.infra.gate import Gate
from .advanced_player import AdvancedPlayer


class QuickLookDialog(QDialog):
    """
    navigator: optional callable(step) -> (name, path) | None. step is -1 or 1.
    It moves the caller's own selection and says what to show next.
    """

    def __init__(self, parent=None, asset_name="Asset", asset_path=None, navigator=None,
                 autoplay=True):
        super().__init__(parent)
        self.setWindowTitle(asset_name or "Preview")
        self.setModal(True)
        self.navigator = navigator

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.player = AdvancedPlayer(self)
        self.main_layout.addWidget(self.player, 1)
        self.player.prev_requested.connect(lambda: self.step(-1))
        self.player.next_requested.connect(lambda: self.step(1))
        self.player.btn_prev.setVisible(navigator is not None)
        self.player.btn_next.setVisible(navigator is not None)

        hint = "Space plays and pauses  ·  ← → step a frame  ·  Esc closes"
        if navigator is not None:
            hint = "Space plays and pauses  ·  Page Up / Page Down: previous / next  ·  Esc closes"
        self.hint = QLabel(hint)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet(f"color: {Gate.TEXT_DIM}; padding: 4px; background: {Gate.PANEL};")
        self.main_layout.addWidget(self.hint)

        from ..components.screen_fit import fit_to_screen
        fit_to_screen(self, 1280, 760, fraction=0.8)

        if asset_path:
            self.player._pending_autoplay = bool(autoplay)
            self.player.load(asset_path)

    def step(self, direction):
        """Show the previous or next asset of the caller's list."""
        if self.navigator is None:
            return False
        nxt = self.navigator(direction)
        if not nxt:
            return False
        name, path = nxt
        self.setWindowTitle(name or "Preview")
        self.player._pending_autoplay = True
        self.player.load(path)
        return True

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape and not self.player._is_fullscreen:
            self.close()
            return
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_Up):
            self.step(-1)
            return
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_Down):
            self.step(1)
            return
        if self.player.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.player.stop_media()
        super().closeEvent(event)

    def reject(self):
        self.player.stop_media()
        super().reject()
