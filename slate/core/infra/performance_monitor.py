import time
import psutil
import threading
from typing import Dict, Any
from functools import wraps
from datetime import datetime
from collections import deque
import logging


class PerformanceMonitor:
    """Enhanced performance monitoring with comprehensive metrics collection."""
    
    def __init__(self, max_metrics=1000):
        """Initialize performance monitor with configurable metrics buffer.
        
        Args:
            max_metrics: Maximum number of metrics to store in memory (default: 1000)
        """
        self.metrics = {
            'db_pool_stats': deque(maxlen=max_metrics),
            'query_times': deque(maxlen=max_metrics),
            'worker_durations': deque(maxlen=max_metrics),
            'memory_usage': deque(maxlen=max_metrics)
        }
        self.start_times = {}
        self.lock = threading.Lock()
        self.process = psutil.Process()
        
        # Configurable alert thresholds
        self.thresholds = {
            'slow_query_ms': 100,
            'memory_percent': 80,
            'pool_usage_percent': 90,
            'worker_timeout_sec': 300
        }
        
    def log_db_pool_stats(self, active: int, idle: int, total: int):
        """Log connection pool statistics.
        
        Args:
            active: Number of active connections
            idle: Number of idle connections
            total: Total pool size
        """
        with self.lock:
            usage_percent = (active / total * 100) if total > 0 else 0
            
            metric = {
                'timestamp': datetime.now(),
                'active': active,
                'idle': idle,
                'total': total,
                'usage_percent': usage_percent
            }
            self.metrics['db_pool_stats'].append(metric)
            
            # Alert on high pool usage
            if usage_percent > self.thresholds['pool_usage_percent']:
                logging.warning(f"High DB pool usage: {usage_percent:.1f}% ({active}/{total})")
    
    def log_query_performance(self, query: str, duration_ms: float, success: bool = True):
        """Log query execution time with slow query detection.
        
        Args:
            query: SQL query string (will be truncated to 100 chars)
            duration_ms: Query duration in milliseconds
            success: Whether query executed successfully
        """
        with self.lock:
            metric = {
                'query': query[:100],  # Truncate query for storage
                'duration_ms': duration_ms,
                'timestamp': datetime.now(),
                'success': success
            }
            self.metrics['query_times'].append(metric)
            
            # Alert on slow queries
            if duration_ms > self.thresholds['slow_query_ms']:
                logging.warning(f"Slow query ({duration_ms:.1f}ms): {query[:50]}...")
    
    def log_worker_duration(self, worker_name: str, duration_sec: float, success: bool = True):
        """Log worker thread execution duration.
        
        Args:
            worker_name: Name of the worker thread
            duration_sec: Execution duration in seconds
            success: Whether worker completed successfully
        """
        with self.lock:
            metric = {
                'worker': worker_name,
                'duration_sec': duration_sec,
                'timestamp': datetime.now(),
                'success': success
            }
            self.metrics['worker_durations'].append(metric)
            
            # Alert on worker timeout
            if duration_sec > self.thresholds['worker_timeout_sec']:
                logging.warning(f"Long-running worker: {worker_name} ({duration_sec:.1f}s)")
    
    def log_memory_usage(self):
        """Log current memory usage with high memory alert."""
        try:
            mem_info = self.process.memory_info()
            mem_percent = self.process.memory_percent()
            
            with self.lock:
                metric = {
                    'timestamp': datetime.now(),
                    'rss_mb': mem_info.rss / 1024 / 1024,
                    'vms_mb': mem_info.vms / 1024 / 1024,
                    'percent': mem_percent
                }
                self.metrics['memory_usage'].append(metric)
                
                # Alert on high memory
                if mem_percent > self.thresholds['memory_percent']:
                    logging.warning(f"High memory usage: {mem_percent:.1f}%")
        except Exception as e:
            logging.debug(f"Memory usage logging failed: {e}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary.
        
        Returns:
            Dictionary containing metrics summary for all monitored areas
        """
        with self.lock:
            return {
                'db_pool': {
                    'count': len(self.metrics['db_pool_stats']),
                    'avg_usage': self._avg([s['usage_percent'] for s in self.metrics['db_pool_stats']]) if self.metrics['db_pool_stats'] else 0,
                    'current': self.metrics['db_pool_stats'][-1] if self.metrics['db_pool_stats'] else None
                },
                'queries': {
                    'count': len(self.metrics['query_times']),
                    'avg_duration_ms': self._avg([q['duration_ms'] for q in self.metrics['query_times']]) if self.metrics['query_times'] else 0,
                    'slow_queries': len([q for q in self.metrics['query_times'] if q['duration_ms'] > self.thresholds['slow_query_ms']]),
                    'failure_count': len([q for q in self.metrics['query_times'] if not q.get('success', True)])
                },
                'workers': {
                    'count': len(self.metrics['worker_durations']),
                    'avg_duration_sec': self._avg([w['duration_sec'] for w in self.metrics['worker_durations']]) if self.metrics['worker_durations'] else 0,
                    'success_rate': self._success_rate(self.metrics['worker_durations'])
                },
                'memory': {
                    'current_mb': self.metrics['memory_usage'][-1]['rss_mb'] if self.metrics['memory_usage'] else 0,
                    'peak_mb': max([m['rss_mb'] for m in self.metrics['memory_usage']], default=0),
                    'avg_percent': self._avg([m['percent'] for m in self.metrics['memory_usage']]) if self.metrics['memory_usage'] else 0
                }
            }
    
    def _avg(self, values: list) -> float:
        """Calculate average of values."""
        return sum(values) / len(values) if values else 0.0
    
    def _success_rate(self, metrics: list) -> float:
        """Calculate success rate percentage."""
        if not metrics:
            return 100.0
        successes = len([m for m in metrics if m.get('success', True)])
        return (successes / len(metrics)) * 100.0
    
    # Legacy methods for backward compatibility
    def start_timer(self, operation_name: str):
        """Start timing for an operation."""
        self.start_times[operation_name] = time.time()
        
    def stop_timer(self, operation_name: str) -> float:
        """Stop timing for an operation and return elapsed time."""
        if operation_name in self.start_times:
            elapsed = time.time() - self.start_times[operation_name]
            del self.start_times[operation_name]
            return elapsed
        return 0.0
    
    def get_memory_usage(self) -> float:
        """Get current memory usage in MB."""
        return self.process.memory_info().rss / 1024 / 1024
    
    def get_cpu_percent(self) -> float:
        """Get current CPU usage percentage."""
        return self.process.cpu_percent()
    
    def profile_function(self, func_name: str = None):
        """Decorator to profile function execution."""
        def decorator(func):
            name = func_name or func.__name__
            
            @wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                start_memory = self.get_memory_usage()
                
                try:
                    result = func(*args, **kwargs)
                    success = True
                except Exception as e:
                    result = None
                    success = False
                    logging.exception(f"Function {name} failed: {e}")
                    raise  # Re-raise exception after logging
                finally:
                    end_time = time.time()
                    end_memory = self.get_memory_usage()
                    
                    # Log performance metrics
                    duration = end_time - start_time
                    memory_delta = end_memory - start_memory
                    
                    logging.debug(f"Performance: {name} - Duration: {duration:.2f}s, "
                               f"Memory Delta: {memory_delta:.2f}MB, Success: {success}")
                
                return result
            return wrapper
        return decorator
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Get a comprehensive performance report (legacy compatibility)."""
        return {
            'current_memory_mb': self.get_memory_usage(),
            'current_cpu_percent': self.get_cpu_percent(),
            'summary': self.get_summary(),
            'timestamp': datetime.now()
        }


# Global performance monitor instance
performance_monitor = PerformanceMonitor()


def batch_process(items, process_func, batch_size=100, progress_callback=None):
    """
    Process items in batches for better performance and memory management.
    
    Args:
        items: List of items to process
        process_func: Function to process each item
        batch_size: Number of items to process at once
        progress_callback: Optional callback to report progress
    """
    total_items = len(items)
    processed = 0
    all_results = []
    
    for i in range(0, total_items, batch_size):
        batch = items[i:i + batch_size]
        batch_results = []
        
        for item in batch:
            try:
                result = process_func(item)
                batch_results.append(result)
            except Exception as e:
                logging.exception(f"Error processing item {item}: {e}")
                batch_results.append(None)
        
        all_results.extend(batch_results)
        processed += len(batch)
        
        if progress_callback:
            progress_callback(processed, total_items)
        
        # Allow other threads to run
        time.sleep(0.001)  # Small delay to prevent blocking
    
    return all_results


def async_operation(func):
    """Decorator to run operations asynchronously."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper


def memory_efficient_file_operation(file_path, operation_func, chunk_size=8192):
    """
    Perform file operations in chunks for memory efficiency.
    
    Args:
        file_path: Path to the file
        operation_func: Function to apply to each chunk
        chunk_size: Size of chunks to read
    """
    results = []
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            
            result = operation_func(chunk)
            results.append(result)
    
    return results