"""
Image Viewer Widget

Simple image display widget with auto-scaling and fit-to-window mode.
Displays numpy arrays as QPixmap.
Supports optional OpenColorIO color management.
"""

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPixmap, QImage
import numpy as np
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# OCIO integration (optional)
try:
    from slate.core.domain.color_manager import ColorManager
    _HAS_OCIO = True
    logging.info("Successfully imported ColorManager in ImageViewer.")
except ImportError as e:
    logging.error(f"Failed to import ColorManager in ImageViewer: {e}")
    _HAS_OCIO = False


class ImageViewer(QLabel):
    """
    Simple image viewer widget
    
    Features:
    - Display numpy array as QPixmap
    - Auto-scale to fit widget
    - Maintain aspect ratio
    - Display loading/error states
    """
    
    image_loaded = Signal(dict)  # Emit image metadata
    
    def __init__(self, title: str = "Image", parent=None):
        super().__init__(parent)
        
        self.title = title
        self.current_image = None
        self.current_path = None
        self.original_pixmap = None
        self.overlay_text = ""  # Initialize HUD overlay text
        self.hud_enabled = True
        self.hud_hover_active = False
        self._src_colorspace = None  # OCIO source colorspace (None = no transform)
        
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "border: 1px solid #2C2C34; "
            "background: #16161A; "
            "color: #87857F; "
            "padding: 10px;"
        )
        
        self.set_placeholder()
    
    def set_title(self, title):
        """Update the viewer title"""
        self.title = title
        # Only show placeholder if no image is currently loaded
        if self.current_image is None:
            self.set_placeholder()

    def set_placeholder(self):
        """Show placeholder text"""
        self.setText(f"{self.title}\n\nNo image loaded")
    
    def set_colorspace(self, colorspace_name: Optional[str]):
        """
        Set the source colorspace for OCIO color management.

        Args:
            colorspace_name: OCIO colorspace name (e.g. 'ACEScg').
                             Set to None to disable transform.
        """
        self._src_colorspace = colorspace_name
        # Re-apply transform if an image is already loaded
        if self.current_image is not None:
            self.load_image(self.current_image, self.current_path)

    def _apply_ocio_transform(self, image_array: np.ndarray) -> np.ndarray:
        """
        Apply OCIO color transform if enabled.

        Returns the transformed image, or the original if OCIO
        is not available or no colorspace is set.
        """
        if not _HAS_OCIO or not self._src_colorspace:
            return image_array

        try:
            cm = ColorManager.instance()
            if cm.is_enabled():
                return cm.transform_image(image_array, self._src_colorspace)
        except Exception as e:
            logger.debug(f"OCIO transform skipped: {e}")

        return image_array

    def load_image(self, image_array: Optional[np.ndarray], path: Optional[Path] = None):
        """
        Display numpy array as image

        Args:
            image_array: RGB numpy array (8-bit, 0-255 or float32)
            path: Optional source path for metadata
        """
        if image_array is None or image_array.size == 0:
            self.clear_image()
            return

        try:
            self.current_image = image_array
            self.current_path = path

            # Apply OCIO color transform (if enabled)
            display_image = self._apply_ocio_transform(image_array)

            # Ensure uint8 for Qt display
            if display_image.dtype != np.uint8:
                display_image = np.clip(display_image * 255.0, 0, 255).astype(np.uint8)

            # Convert numpy to QPixmap
            height, width = display_image.shape[:2]

            # Determine format based on channels
            if len(display_image.shape) == 2:
                # Grayscale
                bytes_per_line = width
                q_image = QImage(
                    display_image.data,
                    width,
                    height,
                    bytes_per_line,
                    QImage.Format_Grayscale8
                )
            else:
                # RGB
                channels = display_image.shape[2]
                bytes_per_line = channels * width
                q_image = QImage(
                    display_image.data,
                    width,
                    height,
                    bytes_per_line,
                    QImage.Format_RGB888
                )

            # Store original
            self.original_pixmap = QPixmap.fromImage(q_image)

            # Scale to fit
            self.scale_to_fit()

            # Emit metadata
            self.image_loaded.emit({
                'width': width,
                'height': height,
                'channels': display_image.shape[2] if len(display_image.shape) == 3 else 1,
                'path': str(path) if path else None,
                'title': self.title
            })

            logger.debug(f"{self.title}: Loaded image {width}x{height}")

        except Exception as e:
            logger.error(f"Error displaying image: {e}", exc_info=True)
            self.show_error(f"Display error: {str(e)}")
    
    def scale_to_fit(self):
        """Scale pixmap to fit widget while maintaining aspect ratio"""
        if self.original_pixmap is None:
            return
        
        scaled_pixmap = self.original_pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        self.setPixmap(scaled_pixmap)
        
    def get_display_rect(self):
        """Get the actual rectangle where the image is drawn within the widget"""
        if not self.pixmap() or self.pixmap().isNull():
            return None
            
        pixmap_size = self.pixmap().size()
        widget_size = self.size()
        
        # Calculate centering offsets (QLabel centers content)
        x = (widget_size.width() - pixmap_size.width()) // 2
        y = (widget_size.height() - pixmap_size.height()) // 2
        
        from PySide6.QtCore import QRect
        return QRect(x, y, pixmap_size.width(), pixmap_size.height())

    def screen_to_image(self, screen_pos: QPointF) -> QPointF:
        """Convert widget coordinates to image pixel coordinates"""
        if self.original_pixmap is None:
            return screen_pos
            
        display_rect = self.get_display_rect()
        if not display_rect:
            return screen_pos
            
        # 1. Remove offset (translation)
        local_x = screen_pos.x() - display_rect.x()
        local_y = screen_pos.y() - display_rect.y()
        
        # 2. Apply scale
        scale_x = self.original_pixmap.width() / display_rect.width()
        scale_y = self.original_pixmap.height() / display_rect.height()
        
        image_x = local_x * scale_x
        image_y = local_y * scale_y
        
        return QPointF(image_x, image_y)
        
    def image_to_screen(self, image_pos: QPointF) -> QPointF:
        """Convert image pixel coordinates to widget coordinates"""
        if self.original_pixmap is None:
            return image_pos
            
        display_rect = self.get_display_rect()
        if not display_rect:
            return image_pos
            
        # 1. Apply reverse scale
        # scale = original / display  -->  display = original / scale
        # we want to go image -> display
        # factor = display / original
        scale_x = display_rect.width() / self.original_pixmap.width()
        scale_y = display_rect.height() / self.original_pixmap.height()
        
        local_x = image_pos.x() * scale_x
        local_y = image_pos.y() * scale_y
        
        # 2. Add offset
        screen_x = local_x + display_rect.x()
        screen_y = local_y + display_rect.y()
        
        return QPointF(screen_x, screen_y)
    
    def clear_image(self):
        """Clear displayed image"""
        self.clear()
        self.current_image = None
        self.current_path = None
        self.original_pixmap = None
        self.set_placeholder()
    
    def show_error(self, message: str):
        """Show error message"""
        self.clear()
        self.setText(f"{self.title}\n\n\u274C {message}")
    
    def show_loading(self):
        """Show loading state"""
        self.clear()
        self.setText(f"{self.title}\n\n\u23F3 Loading...")

    def set_hud_enabled(self, enabled: bool):
        """Enable/disable metadata HUD overlay."""
        self.hud_enabled = bool(enabled)
        self.update()
    
    def set_overlay_text(self, text: str):
        """Set text for Smart HUD overlay"""
        self.overlay_text = text
        self.update() # Trigger repaint

    def enterEvent(self, event):
        self.hud_hover_active = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hud_hover_active = False
        self.update()
        super().leaveEvent(event)
        
    def paintEvent(self, event):
        """Draw image and Smart HUD overlay"""
        # 1. Draw standard label content (the image)
        super().paintEvent(event)
        
        # 2. Draw Smart HUD overlay
        if (
            self.hud_enabled
            and self.hud_hover_active
            and hasattr(self, "overlay_text")
            and self.overlay_text
            and self.original_pixmap
        ):
            from PySide6.QtGui import QPainter, QColor, QFont
            
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Setup font
            font = QFont("Consolas", 10)
            font.setBold(True)
            painter.setFont(font)
            
            # Calculate text rect
            padding = 10
            rect = painter.fontMetrics().boundingRect(self.overlay_text)
            rect.moveTo(padding, padding)
            rect.adjust(-5, -5, 5, 5)  # Add margin
            
            # Draw semi-transparent background
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 4, 4)
            
            # Draw text
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(padding, padding + painter.fontMetrics().ascent(), self.overlay_text)
            
            painter.end()

    
    def resizeEvent(self, event):
        """Rescale image on widget resize"""
        super().resizeEvent(event)
        
        # Rescale if image is loaded
        if self.original_pixmap is not None:
            self.scale_to_fit()
            
        # Resize overlay to match
        if hasattr(self, 'annotation_overlay'):
            self.annotation_overlay.resize(self.size())
    
    def get_image_size(self) -> tuple:
        """Get current image dimensions"""
        if self.current_image is not None:
            return (self.current_image.shape[1], self.current_image.shape[0])
        return (0, 0)

    # --- Annotation Integration ---
    def enable_annotations(self, enabled: bool):
        """Enable/Disable annotation overlay"""
        if not hasattr(self, 'annotation_overlay'):
            # Lazy init
            from .annotation_overlay import AnnotationOverlay
            self.annotation_overlay = AnnotationOverlay(self)
            self.annotation_overlay.resize(self.size())
            self.annotation_overlay.show()
            
        self.annotation_overlay.setVisible(enabled)
        # Pass through mouse events if disabled? 
        # Actually it's transparent, but if hidden it won't catch events.
        
    def clear_annotations(self):
        if hasattr(self, 'annotation_overlay'):
            self.annotation_overlay.clear()
            
    def set_annotation_color(self, color):
        if hasattr(self, 'annotation_overlay'):
            self.annotation_overlay.set_color(color)
