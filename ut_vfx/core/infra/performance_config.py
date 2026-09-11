import logging
import json
from pathlib import Path
from typing import Dict, Any


class PerformanceConfig:
    """Configuration for performance settings."""
    
    def __init__(self):
        self.config_path = self._get_config_path()
        self.default_config = {
            "thread_pool_size": 4,
            "batch_size": 100,
            "chunk_size": 8192,
            "max_memory_usage_mb": 1024,
            "progress_update_interval": 0.1,
            "disk_space_check_buffer": 0.25,  # 25% buffer
            "checksum_threshold_mb": 1024,  # Skip checksum for files > 1GB
            "max_concurrent_operations": 1
        }
        self.config = self._load_config()
    
    def _get_config_path(self) -> Path:
        """Get the path for performance config file."""
        # Try to get the config directory
        try:
            from importlib.resources import files
            config_path = files('ut_vfx.data').joinpath('performance_config.json')
            return Path(config_path)
        except Exception:
            # Fallback: look in data directory
            return Path(__file__).parent.parent / "data" / "performance_config.json"
    
    def _load_config(self) -> Dict[str, Any]:
        """Load performance configuration from file or use defaults."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    config = self.default_config.copy()
                    config.update(loaded_config)
                    return config
            except Exception as e:
                logging.info(f"Could not load performance config, using defaults: {e}")
        
        return self.default_config.copy()
    
    def get(self, key: str, default=None):
        """Get a configuration value."""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """Set a configuration value."""
        self.config[key] = value
    
    def save(self):
        """Save the current configuration to file."""
        try:
            # Ensure the directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logging.info(f"Could not save performance config: {e}")


# Global instance
performance_config = PerformanceConfig()