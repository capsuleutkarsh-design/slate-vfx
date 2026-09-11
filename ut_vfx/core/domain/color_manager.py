"""
Color Manager — OpenColorIO Integration for VFX Review

Provides color-managed image display using ACES via OpenColorIO.
Uses the built-in studio config (no external .ocio file required).

Graceful degradation: if PyOpenColorIO is not installed, all transforms
return the image unchanged.
"""

import logging
from typing import List, Optional, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

# --- OCIO availability check ---
try:
    import PyOpenColorIO as ocio
    OCIO_AVAILABLE = True
    OCIO_VERSION = ocio.__version__
    logger.info(f"OpenColorIO {OCIO_VERSION} available")
except ImportError:
    OCIO_AVAILABLE = False
    OCIO_VERSION = None
    ocio = None
    logger.info("OpenColorIO not installed — color management disabled")


# Built-in ACES config name (ships with OCIO 2.3+)
_BUILTIN_CONFIG = "studio-config-v2.1.0_aces-v1.3_ocio-v2.3"


class ColorManager:
    """
    Singleton color manager wrapping OpenColorIO.

    Usage:
        cm = ColorManager.instance()
        if cm.is_available():
            transformed = cm.transform_image(img_float32, 'ACEScg')
    """

    _instance = None

    @classmethod
    def instance(cls) -> "ColorManager":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._config = None
        self._current_display = None
        self._current_view = None
        self._processor_cache = {}
        self._enabled = False        # User toggle

        if OCIO_AVAILABLE:
            self._init_config()

    def _init_config(self):
        """Load the built-in ACES config."""
        try:
            self._config = ocio.Config.CreateFromBuiltinConfig(_BUILTIN_CONFIG)
            self._current_display = self._config.getDefaultDisplay()
            self._current_view = self._config.getDefaultView(self._current_display)
            logger.info(
                f"OCIO config loaded: {len(list(self._config.getColorSpaceNames()))} colorspaces, "
                f"display={self._current_display}, view={self._current_view}"
            )
        except Exception as e:
            logger.warning(f"Failed to load OCIO config: {e}")
            self._config = None

    # --- Public API ---

    def is_available(self) -> bool:
        """Check if OCIO is installed and config is loaded."""
        return OCIO_AVAILABLE and self._config is not None

    def is_enabled(self) -> bool:
        """Check if color management is currently enabled by the user."""
        return self._enabled and self.is_available()

    def set_enabled(self, enabled: bool):
        """Enable/disable color management."""
        self._enabled = enabled
        self._processor_cache.clear()

    def get_colorspaces(self) -> List[str]:
        """List available input colorspaces."""
        if not self.is_available():
            return []
        return list(self._config.getColorSpaceNames())

    def get_common_colorspaces(self) -> List[Tuple[str, str]]:
        """
        Return commonly used colorspaces as (name, label) tuples.
        These are the ones most VFX artists will use.
        """
        if not self.is_available():
            return []

        all_cs = set(self._config.getColorSpaceNames())

        # Prioritised list of VFX-relevant spaces
        _WANTED = [
            ("ACEScg", "ACEScg (Linear)"),
            ("ACES2065-1", "ACES 2065-1"),
            ("ACEScc", "ACEScc (Log)"),
            ("ACEScct", "ACEScct (Log)"),
            ("sRGB Encoding", "sRGB"),
            ("Raw", "Raw (No Transform)"),
            ("Linear Rec.709 (sRGB)", "Linear Rec.709"),
            ("Linear ARRI Wide Gamut 3", "ARRI LogC3"),
            ("Linear REDWideGamutRGB", "RED Wide Gamut"),
        ]

        result = [(name, label) for name, label in _WANTED if name in all_cs]
        return result

    def get_displays(self) -> List[str]:
        """List available display devices."""
        if not self.is_available():
            return []
        return list(self._config.getDisplays())

    def get_views(self, display: Optional[str] = None) -> List[str]:
        """List available views for a display."""
        if not self.is_available():
            return []
        display = display or self._current_display
        return list(self._config.getViews(display))

    def get_current_display(self) -> str:
        """Get current display device name."""
        return self._current_display or ""

    def get_current_view(self) -> str:
        """Get current view transform name."""
        return self._current_view or ""

    def set_display_view(self, display: str, view: str):
        """Set display and view transform."""
        self._current_display = display
        self._current_view = view
        self._processor_cache.clear()
        logger.info(f"OCIO display/view set to: {display} / {view}")

    # --- Transform ---

    def transform_image(
        self,
        image,
        src_colorspace: str = "ACEScg",
    ) -> "np.ndarray":
        """
        Apply OCIO color transform to a numpy image array.

        Args:
            image: Input image as numpy array.
                   - uint8 (0-255): will be converted to float32 internally
                   - float32 (0.0-1.0+): used directly
            src_colorspace: Source colorspace name (e.g. 'ACEScg', 'sRGB Encoding').

        Returns:
            Transformed image as uint8 numpy array (0-255), ready for Qt display.
            If OCIO is disabled or unavailable, returns the input unchanged.
        """
        if not self.is_enabled():
            return image

        if image is None or image.size == 0:
            return image

        try:
            return self._apply_transform(image, src_colorspace)
        except Exception as e:
            logger.error(f"OCIO transform failed: {e}", exc_info=True)
            return image

    def _apply_transform(self, image, src_colorspace: str):
        """Internal transform implementation."""
        # Get or create CPU processor
        cache_key = (src_colorspace, self._current_display, self._current_view)
        cpu_proc = self._processor_cache.get(cache_key)

        if cpu_proc is None:
            transform = ocio.DisplayViewTransform()
            transform.setSrc(src_colorspace)
            transform.setDisplay(self._current_display)
            transform.setView(self._current_view)

            processor = self._config.getProcessor(transform)
            cpu_proc = processor.getDefaultCPUProcessor()
            self._processor_cache[cache_key] = cpu_proc

        # Prepare image data
        was_uint8 = (image.dtype == np.uint8)
        if was_uint8:
            img_float = image.astype(np.float32) / 255.0
        else:
            img_float = image.astype(np.float32, copy=True)

        # OCIO expects contiguous RGB array
        if not img_float.flags['C_CONTIGUOUS']:
            img_float = np.ascontiguousarray(img_float)

        # Apply transform (in-place for performance)
        h, w = img_float.shape[:2]
        channels = img_float.shape[2] if len(img_float.shape) == 3 else 1

        if channels >= 3:
            # Process as RGB, strip alpha if present
            rgb = img_float[:, :, :3].reshape(-1, 3)
            cpu_proc.applyRGB(rgb)
            img_float[:, :, :3] = rgb.reshape(h, w, 3)

        # Convert back to uint8 for display
        np.clip(img_float, 0.0, 1.0, out=img_float)
        result = (img_float * 255.0).astype(np.uint8)

        return result

    def get_status_text(self) -> str:
        """Return a short status string for the UI."""
        if not OCIO_AVAILABLE:
            return "OCIO: Not Installed"
        if not self._config:
            return "OCIO: Config Error"
        if not self._enabled:
            return "OCIO: Off"
        return f"OCIO: {self._current_display} / {self._current_view}"
