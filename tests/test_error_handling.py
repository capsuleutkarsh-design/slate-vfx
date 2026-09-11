"""
Error Handling Utilities Unit Tests.

This suite verifies the robustness of the application's error management:
1. Error Capture: Ensuring exceptions are caught and formatted correctly.
2. Safe Execution: Verifying wrappers that prevent crashes on failure.
3. Decorators: Testing retry logic and automatic error logging.
4. Operation Tracking: Confirming that long-running tasks can be monitored.
"""

import unittest
import tempfile
import shutil
from pathlib import Path
import sys
import os
import logging

# Add the project root to the path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from ut_vfx.utils.error_handler import ErrorHandler, safe_execute, retry_on_failure, log_exceptions, suppress_errors

class TestErrorHandler(unittest.TestCase):
    """Test the error handling utilities."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.error_handler = ErrorHandler(self.temp_dir)
    
    def tearDown(self):
        """Clean up test environment."""
        # Close all handlers associated with the test logger
        logger = logging.getLogger("ut_vfx_errors")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        
        # Also close root logger handlers if they point to the temp dir
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if hasattr(handler, 'baseFilename') and str(handler.baseFilename).startswith(str(self.temp_dir)):
                handler.close()
                root_logger.removeHandler(handler)
        
        # Shutdown logging system to release all file locks
        logging.shutdown()
        
        try:
            shutil.rmtree(self.temp_dir)
        except PermissionError:
            # Fallback for Windows if file is still locked briefly
            pass
    
    def test_error_handling_basic(self):
        """Test basic error handling functionality."""
        try:
            raise ValueError("Test error for error handling")
        except ValueError as e:
            error_result = self.error_handler.handle_error(e, "Test context")
        
        self.assertTrue(error_result['handled'])
        self.assertEqual(error_result['message'], "Test error for error handling")
        self.assertEqual(error_result['context'], "Test context")
        self.assertIsInstance(error_result['recovery_options'], list)
    
    def test_safe_execute_success(self):
        """Test safe execution with successful function."""
        def success_func(x, y):
            return x + y
        
        success, result, error_info = safe_execute(success_func, 5, 3, context="Test success")
        
        self.assertTrue(success)
        self.assertEqual(result, 8)
        self.assertIsNone(error_info)
    
    def test_safe_execute_failure(self):
        """Test safe execution with failing function."""
        def failure_func():
            raise RuntimeError("Test failure")
        
        success, result, error_info = safe_execute(failure_func, context="Test failure")
        
        self.assertFalse(success)
        self.assertIsNone(result)
        self.assertIsNotNone(error_info)
        self.assertEqual(error_info['message'], "Test failure")
    
    def test_operation_tracking(self):
        """Test operation registration and tracking."""
        op_id = "test_op_123"
        op_details = {"type": "test", "param": "value"}
        
        self.error_handler.register_operation(op_id, op_details)
        
        active_ops = self.error_handler.get_active_operations()
        self.assertIn(op_id, active_ops)
        self.assertEqual(active_ops[op_id]['details'], op_details)
        self.assertEqual(active_ops[op_id]['status'], 'active')
        
        # Update status
        self.error_handler.update_operation_status(op_id, 'completed')
        active_ops = self.error_handler.get_active_operations()
        self.assertEqual(active_ops[op_id]['status'], 'completed')
        
        # Cleanup
        self.error_handler.cleanup_operation(op_id)
        active_ops = self.error_handler.get_active_operations()
        self.assertNotIn(op_id, active_ops)
    
    def test_retry_decorator(self):
        """Test retry decorator functionality."""
        call_count = 0
        
        @retry_on_failure(max_retries=2, delay=0.1, context="Test retry")
        def sometimes_fails():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise ConnectionError("Simulated connection error")
            return "Success!"
        
        # First call will fail, second will succeed
        result = sometimes_fails()
        self.assertEqual(result, "Success!")
        self.assertEqual(call_count, 2)
    
    def test_log_exceptions_decorator(self):
        """Test log exceptions decorator."""
        @log_exceptions(context="Test logging")
        def raises_error():
            raise ValueError("Test error for logging")
        
        with self.assertRaises(ValueError):
            raises_error()
    
    def test_suppress_errors_decorator(self):
        """Test suppress errors decorator."""
        @suppress_errors(default_return="Default", context="Test suppression")
        def raises_error():
            raise ValueError("Test error for suppression")
        
        result = raises_error()
        self.assertEqual(result, "Default")


if __name__ == '__main__':
    unittest.main()