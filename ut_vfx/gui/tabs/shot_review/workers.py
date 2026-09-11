"""
Shot Review Workers — Background QThread workers for Shot Review Tab.

Extracted from shot_review_tab.py as part of God-class decomposition.
"""

from pathlib import Path
import re
import subprocess
import logging

from PySide6.QtCore import QThread, Signal

from ....core.domain.review_shot import ReviewShot
from ....utils.media_capabilities import is_video

logger = logging.getLogger(__name__)


class ShotProxyRenderWorker(QThread):
    """Render approved shot scan/render media into MP4 proxies."""

    progress = Signal(int, str)
    completed = Signal(bool, str, str, str, str)  # success, message, scan_proxy, render_proxy, shot_id

    def __init__(self, shot: ReviewShot, config_manager, project_name: str):
        super().__init__()
        self.shot = shot
        self.config = config_manager
        self.project_name = project_name or getattr(shot, 'project_name', '') or "Unknown_Project"

    def run(self):
        from ....core.domain.video_exporter import VideoExporter
        from ....core.infra.config_manager import ConfigManager

        scan_proxy_path = ""
        render_proxy_path = ""

        try:
            self.progress.emit(5, f"Preparing MP4 proxy output for {self.shot.name}...")

            config = self.config if self.config else ConfigManager()
            base_dir = config.get_path("central_library")
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                base_dir = Path.home() / "RuntimeData" / "UT_VFX_Lineups"
                base_dir.mkdir(parents=True, exist_ok=True)

            project_key = self._sanitize_key(self.project_name)
            shot_key = self._sanitize_key(self.shot.name)
            output_dir = base_dir / "Shot_Checker" / project_key / shot_key
            output_dir.mkdir(parents=True, exist_ok=True)

            exporter = VideoExporter(config)
            if not exporter.check_ffmpeg_available():
                self.completed.emit(False, "FFmpeg is not available. Cannot render MP4 proxies.", "", "", str(self.shot.id))
                return

            start_number = self._infer_start_number()

            scan_ok = True
            render_ok = True

            if getattr(self.shot, 'scan_path', None):
                self.progress.emit(25, f"Rendering Scan MP4 for {self.shot.name}...")
                scan_out = output_dir / "scan.mp4"
                scan_ok = self._render_mp4(exporter, Path(self.shot.scan_path), scan_out, start_number)
                if scan_ok and scan_out.exists():
                    scan_proxy_path = str(scan_out)
            else:
                scan_ok = False

            if getattr(self.shot, 'render_path', None):
                self.progress.emit(65, f"Rendering Render MP4 for {self.shot.name}...")
                render_out = output_dir / "render.mp4"
                render_ok = self._render_mp4(exporter, Path(self.shot.render_path), render_out, start_number)
                if render_ok and render_out.exists():
                    render_proxy_path = str(render_out)
            else:
                render_ok = False

            self.progress.emit(95, "Finalizing proxy render...")

            if scan_ok and render_ok:
                msg = f"MP4 proxies rendered for {self.shot.name}."
                self.completed.emit(True, msg, scan_proxy_path, render_proxy_path, str(self.shot.id))
            else:
                missing = []
                if not scan_ok:
                    missing.append("scan")
                if not render_ok:
                    missing.append("render")
                msg = f"MP4 proxy render incomplete for {self.shot.name}. Failed: {', '.join(missing)}."
                self.completed.emit(False, msg, scan_proxy_path, render_proxy_path, str(self.shot.id))

        except Exception as e:
            self.completed.emit(False, f"Proxy render error: {e}", scan_proxy_path, render_proxy_path, str(self.shot.id))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

    def _render_mp4(self, exporter, source_path: Path, output_path: Path, start_number: int) -> bool:
        """Render a single media source to MP4."""
        try:
            if self._is_up_to_date(source_path, output_path):
                return True

            cmd = [exporter.ffmpeg_path]

            suffix = source_path.suffix.lower()
            if is_video(suffix):
                cmd.extend(["-i", str(source_path)])
            else:
                input_pattern = exporter._get_ffmpeg_input_pattern(source_path, start_number)
                cmd.extend(["-start_number", str(start_number), "-i", str(input_pattern)])

            cmd.extend([
                "-vf", "scale=-2:720:flags=lanczos",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                "-y",
                str(output_path)
            ])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"MP4 render failed for {source_path}: {result.stderr}")
                return False

            return output_path.exists()
        except Exception as e:
            logger.error(f"Error rendering proxy {source_path}: {e}", exc_info=True)
            return False

    def _is_up_to_date(self, source_path: Path, output_path: Path) -> bool:
        """Skip re-render when proxy already exists and is newer than source file."""
        if not output_path.exists():
            return False

        # Sequence pattern paths often don't exist as direct files; force fresh render.
        if "%" in source_path.name or "#" in source_path.name:
            return False

        if not source_path.exists():
            return True

        try:
            return output_path.stat().st_mtime >= source_path.stat().st_mtime
        except OSError:
            return False

    def _infer_start_number(self) -> int:
        """Infer start frame for image-sequence ffmpeg input."""
        if getattr(self.shot, 'frame_range', None):
            try:
                return int(self.shot.frame_range[0])
            except (TypeError, ValueError):
                logging.debug("Invalid frame_range start for shot %s", getattr(self.shot, "name", ""))

        for path_value in (getattr(self.shot, 'scan_path', None), getattr(self.shot, 'render_path', None)):
            if not path_value:
                continue
            path_obj = Path(path_value)
            frame = self._extract_frame_from_name(path_obj.name)
            if frame is not None:
                return frame
            frame = self._scan_folder_for_first_frame(path_obj)
            if frame is not None:
                return frame

        return 1

    def _extract_frame_from_name(self, filename: str):
        match = re.search(r'(\d+)(?=\.[^.]+$)', filename)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (ValueError, TypeError):
            return None

    def _scan_folder_for_first_frame(self, path_obj: Path):
        folder = path_obj.parent
        if not folder.exists():
            return None

        frame_values = []
        for file_path in folder.iterdir():
            if not file_path.is_file():
                continue
            frame = self._extract_frame_from_name(file_path.name)
            if frame is not None:
                frame_values.append(frame)

        if not frame_values:
            return None
        return min(frame_values)

    def _sanitize_key(self, value: str) -> str:
        clean = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in (value or "").strip())
        clean = clean.strip("._ ")
        return clean or "Unknown"


