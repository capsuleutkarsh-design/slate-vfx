import os
import subprocess
import threading
import logging
from pathlib import Path
import sys
import hashlib
from typing import Tuple
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt  # Moved from line 149

from slate.utils.resource_manager import ResourcePathManager
from slate.utils.media_capabilities import is_image
from slate.utils.sequence_utils import sequence_for

class ProxyManager:
    """
    Handles generation of thumbnails and video proxies.
    SMART UPDATE: Retries thumbnail at frame 0 if seeking 1s fails (fixes short clips).
    """

    # True when the server Cache folder could not be used and pictures are
    # made in a cache on this computer that other computers cannot see.
    local_only = False

    # A review proxy's ffmpeg limit: a minute plus this per frame of the shot.
    SECONDS_PER_FRAME = 2

    def __init__(self):
        self.ffmpeg_path = self._find_ffmpeg()
        self.cache_dir = self._get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _find_ffmpeg(self) -> str:
        resolved = ResourcePathManager.get_ffmpeg_path()
        if not resolved:
            return None
        if Path(resolved).exists():
            return resolved
        from shutil import which
        return resolved if which(resolved) else None

    def _get_cache_dir(self) -> Path:
        """Resolve cache directory. Prefers Network Cache for Centralized Library."""
        try:
            from slate.core.infra.global_config import GlobalConfig
            server_root = GlobalConfig.server_root()
            configured = str(GlobalConfig.get("SERVER_ROOT") or "").strip()

            # Use a centralized 'Cache' folder on the server
            cache_path = server_root / "Cache"

            try:
                # server_root() hands back a folder on this PC when the share
                # is not mounted; pictures made there are this PC's alone.
                if configured and Path(configured) != server_root:
                    raise OSError("the studio folder %s is not reachable" % configured)
                cache_path.mkdir(parents=True, exist_ok=True)
                # mkdir passes on a folder that is there but read-only; a
                # write is the real test. (Not tempfile: on Windows it retries
                # 10,000 names in a folder it may not write to.)
                probe = cache_path / (".write_test_%d" % os.getpid())
                probe.write_bytes(b"")
                probe.unlink()
                return cache_path
            except Exception as e:
                # Fallback to local if network is unwritable. Pictures made
                # here exist on this computer only, so their paths are not
                # stored in the shared library (see LibraryManager._shareable).
                logging.warning("Thumbnails and proxies are kept on this computer only: "
                                "the server Cache folder %s could not be written: %s",
                                cache_path, e)
                self.local_only = True
                return GlobalConfig.local_cache_dir()

        except Exception as e:
            logging.exception(f"Error resolving cache path: {e}")
            self.local_only = True
            return Path.cwd() / "Cache"

    # Proxies are never made bigger than this, and never bigger than the
    # source. They used to be forced to exactly 1920x1080 and cropped to fill
    # it, so a portrait, square, scope or phone clip lost picture and small
    # sources were blown up (MED-024). Now the frame is fitted inside the box,
    # aspect kept, nothing cropped, nothing enlarged.
    PROXY_BOX = (1920, 1080)

    @classmethod
    def fit_filter(cls, width: int = None, height: int = None) -> str:
        """The ffmpeg scale filter that fits a frame inside PROXY_BOX without cropping."""
        w, h = (width, height) if width and height else cls.PROXY_BOX
        return (f"scale='min({w},iw)':'min({h},ih)'"
                ":force_original_aspect_ratio=decrease:force_divisible_by=2")

    @staticmethod
    def long_path(path) -> str:
        """
        The path in a form Windows accepts past 260 characters.

        Deep studio shares easily go past that, and then Python could not see
        the thumbnail Qt had just written, so it was never moved into place and
        the asset stayed without a picture - silently (MED-039). File-system
        calls made here go through this; the long-path prefix is only added on
        Windows, only when the path is long, and only once.
        """
        prefix = "\\\\?\\"
        text = str(path)
        if os.name != "nt" or len(text) < 240 or text.startswith(prefix):
            return text
        absolute = os.path.abspath(text)
        if absolute.startswith("\\\\"):
            # \\server\share\... becomes \\?\UNC\server\share\...
            return prefix + "UNC\\" + absolute.lstrip("\\")
        return prefix + absolute

    @classmethod
    def exists(cls, path) -> bool:
        """os.path.exists that also sees files past 260 characters."""
        try:
            return bool(path) and os.path.exists(cls.long_path(path))
        except (OSError, ValueError):
            return False

    @classmethod
    def _usable(cls, path: Path) -> bool:
        """A cached file counts only if it is there and has something in it."""
        try:
            long = cls.long_path(path)
            return os.path.exists(long) and os.path.getsize(long) > 0
        except OSError:
            return False

    @staticmethod
    def _partial_name(final: Path) -> Path:
        """
        Where a file is written before it is finished.

        ffmpeg writes straight into its output path, so a file appears the
        moment encoding starts and grows for up to a minute. Anything that
        checked for it in that window - the gallery asking for a thumbnail,
        the player asked to show a proxy while the ingest was still making it
        - opened a half-written file. And if the program was closed or died
        mid-way, the stump stayed behind under the final name and was taken
        for finished forever after. Writing next to the final name and
        renaming at the end means the final name only ever exists complete.
        The extension is kept because ffmpeg picks the format from it.
        """
        # Process and thread in the name: the ingest thread and the proxy
        # worker can be asked for the same file at the same time.
        return final.with_name(f"{final.stem}.part{os.getpid()}-{threading.get_ident()}{final.suffix}")

    @classmethod
    def _discard(cls, partial: Path):
        try:
            long = cls.long_path(partial)
            if os.path.exists(long):
                os.unlink(long)
        except OSError:
            pass

    @classmethod
    def _commit_partial(cls, partial: Path, final: Path) -> bool:
        """Move a finished partial file into place, or clean it up."""
        src, dst = cls.long_path(partial), cls.long_path(final)
        # A file written a moment ago is often held briefly by a virus scanner
        # or the indexer ("being used by another process"); a large ingest lost
        # a thumbnail or two that way. A few short retries ride it out.
        import time
        for attempt in range(6):
            try:
                if os.path.exists(src) and os.path.getsize(src) > 0:
                    os.replace(src, dst)
                    return True
                logging.warning("Could not finish %s: the new file was not there to move "
                                "into place (path %d characters long).", final.name, len(str(final)))
                break
            except PermissionError as exc:
                if attempt == 5:
                    logging.warning("Could not finish %s: %s", final.name, exc)
                time.sleep(0.1 * (attempt + 1))
            except OSError as exc:
                logging.warning("Could not finish %s: %s", final.name, exc)
                break
        cls._discard(partial)
        return False

    def get_hash(self, path: Path) -> str:
        stat = os.stat(self.long_path(path))
        unique_str = f"{path}_{stat.st_mtime}_{stat.st_size}"
        return hashlib.md5(unique_str.encode()).hexdigest()

    def identity_hash(self, path: Path) -> str:
        """
        A name for this file that does not change when the file is touched.

        The cache name above includes the modification time, which is right for
        knowing when a picture is stale - but it means copying or re-syncing the
        stock renames every thumbnail, orphaning all the old ones. This second
        name stays put, so the old copies can be found and removed.
        """
        return hashlib.md5(str(path).encode()).hexdigest()

    def cache_path_for(self, file_hash: str, suffix: str, identity: str = "") -> Path:
        """
        Where a cached file belongs, in a folder shallow enough to stay quick.

        Everything used to go into one directory. At tens of thousands of assets
        that is tens of thousands of files in a single folder on a shared drive,
        which Windows file sharing handles badly - every lookup slows as it
        fills. Two characters of the name split it across 256 folders.
        """
        identity = identity or file_hash
        shard = identity[:2] or "00"
        folder = self.cache_dir / shard
        try:
            os.makedirs(self.long_path(folder), exist_ok=True)
        except OSError:
            folder = self.cache_dir
        # The steady name comes first so earlier copies of the same file can be
        # found and cleared; the changing part follows it.
        return folder / f"{identity}_{file_hash}{suffix}"

    def generate_thumbnail(self, input_path: Path, is_seq: bool = False) -> Tuple[bool, Path]:
        """Generate JPG. Retries at frame 0 for short clips."""
        if not self.ffmpeg_path: return False, None

        file_hash = self.get_hash(input_path)
        output_thumb = self.cache_path_for(
            file_hash, "_thumb.jpg", self.identity_hash(input_path))
        if self._usable(output_thumb): return True, output_thumb

        if is_seq:
            try:
                import re
                stem = input_path.stem
                match = re.search(r'(\d+)$', stem)
                base = stem[:match.start()] if match else stem
                glob_pattern = f"{base}*{input_path.suffix}"
                
                from slate.utils.sequence_detector import detect_sequence, format_pattern_with_frame
                seq_info = detect_sequence(input_path.parent, glob_pattern)
                if seq_info:
                    # Thumbnail at 5th frame (or last frame if shorter)
                    target_frame = seq_info['first_frame'] + 4
                    if target_frame > seq_info['last_frame']:
                        target_frame = seq_info['last_frame']
                    
                    frame_name = format_pattern_with_frame(seq_info['pattern'], target_frame)
                    input_path = input_path.parent / frame_name
                    is_seq = False # Downgrade to single image for thumbnail gen
            except Exception as e:
                logging.debug(f"Failed to find sequence 5th frame for thumb: {e}")

        try:
            is_image_file = is_image(input_path.suffix.lower())
            
            # OPTIMIZATION: Use QImage for standard images (Faster/Native)
            if is_image_file and input_path.suffix.lower() not in ['.exr', '.dpx', '.tif', '.tiff']: # EXR/DPX still need FFmpeg
                try:
                    img = QImage(str(input_path))
                    if not img.isNull():
                        # Scale to 320
                        scaled = img.scaled(320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        partial = self._partial_name(output_thumb)
                        if scaled.save(str(partial), "JPG", 80) and self._commit_partial(partial, output_thumb):
                            return True, output_thumb
                except Exception as exc:
                    logging.debug("QImage thumbnail path failed, falling back to ffmpeg: %s", exc)

            # ATTEMPT 1: Try seeking 1 second (skips slates/black frames)
            if not is_image_file:
                self._run_ffmpeg_thumb(input_path, output_thumb, seek_time="1")
            
            # ATTEMPT 2: If failed (or file too short), try Frame 0
            if not self._usable(output_thumb):
                self._run_ffmpeg_thumb(input_path, output_thumb, seek_time="0")

            return (True, output_thumb) if self._usable(output_thumb) else (False, None)


        except Exception as e:
            logging.exception(f"Thumb failed {input_path}: {e}")
            return False, None

    def _run_ffmpeg_thumb(self, input_path, output_path, seek_time="0"):
        """Helper to run the ffmpeg command."""
        output_path = Path(output_path)
        partial = self._partial_name(output_path)
        cmd = [
            self.ffmpeg_path, "-y",
            "-ss", seek_time,
            "-i", str(input_path),
            "-vf", "scale=320:-2",
            "-vframes", "1",
            "-q:v", "5",
            str(partial)
        ]
        startupinfo = None
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        # Add timeout
        try:
            logging.debug(f"Executing FFmpeg Thumb Cmd: {' '.join(cmd)}")
            # Capture output to see error
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, timeout=15, text=True)
            if result.returncode != 0:
                 logging.error(f"FFmpeg Failed. Return Code: {result.returncode}")
                 logging.debug(f"FFmpeg Stderr: {result.stderr}")
                 self._discard(partial)
            else:
                 logging.debug(f"FFmpeg finished cleanly for {input_path}")
                 self._commit_partial(partial, output_path)
                 
        except subprocess.TimeoutExpired:
            logging.error(f"FFmpeg thumb timeout: {input_path}")
            self._discard(partial)
            return  # Exit helper function after timeout
        except Exception as e:
            logging.exception(f"FFmpeg Exception: {e}")
            self._discard(partial)

    @classmethod
    def _sequence_input(cls, seq, partial: Path, fps: float = 24.0) -> list:
        """
        ffmpeg input arguments for every frame of a sequence, at the project's
        rate (it was always 24).

        A render with one frame missing made a proxy of the frames before the
        gap, reported it made, and the lineup played that instead of the shot:
        with a gap the frames are read through FrameSequence.ffconcat.
        """
        if not seq.missing_frames:
            return ["-framerate", f"{fps:g}", "-start_number", str(seq.start), "-i", seq.pattern]
        listing = partial.with_suffix(".txt")
        with open(cls.long_path(listing), "w", encoding="utf-8") as handle:
            handle.write(seq.ffconcat(seq.start, fps))
        return ["-f", "concat", "-safe", "0", "-i", str(listing), "-r", f"{fps:g}"]

    def parse_resolution(self, res_str: str) -> Tuple[int, int]:
        """Parse resolution string into (width, height) tuple."""
        parts = str(res_str or "").lower().split('x')
        try:
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except ValueError:
            pass
        return self.PROXY_BOX

    def get_proxy_codec(self) -> str:
        """Return default video codec for proxies."""
        return "libx264"

    def get_quality_settings(self, preset: str = "standard") -> dict:
        """Return encoder quality parameters for preset."""
        presets = {
            "draft": {"crf": 32, "preset": "ultrafast"},
            "standard": {"crf": 28, "preset": "fast"},
            "high": {"crf": 20, "preset": "medium"},
        }
        return presets.get(preset.lower(), presets["standard"])

    def generate_proxy(self, input_path: Path = None, is_seq: bool = False, source_path: Path = None,
                       proxy_path: Path = None, target_resolution: str = "1920x1080",
                       overwrite: bool = False, fps: float = 24.0) -> Tuple[bool, Path]:
        """
        A review proxy: a JPG for a still, an H.264 MP4 for a movie or sequence
        (a sequence at fps, its project's rate).

        A sequence is found from the frame named (is_seq). overwrite=True
        makes it again over one already there - "Rebuild all" used to hand
        back the old file untouched.
        """
        if input_path is None and source_path is not None:
            input_path = Path(source_path)
        if not input_path:
            return False, None
        if not self.ffmpeg_path:
            return False, None

        input_path = Path(input_path)
        file_hash = self.get_hash(input_path)
        is_image_file = is_image(input_path.suffix.lower())
        
        # Determine output format (JPG for single images, MP4 for sequences/videos)
        if proxy_path is not None:
            output_proxy = Path(proxy_path)
        elif is_image_file and not is_seq:
            output_proxy = self.cache_path_for(
                file_hash, "_proxy.jpg", self.identity_hash(input_path))
        else:
            output_proxy = self.cache_path_for(
                file_hash, "_proxy.mp4", self.identity_hash(input_path))
            
        if not overwrite and self._usable(output_proxy): return True, output_proxy
        partial = self._partial_name(output_proxy)

        seq = None
        try:
            cmd = [self.ffmpeg_path, "-y"]
            
            if is_image_file and not is_seq:
                # Generate a single 1920x1080 JPG proxy
                cmd.extend([
                    "-i", str(input_path),
                    "-vf", self.fit_filter(*self.parse_resolution(target_resolution)),
                    "-vframes", "1",
                    "-q:v", "2",  # High quality JPG
                    str(partial)
                ])
            else:
                # Generate MP4 proxy
                # Long-path form: past 260 characters the frames were not found
                # and a one-frame proxy was made of the frame named.
                seq = sequence_for(Path(self.long_path(input_path))) if is_seq else None
                if seq is not None:
                    cmd.extend(self._sequence_input(seq, partial, fps))
                else:
                    cmd.extend(["-i", str(input_path)])

                cmd.extend([
                    "-vf", self.fit_filter(*self.parse_resolution(target_resolution)) + ",format=yuv420p",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "28",
                    "-an",
                    str(partial)
                ])

            startupinfo = None
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            # LOW CPU PRIORITY
            creationflags = 0
            if sys.platform == 'win32':
                creationflags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW

            # A hung ffmpeg is stopped, but a flat minute failed long EXR
            # shots: the limit grows with the frames.
            limit = 60 + self.SECONDS_PER_FRAME * (seq.frame_count if seq is not None else 0)
            try:
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo, creationflags=creationflags, timeout=limit)
            finally:
                self._discard(partial.with_suffix(".txt"))  # a gap-filling frame list
            if result.returncode == 0 and self._commit_partial(partial, output_proxy):
                return True, output_proxy
            self._discard(partial)
            return False, None

        except Exception as e:
            logging.exception(f"Proxy failed {input_path}: {e}")
            self._discard(partial)
            return False, None
            

# GLOBAL INSTANCE
proxy_manager = ProxyManager()
