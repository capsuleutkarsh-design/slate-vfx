import sys
import time
import logging
import subprocess
import threading
import queue
from pathlib import Path
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QImage

from .base_engine import BaseMediaEngine
from ....utils.resource_manager import ResourcePathManager
from ....utils.process_manager import subprocess_tracker

class StreamEngine(BaseMediaEngine):
    """
    FFmpeg-based engine for Video Streams (MOV, MP4, MKV).
    Uses Producer-Consumer pattern with a Queue to buffer frames and prevent stuttering.
    """
    # Signal emitted when background metadata probe finishes, to safely update Qt state on GUI thread
    metadata_resolved = Signal(float, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.running = False
        self.paused = False
        self.loop = True
        self.playback_speed = 1.0
        
        # Buffer
        self.frame_queue = queue.Queue(maxsize=16) # Buffer ~0.67 sec at 24fps (~128MB at 1080p RGBA)
        self.producer_thread = None
        self._last_error = ""
        
        # Playback Timer (Consumer)
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self._consumer_tick)
        
        self.seek_request = -1.0 
        self.ff_path = self._find_ffmpeg()

        self.current_frame = 0
        self.total_frames = 100
        self.fps = 24.0

        # Thread-safe metadata resolution connection
        self.metadata_resolved.connect(self._on_metadata_resolved)

    def _find_ffmpeg(self):
        resolved = ResourcePathManager.get_ffmpeg_path()
        if Path(str(resolved)).exists():
            return str(resolved)

        from shutil import which
        if resolved and which(str(resolved)):
            return str(resolved)
        return None

    def _on_metadata_resolved(self, fps: float, total_frames: int):
        """Runs safely on the main GUI thread when background probe completes."""
        self.fps = fps
        self.total_frames = max(1, total_frames)
        self.duration_changed.emit(self.total_frames)
        if self.playback_timer.isActive():
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.setInterval(interval)

    @staticmethod
    def _read_exact(stream, n: int) -> bytes:
        """Reads exactly n bytes from stream unless EOF is encountered."""
        data = bytearray()
        while len(data) < n:
            chunk = stream.read(n - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    def _stop_producer(self):
        """Stops and cleans up the producer thread and FFmpeg process."""
        self.running = False
        if self.process:
            subprocess_tracker.unregister(self.process)
            try:
                self.process.kill()
                try:
                    self.process.stdout.read()
                except (AttributeError, OSError, ValueError):
                    pass
                self.process.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
                logging.debug(f"Failed to stop FFmpeg process cleanly: {e}")
            self.process = None

        if self.producer_thread and self.producer_thread.is_alive():
            if threading.current_thread() != self.producer_thread:
                self.producer_thread.join(timeout=1.5)
                if self.producer_thread.is_alive():
                    logging.warning("StreamEngine: Producer thread stuck, ignoring...")
        self.producer_thread = None

    def _start_producer(self, start_frame: int = 0):
        """Starts the background producer thread."""
        self._stop_producer()
        self.running = True
        start_time = (start_frame / self.fps) if self.fps > 0 else 0
        self.producer_thread = threading.Thread(
            target=self._producer_loop,
            args=(self.render_w, self.render_h, start_time),
            name="slate-stream-producer",
            daemon=True
        )
        self.producer_thread.start()

    def load(self, source_path: str):
        self.stop()
        self.source = source_path
        
        if not self.ff_path:
            # Re-resolve to show user where we checked
            try:
                checked_path = ResourcePathManager.describe_tool_search("ffmpeg")
            except Exception:
                checked_path = "Unknown"
            self.error_occurred.emit(
                "FFmpeg not found.\n"
                f"Checked: {checked_path}\n"
                "Also searched system PATH and Slate_FFMPEG_PATH."
            )
            return

        try:
            # Use safe defaults immediately to avoid UI stalls on heavy probe calls.
            self.fps = 24.0
            self.total_frames = 100
            
            # --- OPTIMIZED RESOLUTION SCALING ---
            # Use target size to avoid decoding 4K/8K for small widgets
            target_w, target_h = self.target_size
            meta = {}
            native_w = meta.get('width', 1280)
            native_h = meta.get('height', 720)
            
            decode_w, decode_h = native_w, native_h
            
            # If we know the target size (e.g. 300px sidebar), downscale
            if target_w > 0 and target_h > 0:
                # Calculate scale required to fit
                native_long = max(native_w, native_h)
                target_long = max(target_w, target_h)
                
                # Only downscale if native is significantly larger (>1.2x target)
                if native_long > (target_long * 1.2):
                    # Use target * 1.5 as buffer for zoom/sharpness
                    desired_long = int(target_long * 1.5)
                    # Don't go below 480p equivalent (720px width/height) unless target is very small
                    desired_long = max(desired_long, 720) 
                    
                    if desired_long < native_long:
                        ratio = desired_long / native_long
                        decode_w = int(native_w * ratio)
                        decode_h = int(native_h * ratio)

            # Safety Cap: 1080p Max (1920x1080)
            # Prevents UI starvation on massive files
            if decode_w > 1920:
                scale = 1920 / decode_w
                decode_w = 1920
                decode_h = int(decode_h * scale)
                
            # Alignment for FFmpeg (Must be even)
            if decode_w % 2 != 0: decode_w += 1
            if decode_h % 2 != 0: decode_h += 1
            
            self.render_w = decode_w
            self.render_h = decode_h
            # ------------------------------------
            
            self.duration_changed.emit(self.total_frames)
            
            # Start Producer
            self.current_frame = 0
            self.paused = False
            self._start_producer(0)
            
            # Start Consumer
            interval = max(1, int(1000 / self.fps))
            self.playback_timer.start(interval)
            self._resolve_metadata_async(source_path)
            
        except Exception as e:
            self.error_occurred.emit(f"Stream Load Failed: {e}")

    def _resolve_metadata_async(self, source_path: str):
        """Resolve fps/duration in background and patch playback timing when ready via Qt signal."""
        def _task():
            try:
                from ....core.domain.metadata_engine import SmartMetadataManager
                meta = SmartMetadataManager.extract_tech_metadata(source_path)
                fps = float(meta.get('fps', 0.0) or 0.0)
                if fps <= 0:
                    fps = 24.0
                duration_sec = float(meta.get('duration_sec', 0.0) or 0.0)
                total_frames = int(duration_sec * fps) if duration_sec > 0 else self.total_frames
                # Safe cross-thread signal emission to main Qt event loop
                self.metadata_resolved.emit(fps, total_frames)
            except Exception as exc:
                logging.debug("Async metadata probe failed for %s: %s", source_path, exc)

        threading.Thread(target=_task, daemon=True, name="slate-metadata-probe").start()

    def _launch_ffmpeg(self, start_time_sec=0):
        """Helper to launch FFmpeg process."""
        cmd = [self.ff_path]
        
        # Enable GPU Acceleration for a balanced CPU/GPU decoding workload
        cmd.extend(['-hwaccel', 'auto']) 
        
        if start_time_sec > 0:
            cmd.extend(['-ss', str(start_time_sec)])
            
        cmd.extend([
            '-loglevel', 'error',
            '-probesize', '10000000',
            '-i', self.source,
            '-f', 'rawvideo', '-pix_fmt', 'rgba',
            '-s', f'{self.render_w}x{self.render_h}',
            '-'
        ])
        
        startupinfo = None
        creationflags = 0
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=self.render_w*self.render_h*4*4, startupinfo=startupinfo, creationflags=creationflags
        )

        # Keep what ffmpeg says. It used to go to DEVNULL, so when a file would
        # not play there was no way to find out why - the picture simply stayed
        # blank or stopped after a few frames. The pipe is drained on a thread
        # because a full stderr pipe would block ffmpeg itself.
        self._last_error = ""
        def _drain(stream):
            try:
                for line in iter(stream.readline, b""):
                    text = line.decode("utf-8", "replace").strip()
                    if text:
                        self._last_error = text
                        logging.debug("ffmpeg: %s", text)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        threading.Thread(target=_drain, args=(proc.stderr,), daemon=True,
                         name="slate-ffmpeg-stderr").start()
        return subprocess_tracker.register(proc)

    def _restart_ffmpeg_at(self, start_time_sec):
        """Restarts the FFmpeg process at a specific time."""
        if self.process:
            try:
                self.process.kill()
                self.process.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
                logging.debug(f"Failed to stop FFmpeg process before restart: {e}")
            # Hand it back, or the tracker keeps it - and its pipe - forever.
            # Scrubbing restarts ffmpeg constantly, so this leaked a file
            # handle per drag until playback stopped working altogether.
            subprocess_tracker.unregister(self.process)
            self.process = None
            
        try:
            self.process = self._launch_ffmpeg(start_time_sec)
        except Exception as e:
            logging.exception(f"FFmpeg Restart Failed: {e}")
            self.process = None

    def _producer_loop(self, width, height, start_time_sec=0):
        """Reads frames from FFmpeg and puts them in Queue."""
        # Capture dimensions locally to avoid race condition if self.render_w changes during reload
        render_w = width
        render_h = height
        self.render_w = width
        self.render_h = height
        
        try:
            self.process = self._launch_ffmpeg(start_time_sec)
        except Exception as e:
            self.error_occurred.emit(f"FFmpeg Launch Failed: {e}")
            return
        
        frame_size = render_w * render_h * 4
        consecutive_restarts = 0
        
        while self.running and self.process:
            # 1. Handle Seek Request
            if self.seek_request >= 0:
                with self.frame_queue.mutex:
                    self.frame_queue.queue.clear()
                self._restart_ffmpeg_at(self.seek_request)
                self.seek_request = -1.0
                consecutive_restarts = 0
                continue
                
            # 2. Read Frame using accumulation buffer
            try:
                if not self.process or not self.process.stdout:
                    break

                raw = self._read_exact(self.process.stdout, frame_size)
                
                if len(raw) < frame_size:
                    if not self.running:
                        break

                    # Did it end, or did it fall over? The two were treated
                    # identically, so a file that failed part-way through
                    # looked exactly like one that had finished playing.
                    exit_code = self.process.poll() if self.process else None
                    reason = (self._last_error or "").strip()
                    short = self.total_frames > 0 and self.current_frame < (self.total_frames - 2)
                    broke = (exit_code not in (0, None)) or (short and reason)

                    if self.current_frame == 0:
                        self.error_occurred.emit(
                            reason or "This file produced no picture. It may be an "
                                      "unsupported format, or unreadable from here."
                        )
                        break

                    if broke and not self.loop:
                        self.error_occurred.emit(
                            f"Playback stopped early at frame {self.current_frame}"
                            + (f": {reason}" if reason else
                               ". The file may be slow or unreachable over the network.")
                        )
                        break
                    
                    if self.loop and self.running:
                        consecutive_restarts += 1
                        if consecutive_restarts > 5:
                            logging.error("StreamEngine: Excessive restart attempts in loop, stopping producer.")
                            break
                        time.sleep(0.02)  # Backoff to avoid tight spin
                        self._restart_ffmpeg_at(0)
                        if not self.process:
                            break
                        continue
                    else:
                        break # Stop on natural EOF
                
                consecutive_restarts = 0
                
                # 3. Create Image and push to queue
                img = QImage(raw, render_w, render_h, render_w * 4, QImage.Format_RGBA8888).copy()
                
                while self.running:
                    try:
                        self.frame_queue.put(img, timeout=0.1)
                        break
                    except queue.Full:
                        if self.seek_request >= 0:
                            break
                        continue
            
            except Exception as e:
                logging.exception(f"Producer Error (Frame Read): {e}")
                break

    def stop(self):
        self.running = False
        self.paused = False
        self.playback_timer.stop()
        self._stop_producer()

        # Clear Queue
        with self.frame_queue.mutex:
            self.frame_queue.queue.clear()

    def _consumer_tick(self):
        """Called by QTimer to show next frame."""
        if self.paused: 
            return

        if self.frame_queue.empty():
            # Check if producer finished and buffer is exhausted
            if self.producer_thread and not self.producer_thread.is_alive():
                self.playback_timer.stop()
                self.paused = True
                self.finished.emit()
            return

        try:
            img = self.frame_queue.get_nowait()
            self.frame_ready.emit(img)
            self.position_changed.emit(self.current_frame)
            self.current_frame += 1
            if self.loop and self.total_frames > 0 and self.current_frame >= self.total_frames:
                self.current_frame = 0
            
        except queue.Empty:
            pass

    def _force_frame_pull(self):
        """Forces the consumer to grab a single frame even if paused (e.g. for seeking/stepping)."""
        try:
            img = self.frame_queue.get_nowait()
            self.frame_ready.emit(img)
            self.position_changed.emit(self.current_frame)
        except queue.Empty:
            if self.running:
                # Buffer empty, check again shortly
                QTimer.singleShot(50, self._force_frame_pull)

    def seek(self, frame_num):
        if self.fps > 0:
            self.seek_request = frame_num / self.fps
            self.current_frame = frame_num # Immediate UI update prediction
            if not self.producer_thread or not self.producer_thread.is_alive():
                if self.source:
                    self._start_producer(frame_num)
            if self.paused:
                self._force_frame_pull()
    
    def step(self, frames):
        if frames > 0 and not self.frame_queue.empty():
            # Forward step: consume next frame from existing buffer (no FFmpeg restart)
            try:
                for _ in range(frames):
                    if self.frame_queue.empty():
                        break
                    img = self.frame_queue.get_nowait()
                    self.frame_ready.emit(img)
                    self.current_frame += 1
                self.position_changed.emit(self.current_frame)
            except queue.Empty:
                pass
            self.pause()
        else:
            # Backward step: must seek (unavoidable with streams)
            target = self.current_frame + frames
            target = max(0, min(target, self.total_frames))
            self.seek(target)
            self.pause()
    
    def set_speed(self, speed):
        self.playback_speed = max(0.1, float(speed))
        if self.playback_timer.isActive():
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.setInterval(interval)
    
    def set_loop(self, loop):
        self.loop = loop

    def play(self):
        """Resume playback."""
        self.paused = False
        # If producer thread is not active, restart from current frame or beginning
        if not self.producer_thread or not self.producer_thread.is_alive():
            if self.source:
                start_frame = self.current_frame
                if self.total_frames > 0 and start_frame >= self.total_frames:
                    start_frame = 0
                    self.current_frame = 0
                self._start_producer(start_frame)
        if not self.playback_timer.isActive():
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.start(interval)

    def pause(self):
        """Pause playback."""
        self.paused = True
        self.playback_timer.stop()
