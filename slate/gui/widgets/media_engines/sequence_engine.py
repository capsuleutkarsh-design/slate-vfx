import sys
import subprocess
from pathlib import Path

from .stream_engine import StreamEngine


class SequenceEngine(StreamEngine):
    """
    Specialized engine for Numbered Sequences (EXR, DPX).
    Inherits StreamEngine's producer-consumer architecture.

    Loading no longer runs ffprobe and ffmpeg on the interface thread - up to
    27 seconds of timeouts on a slow share, on every selection (MED-110). The
    frame count comes from the file names (a folder listing), the picture
    size from a probe of one frame on a thread, and decoding starts when that
    answer is back, like a movie's.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_frame_idx = 0
        self.seq_pattern = ""
        self.seq_frames = 0

    def set_sequence_details(self, pattern_path, start_frame, frame_count=0):
        """Configure sequence specifics before loading."""
        self.seq_pattern = pattern_path
        self.start_frame_idx = int(start_frame or 0)
        self.seq_frames = int(frame_count or 0)

    def load(self, source_path: str):
        """Load from the printf pattern; the frame named is probed for its size."""
        self._probe_frame = source_path
        super().load(source_path)
        if self.seq_pattern:
            self.source = self.seq_pattern

    def _probe(self, source_path: str) -> dict:
        from ....core.domain.metadata_engine import SmartMetadataManager
        frames = self.seq_frames
        if not frames:
            try:
                from ....utils.sequence_utils import sequence_for
                seq = sequence_for(Path(getattr(self, "_probe_frame", source_path)))
                frames = seq.frame_count if seq else 0
            except Exception:
                frames = 0
        meta = SmartMetadataManager.extract_tech_metadata(getattr(self, "_probe_frame", source_path))
        # A sequence has no rate of its own: it plays at 24 unless the
        # project says otherwise.
        return {"fps": 24.0, "frames": frames or 1, "width": int(meta.get("width") or 0),
                "height": int(meta.get("height") or 0)}

    def _launch_ffmpeg(self, start_time_sec=0):
        """Override: sequence-specific FFmpeg command with -start_number."""
        frames_to_skip = int(round(start_time_sec * self.fps)) if start_time_sec > 0 else 0
        start_num = self.start_frame_idx + frames_to_skip

        cmd = [
            self.ff_path,
            '-loglevel', 'error',
            '-framerate', f"{self.fps:g}",
            '-start_number', str(start_num),
            '-i', self.source,
        ] + self._video_args() + [
            '-f', 'rawvideo', '-pix_fmt', 'rgba',
            '-'
        ]

        startupinfo = None
        creationflags = 0
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=self.render_w * self.render_h * 4 * 4,
            startupinfo=startupinfo, creationflags=creationflags
        )
        from ....utils.process_manager import subprocess_tracker
        return subprocess_tracker.register(proc)
