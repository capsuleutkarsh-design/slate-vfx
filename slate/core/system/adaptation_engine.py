import os
import platform
import logging
import psutil
from pathlib import Path

logger = logging.getLogger(__name__)

class SystemAdaptationEngine:
    """
    The 'Write Once, Run Anywhere' Engine.
    Adapts the application to the current PC's environment:
    - Display: Scales UI for 1080p vs 4K.
    - Paths: Maps network drives across OS (Win/Mac).
    - Hardware: Tunes cache/threading based on RAM/CPU.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SystemAdaptationEngine, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        logger.info("Initializing System Adaptation Engine...")
        
        # 1. Hardware Profile
        self.specs = self._analyze_hardware()
        self.perf_profile = self._determine_performance_profile()
        
        # 2. Display Config (Calculated later when GUI is ready, but defaults here)
        self.ui_scale = 1.0 
        self.font_base_size = 12
        self.user_scale_override = None
        
        # 3. Path Config
        self.mount_map = self._load_mount_map()
        
        logger.info(f"System Profile: {self.perf_profile} | RAM: {self.specs['ram_gb']}GB | Cores: {self.specs['cpu_cores']}")

    def _analyze_hardware(self):
        """Detect System Specs."""
        try:
            ram_bytes = psutil.virtual_memory().total
            ram_gb = round(ram_bytes / (1024**3), 1)
            cpu_cores = os.cpu_count() or 4
            return {"ram_gb": ram_gb, "cpu_cores": cpu_cores}
        except Exception as e:
            logger.error(f"Hardware detection failed: {e}")
            return {"ram_gb": 8.0, "cpu_cores": 4} # Safe defaults

    def _determine_performance_profile(self):
        """Classify system power: LOW, MEDIUM, HIGH."""
        ram = self.specs["ram_gb"]
        cores = self.specs["cpu_cores"]
        
        if ram < 12 or cores < 4:
            return "LOW"
        elif ram >= 32 and cores >= 8:
            return "HIGH"
        else:
            return "MEDIUM"

    def get_performance_settings(self):
        """Return tuned settings for this PC."""
        if self.perf_profile == "LOW":
            return {
                "image_cache_size_mb": 1024,
                "threading_enabled": False, # Safer Single Thread
                "auto_scan_interval": 0, # Disabled
                "animations": False
            }
        elif self.perf_profile == "HIGH":
            return {
                "image_cache_size_mb": 8192,
                "threading_enabled": True,
                "max_workers": self.specs["cpu_cores"] - 1,
                "auto_scan_interval": 300,
                "animations": True
            }
        else: # MEDIUM
            return {
                "image_cache_size_mb": 4096,
                "threading_enabled": True,
                "max_workers": 4,
                "auto_scan_interval": 600,
                "animations": True
            }

    # --- DISPLAY ADAPTER ---
    def calculate_ui_scale(self, screen_geometry, logical_dots_per_inch):
        """
        Calculate ideal UI scale factor based on screen resolution and DPI.
        Call this from GUI init (QApplication).
        """
        width = screen_geometry.width()
        height = screen_geometry.height()
        dpi = logical_dots_per_inch
        
        # Base settings
        final_scale = 1.0
        
        # User override wins if explicitly set.
        if self.user_scale_override is not None:
            final_scale = float(self.user_scale_override)
            logger.info("Using user UI scale override: %.2fx", final_scale)
        else:
            # Check for High DPI (OS Scaling)
            # Standard DPI is 96. If > 120 (approx 125%), the OS is likely handling scaling.
            if dpi > 120:
                logger.info(f"High DPI Detected ({dpi}). Disabling manual resolution scaling to prevent double-scaling.")
                final_scale = 1.0
            else:
                # Traditional Logic for 100% Scaling (Low DPI)
                if width > 3000: # 4K+ at 100%
                    final_scale = 1.8 
                elif width > 2000: # 1440p / 2K at 100%
                    final_scale = 1.4
                elif width < 1400: # Old Laptops
                    final_scale = 0.9
            
        self.ui_scale = final_scale
        self.font_base_size = int(12 * final_scale)
        
        logger.info(f"Display Detected: {width}x{height} | DPI: {dpi} | Scale Factor: {final_scale}x | Base Font: {self.font_base_size}px")
        return final_scale

    def set_user_scale_override(self, value) -> None:
        """
        Set optional manual UI scale override.
        value <= 0 disables override and returns to automatic detection.
        """
        try:
            numeric = float(value)
        except Exception:
            self.user_scale_override = None
            return

        if numeric <= 0:
            self.user_scale_override = None
            return

        self.user_scale_override = max(0.75, min(2.0, numeric))


    def generate_stylesheet_dams(self):
        """
        Return a dictionary of QSS variables for dynamic injection.
        Uses 'px' units consistently to avoid mixing px/pt which causes
        Qt QFont::setPointSize warnings.
        """
        s = self.ui_scale
        return {
            "font_size_small": f"{max(1, int(11 * s))}px",
            "font_size_main": f"{max(1, int(12 * s))}px",
            "font_size_header": f"{max(1, int(15 * s))}px",
            "font_size_h1": f"{max(1, int(19 * s))}px",
            "padding_small": f"{int(4 * s)}px",
            "padding_medium": f"{int(8 * s)}px",
            "padding_large": f"{int(16 * s)}px",
            "icon_size_small": f"{int(16 * s)}px",
            "icon_size_main": f"{int(24 * s)}px",
            "radius_main": f"{int(8 * s)}px"
        }

    def scale_px(self, value: int, minimum: int | None = 1, maximum: int | None = None) -> int:
        """
        Scale pixel values using current UI scale.
        Useful for replacing hardcoded fixed widths/heights in widgets.
        """
        try:
            scaled = int(round(float(value) * float(self.ui_scale)))
        except Exception:
            scaled = int(value)

        if minimum is not None:
            scaled = max(int(minimum), scaled)
        if maximum is not None:
            scaled = min(int(maximum), scaled)
        return scaled

    # --- PATH ADAPTER ---
    def _load_mount_map(self):
        """
        Load storage mapping from JSON config or defaults.
        Ex: {'Z:/': '/Volumes/StudioServer/'}
        """
        # Try loading from JSON config
        config_file = Path(__file__).parent.parent.parent / "data" / "mount_config.json"
        
        if config_file.exists():
            try:
                import json
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    mount_map = config.get("mount_mappings", {})
                    logger.info(f"Loaded mount config from {config_file} ({len(mount_map)} mappings)")
                    return mount_map
            except Exception as e:
                logger.warning(f"Failed to load mount config from {config_file}: {e}, using defaults")
        else:
            logger.info(f"Mount config not found at {config_file}, using defaults")
        
        # Fallback to defaults if file doesn't exist or fails to load
        return {
            # Windows -> Mac/Linux Mappings
            "Z:/": "/Volumes/Projects/",
            "Y:/": "/Volumes/Assets/",
            # Mac/Linux -> Windows Mappings
            "/Volumes/Projects/": "Z:/",
            "/Volumes/Assets/": "Y:/"
        }

    def resolve_path(self, raw_path):
        """
        Convert a path from 'Stored Format' to 'Local Format'.
        """
        if not raw_path: return ""
        
        platform.system() # Windows, Darwin, Linux
        p = str(Path(raw_path))
        
        # Simple string replacement based on execution context
        # Ideally we store UNCs or Relative paths, but for legacy absolute paths:
        
        for search, replace in self.mount_map.items():
            if p.startswith(search):
                # Only apply if it makes sense for current OS?
                # Actually, we rely on the map being specific for THIS machine's needs vs the Input.
                # But a global map needs logic.
                
                # Assume map is "Remote Prefix" -> "Local Prefix"
                # (Simplified for now - can be expanded)
                pass

        return Path(p) # For now pass-through until user defines specific map

# Global Instance
system_engine = SystemAdaptationEngine()
