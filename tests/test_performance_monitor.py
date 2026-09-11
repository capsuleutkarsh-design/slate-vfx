"""
Tests for Enhanced Performance Monitor.

Week 1 Day 3: Comprehensive monitoring tests for DB pool stats,
query performance, worker duration, and memory tracking.
"""

import time
from ut_vfx.core.infra.performance_monitor import PerformanceMonitor, performance_monitor


class TestPerformanceMonitorInitialization:
    """Test PerformanceMonitor initialization and configuration."""
    
    def test_monitor_initialization(self):
        """Test monitor initializes with correct structure."""
        monitor = PerformanceMonitor()
        assert monitor.metrics is not None
        assert 'db_pool_stats' in monitor.metrics
        assert 'query_times' in monitor.metrics
        assert 'worker_durations' in monitor.metrics
        assert 'memory_usage' in monitor.metrics
    
    def test_monitor_thresholds(self):
        """Test default thresholds are set correctly."""
        monitor = PerformanceMonitor()
        assert monitor.thresholds['slow_query_ms'] == 100
        assert monitor.thresholds['memory_percent'] == 80
        assert monitor.thresholds['pool_usage_percent'] == 90
        assert monitor.thresholds['worker_timeout_sec'] == 300
    
    def test_custom_max_metrics(self):
        """Test custom max_metrics configuration."""
        monitor = PerformanceMonitor(max_metrics=500)
        assert monitor.metrics['query_times'].maxlen == 500


class TestDBPoolMonitoring:
    """Test database pool statistics monitoring."""
    
    def test_log_db_pool_stats(self):
        """Test DB pool stats logging."""
        monitor = PerformanceMonitor()
        monitor.log_db_pool_stats(active=5, idle=3, total=10)
        
        assert len(monitor.metrics['db_pool_stats']) == 1
        stat = monitor.metrics['db_pool_stats'][0]
        assert stat['active'] == 5
        assert stat['idle'] == 3
        assert stat['total'] == 10
        assert stat['usage_percent'] == 50.0
    
    def test_pool_usage_calculation(self):
        """Test pool usage percentage calculation."""
        monitor = PerformanceMonitor()
        monitor.log_db_pool_stats(active=9, idle=1, total=10)
        
        stat = monitor.metrics['db_pool_stats'][0]
        assert stat['usage_percent'] == 90.0
    
    def test_pool_zero_total(self):
        """Test handling of zero total connections."""
        monitor = PerformanceMonitor()
        monitor.log_db_pool_stats(active=0, idle=0, total=0)
        
        stat = monitor.metrics['db_pool_stats'][0]
        assert stat['usage_percent'] == 0.0


class TestQueryPerformanceMonitoring:
    """Test query performance tracking."""
    
    def test_log_query_performance(self):
        """Test query performance logging."""
        monitor = PerformanceMonitor()
        monitor.log_query_performance("SELECT * FROM users", 50.5, True)
        
        assert len(monitor.metrics['query_times']) == 1
        query = monitor.metrics['query_times'][0]
        assert query['duration_ms'] == 50.5
        assert query['success'] is True
        assert 'SELECT * FROM users' in query['query']
    
    def test_query_truncation(self):
        """Test long queries are truncated."""
        monitor = PerformanceMonitor()
        long_query = "SELECT " + "a, " * 100 + "FROM table"
        monitor.log_query_performance(long_query, 10.0)
        
        query = monitor.metrics['query_times'][0]
        assert len(query['query']) <= 100
    
    def test_slow_query_detection(self):
        """Test slow queries are detected."""
        monitor = PerformanceMonitor()
        # This should trigger warning (150ms > 100ms threshold)
        monitor.log_query_performance("SELECT * FROM big_table", 150.0)
        
        query = monitor.metrics['query_times'][0]
        assert query['duration_ms'] == 150.0
    
    def test_failed_query_tracking(self):
        """Test failed queries are tracked."""
        monitor = PerformanceMonitor()
        monitor.log_query_performance("INVALID SQL", 5.0, success=False)
        
        query = monitor.metrics['query_times'][0]
        assert query['success'] is False


class TestWorkerMonitoring:
    """Test worker thread duration monitoring."""
    
    def test_log_worker_duration(self):
        """Test worker duration logging."""
        monitor = PerformanceMonitor()
        monitor.log_worker_duration("FolderCreationWorker", 12.5, True)
        
        assert len(monitor.metrics['worker_durations']) == 1
        worker = monitor.metrics['worker_durations'][0]
        assert worker['worker'] == "FolderCreationWorker"
        assert worker['duration_sec'] == 12.5
        assert worker['success'] is True
    
    def test_worker_timeout_detection(self):
        """Test long-running workers are detected."""
        monitor = PerformanceMonitor()
        # 350s exceeds 300s threshold
        monitor.log_worker_duration("SlowWorker", 350.0, True)
        
        worker = monitor.metrics['worker_durations'][0]
        assert worker['duration_sec'] == 350.0
    
    def test_failed_worker_tracking(self):
        """Test failed workers are tracked."""
        monitor = PerformanceMonitor()
        monitor.log_worker_duration("FailedWorker", 5.0, success=False)
        
        worker = monitor.metrics['worker_durations'][0]
        assert worker['success'] is False


