from PySide6.QtGui import QImage, QImageReader
from PySide6.QtCore import Qt, QTimer
import logging
from pathlib import Path
from .base_engine import BaseMediaEngine
from ....utils.image_loader import ImageLoader
import numpy as np

# Optional dependencies
try:
    from OpenImageIO import ImageInput
    HAS_OIIO = True
except ImportError:
    HAS_OIIO = False

try:
    import imageio.v3 as iio
except ImportError:
    iio = None
    if HAS_OIIO:
        logging.info("ImageEngine: imageio/numpy not available; using OpenImageIO-only EXR path.")
    else:
        logging.warning("ImageEngine: OpenImageIO and imageio/numpy not found. EXR support limited.")

# OCIO via centralized ColorManager (safe — handles import errors internally)
try:
    from ut_vfx.core.domain.color_manager import ColorManager, OCIO_AVAILABLE as HAS_OCIO
    logging.info("Successfully imported ColorManager in ImageEngine.")
except ImportError as e:
    logging.error(f"Failed to import ColorManager in ImageEngine: {e}")
    HAS_OCIO = False

if HAS_OIIO:
    logging.info("ImageEngine: OpenImageIO available for fast EXR loading")
if HAS_OCIO:
    logging.info("ImageEngine: OpenColorIO available via ColorManager")

