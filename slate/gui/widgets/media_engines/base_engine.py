from PySide6.QtCore import QObject, Signal, QSize
from PySide6.QtGui import QImage

class BaseMediaEngine(QObject):
    """
    Interface for all Media Engines (Image, Stream, Sequence).
    Ensures AdvancedPlayer can treat them all identically.
    """
    # Signals matching the original FFmpegPlayerWorker
    frame_ready = Signal(QImage)
    position_changed = Signal(int)
    duration_changed = Signal(int)
    finished = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.fps = 24.0
        self.total_frames = 1
        self.target_size = (640, 360)
        self.pixel_ratio = 1.0

    def load(self, source_path: str):
        """Load the media source."""
        raise NotImplementedError

    def play(self):
        """Start playback."""
        pass

    def pause(self):
        """Pause playback."""
        pass

    def stop(self):
        """Stop playback and release resources."""
        pass

    def seek(self, frame_num: int):
        """Jump to specific frame."""
        pass

    def step(self, frames: int):
        """Step forward/backward by N frames."""
        pass

    def set_target_size(self, size: QSize):
        """Update render target size."""
        self.target_size = (size.width(), size.height())

    def set_pixel_ratio(self, ratio: float):
        """Set high-DPI scaling ratio."""
        self.pixel_ratio = ratio

    def set_speed(self, speed: float):
        """Set playback speed."""
        pass

    def set_loop(self, loop: bool):
        """Enable/Disable looping."""
        pass
