import logging
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QComboBox, QSlider, QSizePolicy, QFrame
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QRect
)
from PySide6.QtGui import QPainter, QColor

# --- NEW ENGINE IMPORTS ---
from .media_engines.image_engine import ImageEngine
from .media_engines.stream_engine import StreamEngine
from .media_engines.sequence_engine import SequenceEngine
from ..components.qt_safety import safe_single_shot
from ...utils.media_capabilities import is_video, is_image

class VideoWidget(QWidget):
    """
    Custom Widget for rendering video/images.
    Maintains fixed size policy (filling available space) but renders content 
    aspect-ratio correct with letterboxing (black bars).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #000;")
        self.current_image = None
        self._text_msg = "Select Asset"
        # OPTIMIZATION: Prevent flickering by disabling automatic background erase
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_image(self, image):
        self.current_image = image
        self.update() # Trigger paintEvent

    def set_text(self, text):
        self._text_msg = text
        self.current_image = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        
        # 1. Setup
        rect = self.rect()
        bg_color = QColor("#000000")
        
        # CRITICAL FIX: Always clear background to prevent "ghosting" / trails
        # The optimization WA_OpaquePaintEvent requires us to paint every pixel.
        # Previously we only painted borders, which caused trails if the image had alpha
        # or if the calculations were slightly off.
        painter.fillRect(rect, bg_color)
        
        if self.current_image and not self.current_image.isNull():
            widget_w = rect.width()
            widget_h = rect.height()
            img_w = self.current_image.width()
            img_h = self.current_image.height()
            
            if img_w > 0 and img_h > 0:
                # Calculate scale to fit
                scale_w = widget_w / img_w
                scale_h = widget_h / img_h
                scale = min(scale_w, scale_h)
                
                target_w = int(img_w * scale)
                target_h = int(img_h * scale)
                
                x = (widget_w - target_w) // 2
                y = (widget_h - target_h) // 2
                
                target_rect = QRect(x, y, target_w, target_h)
                
                # 2. Draw Image
                # Use Source composition to overwrite buffer (ignores alpha blending from prev frame)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.drawImage(target_rect, self.current_image)
                    
                painter.end()
                return # Done
                
        # Text Message (if no image)
        if self._text_msg:
            painter.setPen(QColor("#87857F"))
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text_msg)
            
        painter.end()
                


class AdvancedPlayer(QWidget):
    """
    Smart Host for Media Engines.
    Routes playback to ImageEngine, StreamEngine, or SequenceEngine.
    """
    frame_changed = Signal(int) # Current frame
    duration_changed = Signal(int) # Total frames
    next_requested = Signal() # NEW: Playlist Navigation
    prev_requested = Signal() # NEW: Playlist Navigation

    def __init__(self, parent=None):
        super().__init__(parent)
        # REMOVED: self.setMinimumSize(320, 180) - Let Layout/Splitter decide
        self.setStyleSheet("background-color: #000; border-radius: 4px;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self._is_closing = False

        # Connect Helpers
        self.active_engine = None
        
        # Instantiate Engines
        self.engines = {
            'image': ImageEngine(self),
            'stream': StreamEngine(self),
            'sequence': SequenceEngine(self)
        }
        
        # Initialize UI State
        self.is_slider_dragging = False
        self.total_frames = 1
        self.current_fps = 24.0
        self.show_timecode = False
        self.paused = False 

        # Scrub preview debounce timer
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(150)  # 150ms debounce
        self._scrub_timer.timeout.connect(self._do_scrub_seek)
        self._scrub_target = 0
        
        # State tracking for fullscreen
        self._is_fullscreen = False
        self._cached_parent = None
        self._cached_layout = None
        self._cached_layout_index = -1
        self._cached_geometry = None
        self._placeholder = None
        
        # Load debounce timer (Fix for scroll performance)
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(200) # 200ms debounce
        self._load_timer.timeout.connect(self._perform_load)
        self._pending_load_path = None

        # Build UI immediately
        self.setup_ui()
        
    def set_controls_visible(self, visible: bool):
        """Show/Hide internal controls (for embedding)."""
        if hasattr(self, 'controls_widget'):
            self.controls_widget.setVisible(visible)
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        
        # Screen
        self.screen = VideoWidget()
        layout.addWidget(self.screen, 1)
        
        # Controls Container
        self.controls_widget = QWidget()
        self.controls_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.controls_widget.setStyleSheet("""
            QWidget { 
                background: rgba(15, 20, 25, 0.85); 
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                margin: 0px 4px 4px 4px;
            }
        """)
        
        controls_layout = QVBoxLayout(self.controls_widget)
        controls_layout.setContentsMargins(8, 6, 8, 6) # More breathing room
        controls_layout.setSpacing(6)
        
        # --- Row 1: Slider + Time ---
        row_scrub = QHBoxLayout()
        row_scrub.setContentsMargins(0,0,0,0)
        row_scrub.setSpacing(8)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedHeight(14)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal { border: none; height: 4px; background: #26262D; margin: 5px 0; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #3EA8BF; border-radius: 2px; }
            QSlider::handle:horizontal { background: #E8E6E1; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }
            QSlider::handle:horizontal:hover { background: #3EA8BF; transform: scale(1.1); }
        """)
        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.sliderMoved.connect(self.on_slider_move)
        
        self.lbl_time = QPushButton("00:00")
        self.lbl_time.setFlat(True)
        self.lbl_time.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_time.setFixedWidth(45) # Slightly smaller
        self.lbl_time.setStyleSheet("color: #87857F; font-weight: bold; font-family: monospace; font-size: 10px; text-align: right; border: none;")
        self.lbl_time.clicked.connect(self.toggle_time_display)
        
        row_scrub.addWidget(self.slider)
        row_scrub.addWidget(self.lbl_time)
        controls_layout.addLayout(row_scrub)

        # --- Row 2: Transport (Centered and compact) ---
        row_transport = QHBoxLayout()
        row_transport.setContentsMargins(0, 4, 0, 4)
        row_transport.setSpacing(6) # Reduced spacing to prevent overlap
        row_transport.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_style = """
            QPushButton {
                background: rgba(255, 255, 255, 0.05); color: #E8E6E1;
                border-radius: 6px; padding: 2px;
                border: 1px solid transparent;
                font-family: "Segoe UI", "DejaVu Sans", sans-serif;
                font-size: 12px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.15); color: #E8E6E1; }
            QPushButton:pressed { background: rgba(255, 255, 255, 0.1); }
            QPushButton:checked { background: rgba(62, 168, 191, 0.2); color: #3EA8BF; border: 1px solid rgba(62, 168, 191, 0.5); }
        """
        
        self.btn_prev = QPushButton("|<")
        self.btn_prev.setFixedSize(28, 28); self.btn_prev.setStyleSheet(self.btn_style)
        self.btn_prev.setToolTip("Previous Asset")
        self.btn_prev.clicked.connect(self.prev_requested.emit)
        
        self.btn_step_back = QPushButton("<|")
        self.btn_step_back.setFixedSize(28, 28); self.btn_step_back.setStyleSheet(self.btn_style)
        self.btn_step_back.setToolTip("Step Back 1 Frame")
        self.btn_step_back.clicked.connect(lambda: self.step_active(-1))
        
        self.btn_play = QPushButton("►")
        self.btn_play.setFixedSize(40, 28) # Wide enough, but compact
        self.btn_play.setToolTip("Play / Pause")
        self.btn_play.setStyleSheet(self.btn_style + """
            QPushButton { 
                background: rgba(62, 168, 191, 0.1); 
                color: #3EA8BF; 
                font-size: 16px; 
                border: 1px solid rgba(62, 168, 191, 0.3);
                border-radius: 6px;
            }
            QPushButton:hover { background: rgba(62, 168, 191, 0.3); border: 1px solid rgba(62, 168, 191, 0.8); color: #E8E6E1; }
        """)
        self.btn_play.clicked.connect(self.toggle_play)
        
        self.btn_step_forward = QPushButton("|>")
        self.btn_step_forward.setFixedSize(28, 28); self.btn_step_forward.setStyleSheet(self.btn_style)
        self.btn_step_forward.setToolTip("Step Forward 1 Frame")
        self.btn_step_forward.clicked.connect(lambda: self.step_active(1))
        
        self.btn_next = QPushButton(">|")
        self.btn_next.setFixedSize(28, 28); self.btn_next.setStyleSheet(self.btn_style)
        self.btn_next.setToolTip("Next Asset")
        self.btn_next.clicked.connect(self.next_requested.emit)

        row_transport.addWidget(self.btn_prev)
        row_transport.addWidget(self.btn_step_back)
        row_transport.addWidget(self.btn_play)
        row_transport.addWidget(self.btn_step_forward)
        row_transport.addWidget(self.btn_next)
        
        controls_layout.addLayout(row_transport)

        # --- Row 3: Tools (Floating Media Island) ---
        island_frame = QFrame()
        island_frame.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.05);
                border-radius: 14px;
            }
            QPushButton {
                background: transparent;
                color: #B4B1AA;
                border: none;
                font-weight: bold;
                font-size: 11px;
                padding: 4px 8px;
                border-radius: 6px;
            }
            QPushButton:hover { color: #3EA8BF; background: rgba(62, 168, 191, 0.1); }
            QPushButton:checked { color: #3EA8BF; }
            QComboBox { 
                background: transparent; 
                color: #B4B1AA; 
                border: none; 
                font-size: 11px;
                padding: 4px 8px;
            }
            QComboBox:hover { color: #3EA8BF; }
            QComboBox::drop-down { border: none; width: 14px; }
            QComboBox::down-arrow { width: 0px; height: 0px; border: none; }
        """)
        row_tools = QHBoxLayout(island_frame)
        row_tools.setContentsMargins(12, 4, 12, 4)
        row_tools.setSpacing(6)
        
        # Left: View Settings
        self.combo_view = QComboBox()
        self.combo_view.addItem("Std")
        self.combo_view.currentIndexChanged.connect(self.change_view_transform)
        row_tools.addWidget(self.combo_view)
        
        # Speed
        self.combo_speed = QComboBox()
        self.combo_speed.addItems(["0.5x", "1.0x", "2.0x"])
        self.combo_speed.setCurrentIndex(1)
        self.combo_speed.currentIndexChanged.connect(self.change_speed)
        row_tools.addWidget(self.combo_speed)
        
        row_tools.addStretch(1)
        
        # Right: Actions
        self.btn_loop = QPushButton("Loop")
        self.btn_loop.setCheckable(True); self.btn_loop.setChecked(True)
        self.btn_loop.clicked.connect(self.toggle_loop)

        self.btn_snap = QPushButton("Snap")
        self.btn_snap.clicked.connect(self.take_snapshot)
        
        # Fullscreen
        self.btn_fullscreen = QPushButton("Full")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)

        row_tools.addWidget(self.btn_loop)
        row_tools.addWidget(self.btn_snap)
        row_tools.addWidget(self.btn_fullscreen)
        
        controls_layout.addWidget(island_frame)
        
        layout.addWidget(self.controls_widget, 0)

    # --- DEBOUNCE LOGIC ---
    def load(self, path):
        """Standard Routing Logic with Debounce."""
        Path(path)
        str_path = str(path)
        
        # 1. Immediate Debounce: If same file, ignore
        if getattr(self, 'current_path', None) == str_path:
            return

        # 2. Stop immediately (clear UI)
        self.stop_media()
        self.current_path = str_path # Claim it
        
        # 3. Start Debounce Timer
        # If user scrolls, this function is called again, 
        # stopping previous timer and starting a new one.
        self._pending_load_path = str_path
        self._load_timer.start(200) # 200ms debounce
        
    def _perform_load(self):
        """Called by timer to actually load the media."""
        path = getattr(self, '_pending_load_path', None)
        if not path: return
        
        # Double check: if current_path changed in mean time (shouldn't happen with timer logic)
        if path != self.current_path:
             return
             
        logging.info(f"AdvancedPlayer: Loading {path}")
        
        path_obj = Path(path)
        
        # Detection Logic
        engine_key = 'stream' # Default
        
        # 1. Check for Sequence
        # Frame sequence detection using VFX standard utility
        is_sequence = False
        seq_pattern = ""
        seq_start = 0
        
        # Check if this might be a sequence (not a video file)
        if not is_video(path_obj.suffix.lower()):
            # Import our centralized sequence detector
            from ...utils.sequence_utils import SequenceDetector
            
            try:
                # Try to detect sequence using fileseq (VFX standard)
                seq = SequenceDetector.find_sequence(path_obj)
                
                if seq:
                    # Extract sequence information
                    seq_pattern = SequenceDetector.get_pattern(seq)
                    seq_start, _ = SequenceDetector.get_frame_range(seq)
                    is_sequence = True
                    
                    logging.info(f"Detected frame sequence: {seq_pattern} starting at frame {seq_start}")
                else:
                    logging.debug(f"Not a sequence: {path_obj}")
                    
            except Exception as e:
                logging.exception(f"Sequence detection error: {e}")
        
        if is_sequence:
            engine_key = 'sequence'
            self.engines['sequence'].set_sequence_details(seq_pattern, seq_start)
        else:
            # 2. Check for Image
            if is_image(path_obj.suffix.lower()):
                engine_key = 'image'
            else:
                engine_key = 'stream'

        # Switch Engine
        self._activate_engine(engine_key)
        
        # CRITICAL FIX: Set target size BEFORE loading
        # Otherwise ImageEngine will have 0x0 size and won't display anything
        if hasattr(self, 'screen'):
            size = self.screen.size()
            dpr = self.devicePixelRatio()
            self.active_engine.set_pixel_ratio(dpr)
            self.active_engine.set_target_size(size)
        
        # Load
        try:
            logging.info(f"AdvancedPlayer: Loading {path} with {engine_key.upper()} Engine")
            self.active_engine.load(str(path))
            
            # OCIO: Populate Views if available
            self.combo_view.blockSignals(True)
            self.combo_view.clear()
            self.combo_view.addItem("Standard")
            
            if hasattr(self.active_engine, 'views') and self.active_engine.views:
                self.combo_view.clear()
                # Determine current
                current_view = getattr(self.active_engine, 'view', 'Standard')
                for v in self.active_engine.views:
                    self.combo_view.addItem(v)
                self.combo_view.setCurrentText(current_view)
                self.combo_view.setEnabled(True)
            else:
                self.combo_view.setEnabled(False)
                
            self.combo_view.blockSignals(False)

            # Handle Autoplay on double-click
            if getattr(self, '_pending_autoplay', False):
                self.active_engine.play()
                self.btn_play.setText("||")
                self.paused = False
                self._pending_autoplay = False
            else:
                self.btn_play.setText("►")
                self.paused = True
            # self.setFocus() # Removed to prevent focus stealing from file list
        except Exception as e:
            logging.exception(f"Engine Load Error: {e}")
            self.screen.set_text(f"Error: {e}")
            # Ensure we don't crash
            try:
                self.stop_media()
            except RuntimeError as stop_err:
                logging.debug(f"stop_media failed during engine-load error recovery: {stop_err}")
        

    def change_view_transform(self, index):
        """Update OCIO View"""
        view = self.combo_view.currentText()
        if hasattr(self.active_engine, 'set_view_transform'):
            # We assume Display is constant "sRGB" for now or retrieved from engine
            display = getattr(self.active_engine, 'display', 'sRGB')
            self.active_engine.set_view_transform(display, view)


    def _activate_engine(self, key):
        """Disconnect old, Connect new."""
        # 1. Disconnect Old
        if self.active_engine:
            try:
                self.active_engine.frame_ready.disconnect(self.update_screen)
                self.active_engine.position_changed.disconnect(self.update_slider_pos)
                self.active_engine.duration_changed.disconnect(self.set_duration)
                self.active_engine.finished.disconnect(self.on_engine_finished)
                self.active_engine.error_occurred.disconnect(self.on_engine_error)
            except (TypeError, RuntimeError):
                pass
            
        # 2. Set New
        self.active_engine = self.engines[key]
        
        # 3. Connect New
        self.active_engine.frame_ready.connect(self.update_screen)
        self.active_engine.position_changed.connect(self.update_slider_pos)
        self.active_engine.duration_changed.connect(self.set_duration)
        self.active_engine.finished.connect(self.on_engine_finished)
        self.active_engine.error_occurred.connect(self.on_engine_error)
        
        # 4. Sync State
        self.active_engine.set_target_size(self.screen.size())
        self.active_engine.set_speed(self.get_current_speed())
        self.active_engine.set_loop(self.btn_loop.isChecked())
        try:
            self.active_engine.set_pixel_ratio(self.devicePixelRatio())
        except AttributeError:
            pass

    def on_engine_finished(self):
        sender = self.sender()
        if sender is not None and sender is not self.active_engine:
            return
        if self._is_closing:
            return
        self.btn_play.setText("►")

    def on_engine_error(self, message):
        sender = self.sender()
        if sender is not None and sender is not self.active_engine:
            return
        if self._is_closing:
            return
        logging.error(f"Engine Error: {message}")
        self.screen.set_text(f"Playback Error:\n{message}")
        self.stop_media()

    def stop_media(self):
        if self.active_engine:
            self.active_engine.stop()
        self.btn_play.setText("►")
        self.slider.setValue(0)
        self.update_time_label(0)
        self.current_path = None

    # --- DELEGATED CONTROLS ---

    def toggle_play(self):
        if not self.active_engine: return
        
        # Use simple text check for now, can be improved with state tracking
        if self.btn_play.text() == "►":
            self.active_engine.play()
            self.btn_play.setText("||") # Immediate Feedback
        else:
            self.active_engine.pause()
            self.btn_play.setText("►") # Immediate Feedback

    def step_active(self, frames):
        if self.active_engine:
             self.active_engine.step(frames)
             self.btn_play.setText("►") # Stepping pauses usually

    def toggle_loop(self):
        loop = self.btn_loop.isChecked()
        if self.active_engine:
            self.active_engine.set_loop(loop)
            
    def change_speed(self):
        if self.active_engine:
            self.active_engine.set_speed(self.get_current_speed())

    def get_current_speed(self):
        txt = self.combo_speed.currentText()
        return float(txt.replace('x', ''))

    # --- UI UPDATES ---

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.active_engine:
            dpr = self.devicePixelRatio()
            self.active_engine.set_pixel_ratio(dpr)
            self.active_engine.set_target_size(self.screen.size())

    def update_screen(self, image):
        # Delegate to VideoWidget (paintEvent)
        # No pixmap conversion needed here (VideoWidget handles QImage drawing directly)
        self.screen.set_image(image)

    def set_duration(self, frames):
        self.total_frames = frames
        self.slider.setRange(0, frames)
        if hasattr(self.active_engine, 'fps'):
            self.current_fps = self.active_engine.fps
        self.update_time_label(0)
        self.duration_changed.emit(frames)

    def update_slider_pos(self, frame):
        if not self.is_slider_dragging:
            self.slider.setValue(frame)
            self.update_time_label(frame)
        self.frame_changed.emit(frame)

    def on_slider_pressed(self):
        self.is_slider_dragging = True
        if self.active_engine:
            self.active_engine.pause()

    def on_slider_released(self):
        self.is_slider_dragging = False
        if self.active_engine:
            self.active_engine.seek(self.slider.value())
            # Auto-resume if it was playing? 
            # Original behavior: if self.btn_play.text() == "||": self.worker.play()
            if self.btn_play.text() == "||":
                self.active_engine.play()

    def on_slider_move(self, val):
        self.update_time_label(val)
        # Debounced scrub preview: show the frame at slider position during drag
        if self.is_slider_dragging and self.active_engine:
            self._scrub_target = val
            self._scrub_timer.start()  # Restart debounce timer

    def _do_scrub_seek(self):
        """Execute the debounced scrub seek."""
        if self.active_engine and self.is_slider_dragging:
            self.active_engine.seek(self._scrub_target)

    def update_time_label(self, frame):
        if self.show_timecode and self.current_fps > 0:
            cur_sec = int(frame / self.current_fps)
            tot_sec = int(self.total_frames / self.current_fps)
            self.lbl_time.setText(f"{cur_sec//60:02d}:{cur_sec%60:02d} / {tot_sec//60:02d}:{tot_sec%60:02d}")
        else:
            self.lbl_time.setText(f"F: {frame} / {self.total_frames}")

    def toggle_time_display(self):
        self.show_timecode = not self.show_timecode
        self.update_time_label(self.slider.value())

    # --- EXTRAS ---

    def take_snapshot(self):
        # Snapshot from VideoWidget's current image
        if not self.screen.current_image: return
        
        save_dir = Path.home() / "Pictures" / "Slate_Snaps"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"Snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        save_path = save_dir / filename
        
        self.screen.current_image.save(str(save_path))
        
        # Flash Effect (Optional - maybe just overlay?)
        # For now, just skip the flash or implement custom overlay in VideoWidget
        # self.screen.flash() # To be implemented if needed

    def toggle_fullscreen(self):
        """Toggle fullscreen mode seamlessly without rebuilding the player."""
        if not self._is_fullscreen:
            # Enter Fullscreen
            global_pos = self.mapToGlobal(self.rect().topLeft())
            self._cached_parent = self.parentWidget()
            self._cached_layout = self._cached_parent.layout() if self._cached_parent else None
            self._cached_layout_index = -1
            self._cached_geometry = self.geometry()

            # Find our position in the parent layout if we have one
            if self._cached_layout:
                parent_layout = self._cached_layout
                for i in range(parent_layout.count()):
                    item = parent_layout.itemAt(i)
                    if item and item.widget() == self:
                        self._cached_layout_index = i
                        # Create a placeholder so the layout doesn't collapse
                        self._placeholder = QWidget(self._cached_parent)
                        self._placeholder.setSizePolicy(self.sizePolicy())
                        self._placeholder.setMinimumSize(self.minimumSize())
                        parent_layout.replaceWidget(self, self._placeholder)
                        break

            # Detach from parent and go fullscreen
            self.hide()
            self.setParent(None)
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
            self.move(global_pos)
            self.showFullScreen()
            self.btn_fullscreen.setText("IN")
            self._is_fullscreen = True
            self.setFocus()
        else:
            # Exit Fullscreen
            self.hide()

            # CRITICAL: reparent BEFORE changing window flags.
            # Calling setWindowFlags on a parentless widget destroys and
            # recreates the native window, breaking child signal connections.
            if self._cached_parent:
                self.setParent(self._cached_parent)

            self.setWindowFlags(Qt.Widget)

            if self._placeholder and self._cached_layout:
                self._cached_layout.replaceWidget(self._placeholder, self)
                self._placeholder.deleteLater()
                self._placeholder = None

            self.show()
            self.raise_()
            self.activateWindow()
            if hasattr(self, "controls_widget") and self.controls_widget:
                self.controls_widget.setEnabled(True)
                self.controls_widget.setVisible(True)
            self.btn_fullscreen.setText("FS")
            self._is_fullscreen = False
            self._cached_parent = None
            self._cached_layout = None
            self._cached_layout_index = -1
            self.setFocus()
            safe_single_shot(0, self, self._refresh_after_fullscreen_restore)

    def _refresh_after_fullscreen_restore(self):
        """Re-sync engine target after fullscreen restore to keep controls/playback responsive."""
        if not self.active_engine:
            return
        try:
            self.active_engine.set_pixel_ratio(self.devicePixelRatio())
            self.active_engine.set_target_size(self.screen.size())
        except Exception as exc:
            logging.debug("AdvancedPlayer: fullscreen restore refresh skipped: %s", exc)

    def keyPressEvent(self, event):
        """VFX-standard keyboard shortcuts; only active when player has focus and media is loaded."""
        if not self.active_engine or not self.hasFocus():
            super().keyPressEvent(event)
            return

        if event.isAutoRepeat():
            return

        key = event.key()
        
        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Right:
            self.step_active(1)   # Forward 1 frame
        elif key == Qt.Key.Key_Left:
            self.step_active(-1)  # Backward 1 frame
        elif key == Qt.Key.Key_L:
            # Forward play
            self.active_engine.play()
            self.btn_play.setText("||")
        elif key == Qt.Key.Key_K:
            # Pause
            self.active_engine.pause()
            self.btn_play.setText("►")
        elif key == Qt.Key.Key_J:
            # Step backward (no true reverse in FFmpeg pipe mode)
            self.step_active(-1)
        elif key == Qt.Key.Key_Home:
            self.active_engine.seek(0)
        elif key == Qt.Key.Key_End:
            self.active_engine.seek(self.total_frames)
        elif key == Qt.Key.Key_Escape:
            if self._is_fullscreen:
                event.accept()
                safe_single_shot(0, self, self.toggle_fullscreen)
                return
        elif key == Qt.Key.Key_F:
            event.accept()
            safe_single_shot(0, self, self.toggle_fullscreen)
            return
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self._is_closing = True
        self.stop_media()
        super().closeEvent(event)

