import logging
import gc
from PySide6.QtCore import Qt, QTimer, QThread
from PySide6.QtCore import Qt, QTimer, QThread
from PySide6.QtGui import QImage, QPixmap
import numpy as np
from ..comparison_viewer import ImageLoaderWorker

class ViewerLoaderMixin:

    def _load_shot_internal(self, shot):
            """
            Internal shot loading logic (actual implementation)

            Args:
                shot: ReviewShot object
            """
            if not shot:
                raise ValueError("Shot is None")

            self.current_shot = shot

            try:
                # Clear previous images
                if hasattr(self, 'scan_viewer'):
                    self.scan_viewer.clear_image()
                if hasattr(self, 'render_viewer'):
                    self.render_viewer.clear_image()
            except Exception as e:
                logger.warning(f"Failed to clear viewers: {e}")

            # Check if shot is complete
            if not shot.is_complete():
                missing = []
                if not shot.has_scan():
                    missing.append("scan (EXR disabled for safety)")
                if not shot.has_render():
                    missing.append("render")

                if missing:
                    error_msg = f"\u26A0\uFE0F {', '.join(missing)} unavailable"
                    if hasattr(self, 'frame_info'):
                        self.frame_info.setText(error_msg)
                # Continue loading - show whatever media IS available

            # Load first frame (with safety checks)
            try:
                if shot.frame_range:
                    start, end = shot.frame_range

                    # Validate frame range
                    if not isinstance(start, int) or not isinstance(end, int):
                        raise ValueError(f"Invalid frame range: {shot.frame_range}")

                    if start > end:
                        raise ValueError(f"Frame range invalid: start ({start}) > end ({end})")

                    # Setup slider
                    if hasattr(self, 'frame_slider'):
                        self.frame_slider.setRange(start, end)
                        self.frame_slider.setValue(start)
                        self.frame_slider.setEnabled(True)

                    # Enable controls
                    self.enable_controls()

                    # Reset grading state safety
                    self.is_interactive_grading = False

                    # Set FPS if available (Metadata priority)
                    self.playback_fps = 24.0
                    if self.current_shot:
                        if hasattr(self.current_shot, 'metadata') and isinstance(self.current_shot.metadata, dict) and 'fps' in self.current_shot.metadata:
                            try:
                                fps_val = float(self.current_shot.metadata['fps'])
                                if 1.0 <= fps_val <= 120.0:  # Sanity check
                                    self.playback_fps = fps_val
                            except (ValueError, TypeError):
                                logger.debug(f"Invalid FPS value: {self.current_shot.metadata.get('fps')}")
                        elif hasattr(self.current_shot, 'fps'):
                            try:
                                fps_val = float(self.current_shot.fps)
                                if 1.0 <= fps_val <= 120.0:
                                    self.playback_fps = fps_val
                            except (ValueError, TypeError):
                                logger.debug(f"Invalid FPS attribute: {self.current_shot.fps}")

                        if hasattr(self, 'playback_timer'):
                            interval = int(1000.0 / max(1.0, self.playback_fps))  # Avoid div by zero
                            self.playback_timer.setInterval(interval)

                    # Load first frame (async via worker)
                    self.current_frame = start
                    try:
                        self.load_frame(start)
                    except Exception as load_err:
                        logger.error(f"Failed to load frame {start}: {load_err}", exc_info=True)
                        if hasattr(self, 'frame_info'):
                            self.frame_info.setText(f"\u274C Failed to load frame: {str(load_err)[:30]}")
                else:
                    if hasattr(self, 'frame_info'):
                        self.frame_info.setText("\u274C No frame range detected")
                    if not shot.render_path or not str(shot.render_path).lower().endswith(('.mov', '.mp4', '.mkv', '.avi')):
                        # Only disable if it's not a video rendering fallback
                        self.disable_controls()
            except Exception as frame_err:
                logger.error(f"Frame loading setup failed: {frame_err}", exc_info=True)
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(f"\u274C Frame setup error: {str(frame_err)[:30]}")

            # VIDEO MODE DETECTION (wrapped with error handling)
            self.is_video_mode = False
            try:
                if shot.render_path:
                    render_path_str = str(shot.render_path).lower()
                    if render_path_str.endswith(('.mov', '.mp4', '.mkv', '.avi')):
                        self.is_video_mode = True

                        # Setup Video Player
                        if hasattr(self, 'render_viewer'):
                            self.render_viewer.hide()
                        if hasattr(self, 'video_player'):
                            self.video_player.show()

                            try:
                                self.video_player.load(shot.render_path)
                                logger.debug(f"Video player loading: {shot.render_path}")
                            except Exception as vp_err:
                                logger.error(f"Video player load failed: {vp_err}", exc_info=True)
                                self.is_video_mode = False
                                if hasattr(self, 'frame_info'):
                                    self.frame_info.setText(f"\u26A0\uFE0F Video load failed: {str(vp_err)[:25]}")

                        # Enable controls for video playback
                        self.enable_controls()

                        # Calculate Offset (Slider Start vs Video 0)
                        if shot.frame_range:
                            self.video_start_offset = shot.frame_range[0]
                        else:
                            self.video_start_offset = 0
                            self.current_frame = 0

                        # Connect Sync - First connection to new player, no need to disconnect
                        # Only disconnect if we're reconnecting to an existing player
                        try:
                            if hasattr(self, '_video_player_previous'):
                                try:
                                    self._video_player_previous.frame_changed.disconnect(self.sync_from_video)
                                except (TypeError, RuntimeError):
                                    pass

                            # Always safe to connect to new player
                            self.video_player.frame_changed.connect(self.sync_from_video)
                            self._video_player_previous = self.video_player
                        except Exception as conn_err:
                            logger.warning(f"Failed to connect video player signals: {conn_err}")

                        # Attempt to set range when video is loaded
                        try:
                            if not shot.frame_range and hasattr(self.video_player, 'total_frames'):
                                if self.video_player.total_frames > 0:
                                    if hasattr(self, 'frame_slider'):
                                        self.frame_slider.setRange(0, self.video_player.total_frames)

                            # Connect duration_changed to sync slider range
                            if not shot.frame_range:
                                # Safe to connect directly on new player (no previous connection)
                                self.video_player.duration_changed.connect(self._on_video_duration_changed)
                        except Exception as dur_err:
                            logger.debug(f"Video duration setup failed: {dur_err}")
            except Exception as video_err:
                logger.error(f"Video mode setup failed: {video_err}", exc_info=True)
                self.is_video_mode = False

            else:
                # Standard Image Mode
                self.video_player.hide()
                self.render_viewer.show()
                self.video_player.stop_media()

    def _load_frame_internal(self, frame_number: int, skip_video_seek=False):
            """
            Internal frame loading logic (actual implementation)

            Args:
                frame_number: Frame number to load
                skip_video_seek: If True, do not seek video player (avoid loops)
            """
            if not self.current_shot:
                logger.debug("No shot loaded, skipping frame load")
                return

            shot = self.current_shot

            logger.debug(f"load_frame called for frame {frame_number}")

            # UPDATE current_frame BEFORE checking cache (important for race condition check)
            self.current_frame = frame_number

            # Try Cache First (with safety checks)
            try:
                cache_key_scan = f"{shot.id}_scan_{frame_number}"
                cache_key_render = f"{shot.id}_render_{frame_number}"

                cached_scan = None
                cached_render = None

                if hasattr(self, 'cache'):
                    cached_scan = self.cache.get(cache_key_scan)
                    cached_render = self.cache.get(cache_key_render)

                if cached_scan is not None or (not shot.scan_path):
                     if cached_render is not None or (not shot.render_path):
                         # Cache Hit! Immediate update
                         logger.debug(f"Cache hit for frame {frame_number}")
                         self.scan_image = cached_scan
                         self.render_image = cached_render
                         self.update_display()
                         self.update_frame_info(frame_number)

                         # Still trigger caching for NEXT frame if we are playing
                         if self.is_playing:
                             self.preload_next_frame(frame_number)
                         return
            except Exception as cache_err:
                logger.debug(f"Cache lookup failed: {cache_err}")

            # Pre-cache next frame for smooth playback
            try:
                self.preload_next_frame(frame_number)
            except Exception as preload_err:
                logger.debug(f"Preload next frame failed: {preload_err}")

            # Update slider (without triggering valueChanged signal)
            try:
                if hasattr(self, 'frame_slider'):
                    self.frame_slider.blockSignals(True)
                    self.frame_slider.setValue(frame_number)
                    self.frame_slider.blockSignals(False)
            except Exception as slider_err:
                logger.debug(f"Slider update failed: {slider_err}")

            # Update HUD
            try:
                self.update_hud()
            except Exception as hud_err:
                logger.debug(f"HUD update failed: {hud_err}")

            # Cache Miss - Start Worker
            logger.debug(f"Cache miss for frame {frame_number}, starting worker")

            try:
                if self.active_worker and self.active_worker.isRunning():
                    old_worker = self.active_worker
                    logger.debug(f"Cancelling previous worker for frame {old_worker.frame}")
                    old_worker.is_cancelled = True
                    # Handle old worker safely
                    try:
                        old_worker.result_ready.disconnect(self.on_frame_loaded)
                    except Exception as exc:
                        logger.debug("Previous frame worker disconnect skipped: %s", exc)

                    # Keep alive until finished
                    self._active_threads.add(old_worker)
                    old_worker.finished.connect(self._on_worker_thread_done)
            except Exception as worker_cancel_err:
                logger.warning(f"Failed to cancel previous worker: {worker_cancel_err}")

            try:
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(f"Loading Frame {frame_number}...")
            except Exception as info_err:
                logger.debug(f"Frame info update failed: {info_err}")

            # Video Seek Logic (with safety checks)
            try:
                if self.is_video_mode and not skip_video_seek:
                    # User dragged slider or clicked button
                    video_frame = frame_number - self.video_start_offset

                    # Safety check: ensure active_engine exists before calling seek
                    if hasattr(self, 'video_player') and hasattr(self.video_player, 'active_engine'):
                        if self.video_player.active_engine:
                            self.video_player.active_engine.seek(video_frame)
                        else:
                            logger.debug(f"Video engine not ready for seek (frame {frame_number})")
                    else:
                        logger.debug("Video player not fully initialized for seek")
            except Exception as video_seek_err:
                logger.warning(f"Video seek failed: {video_seek_err}", exc_info=True)

            # Show loading state in viewers (with safety checks)
            try:
                if hasattr(self, 'scan_viewer'):
                    self.scan_viewer.show_loading()
                if hasattr(self, 'render_viewer'):
                    self.render_viewer.show_loading()
            except Exception as loading_err:
                logger.debug(f"Show loading failed: {loading_err}")

            # Create new worker (with safety checks)
            try:
                if not hasattr(self, '_active_threads'):
                    self._active_threads = set()

                self.active_worker = ImageLoaderWorker(self.loader, shot, frame_number)
                self.active_worker.result_ready.connect(self.on_frame_loaded)
                self._active_threads.add(self.active_worker)
                # Auto-cleanup when done
                self.active_worker.finished.connect(self._on_worker_thread_done)

                logger.debug(f"Worker started for frame {frame_number}")
                self.active_worker.start()
            except Exception as worker_start_err:
                logger.error(f"Failed to start frame loader worker: {worker_start_err}", exc_info=True)
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(f"\u26A0\uFE0F Worker error: {str(worker_start_err)[:25]}")

    def preload_next_frame(self, current_frame):
            """Smart Pre-Caching: Load next frame in background"""
            # SKIP if Video Player is active (FFmpeg/Qt handles buffering)
            if hasattr(self, 'video_player') and self.video_player.isVisible():
                return

            if not self.current_shot or not self.current_shot.frame_range:
                return

            _, end = self.current_shot.frame_range
            next_frame = current_frame + 1
            if next_frame > end:
                if self.is_playing:
                    next_frame = self.current_shot.frame_range[0] # Loop
                else:
                    return # Don't cache past end if not playing

            # Check if already cached
            key = f"{self.current_shot.id}_scan_{next_frame}"
            if self.cache.get(key) is not None:
                return # Already cached

            # Start Low-Priority Worker

            if self.precache_worker and self.precache_worker.isRunning():
                # If busy, we can either skip or cancel. 
                # For precache, skipping is often better to avoid churn, 
                # BUT if we are scrubbing fast, we might want to cancel old lookahead.
                # Let's skip to be safe and simple for now.
                return 

            self.precache_worker = ImageLoaderWorker(self.loader, self.current_shot, next_frame)
            self.precache_worker.result_ready.connect(self.on_precache_finished)
            self._active_threads.add(self.precache_worker)
            self.precache_worker.finished.connect(self._on_worker_thread_done)
            self.precache_worker.start()