class TestMemoryMonitoring:
    """Test memory usage tracking."""
    
    def test_log_memory_usage(self):
        """Test memory usage logging."""
        monitor = PerformanceMonitor()
        monitor.log_memory_usage()
        
        assert len(monitor.metrics['memory_usage']) == 1
        mem = monitor.metrics['memory_usage'][0]
        assert 'rss_mb' in mem
        assert 'vms_mb' in mem
        assert 'percent' in mem
        assert mem['rss_mb'] > 0  # Should have some memory usage
    
    def test_multiple_memory_logs(self):
        """Test multiple memory usage logs."""
        monitor = PerformanceMonitor()
        monitor.log_memory_usage()
        time.sleep(0.1)
        monitor.log_memory_usage()
        
        assert len(monitor.metrics['memory_usage']) == 2


class TestPerformanceSummary:
    """Test performance summary generation."""
    
    def test_get_summary_empty(self):
        """Test summary with no metrics."""
        monitor = PerformanceMonitor()
        summary = monitor.get_summary()
        
        assert 'db_pool' in summary
        assert 'queries' in summary
        assert 'workers' in summary
        assert 'memory' in summary
        assert summary['queries']['count'] == 0
    
    def test_get_summary_with_data(self):
        """Test summary with real metrics."""
        monitor = PerformanceMonitor()
        
        # Add various metrics
        monitor.log_db_pool_stats(5, 5, 10)
        monitor.log_query_performance("SELECT 1", 50.0, True)
        monitor.log_query_performance("SELECT 2", 150.0, True)  # Slow query
        monitor.log_worker_duration("Worker1", 10.0, True)
        monitor.log_worker_duration("Worker2", 5.0, False)  # Failed
        monitor.log_memory_usage()
        
        summary = monitor.get_summary()
        
        # Check counts
        assert summary['db_pool']['count'] == 1
        assert summary['queries']['count'] == 2
        assert summary['workers']['count'] == 2
        assert summary['memory']['current_mb'] > 0
        
        # Check calculations
        assert summary['queries']['slow_queries'] == 1
        assert summary['queries']['failure_count'] == 0
        assert summary['workers']['success_rate'] == 50.0  # 1 success, 1 failure
    
    def test_summary_averages(self):
        """Test summary calculates averages correctly."""
        monitor = PerformanceMonitor()
        
        # Add queries with specific durations
        monitor.log_query_performance("Q1", 100.0)
        monitor.log_query_performance("Q2", 200.0)
        
        summary = monitor.get_summary()
        assert summary['queries']['avg_duration_ms'] == 150.0


class TestLegacyCompatibility:
    """Test backward compatibility with legacy methods."""
    
    def test_start_stop_timer(self):
        """Test legacy timer methods."""
        monitor = PerformanceMonitor()
        monitor.start_timer("operation1")
        time.sleep(0.1)
        elapsed = monitor.stop_timer("operation1")
        
        assert elapsed > 0.09  # At least 0.09 seconds
        assert elapsed < 0.2   # But not too long
    
    def test_get_memory_usage_legacy(self):
        """Test legacy memory usage method."""
        monitor = PerformanceMonitor()
        mem = monitor.get_memory_usage()
        assert mem > 0
        assert isinstance(mem, float)
    
    def test_get_cpu_percent_legacy(self):
        """Test legacy CPU percentage method."""
        monitor = PerformanceMonitor()
        cpu = monitor.get_cpu_percent()
        assert cpu >= 0
        assert cpu <= 100
    
    def test_get_performance_report_legacy(self):
        """Test legacy performance report."""
        monitor = PerformanceMonitor()
        report = monitor.get_performance_report()
        
        assert 'current_memory_mb' in report
        assert 'current_cpu_percent' in report
        assert 'summary' in report
        assert 'timestamp' in report


class TestThreadSafety:
    """Test thread-safe operations."""
    
    def test_concurrent_logging(self):
        """Test concurrent metric logging is thread-safe."""
        import threading
        
        monitor = PerformanceMonitor()
        
        def log_queries():
            for i in range(10):
                monitor.log_query_performance(f"SELECT {i}", 50.0)
        
        threads = [threading.Thread(target=log_queries) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Should have 50 total queries (5 threads * 10 queries)
        assert len(monitor.metrics['query_times']) == 50


class TestGlobalInstance:
    """Test global performance_monitor instance."""
    
    def test_global_instance_exists(self):
        """Test global instance is accessible."""
        assert performance_monitor is not None
        assert isinstance(performance_monitor, PerformanceMonitor)
    
    def test_global_instance_functional(self):
        """Test global instance can log metrics."""
        # Clear any existing metrics
        performance_monitor.metrics['query_times'].clear()
        
        performance_monitor.log_query_performance("TEST QUERY", 25.0)
        assert len(performance_monitor.metrics['query_times']) >= 1
