"""
Legacy timeline graphics shim.

The OTIO-driven interactive timeline view has been retired.
This widget intentionally stays lightweight for compatibility.
"""

from PySide6.QtWidgets import QGraphicsScene, QGraphicsView


class TimelineGraphicsView(QGraphicsView):
    """Compatibility view that shows a static retirement message."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self._show_retired_message()

    def _show_retired_message(self):
        self.scene.clear()
        self.scene.addText("Timeline graphics are retired in this build.")

    def init_default_tracks(self):
        return None

    def set_tool_mode(self, mode):
        return None

    def add_shot_at(self, shot, frame):
        return False

    def add_shot_to_track_type(self, shot, track_type, frame=None):
        return False

    def load_shots(self, shots, append=False):
        return None

    def clear_timeline(self):
        self._show_retired_message()

    def get_shot_at_frame(self, frame):
        return None

