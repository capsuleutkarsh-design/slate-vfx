import os
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

FFMPEG_MISSING = "Previews need FFmpeg, which is missing on this machine - tell IT."


def fit_decode_size(native_w, native_h, target_w, target_h, cap_w=1920):
    """
    The size to decode at: the picture's own shape, no bigger than needed.

    It used to be a fixed 1280x720 whatever the file was, so scope, square,
    portrait and phone clips were squashed into 16:9 (MED-028). Now the
    native aspect is kept; the long edge comes down to about 1.5x the space
    on screen (never under 720) and never above 1920 wide. Both sides even.
    """
    try:
        native_w, native_h = int(native_w or 0), int(native_h or 0)
    except (TypeError, ValueError):
        native_w = native_h = 0
    if native_w <= 0 or native_h <= 0:
        native_w, native_h = 1280, 720
    w, h = float(native_w), float(native_h)
    long_native = max(w, h)
    target_long = max(int(target_w or 0), int(target_h or 0))
    if target_long > 0 and long_native > target_long * 1.2:
        desired = max(int(target_long * 1.5), 720)
        if desired < long_native:
            ratio = desired / long_native
            w, h = w * ratio, h * ratio
    if w > cap_w:
        ratio = cap_w / w
        w, h = w * ratio, h * ratio
    w, h = max(2, int(round(w))), max(2, int(round(h)))
    return w + (w % 2), h + (h % 2)