class ImageEngine(BaseMediaEngine):
    """
    Optimized engine for static images.
    Uses Qt Native QImageReader for fast loading (JPG, PNG).
    Uses ColorManager (OCIO) + OIIO/ImageIO for EXR/HDR files (High Fidelity).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_image = None
        self.is_exr = False
        self.raw_buffer = None # Keep float buffer for re-processing

        # Debounce timer for resize (prevents dozens of re-scales per second)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)  # 100ms debounce
        self._resize_timer.timeout.connect(self._emit_frame)
        
        # OCIO State — delegated to centralized ColorManager singleton
        self._color_manager = None
        self.displays = []
        self.views = []
        
        if HAS_OCIO:
            try:
                self._color_manager = ColorManager.instance()
                if self._color_manager.is_available():
                    self.displays = self._color_manager.get_displays()
                    self.views = self._color_manager.get_views()
                    logging.info(
                        f"ImageEngine OCIO: {self._color_manager.get_current_display()} / "
                        f"{self._color_manager.get_current_view()}"
                    )
                else:
                    logging.info("ImageEngine: ColorManager loaded but OCIO config not available")
            except Exception as e:
                logging.exception(f"OCIO Init Error: {e}")

    def load(self, source_path: str):
        self.source = source_path
        self.total_frames = 1
        self.fps = 0.0 # Static
        self.is_exr = source_path.lower().endswith('.exr')
        
        try:
            if self.is_exr:
                if HAS_OCIO:
                    self._load_exr_ocio(source_path)
                else:
                    self._load_exr_safe(source_path)
            else:
                self._load_standard(source_path)
                
            if self.current_image and not self.current_image.isNull():
                 # Send duration (1 frame)
                self.duration_changed.emit(1)
                # Emit frame immediately
                self._emit_frame()
            else:
                 self.error_occurred.emit(f"Failed to load: {source_path}")

        except Exception as e:
            logging.exception(f"ImageEngine Load Error: {e}")
            self.error_occurred.emit(str(e))

    def _load_exr_safe(self, source_path):
        """Load EXR through shared ImageLoader policy when OCIO is unavailable."""
        img_np = ImageLoader.load_image(Path(source_path))
        if img_np is None:
            raise Exception("EXR unavailable: enable EXR loading or verify file/decoder support")
        self._set_current_image_from_numpy(img_np)
        self.raw_buffer = None

    def _load_standard(self, source_path):
        """
        Qt for the everyday formats, OpenImageIO for the rest.

        DPX and some TGA files are listed as supported but Qt cannot read
        either, so they showed an "unsupported format" message or simply a
        blank panel. OpenImageIO reads both, and is already here for EXR.
        """
        reader = QImageReader(source_path)
        if reader.canRead():
            self.current_image = reader.read()
            self.raw_buffer = None
            if self.current_image and not self.current_image.isNull():
                return

        if HAS_OIIO:
            try:
                self._load_via_oiio(source_path)
                return
            except Exception as exc:
                logging.debug("OpenImageIO could not read %s: %s", source_path, exc)

        raise Exception(f"Unsupported format: {reader.errorString()}")

    def _load_via_oiio(self, source_path):
        """Read anything OpenImageIO understands into a picture we can show."""
        inp = ImageInput.open(str(source_path))
        if not inp:
            raise Exception("OpenImageIO could not open the file")
        try:
            spec = inp.spec()
            pixels = inp.read_image(0, 0, 0, spec.nchannels, "float")
        finally:
            inp.close()

        if pixels is None:
            raise Exception("OpenImageIO returned no pixels")

        img_np = np.array(pixels).reshape(spec.height, spec.width, spec.nchannels)
        self._set_current_image_from_numpy(img_np)
        self.raw_buffer = None

    def _load_exr_ocio(self, source_path):
        """Read EXR as float, apply OCIO via ColorManager, convert to QImage"""
        img_np = None
        # Try OpenImageIO first (fastest and most reliable)
        if HAS_OIIO:
            try:
                inp = ImageInput.open(str(source_path))
                if inp:
                    spec = inp.spec()
                    # Read as float32 RGB/RGBA
                    # Every channel. Passing -1 as the end channel does not
                    # mean "all of them" - it reads a single channel, and the
                    # reshape below then fails, so every EXR fell through to a
                    # fallback whose EXR support is disabled and rendered blank.
                    pixels = inp.read_image(0, 0, 0, spec.nchannels, "float")
                    inp.close()
                    
                    if pixels is not None:
                        # Reshape to (H, W, C)
                        img_np = np.array(pixels).reshape(spec.height, spec.width, spec.nchannels)
                        logging.debug(f"Loaded EXR via OIIO: {img_np.shape}, {img_np.dtype}")
                    else:
                        raise Exception("OIIO read_image returned None")
            except Exception as e:
                logging.warning(f"OIIO failed, falling back to imageio: {e}")
                img_np = None

        # Fallback to imageio
        if img_np is None and iio is not None:
            img_np = iio.imread(source_path)

        if img_np is None:
            raise Exception("EXR unavailable: no loader could read the file")
        
        # Handle channels (RGB vs RGBA)
        if img_np.ndim == 3:
             if img_np.shape[2] == 4:
                 # Standard RGBA
                 pass 
             elif img_np.shape[2] == 3:
                 # RGB
                 pass
        else:
             # Grayscale (H, W) -> (H, W, 3)
             img_np = np.stack((img_np,)*3, axis=-1)
             
        # Store full raw buffer for real-time color switching
        self.raw_buffer = img_np.astype(np.float32)
        
        self._process_ocio()

    def _set_current_image_from_numpy(self, img_np):
        """Convert numpy image to QImage for display path."""
        if img_np is None:
            raise Exception("Cannot convert empty image")
        if img_np.ndim == 2:
            img_np = np.stack((img_np,) * 3, axis=-1)
        channels = img_np.shape[2] if img_np.ndim == 3 else 1
        if channels < 3:
            img_np = np.stack((img_np[:, :, 0],) * 3, axis=-1)
            channels = 3
        if channels >= 4:
            final = np.ascontiguousarray(img_np[:, :, :4], dtype=np.uint8)
            fmt = QImage.Format_RGBA8888
        else:
            final = np.ascontiguousarray(img_np[:, :, :3], dtype=np.uint8)
            fmt = QImage.Format_RGB888
        self._processed_data = final
        h, w, c = final.shape
        self.current_image = QImage(final.data, w, h, w * c, fmt).copy()

    def _process_ocio(self):
        """Apply current Display/View transform to raw_buffer via ColorManager."""
        if self.raw_buffer is None:
            return
        
        cm = self._color_manager
        if cm is None or not cm.is_available():
            # No OCIO — just tonemap to uint8 for display
            buf = np.clip(self.raw_buffer, 0.0, 1.0)
            self._set_current_image_from_numpy((buf * 255).astype(np.uint8))
            return
        
        try:
            # Enable OCIO transform for this processing pass
            was_enabled = cm.is_enabled()
            cm.set_enabled(True)
            
            # Use ACEScg as default source space for EXR (scene-linear)
            result = cm.transform_image(self.raw_buffer, src_colorspace="ACEScg")
            
            # Restore previous enable state
            cm.set_enabled(was_enabled)
            
            # Build QImage from result (already uint8 from ColorManager)
            height, width = result.shape[:2]
            result.shape[2] if result.ndim > 2 else 1
            
            alpha_buffer = None
            if self.raw_buffer.ndim == 3 and self.raw_buffer.shape[2] == 4:
                alpha_buffer = self.raw_buffer[:, :, 3]
            
            if alpha_buffer is not None:
                alpha_uint8 = np.clip(alpha_buffer * 255, 0, 255).astype(np.uint8)
                if alpha_uint8.ndim == 2:
                    alpha_uint8 = np.expand_dims(alpha_uint8, axis=2)
                final_buf = np.dstack((result[:, :, :3], alpha_uint8))
                fmt = QImage.Format_RGBA8888
            else:
                final_buf = result[:, :, :3]
                fmt = QImage.Format_RGB888
            
            self._processed_data = np.ascontiguousarray(final_buf)
            
            h, w, c = self._processed_data.shape
            bytes_per_line = w * c
            
            self.current_image = QImage(
                self._processed_data.data,
                w,
                h,
                bytes_per_line,
                fmt
            ).copy()
            
        except Exception as e:
            logging.exception(f"OCIO Process Error: {e}")
            # Fallback: basic tonemap without OCIO
            buf = np.clip(self.raw_buffer, 0.0, 1.0)
            self._set_current_image_from_numpy((buf * 255).astype(np.uint8))

    def set_view_transform(self, display, view):
        """Update display/view via ColorManager and re-process if we have raw buffer."""
        if self._color_manager and self._color_manager.is_available():
            self._color_manager.set_display_view(display, view)
        if self.is_exr and self.raw_buffer is not None:
            self._process_ocio()
            self._emit_frame()

    def _emit_frame(self):
        if self.current_image:
            w, h = self.target_size
            
            # CRITICAL FIX: Use fallback dimensions if target_size not set yet
            # This happens when player loads before screen widget is sized
            if w <= 0 or h <= 0:
                logging.warning(f"ImageEngine: target_size is {w}x{h}, using fallback 640x360")
                w, h = 640, 360  # Fallback dimensions

            scaled = self.current_image.scaled(
                w * self.pixel_ratio, 
                h * self.pixel_ratio, 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            
            if self.pixel_ratio > 1.0:
                scaled.setDevicePixelRatio(self.pixel_ratio)
                
            self.frame_ready.emit(scaled)
            self.position_changed.emit(0)

    def set_target_size(self, size):
        super().set_target_size(size)
        # Debounced: avoid re-scaling on every resize event
        self._resize_timer.start()

    def play(self):
        self._emit_frame()

    def pause(self):
        pass

    def stop(self):
        self.current_image = None
        self.raw_buffer = None
        self.finished.emit()

    def seek(self, frame_num):
        self.position_changed.emit(0)

    def step(self, frames):
        """No-op for static images."""
        pass
