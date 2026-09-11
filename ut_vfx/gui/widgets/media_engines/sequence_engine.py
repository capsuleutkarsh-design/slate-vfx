import sys
import subprocess
from .stream_engine import StreamEngine


class SequenceEngine(StreamEngine):
    """
    Specialized engine for Numbered Sequences (EXR, DPX).
    Inherits StreamEngine's producer-consumer architecture.
    Only overrides FFmpeg command construction for sequence-specific args.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_frame_idx = 0
        self.seq_pattern = ""

    def set_sequence_details(self, pattern_path, start_frame):
        """Configure sequence specifics before loading."""
        self.seq_pattern = pattern_path
        self.start_frame_idx = start_frame

    def load(self, source_path: str):
        """Override load to use sequence pattern as source."""
        # Use the sequence pattern instead of the single file path
        if self.seq_pattern:
            self.source = self.seq_pattern
        else:
            self.source = source_path

        # Delegate to StreamEngine's load, which handles metadata, producer, consumer
        # But we need to extract metadata from the actual file (not the pattern)
        self.stop()

        if not self.ff_path:
            try:
                from ....utils.resource_manager import ResourcePathManager
                checked_path = ResourcePathManager.describe_tool_search("ffmpeg")
            except Exception:
                checked_path = "Unknown"
            self.error_occurred.emit(
                "FFmpeg not found.\n"
                f"Checked: {checked_path}\n"
                "Also searched system PATH and UTVFX_FFMPEG_PATH."
            )
            return

        try:
            from ....core.domain.metadata_engine import SmartMetadataManager
            meta = SmartMetadataManager.extract_tech_metadata(source_path)
            self.fps = meta.get('fps', 24.0) or 24.0
            duration_sec = meta.get('duration_sec', 0) or 0

            # For sequences, duration might not be in metadata — estimate from frame count
            if duration_sec <= 0:
                # Try to count frames from sequence pattern
                try:
                    from ....utils.sequence_utils import SequenceDetector
                    from pathlib import Path
                    seq = SequenceDetector.find_sequence(Path(source_path))
                    if seq:
                        start, end = SequenceDetector.get_frame_range(seq)
                        self.total_frames = end - start + 1
                    else:
                        self.total_frames = 100  # fallback
                except Exception:
                    self.total_frames = 100
            else:
                self.total_frames = int(duration_sec * self.fps)

            # Resolution scaling (reuse StreamEngine's logic)
            target_w, target_h = self.target_size
            native_w = meta.get('width', 1280)
            native_h = meta.get('height', 720)

            decode_w, decode_h = native_w, native_h

            if target_w > 0 and target_h > 0:
                native_long = max(native_w, native_h)
                target_long = max(target_w, target_h)

                if native_long > (target_long * 1.2):
                    desired_long = int(target_long * 1.5)
                    desired_long = max(desired_long, 720)

                    if desired_long < native_long:
                        ratio = desired_long / native_long
                        decode_w = int(native_w * ratio)
                        decode_h = int(native_h * ratio)

            # Safety Cap: 1080p Max
            if decode_w > 1920:
                scale = 1920 / decode_w
                decode_w = 1920
                decode_h = int(decode_h * scale)

            # Alignment (must be even for FFmpeg)
            if decode_w % 2 != 0: decode_w += 1
            if decode_h % 2 != 0: decode_h += 1

            self.render_w = decode_w
            self.render_h = decode_h

            self.duration_changed.emit(self.total_frames)

            # Start Producer (inherited from StreamEngine)
            self.current_frame = 0
            self.paused = False
            self._start_producer(0)

            # Start Consumer (inherited QTimer from StreamEngine)
            interval = max(1, int(1000 / self.fps))
            self.playback_timer.start(interval)

        except Exception as e:
            self.error_occurred.emit(f"Sequence Load Failed: {e}")

    def _launch_ffmpeg(self, start_time_sec=0):
        """Override: sequence-specific FFmpeg command with -start_number."""
        # Calculate start frame from time offset
        frames_to_skip = int(start_time_sec * self.fps) if start_time_sec > 0 else 0
        start_num = self.start_frame_idx + frames_to_skip

        cmd = [
            self.ff_path,
            '-loglevel', 'error',
            '-start_number', str(start_num),
            '-i', self.source,
            '-f', 'rawvideo', '-pix_fmt', 'rgba',
            '-s', f'{self.render_w}x{self.render_h}',
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
