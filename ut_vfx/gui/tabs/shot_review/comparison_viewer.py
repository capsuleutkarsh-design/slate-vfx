"""
Comparison Viewer - Side-by-Side Scan vs Render

Displays scan (plate) and render (comp) side-by-side for comparison.
Synchronized frame viewing with navigation controls.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QSlider, QButtonGroup, QComboBox, QFrame
)
from PySide6.QtCore import Qt, Signal, QTimer
from pathlib import Path
import numpy as np
import logging

from .image_viewer import ImageViewer
from ....utils.image_loader import ImageLoader
from ....utils.sequence_utils import format_pattern_with_frame
from ....utils.frame_cache import FrameCache
from ut_vfx.utils.media_capabilities import is_video
from ...widgets.nuke_slider import NukeSlider

import cv2
from enum import Enum
from PySide6.QtCore import QThread

class ViewMode(Enum):
    SIDE_BY_SIDE = 0
    SCAN_ONLY = 1
    RENDER_ONLY = 2
    WIPE = 3
    DIFFERENCE = 4

logger = logging.getLogger(__name__)

# OCIO integration (optional)
try:
    from ut_vfx.core.domain.color_manager import ColorManager
    _HAS_OCIO = True
    logger.info("Successfully imported ColorManager in ComparisonViewer.")
except ImportError as e:
    logger.error(f"Failed to import ColorManager: {e}")
    _HAS_OCIO = False

class ImageLoaderWorker(QThread):
    """
    Background worker for loading frames (ULTRA-SAFE).
    Prevents UI freezing during checking/loading.
    Handles all errors including segfaults from C libraries.
    
    NOTE: OIIO is disabled by default to prevent segfault crashes.
    Set UTVFX_ENABLE_OIIO=1 environment variable to opt-in (at your own risk).
    Default uses OpenCV + imageio fallback which is much more stable.
    """
    result_ready = Signal(object, object, int) # scan_img, render_img, frame
    error_occurred = Signal(str, int)  # error_message, frame

    def __init__(self, loader, shot, frame):
        super().__init__()
        self.loader = loader
        self.shot = shot
        self.frame = frame
        self.is_cancelled = False
        # Track failed formats to avoid retrying
        self._skip_formats = getattr(loader, '_skip_formats', set())

    def run(self):
        """
        Ultra-safe frame loading with comprehensive error handling.
        Even if OpenCV/OIIO crashes, this won't kill the app.
        """
        if self.is_cancelled: 
            return
        
        scan_img = None
        render_img = None
        
        try:
            # Load Scan (ULTRA-SAFE)
            if self.shot.scan_path and not is_video(Path(str(self.shot.scan_path)).suffix.lower()):
                try:
                    path = self._get_path(self.shot.scan_path, self.frame)
                    if path and path.exists():
                        # Skip EXR if previous load crashed
                        ext = path.suffix.lower()
                        if ext in self._skip_formats:
                            logger.debug(f"Skipping {ext} (known crash format)")
                            scan_img = None
                        else:
                            try:
                                scan_img = self.loader.load_image(path)
                            except Exception as e:
                                logger.error(f"Scan load crashed: {e}, marking {ext} as unsafe", exc_info=True)
                                self._skip_formats.add(ext)
                                self.loader._skip_formats = self._skip_formats
                                scan_img = None
                except Exception as e:
                    logger.warning(f"Failed to load scan frame {self.frame}: {e}")
                    scan_img = None
            
            if self.is_cancelled: 
                return

            # Load Render (ULTRA-SAFE)
            if self.shot.render_path and not is_video(Path(str(self.shot.render_path)).suffix.lower()):
                try:
                    path = self._get_path(self.shot.render_path, self.frame)
                    if path and path.exists():
                        ext = path.suffix.lower()
                        if ext in self._skip_formats:
                            logger.debug(f"Skipping {ext} (known crash format)")
                            render_img = None
                        else:
                            try:
                                render_img = self.loader.load_image(path)
                                if render_img is None:
                                    logger.debug(f"ImageLoader returned None for {path}")
                            except Exception as e:
                                logger.error(f"Render load crashed: {e}, marking {ext} as unsafe", exc_info=True)
                                self._skip_formats.add(ext)
                                self.loader._skip_formats = self._skip_formats
                                render_img = None
                except Exception as e:
                    logger.warning(f"Failed to load render frame {self.frame}: {e}")
                    render_img = None
                     
            # ALWAYS emit result, even if images are None (prevents infinite loading)
            if not self.is_cancelled:
                logger.debug(f"Worker emitting result for frame {self.frame}: scan={scan_img is not None}, render={render_img is not None}")
                self.result_ready.emit(scan_img, render_img, self.frame)
                
        except BaseException as e:
            # Catch EVERYTHING including SystemExit (but not really, we need to emit)
            logger.error(f"CRITICAL WORKER ERROR for frame {self.frame}: {type(e).__name__}: {e}", exc_info=True)
            # Try to emit error signal - but be safe about it
            try:
                if not self.is_cancelled:
                    self.error_occurred.emit(str(e), self.frame)
                    self.result_ready.emit(None, None, self.frame)
            except Exception as emit_error:
                logger.error(f"Failed to emit error signal: {emit_error}")

    def stop(self):
        """Request the thread to stop at its next safe checkpoint."""
        self.requestInterruption()

    def _get_path(self, pattern_path, frame):
        try:
            folder = pattern_path.parent
            pattern = pattern_path.name
            filename = format_pattern_with_frame(pattern, frame)
            return folder / filename
        except Exception as e:
            logger.error(f"Path construction failed: {e}")
            return None

from .components.viewer_ui_mixin import ViewerUIMixin
from .components.viewer_loader_mixin import ViewerLoaderMixin
from .components.viewer_renderer_mixin import ViewerRendererMixin

class ComparisonViewer(QWidget, ViewerUIMixin, ViewerLoaderMixin, ViewerRendererMixin):
    """
    Side-by-side comparison viewer for scan vs render
    
    Features:
    - Dual image viewers (scan | render)
    - Synchronized frame display
    - Frame navigation (slider + buttons)
    - Automatically loads frames from sequences
    """
    
    frame_changed = Signal(int)  # Emit when frame changes
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.current_shot = None
        self.current_frame = None  # Was 0, causing confusion
        self.scan_image = None
        self.render_image = None
        
        # Grading state
        self.gain = 1.0
        self.gamma = 1.0
        self.saturation = 1.0
        self.current_view_mode = ViewMode.SIDE_BY_SIDE
        
        # Track Visibility (Premiere Style)
        self.show_scan_track = True # V1
        self.show_render_track = True # V2
        self.single_view_mode = False
        
        # Polish state
        self.wipe_position = 0.5
        self.is_dragging_wipe = False
        self.is_interactive_grading = False # For performance downscaling
        
        # Playback logic
        self.is_playing = False
        self.playback_timer = QTimer()
        self.playback_timer.timeout.connect(self.next_frame_looped)
        self.playback_fps = 24.0
        
        self.loader = ImageLoader()
        try:
            import psutil
            total_ram_mb = psutil.virtual_memory().total / (1024 * 1024)
            cache_mb = max(512, min(2048, int(total_ram_mb * 0.10)))
        except Exception:
            cache_mb = 1024
        self.cache = FrameCache(max_size_mb=cache_mb)
        self.active_worker = None
        self.is_video_mode = False
        self.video_start_offset = 0
        self._video_player_previous = None  # Track previous player to disconnect properly
        
        self.setup_ui()
        
        # Thread management to prevent crashes
        self._active_threads = set()
        
        # Enable keyboard event handling
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()
        
        # Install event filter for Wipe interaction
        self.scan_viewer.installEventFilter(self)
    
    def toggle_hud(self):
        """Toggle Smart HUD Overlay"""
        self.is_hud_enabled = self.btn_hud.isChecked()
        self.scan_viewer.set_hud_enabled(self.is_hud_enabled)
        self.render_viewer.set_hud_enabled(self.is_hud_enabled)
        if self.is_hud_enabled:
            self.update_hud()
        else:
            self.scan_viewer.set_overlay_text("")
            self.render_viewer.set_overlay_text("")

    def _on_colorspace_changed(self, index: int):
        """
        Handle OCIO colorspace dropdown change.
        Applies the selected color transform to both viewers.
        """
        if not _HAS_OCIO or not self._ocio_combo:
            return

        cs_name = self._ocio_combo.currentData()
        cm = ColorManager.instance()

        if cs_name is None:
            # "Raw" selected - disable OCIO
            cm.set_enabled(False)
        else:
            cm.set_enabled(True)

        self.scan_viewer.set_colorspace(cs_name)
        self.render_viewer.set_colorspace(cs_name)

        logger.info(f"OCIO colorspace changed to: {cs_name or 'Raw'}")
        
    def load_shot(self, shot):
        """
        Load shot for comparison (CRASH-SAFE wrapper with validation)
        
        Args:
            shot: ReviewShot object
        """
        try:
            # Initial validation
            if not shot:
                raise ValueError("Shot object is None")
            
            if not hasattr(shot, 'name'):
                raise ValueError("Shot object missing 'name' attribute")
                
            logger.info(f"Loading shot for comparison: {shot.name}")
            self._load_shot_internal(shot)
            
        except Exception as e:
            logger.error(f"CRITICAL: Failed to load shot: {e}", exc_info=True)
            # Display error message safely
            try:
                error_msg = f"\u274C Error: {str(e)[:40]}"
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(error_msg)
                self.disable_controls()
            except Exception as ui_err:
                logger.error(f"Failed to display error message: {ui_err}")
    
    def _on_video_duration_changed(self, frames: int):
        """Update slider range when video duration is known (fallback mode)"""
        if not self.current_shot or (self.current_shot.frame_range and not getattr(self.current_shot, '_is_fallback_range', False)):
            return
            
        self.current_shot.frame_range = (0, max(0, frames - 1))
        self.current_shot._is_fallback_range = True
        
        self.frame_slider.setRange(0, max(0, frames - 1))
        self.enable_controls()

    
    def load_frame(self, frame_number: int, skip_video_seek=False):
        """
        Load specific frame from scan and render (CRASH-SAFE wrapper)
        
        Args:
            frame_number: Frame number to load
            skip_video_seek: If True, do not seek video player (avoid loops)
        """
        try:
            # Validate frame number
            if not isinstance(frame_number, int) or frame_number < 0:
                logger.error(f"Invalid frame number: {frame_number}")
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(f"\u274C Invalid frame: {frame_number}")
                return
                
            self._load_frame_internal(frame_number, skip_video_seek)
        except Exception as e:
            logger.error(f"CRITICAL: Failed to load frame {frame_number}: {e}", exc_info=True)
            try:
                if hasattr(self, 'frame_info'):
                    self.frame_info.setText(f"\u274C Frame error: {str(e)[:35]}")
            except Exception as ui_err:
                logger.error(f"Failed to update frame_info: {ui_err}")
    
    def _cleanup_thread(self, thread):
        """Safely remove thread reference when finished"""
        if thread is None:
            return

        if thread in self._active_threads:
            self._active_threads.discard(thread)

        if thread is self.active_worker:
            self.active_worker = None
        if thread is self.precache_worker:
            self.precache_worker = None

        try:
            thread.deleteLater()
        except RuntimeError as exc:
            logger.debug("Thread deleteLater skipped in comparison viewer: %s", exc)

    def _on_worker_thread_done(self):
        worker = self.sender()
        if worker is None:
            return
        self._cleanup_thread(worker)
        
    def on_frame_loaded(self, scan_img, render_img, frame):
        """Handle async load completion (with EXR disabled safety handling)"""
        sender = self.sender()
        if sender is not None and sender is not self.active_worker:
            return

        if not self.current_shot: 
            logger.debug(f"on_frame_loaded: No current shot, ignoring frame {frame}")
            return
        
        # Race Condition Check: Is this still the frame we want?
        if frame != self.current_frame:
            logger.debug(f"Ignoring result for frame {frame} (Current: {self.current_frame})")
            return
        
        # Handle case where images are None (EXR disabled or load failed)
        if scan_img is None and render_img is None:
            msg = "\u26A0\uFE0F Frames unavailable (EXR loading disabled for safety)\n\n"
            msg += "To enable EXR frame loading, set environment variable:\n"
            msg += "UTVFX_ENABLE_EXR_LOADING=1"
            if hasattr(self, 'frame_info'):
                self.frame_info.setText(msg)
            logger.warning(f"Both scan and render images None for frame {frame}")
            # Still update display with None values to show placeholder
            self.scan_image = None
            self.render_image = None
            self.update_display()
            return
        
        logger.debug(f"Frame {frame} loaded successfully (scan: {scan_img is not None}, render: {render_img is not None})")
        
        # Update Cache
        if scan_img is not None:
            key = f"{self.current_shot.id}_scan_{frame}"
            self.cache.put(key, scan_img)
            
        if render_img is not None:
            key = f"{self.current_shot.id}_render_{frame}"
            self.cache.put(key, render_img)
            
        # Update State
        self.scan_image = scan_img
        self.render_image = render_img
        # self.current_frame = frame # Already set in load_frame()
        
        self.update_display()
        self.update_frame_info(frame)
        self.update_hud() # Update HUD with new frame info
        self.frame_changed.emit(frame)
        
        # SMART FEATURE: Pre-Cache Next Frame
        self.preload_next_frame(frame)

    def on_precache_finished(self, scan_img, render_img, frame):
        """Store precached frame silently"""
        sender = self.sender()
        if sender is not None and sender is not self.precache_worker:
            return

        if scan_img is not None:
            key = f"{self.current_shot.id}_scan_{frame}"
            self.cache.put(key, scan_img)
        if render_img is not None:
            key = f"{self.current_shot.id}_render_{frame}"
            self.cache.put(key, render_img)
        # logger.debug(f"Pre-cached frame {frame}")
        
    def update_frame_info(self, frame):
        if not self.current_shot: return
        if self.current_shot.frame_range:
            start, end = self.current_shot.frame_range
            total = end - start + 1
            current = frame - start + 1
            self.frame_info.setText(f"Frame: {frame} ({current}/{total})")

    # Removed synchronous _get_frame_path as it's now in worker
        
    # def _get_frame_path(self, pattern_path: Path, frame: int) -> Path: ...
    
    def _get_frame_path(self, pattern_path: Path, frame: int) -> Path:
        """
        Convert pattern path to specific frame path
        
        Args:
            pattern_path: Path with %04d pattern
            frame: Frame number
        
        Returns:
            Path to specific frame file
        """
        folder = pattern_path.parent
        pattern = pattern_path.name
        
        filename = format_pattern_with_frame(pattern, frame)
        return folder / filename
    
    def on_slider_changed(self, value: int):
        """Handle slider value change (Internal scrub)"""
        if self.current_shot and self.current_frame != value:
            self.load_frame(value)
            # Emit signal for sync (e.g. to Timeline)
            self.frame_changed.emit(value)
            
    def seek_to_frame(self, frame):
        """External seek request (e.g. from Timeline)"""
        if not self.current_shot:
            return
            
        # Avoid loop if already at frame
        if self.current_frame == frame:
            return
            
        # Update slider silently to match
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(frame)
        self.frame_slider.blockSignals(False)
        
        # Load the frame
        self.load_frame(frame)
    
    def next_frame(self):
        """Load next frame"""
        if not self.current_shot or not self.current_shot.frame_range:
            return
        
        _, end = self.current_shot.frame_range
        
        if self.current_frame < end:
            new_frame = self.current_frame + 1
            self.frame_slider.setValue(new_frame)
    
    def previous_frame(self):
        """Load previous frame"""
        if not self.current_shot or not self.current_shot.frame_range:
            return
        
        start, _ = self.current_shot.frame_range
        
        if self.current_frame > start:
            new_frame = self.current_frame - 1
            self.frame_slider.setValue(new_frame)
            
    def toggle_playback(self):
        """Toggle play/pause"""
        if self.is_playing:
            self.is_playing = False
            self.playback_timer.stop()
            self.btn_play.setText("\u25B6")
            self.btn_play.setStyleSheet("QPushButton { min-width: 30px; font-size: 16px; padding: 4px; color: #3EA8BF; }")
        else:
            self.is_playing = True
            self.playback_timer.start()
            self.btn_play.setText("\u23F8")
            self.btn_play.setStyleSheet("QPushButton { min-width: 30px; font-size: 16px; padding: 4px; color: #D9635F; }")
            # Trigger immediate precache
            self.preload_next_frame(self.current_frame)

    def next_frame_looped(self):
        """Advance frame, loop at end"""
        if not self.current_shot or not self.current_shot.frame_range:
            self.toggle_playback()
            return
            
        start, end = self.current_shot.frame_range
        next_f = self.current_frame + 1
        if next_f > end:
            next_f = start
        self.load_frame(next_f)

    def sync_from_video(self, frame_in_video):
        """Sync comparison viewer frame based on video player position"""
        if not self.current_shot or not self.current_shot.frame_range:
            return
        
        # Calculate actual frame number (video frame 0 = frame_range[0])
        actual_frame = frame_in_video + self.video_start_offset
        
        # Load frame silently without seeking video again
        self.load_frame(actual_frame, skip_video_seek=True)
    
    def on_grading_changed(self):
        """Handle slider changes"""
        self.gain = self.gain_slider.value() / 100.0
        self.gamma = self.gamma_slider.value() / 100.0
        self.saturation = self.sat_slider.value() / 100.0
        
        self.update_display()
        
    def reset_grading(self):
        """Reset grading to defaults"""
        self.gain_slider.setValue(100)
        self.gamma_slider.setValue(100)
        self.sat_slider.setValue(100)
        self.gain_val_label.setText("1.00")
        self.gamma_val_label.setText("1.00")
        self.sat_val_label.setText("1.00")
        
    def toggle_annotations(self):
        """Enable/Disable drawing on viewers"""
        enabled = self.btn_pen.isChecked()
        self.scan_viewer.enable_annotations(enabled)
        self.render_viewer.enable_annotations(enabled)
        
    def clear_annotations(self):
        """Clear all drawings"""
        self.scan_viewer.clear_annotations()
        self.render_viewer.clear_annotations()
        
    
    def start_interactive_grade(self):
        self.is_interactive_grading = True
        
    def stop_interactive_grade(self):
        self.is_interactive_grading = False
        self.update_display() # Trigger full res update
    
    def set_single_view_mode(self, enabled: bool):
        """Switch between Dual/Side-by-Side and Single Composite View"""
        self.single_view_mode = enabled
        if enabled:
            self.scan_viewer.hide() 
            self.render_viewer.set_title("PROGRAM")
        else:
            self.scan_viewer.show()
            self.render_viewer.set_title("RENDER (Comp)")
            
        self.update_display()

    def set_track_visibility(self, scan_visible: bool, render_visible: bool):
        """Toggle V1/V2 visibility for composite view"""
        self.show_scan_track = scan_visible
        self.show_render_track = render_visible
        self.update_display()

    def eventFilter(self, obj, event):
        """Handle mouse events for Wipe control"""
        if self.current_view_mode == ViewMode.WIPE and obj == self.scan_viewer:
            if event.type() == event.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self.is_dragging_wipe = True
                    return True
            elif event.type() == event.MouseButtonRelease:
                self.is_dragging_wipe = False
            elif event.type() == event.MouseMove:
                if self.is_dragging_wipe or event.buttons() & Qt.LeftButton:
                    # Update wipe position
                    width = self.scan_viewer.width()
                    if width > 0:
                        pos = event.pos().x()
                        self.wipe_position = max(0.0, min(1.0, pos / width))
                        self.update_display()
                        return True
                        
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        if event.key() == Qt.Key.Key_1:
            self.set_view_mode(ViewMode.SCAN_ONLY)
        elif event.key() == Qt.Key.Key_2:
            self.set_view_mode(ViewMode.RENDER_ONLY)
        elif event.key() == Qt.Key.Key_0:
            self.set_view_mode(ViewMode.SIDE_BY_SIDE)
        elif event.key() == Qt.Key.Key_W:
            self.set_view_mode(ViewMode.WIPE)
        elif event.key() == Qt.Key.Key_D:
            self.set_view_mode(ViewMode.DIFFERENCE)
        elif event.key() == Qt.Key.Key_Space:
            self.toggle_playback()
        else:
            super().keyPressEvent(event)
            
    def set_view_mode(self, mode: ViewMode):
        """Set visualization mode"""
        self.current_view_mode = mode
        
        # Update button states
        self.btn_mode_dual.setChecked(mode == ViewMode.SIDE_BY_SIDE)
        self.btn_mode_scan.setChecked(mode == ViewMode.SCAN_ONLY)
        self.btn_mode_render.setChecked(mode == ViewMode.RENDER_ONLY)
        
        # FIXED: Only reload if BOTH images are missing AND a worker is not already loading
        # This prevents infinite loop when images fail to load due to codec issues
        if self.current_shot and self.current_frame is not None:
            both_missing = (self.scan_image is None and self.render_image is None)
            worker_active = (self.active_worker and self.active_worker.isRunning())
            
            # Only trigger load if BOTH missing AND no worker is already running
            if both_missing and not worker_active:
                logger.debug(f"[set_view_mode] Both images missing, reloading frame {self.current_frame}")
                self.load_frame(self.current_frame)
                return  # update_display will be called by on_frame_loaded
        
        if mode == ViewMode.SIDE_BY_SIDE:
            self.scan_viewer.show()
            self.render_container.show()
            self.update_display()
        elif mode == ViewMode.SCAN_ONLY:
            self.scan_viewer.show()
            self.render_container.hide()
            self.update_display()
            
        elif mode == ViewMode.RENDER_ONLY:
            self.scan_viewer.hide()
            self.render_container.show()
            self.update_display()
            self.render_viewer.set_title("RENDER (Comp)")
            
        elif mode == ViewMode.WIPE:
            self.scan_viewer.show()
            self.render_container.hide()
            self.update_display()
        elif mode == ViewMode.DIFFERENCE:
            self.scan_viewer.show()
            self.render_container.hide()
            self.update_display()
            
    def first_frame(self):
        """Jump to first frame"""
        if not self.current_shot or not self.current_shot.frame_range:
            return
        
        start, _ = self.current_shot.frame_range
        self.frame_slider.setValue(start)
    
    def last_frame(self):
        """Jump to last frame"""
        if self.current_shot and self.current_shot.frame_range:
            _, end = self.current_shot.frame_range
            self.load_frame(end)
    
    def seek_frame(self, frame: int):
        """
        Jump to specific frame
        
        Args:
            frame: Frame number to jump to
        """
        if not self.current_shot or not self.current_shot.frame_range:
            return
        
        start, end = self.current_shot.frame_range
        
        # Clamp to valid range
        frame = max(start, min(frame, end))
        
        self.frame_slider.setValue(frame)
    
    def enable_controls(self):
        """Enable navigation controls"""
        self.btn_first.setEnabled(True)
        self.btn_prev.setEnabled(True)
        self.btn_play.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_last.setEnabled(True)
        self.frame_slider.setEnabled(True)
    
    def disable_controls(self):
        """Disable navigation controls"""
        self.btn_first.setEnabled(False)
        self.btn_prev.setEnabled(False)
        self.btn_play.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.btn_last.setEnabled(False)
        self.frame_slider.setEnabled(False)
    def cleanup(self):
        """Safely clean up resources"""
        logger.info("Cleaning up ComparisonViewer...")
        
        # Stop playback
        if self.playback_timer.isActive():
            self.playback_timer.stop()
            
        # Cancel all active workers
        for worker in list(self._active_threads):
            try:
                worker.is_cancelled = True
                worker.requestInterruption()
                if worker.isRunning():
                    worker.wait(1000)
            except Exception as exc:
                logger.debug("Worker shutdown cleanup skipped for %s: %s", worker, exc)
            self._cleanup_thread(worker)
        
        # Stop video player
        if hasattr(self, 'video_player'):
            try:
                self.video_player.stop_media()
                self.video_player.close()
            except Exception as e:
                logger.error(f"Error closing video player: {e}")

        # Clear cache
        if hasattr(self, 'cache'):
            self.cache.clear()
            
        logger.info("ComparisonViewer cleanup complete")

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)