class StreamEngine(BaseMediaEngine):
    """
    FFmpeg-based engine for Video Streams (MOV, MP4, MKV).
    Uses Producer-Consumer pattern with a Queue to buffer frames and prevent stuttering.

    Loading probes the file first (on a thread) and only then starts decoding,
    at the file's own aspect ratio. Until play() is asked for, the first frame
    is shown and the engine stays paused (MED-109) - it used to run while the
    player's button said it was paused.
    """
    # fps, total frames, width, height, load token - from the background probe
    metadata_resolved = Signal(float, int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.running = False
        self.paused = True
        self.loop = True
        self.playback_speed = 1.0

        # Buffer
        self.frame_queue = queue.Queue(maxsize=16)
        self.producer_thread = None
        self._last_error = ""

        # Playback Timer (Consumer)
        self.playback_timer = QTimer(self)
        self.playback_timer.timeout.connect(self._consumer_tick)

        self.seek_request = -1.0
        self.ff_path = self._find_ffmpeg()

        # The frame on screen. _shown says whether it has been shown yet: after
        # a load or a seek the next frame out of ffmpeg IS current_frame;
        # after that each one is the frame after it (MED2-006).
        self.current_frame = 0
        self._shown = False
        # Bumped by every seek; frames decoded before it are dropped, so a
        # seek never shows a frame from the old place under the new number.
        self._gen = 0
        self.total_frames = 1
        self.fps = 24.0
        self.native_size = (0, 0)
        self.render_w, self.render_h = 1280, 720
        self._ready = False
        self._wants_play = False
        self._load_token = 0

        self.metadata_resolved.connect(self._on_metadata_resolved)

    def _find_ffmpeg(self):
        resolved = ResourcePathManager.get_ffmpeg_path()
        if Path(str(resolved)).exists():
            return str(resolved)

        from shutil import which
        if resolved and which(str(resolved)):
            return str(resolved)
        return None

    # ------------------------------------------------------------- state
    def is_playing(self) -> bool:
        return bool(self._wants_play and not self.paused)

    def _set_playing(self, playing: bool):
        self.state_changed.emit(bool(playing))

    def position_seconds(self) -> float:
        return (self.current_frame / self.fps) if self.fps > 0 else 0.0

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
        """
        Stop ffmpeg and the producer thread, then let go of the pipes.

        In that order: the pipes used to be closed (by the tracker) while the
        producer thread could still be reading them, which crashes natively on
        Windows. Killing ffmpeg ends the read; the thread is joined; only then
        are the pipes closed.
        """
        self.running = False
        proc = self.process
        self.process = None
        if proc is not None:
            try:
                proc.kill()
            except (OSError, subprocess.SubprocessError) as e:
                logging.debug(f"Failed to stop FFmpeg process cleanly: {e}")

        if self.producer_thread and self.producer_thread.is_alive():
            if threading.current_thread() != self.producer_thread:
                self.producer_thread.join(timeout=1.5)
                if self.producer_thread.is_alive():
                    logging.warning("StreamEngine: Producer thread stuck, ignoring...")
        self.producer_thread = None

        if proc is not None:
            subprocess_tracker.unregister(proc)
            try:
                proc.wait(timeout=1.0)
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
                logging.debug(f"FFmpeg did not exit cleanly: {e}")

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

    # ------------------------------------------------------------- loading
    def load(self, source_path: str):
        self.stop()
        self.source = source_path
        self._load_token += 1
        self._ready = False
        self._wants_play = False
        self.paused = True
        self.current_frame = 0
        self._shown = False

        if not self.ff_path:
            try:
                checked_path = ResourcePathManager.describe_tool_search("ffmpeg")
            except Exception:
                checked_path = "Unknown"
            # Where it was looked for is for IT: in the log (MED2-059).
            logging.error("FFmpeg not found. Checked: %s; also PATH and Slate_FFMPEG_PATH.",
                          checked_path)
            self.error_occurred.emit(FFMPEG_MISSING)
            return

        self.fps = 24.0
        self.total_frames = 1
        self._resolve_metadata_async(source_path, self._load_token)

    def _probe(self, source_path: str) -> dict:
        """fps, frames, width, height of the file (runs on a thread)."""
        from ....core.domain.metadata_engine import SmartMetadataManager
        meta = SmartMetadataManager.extract_tech_metadata(source_path)
        fps = float(meta.get('fps', 0.0) or 0.0) or 24.0
        duration_sec = float(meta.get('duration_sec', 0.0) or 0.0)
        frames = int(round(duration_sec * fps)) if duration_sec > 0 else 0
        return {"fps": fps, "frames": frames, "width": int(meta.get("width") or 0),
                "height": int(meta.get("height") or 0)}

    def _resolve_metadata_async(self, source_path: str, token: int = 0):
        """Probe in the background; decoding starts when the answer is back."""
        def _task():
            try:
                info = self._probe(source_path)
            except Exception as exc:
                logging.debug("Async metadata probe failed for %s: %s", source_path, exc)
                info = {"fps": 24.0, "frames": 0, "width": 0, "height": 0}
            if token != self._load_token:
                # Stopped or another file loaded meanwhile: nothing to say. An
                # emit into an engine being torn down could take the program
                # down with it (a native crash in the test run).
                return
            try:
                self.metadata_resolved.emit(float(info["fps"]), int(info["frames"]),
                                            int(info["width"]), int(info["height"]), int(token))
            except RuntimeError:
                pass  # the player was closed while the file was being probed

        threading.Thread(target=_task, daemon=True, name="slate-metadata-probe").start()

    def _on_metadata_resolved(self, fps: float, total_frames: int, width: int = 0,
                              height: int = 0, token: int = 0):
        """On the GUI thread: size the decode to the picture and start it."""
        if token and token != self._load_token:
            return  # an answer about a file that is no longer loaded
        self.fps = fps if fps > 0 else 24.0
        self.total_frames = max(1, int(total_frames or 0) or self.total_frames or 1)
        self.native_size = (int(width or 0), int(height or 0))
        tw, th = self.target_size
        ratio = max(1.0, float(self.pixel_ratio or 1.0))
        self.render_w, self.render_h = fit_decode_size(width, height, tw * ratio, th * ratio)
        self.duration_changed.emit(self.total_frames)
        if self._ready:
            return
        self._ready = True
        self.current_frame = 0
        self._shown = False
        self._start_producer(0)
        if self._wants_play:
            self.paused = False
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.start(interval)
            self._set_playing(True)
        else:
            self.paused = True
            self._force_frame_pull()

    def _video_args(self):
        """Fit inside the decode size keeping the shape; pad the rest so every frame is W x H."""
        w, h = self.render_w, self.render_h
        return ['-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                       f'pad={w}:{h}:-1:-1:color=black']

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
            '-an',
        ] + self._video_args() + [
            '-f', 'rawvideo', '-pix_fmt', 'rgba',
            '-'
        ])
        return self._popen(cmd)

    def _popen(self, cmd):
        """Start ffmpeg with its picture on stdout and what it says kept (shared with sequences)."""
        startupinfo = None
        creationflags = 0
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        # stderr goes to a pipe of our own, read and closed only by the drain
        # thread. Through stderr=subprocess.PIPE the process tracker closed it
        # while the drain thread was still reading - a native crash on Windows
        # that took the whole program down when clips were switched quickly.
        err_read, err_write = os.pipe()
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=err_write,
                bufsize=self.render_w*self.render_h*4*4, startupinfo=startupinfo, creationflags=creationflags
            )
        except Exception:
            os.close(err_read)
            raise
        finally:
            os.close(err_write)
        stderr_stream = os.fdopen(err_read, "rb")

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

        threading.Thread(target=_drain, args=(stderr_stream,), daemon=True,
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
        gen = self._gen

        while self.running and self.process:
            # 1. Handle Seek Request
            if self.seek_request >= 0:
                gen = self._gen
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

                    if self.current_frame == 0 and not self._shown and self.frame_queue.empty():
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
                        self.frame_queue.put((gen, img), timeout=0.1)
                        break
                    except queue.Full:
                        if self.seek_request >= 0:
                            break
                        continue

            except Exception as e:
                logging.exception(f"Producer Error (Frame Read): {e}")
                break

        # Stopped while restarting: a process launched after the stop is ours
        # to end (stop() had already taken the one before it).
        if not self.running and self.process is not None:
            leftover, self.process = self.process, None
            try:
                leftover.kill()
            except (OSError, subprocess.SubprocessError):
                pass
            subprocess_tracker.unregister(leftover)

    def stop(self):
        was_playing = self.is_playing()
        self._load_token += 1          # a probe still running is for nothing now
        self.running = False
        self.paused = True
        self._wants_play = False
        self._ready = False
        self.playback_timer.stop()
        self._stop_producer()

        # Clear Queue
        with self.frame_queue.mutex:
            self.frame_queue.queue.clear()
        if was_playing:
            self._set_playing(False)

    def _consumer_tick(self):
        """Called by QTimer to show next frame."""
        if self.paused:
            return

        if self.frame_queue.empty():
            # Check if producer finished and buffer is exhausted
            if self.producer_thread and not self.producer_thread.is_alive():
                self.playback_timer.stop()
                self.paused = True
                self._wants_play = False
                self._set_playing(False)
                self.finished.emit()
            return

        img = self._take()
        if img is not None:
            self._show(img)

    def _take(self):
        """The next decoded frame of the current place, or None (older ones are dropped)."""
        while True:
            try:
                gen, img = self.frame_queue.get_nowait()
            except queue.Empty:
                return None
            if gen == self._gen:
                return img

    def _show(self, img):
        """
        Put the next decoded frame on screen and count it.

        current_frame is the frame on screen, so step, seek, the readout and a
        snapshot all mean the same frame (MED2-006). It never passes the last
        frame: ffmpeg can hand over a frame or two beyond the probed length,
        which read '148 / 144' (MED2-055).
        """
        if self._shown:
            last = max(0, self.total_frames - 1)
            if self.current_frame >= last and self.loop and self.total_frames > 1:
                self.current_frame = 0
                self.looped.emit()
            else:
                self.current_frame = min(self.current_frame + 1, last)
        self._shown = True
        self.frame_ready.emit(img)
        self.position_changed.emit(self.current_frame)

    def _force_frame_pull(self, attempts: int = 60):
        """Forces the consumer to grab a single frame even if paused (e.g. for seeking/stepping)."""
        try:
            img = self._take()
            if img is not None:
                self._show(img)
            elif self.running and attempts > 0:
                # Buffer empty, check again shortly. Tied to this engine, so a
                # retry never fires after the player has been closed.
                QTimer.singleShot(50, self, lambda: self._force_frame_pull(attempts - 1))
        except RuntimeError:
            pass  # the engine was deleted under a pending retry

    def seek(self, frame_num):
        if self.fps > 0:
            frame_num = max(0, min(int(frame_num), max(0, self.total_frames - 1)))
            # The new place first, then the request: the producer tags what it
            # decodes after the request with the place it read.
            self._gen += 1
            with self.frame_queue.mutex:
                self.frame_queue.queue.clear()
            self.current_frame = frame_num   # the next frame out is this one
            self._shown = False
            self.seek_request = frame_num / self.fps
            if not self.producer_thread or not self.producer_thread.is_alive():
                if self.source and self._ready:
                    self._start_producer(frame_num)
            if self.paused:
                self._force_frame_pull()

    def step(self, frames):
        if frames > 0 and not self.frame_queue.empty():
            # Forward step: consume next frame from existing buffer (no FFmpeg restart)
            for _ in range(frames):
                img = self._take()
                if img is None:
                    break
                self._show(img)
            self.pause()
        else:
            # Backward step: must seek (unavoidable with streams)
            target = self.current_frame + frames
            target = max(0, min(target, self.total_frames - 1))
            self.pause()
            self.seek(target)

    def set_speed(self, speed):
        self.playback_speed = max(0.1, float(speed))
        if self.playback_timer.isActive():
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.setInterval(interval)

    def set_loop(self, loop):
        self.loop = loop

    def play(self):
        """Resume playback (or start it as soon as the file is ready)."""
        self._wants_play = True
        if not self._ready:
            return
        was = self.is_playing()
        self.paused = False
        # If producer thread is not active, restart from current frame or beginning
        if not self.producer_thread or not self.producer_thread.is_alive():
            if self.source:
                # On from the frame after the one shown; from the top at the end.
                start_frame = self.current_frame + (1 if self._shown else 0)
                if start_frame >= self.total_frames:
                    start_frame = 0
                self.current_frame = start_frame
                self._shown = False
                self._start_producer(start_frame)
        if not self.playback_timer.isActive():
            interval = max(1, int((1000 / self.fps) / self.playback_speed))
            self.playback_timer.start(interval)
        if not was:
            self._set_playing(True)

    def pause(self):
        """Pause playback."""
        was = self.is_playing()
        self._wants_play = False
        self.paused = True
        self.playback_timer.stop()
        if was:
            self._set_playing(False)