class AutoPullWorker(QThread):
    """Worker thread for recursive scanning"""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, engine, path):
        super().__init__()
        self.engine = engine
        self.path = path

    def run(self):
        try:
            shots = self.engine.detect_shots(self.path)
            self.finished.emit(shots)
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()


class FrameCacheWorker(QThread):
    """Background worker that pre-caches all frames of a shot.

    Replaces the old synchronous cache loop that used processEvents().
    Emits progress signals back to the main thread for UI updates.
    """
    progress = Signal(int, int)          # cached_count, total_frames
    finished_caching = Signal(int, int, str)  # cached_count, total_frames, shot_id

    def __init__(self, shot, cache):
        super().__init__()
        self.shot = shot
        self.cache = cache

    def run(self):
        from ....utils.image_loader import ImageLoader
        from ....utils.sequence_utils import format_pattern_with_frame

        loader = ImageLoader()
        start, end = self.shot.frame_range
        total_frames = end - start + 1
        cached_count = 0

        for frame in range(start, end + 1):
            if self.isInterruptionRequested():
                break

            # Load scan
            if self.shot.scan_path:
                try:
                    scan_folder = self.shot.scan_path.parent
                    scan_pattern = self.shot.scan_path.name
                    scan_file = scan_folder / format_pattern_with_frame(scan_pattern, frame)
                    if scan_file.exists():
                        scan_img = loader.load_image(scan_file)
                        if scan_img is not None:
                            key = f"{self.shot.id}_scan_{frame}"
                            self.cache.put(key, scan_img)
                except Exception as e:
                    logger.error(f"Failed to cache scan frame {frame}: {e}")

            # Load render
            if self.shot.render_path:
                try:
                    render_folder = self.shot.render_path.parent
                    render_pattern = self.shot.render_path.name
                    render_file = render_folder / format_pattern_with_frame(render_pattern, frame)
                    if render_file.exists():
                        render_img = loader.load_image(render_file)
                        if render_img is not None:
                            key = f"{self.shot.id}_render_{frame}"
                            self.cache.put(key, render_img)
                except Exception as e:
                    logger.error(f"Failed to cache render frame {frame}: {e}")

            cached_count += 1
            # Throttle progress signals to every 5 frames to avoid event loop saturation
            if cached_count % 5 == 0 or cached_count == total_frames:
                self.progress.emit(cached_count, total_frames)

        self.finished_caching.emit(cached_count, total_frames, str(self.shot.id))

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()
