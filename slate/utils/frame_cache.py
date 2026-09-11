
import logging
from collections import OrderedDict
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

class FrameCache:
    """
    LRU (Least Recently Used) Cache for image frames.
    Stores numpy arrays to prevent redundant disk I/O.
    
    Enhanced with performance statistics tracking.
    """
    
    def __init__(self, max_size_mb: int = 2048):
        self.cache = OrderedDict()
        self.max_size_mb = max_size_mb
        self.current_size_mb = 0.0
        
        # Statistics tracking
        self.stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0
        }
        
    def __len__(self) -> int:
        return len(self.cache)
    
    def get_stats(self) -> dict:
        """Get cache statistics including hit rate and memory usage"""
        hit_rate = 0.0
        total = self.stats['hits'] + self.stats['misses']
        if total > 0:
            hit_rate = self.stats['hits'] / total * 100
        
        return {
            'size_mb': round(self.current_size_mb, 2),
            'max_size_mb': self.max_size_mb,
            'usage_percent': round((self.current_size_mb / self.max_size_mb) * 100, 2),
            'items': len(self.cache),
            'hit_rate': round(hit_rate, 2),
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'evictions': self.stats['evictions']
        }
        
    def get(self, key: str) -> Optional[np.ndarray]:
        """Retrieve frame from cache"""
        if key in self.cache:
            # Move to end (mark as recently used)
            self.cache.move_to_end(key)
            self.stats['hits'] += 1
            return self.cache[key]
        self.stats['misses'] += 1
        return None
        
    def put(self, key: str, image: np.ndarray):
        """Add frame to cache"""
        if key in self.cache:
            self.cache.move_to_end(key)
            return
            
        # Calculate size in MB
        size_mb = image.nbytes / (1024 * 1024)
        
        # Make space if needed
        while self.current_size_mb + size_mb > self.max_size_mb and self.cache:
            self._evict_last()
            
        # Store
        self.cache[key] = image
        self.current_size_mb += size_mb
        
        logger.debug(f"Cached {key} ({size_mb:.2f} MB). Total: {self.current_size_mb:.2f}/{self.max_size_mb} MB")

    def _evict_last(self):
        """Remove least recently used item"""
        key, img = self.cache.popitem(last=False)
        self.current_size_mb -= img.nbytes / (1024 * 1024)
        self.stats['evictions'] += 1
        
    def clear(self):
        """Clear cache"""
        self.cache.clear()
        self.current_size_mb = 0
        # Note: We don't reset stats on clear, only on init

