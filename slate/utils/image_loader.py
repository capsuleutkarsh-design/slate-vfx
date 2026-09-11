"""
Universal Image Loader for VFX Formats

Loads and normalizes images from various VFX formats:
- EXR (32-bit float)
- DPX (10-bit)
- TIFF, PNG, JPG (standard)

All images are normalized to 8-bit RGB for display.
"""

import os

def _should_enable_opencv_exr_codec() -> bool:
    """Enable OpenCV EXR codec only when EXR loading is explicitly enabled."""
    env = os.environ.get("SLATE_ENABLE_EXR_LOADING", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from slate.core.infra.global_config import GlobalConfig
        return bool(GlobalConfig.exr_loading_enabled())
    except Exception:
        return False


if _should_enable_opencv_exr_codec():
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Dict
import logging

# Try to import OpenImageIO (industry standard for VFX)
# DISABLED BY DEFAULT due to segfault crashes on some systems
# Set environment variable SLATE_ENABLE_OIIO=1 to opt-in
try:
    OIIO_ENABLED = os.environ.get("SLATE_ENABLE_OIIO", "").lower() == "1"
    if OIIO_ENABLED:
        from OpenImageIO import ImageInput
        HAS_OIIO = True
    else:
        HAS_OIIO = False
except ImportError:
    HAS_OIIO = False

# Fallback to imageio if needed
try:
    import imageio.v3 as iio
    HAS_IMAGEIO = True
except ImportError:
    HAS_IMAGEIO = False

logger = logging.getLogger(__name__)

# === EXR LOADING SAFETY ===
# EXR loading uses safe fallback chain: OpenCV → imageio → OIIO (opt-in)
# Tech check dialogs are protected separately to prevent metadata extraction crashes
def _resolve_exr_loading_flag() -> bool:
    """Single source-of-truth for EXR loading policy."""
    env = os.environ.get("SLATE_ENABLE_EXR_LOADING", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from slate.core.infra.global_config import GlobalConfig
        return bool(GlobalConfig.exr_loading_enabled())
    except Exception:
        return False


EXR_LOADING_ENABLED = _resolve_exr_loading_flag()

# If crashes occur, disable with:
# EXR_LOADING_ENABLED = False  # WARNING: Disables EXR display in viewer

logger.info("="*70)
logger.info("ImageLoader Configuration:")
logger.info(f"  EXR Loading: {'ENABLED (OPT-IN)' if EXR_LOADING_ENABLED else 'DISABLED (SAFE DEFAULT)'}")
logger.info("  Enable via config: enable_exr_loading=true or env SLATE_ENABLE_EXR_LOADING=1")
logger.info("="*70)


class ImageLoader:
    """Universal image loader supporting VFX formats"""
    
    @staticmethod
    def load_image(path: Path) -> Optional[np.ndarray]:
        """
        Load image from file (CRASH-SAFE with EXR disabled by default)
        
        Args:
            path: Path to image file
        
        Returns:
            RGB numpy array (8-bit, 0-255) or None if failed
        
        Supports: TIFF, PNG, JPG, DPX
        EXR: DISABLED by default (can cause crashes). Set SLATE_ENABLE_EXR_LOADING=1 to enable.
        
        NOTE: OIIO is also disabled by default due to segfault crashes.
              Set SLATE_ENABLE_OIIO=1 to opt-in.
        """
        if not isinstance(path, Path):
            path = Path(path)
        
        if not path.exists():
            logger.error(f"File not found: {path}")
            return None
        
        ext = path.suffix.lower()
        
        # CRITICAL: Skip EXR by default to prevent crashes
        if ext in ['.exr']:
            if not EXR_LOADING_ENABLED:
                logger.warning(f"EXR loading disabled by default: {path.name}")
                logger.info("To enable EXR loading, set SLATE_ENABLE_EXR_LOADING=1 environment variable")
                return None
            else:
                logger.warning(f"EXR loading ENABLED (may cause crashes): {path.name}")
                return ImageLoader.load_exr(path)
        
        try:
            if ext in ['.dpx']:
                return ImageLoader.load_dpx(path)
            else:
                # Standard formats (PNG, JPG, TIFF, BMP)
                try:
                    img = cv2.imread(str(path))
                    if img is not None:
                        # Convert BGR to RGB
                        try:
                            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        except Exception as e:
                            logger.warning(f"Color conversion failed for {path}: {e}, using BGR")
                            pass
                    return img
                except Exception as e:
                    logger.error(f"Error reading {path}: {e}")
                    return None
        
        except Exception as e:
            logger.error(f"Critical error loading {path}: {e}", exc_info=True)
            return None
    
    @staticmethod
    def load_exr(path: Path) -> Optional[np.ndarray]:
        """
        Load EXR file with SAFE fallback chain
        
        CRITICAL: EXR loading is DISABLED by default to prevent crashes.
        To opt-in to EXR support (at your own risk), set environment variable:
        export SLATE_ENABLE_EXR_LOADING=1
        
        If EXR is disabled, this returns None immediately.
        
        If enabled, priority:
        1. OpenCV (safe, reliable)
        2. imageio (fallback)
        3. OpenImageIO (opt-in only, can crash)
        
        NOTE: Even with all these methods, EXR files can cause crashes.
              Use at your own risk.
        """
        # Early exit if EXR loading is disabled
        if not EXR_LOADING_ENABLED:
            logger.info(f"EXR loading disabled, returning placeholder: {path.name}")
            return None
        
        img = None
        
        # Method 1: Try OpenCV first (safest)
        try:
            logger.debug(f"Loading EXR via OpenCV: {path.name}")
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            
            if img is not None:
                logger.debug(f"OpenCV loaded EXR: {img.shape}, dtype: {img.dtype}")
                # Normalize to 8-bit for display
                img = ImageLoader.normalize_for_display(img)
                # Convert BGR to RGB
                if len(img.shape) == 3 and img.shape[2] >= 3:
                    try:
                        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    except Exception as e:
                        logger.warning(f"Color conversion failed: {e}, returning BGR")
                return img
        except Exception as e:
            logger.debug(f"OpenCV EXR load failed: {e}")
            img = None

        # Method 2: Try imageio fallback
        if img is None and HAS_IMAGEIO:
            try:
                logger.debug(f"Loading EXR via imageio: {path.name}")
                img = iio.imread(path)
                
                if img is not None:
                    logger.debug(f"ImageIO loaded EXR: {img.shape}")
                    img = ImageLoader.normalize_for_display(img)
                    if len(img.shape) == 3 and img.shape[2] >= 3:
                        try:
                            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        except Exception:
                            pass
                    return img
            except Exception as e:
                logger.debug(f"ImageIO fallback failed: {e}")
                img = None

        # Method 3: OPTIONAL OpenImageIO (OPT-IN only, can segfault)
        if img is None and HAS_OIIO and OIIO_ENABLED:
            logger.warning(f"Attempting OIIO (may crash): {path.name}")
            try:
                inp = ImageInput.open(str(path))
                if inp:
                    spec = inp.spec()
                    pixels = inp.read_image(0, 0, 0, 3, "uint8")
                    inp.close()
                    
                    if pixels is not None:
                        img = np.array(pixels).reshape(spec.height, spec.width, 3)
                        logger.debug(f"OIIO loaded EXR: {img.shape}")
                        return img
            except Exception as e:
                logger.error(f"OIIO failed: {e}", exc_info=True)
                img = None

        # All methods failed
        if img is None:
            logger.warning(f"Failed to load EXR with available methods: {path}")
            return None
        
        return img
    
    @staticmethod
    def load_dpx(path: Path) -> Optional[np.ndarray]:
        """
        Load DPX file with 10-bit to 8-bit conversion (CRASH-SAFE)
        
        DPX files are typically 10-bit log encoded.
        """
        try:
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            
            if img is None:
                logger.warning(f"Failed to load DPX: {path}")
                return None
            
            logger.debug(f"Loaded DPX: {img.shape}, dtype: {img.dtype}")
            
            # DPX 10-bit is stored as 16-bit, convert to 8-bit
            if img.dtype == np.uint16:
                # Simple linear conversion (lossy but fast)
                img = (img / 256).astype(np.uint8)
            
            # Convert BGR to RGB
            if len(img.shape) == 3 and img.shape[2] >= 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            
            return img
            
        except Exception as e:
            logger.error(f"Error loading DPX {path}: {e}")
            return None
        
        # Convert BGR to RGB
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        return img
    
    @staticmethod
    def normalize_for_display(img: np.ndarray) -> np.ndarray:
        """
        Normalize image to 8-bit for display
        
        Handles:
        - 32-bit float (EXR) - 0.0-1.0 range
        - 16-bit int - 0-65535 range
        
        Returns:
            8-bit image (0-255)
        """
        if img.dtype == np.float32 or img.dtype == np.float64:
            # Float images (linear, 0.0-1.0 range typically)
            # Clamp negative values and >1.0 highlights
            img = np.clip(img, 0, 1)
            img = (img * 255).astype(np.uint8)
        
        elif img.dtype == np.uint16:
            # 16-bit images (0-65535)
            # Simple division for speed (lossy)
            img = (img / 256).astype(np.uint8)
        
        elif img.dtype == np.uint8:
            # Already 8-bit, pass through
            pass
        
        else:
            logger.warning(f"Unknown dtype: {img.dtype}, attempting conversion")
            img = img.astype(np.uint8)
        
        return img
    
    @staticmethod
    def get_image_info(path: Path) -> Dict:
        """
        Get image metadata without full load
        
        Returns:
            Dict with width, height, channels, dtype, size
        """
        try:
            img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            
            if img is None:
                return {}
            
            info = {
                'width': img.shape[1],
                'height': img.shape[0],
                'channels': img.shape[2] if len(img.shape) == 3 else 1,
                'dtype': str(img.dtype),
                'size_mb': round(img.nbytes / (1024 * 1024), 2)
            }
            
            return info
        
        except (cv2.error, OSError, TypeError, ValueError) as e:
            logger.error(f"Error getting image info: {e}")
            return {}
    
    @staticmethod
    def is_supported_format(path: Path) -> bool:
        """Check if file format is supported"""
        if not isinstance(path, Path):
            path = Path(path)
        
        supported = ['.exr', '.dpx', '.tif', '.tiff', '.png', '.jpg', '.jpeg', '.bmp']
        return path.suffix.lower() in supported
