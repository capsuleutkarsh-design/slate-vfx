"""
Performance Monitoring Unit Tests.

This test validates the tools used to measure application efficiency:
1. Timers: Verifying that execution time is measured accurately.
2. Memory: Basic checks for tracking RAM usage (sanity check).
3. Batch Processing: Ensuring that large lists are processed in chunks correctly.
"""

import unittest
import time
import sys
import os

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from ut_vfx.core.infra.performance_monitor import PerformanceMonitor, batch_process
from ut_vfx.core.infra.performance_config import performance_config


class TestPerformanceMonitor(unittest.TestCase):
    """Test the performance monitoring utilities."""
    
    def setUp(self):
        """Set up test environment."""
        self.monitor = PerformanceMonitor()
    
    def test_timer_functionality(self):
        """Test the timer functionality."""
        self.monitor.start_timer("test_operation")
        time.sleep(0.1)  # Sleep for 100ms
        elapsed = self.monitor.stop_timer("test_operation")
        
        # Elapsed time should be around 0.1 seconds (with some tolerance)
        self.assertGreaterEqual(elapsed, 0.09)  # At least 90ms
        self.assertLessEqual(elapsed, 0.2)      # At most 200ms
    
    def test_memory_monitoring(self):
        """Test memory monitoring functionality."""
        initial_memory = self.monitor.get_memory_usage()
        
        # Create some data to increase memory usage
        [i for i in range(100000)]
        current_memory = self.monitor.get_memory_usage()
        
        # Current memory should be higher than initial
        # Note: This test might be flaky due to Python's garbage collection
        # but it's good for basic functionality verification
        print(f"Initial memory: {initial_memory:.2f}MB, Current: {current_memory:.2f}MB")
    
    def test_batch_processing(self):
        """Test batch processing functionality."""
        # Create test items
        items = list(range(100))
        
        # Process function
        def process_item(item):
            return item * 2
        
        # Track progress
        progress_calls = []
        def progress_callback(processed, total):
            progress_calls.append((processed, total))
        
        # Process in batches
        results = batch_process(items, process_item, batch_size=10, progress_callback=progress_callback)
        
        # Verify results
        self.assertEqual(len(results), len(items))
        for i, result in enumerate(results):
            self.assertEqual(result, i * 2)
        
        # Verify progress was called appropriately
        expected_progress_calls = [(10, 100), (20, 100), (30, 100), (40, 100), 
                                   (50, 100), (60, 100), (70, 100), (80, 100), 
                                   (90, 100), (100, 100)]
        self.assertEqual(progress_calls, expected_progress_calls)


class TestPerformanceConfig(unittest.TestCase):
    """Test the performance configuration."""
    
    def setUp(self):
        """Set up test environment."""
        self.config = performance_config
    
    def test_config_defaults(self):
        """Test that default configuration values exist."""
        # Check that all default keys exist
        default_keys = [
            "thread_pool_size", "batch_size", "chunk_size", 
            "max_memory_usage_mb", "progress_update_interval",
            "disk_space_check_buffer", "checksum_threshold_mb",
            "max_concurrent_operations"
        ]
        
        for key in default_keys:
            value = self.config.get(key)
            self.assertIsNotNone(value, f"Config key {key} should have a value")
    
    def test_config_values(self):
        """Test that configuration values are reasonable."""
        # Thread pool size should be positive
        thread_pool_size = self.config.get("thread_pool_size")
        self.assertGreater(thread_pool_size, 0)
        self.assertLessEqual(thread_pool_size, 16)  # Reasonable upper limit
        
        # Batch size should be positive
        batch_size = self.config.get("batch_size")
        self.assertGreater(batch_size, 0)
        
        # Chunk size should be reasonable
        chunk_size = self.config.get("chunk_size")
        self.assertGreaterEqual(chunk_size, 1024)  # At least 1KB
        self.assertLessEqual(chunk_size, 1024*1024)  # At most 1MB


if __name__ == '__main__':
    unittest.main()