import sys
import traceback
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
import json
from datetime import datetime
import threading
from contextlib import contextmanager
import time
import queue
import atexit
import os
try:
    import sentry_sdk
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False


class ErrorHandler:
    """Comprehensive error handling and recovery system."""
    
    def __init__(self, log_directory: Optional[Path] = None):
        self.log_directory = log_directory or self._get_default_log_directory()
        self.log_directory.mkdir(parents=True, exist_ok=True)
        
        # Setup error-specific logger
        self.logger = self._setup_error_logger()
        
        # Track active operations for recovery
        self.active_operations = {}
        self.operation_lock = threading.Lock()
        
        # Error recovery strategies
        self.recovery_strategies = {
            'file_not_found': self._recover_file_not_found,
            'permission_denied': self._recover_permission_denied,
            'disk_full': self._recover_disk_full,
            'network_error': self._recover_network_error,
            'timeout': self._recover_timeout,
        }
        
        # Error queue for background processing
        self.error_queue = queue.Queue()
        self._running = True
        self.error_processor = threading.Thread(target=self._process_error_queue, daemon=True)
        self.error_processor.start()
        
        # Register cleanup function
        atexit.register(self.cleanup)
        
        # Initialize Sentry if configured
        self._init_sentry()
    
    def _init_sentry(self):
        """Initialize Sentry SDK if DSN is available."""
        if not SENTRY_AVAILABLE: return
        
        dsn = os.environ.get("SENTRY_DSN")
        if dsn:
            sentry_sdk.init(
                dsn=dsn,
                traces_sample_rate=1.0,
                profiles_sample_rate=1.0,
            )
            logging.info("Sentry initialized successfully.")
        else:
            logging.debug("Sentry DSN not found. Crash reporting disabled.")
    
    def _get_default_log_directory(self) -> Path:
        """Get the default log directory."""
        try:
            if sys.platform == "win32":
                return Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX" / "logs"
            else:
                return Path.home() / ".ut_vfx" / "logs"
        except Exception:
            return Path.cwd() / "logs"
    
    def _setup_error_logger(self) -> logging.Logger:
        """Setup the error-specific logger."""
        logger = logging.getLogger('ut_vfx_errors')
        logger.setLevel(logging.DEBUG)
        
        # Create file handler for errors
        error_log_file = self.log_directory / f"errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(error_log_file, encoding='utf-8')
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(name)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        logger.addHandler(file_handler)
        
        logging.info(f"Error handler initialized. Log file: {error_log_file}")
        return logger
    
    def handle_error(self, error: Exception, context: str = "", 
                     severity: str = "error", 
                     recovery_suggestion: Optional[str] = None,
                     user_friendly_message: Optional[str] = None) -> Dict[str, Any]:
        """
        Handle an error comprehensively.
        
        Args:
            error: The exception that occurred
            context: Context information about where the error occurred
            severity: Severity level ('debug', 'info', 'warning', 'error', 'critical')
            recovery_suggestion: Suggested recovery steps
            user_friendly_message: User-friendly error message
            
        Returns:
            Dictionary with error information and recovery options
        """
        error_id = f"ERR_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(error)}"
        
        # Log the error with full details
        error_details = {
            'error_id': error_id,
            'timestamp': datetime.now().isoformat(),
            'context': context,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'severity': severity,
            'recovery_suggestion': recovery_suggestion,
            'python_version': sys.version,
            'platform': sys.platform,
            'user_friendly_message': user_friendly_message or str(error)
        }
        
        # Log to file
        self.logger.log(
            getattr(logging, severity.upper(), logging.ERROR),
            f"Error ID: {error_id}\nContext: {context}\nError: {str(error)}\nTraceback:\n{traceback.format_exc()}"
        )
        
        # Send to Sentry
        if SENTRY_AVAILABLE and severity in ['error', 'critical']:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("context", context)
                scope.set_level(severity)
                scope.set_extra("error_id", error_id)
                sentry_sdk.capture_exception(error)
        
        # Log to general application logger
        logging.log(
            getattr(logging, severity.upper(), logging.ERROR),
            f"Error ID: {error_id} - {context}: {str(error)}"
        )
        
        # Put error in queue for background processing
        self.error_queue.put(error_details)
        
        # Create recovery options based on error type
        recovery_options = self._analyze_recovery_options(error, context)
        
        result = {
            'error_id': error_id,
            'handled': True,
            'severity': severity,
            'message': user_friendly_message or str(error),
            'context': context,
            'recovery_options': recovery_options,
            'recovery_suggestion': recovery_suggestion,
            'details': error_details
        }
        
        return result
    
    def _analyze_recovery_options(self, error: Exception, context: str) -> List[Dict[str, str]]:
        """Analyze error and suggest recovery options."""
        error_type = type(error).__name__.lower()
        context_lower = context.lower()
        
        recovery_options = []
        
        # Analyze based on error type
        if 'file' in error_type or 'path' in error_type or 'directory' in error_type:
            recovery_options.extend([
                {"action": "retry", "label": "Retry Operation"},
                {"action": "skip", "label": "Skip Item"},
                {"action": "change_path", "label": "Change Directory"},
                {"action": "check_permissions", "label": "Check Permissions"}
            ])
        elif 'permission' in error_type.lower() or 'access' in error_type.lower():
            recovery_options.extend([
                {"action": "run_as_admin", "label": "Run as Administrator"},
                {"action": "change_path", "label": "Change Directory"},
                {"action": "continue", "label": "Continue Other Operations"}
            ])
        elif 'disk' in error_type.lower() or 'space' in error_type.lower() or 'quota' in error_type.lower():
            recovery_options.extend([
                {"action": "cleanup", "label": "Cleanup Temporary Files"},
                {"action": "change_location", "label": "Change Storage Location"},
                {"action": "reduce_size", "label": "Reduce Operation Size"}
            ])
        elif 'network' in error_type.lower() or 'connection' in error_type.lower():
            recovery_options.extend([
                {"action": "retry", "label": "Retry Connection"},
                {"action": "use_cached", "label": "Use Cached Data"},
                {"action": "offline_mode", "label": "Work Offline"}
            ])
        elif 'timeout' in error_type.lower():
            recovery_options.extend([
                {"action": "increase_timeout", "label": "Increase Timeout"},
                {"action": "retry", "label": "Retry Operation"},
                {"action": "process_smaller", "label": "Process in Smaller Batches"}
            ])
        elif 'excel' in context_lower or 'spreadsheet' in context_lower:
            recovery_options.extend([
                {"action": "export_csv", "label": "Export as CSV Instead"},
                {"action": "validate_format", "label": "Validate File Format"}
            ])
        else:
            # Generic recovery options
            recovery_options.extend([
                {"action": "retry", "label": "Retry Operation"},
                {"action": "continue", "label": "Continue Other Operations"},
                {"action": "report_issue", "label": "Report Issue"},
                {"action": "save_work", "label": "Save Current Work"}
            ])
        
        # Add context-specific options
        if 'folder' in context_lower or 'directory' in context_lower:
            recovery_options.extend([
                {"action": "create_parent", "label": "Create Parent Directories"}
            ])
        
        if 'scan' in context_lower or 'move' in context_lower:
            recovery_options.extend([
                {"action": "verify_integrity", "label": "Verify File Integrity"},
                {"action": "use_hard_links", "label": "Use Hard Links Instead"}
            ])
        
        return recovery_options
    
    def _recover_file_not_found(self, error_info: Dict[str, Any]) -> bool:
        """Recovery strategy for file not found errors."""
        # This would implement specific recovery logic
        return False
    
    def _recover_permission_denied(self, error_info: Dict[str, Any]) -> bool:
        """Recovery strategy for permission denied errors."""
        # This would implement specific recovery logic
        return False
    
    def _recover_disk_full(self, error_info: Dict[str, Any]) -> bool:
        """Recovery strategy for disk full errors."""
        # This would implement specific recovery logic
        return False
    
    def _recover_network_error(self, error_info: Dict[str, Any]) -> bool:
        """Recovery strategy for network errors."""
        # This would implement specific recovery logic
        return False
    
    def _recover_timeout(self, error_info: Dict[str, Any]) -> bool:
        """Recovery strategy for timeout errors."""
        # This would implement specific recovery logic
        return False
    
    def register_operation(self, operation_id: str, operation_details: Dict[str, Any]):
        """Register an active operation for potential recovery."""
        with self.operation_lock:
            self.active_operations[operation_id] = {
                'start_time': datetime.now(),
                'details': operation_details,
                'status': 'active'
            }
    
    def update_operation_status(self, operation_id: str, status: str, 
                               additional_info: Optional[Dict[str, Any]] = None):
        """Update the status of an active operation."""
        with self.operation_lock:
            if operation_id in self.active_operations:
                self.active_operations[operation_id]['status'] = status
                self.active_operations[operation_id]['last_updated'] = datetime.now()
                if additional_info:
                    self.active_operations[operation_id].update(additional_info)
    
    def get_active_operations(self) -> Dict[str, Any]:
        """Get information about all active operations."""
        with self.operation_lock:
            return self.active_operations.copy()
    
    def cleanup_operation(self, operation_id: str):
        """Remove an operation from active tracking."""
        with self.operation_lock:
            if operation_id in self.active_operations:
                del self.active_operations[operation_id]
    
    def _process_error_queue(self):
        """Process errors from the queue in background."""
        while self._running:
            try:
                error_details = self.error_queue.get(timeout=1.0)
                if error_details is None:
                    break
                
                # Process the error (could involve sending to server, etc.)
                # For now, just log it
                self.logger.debug(f"Processed error in background: {error_details['error_id']}")
                
                self.error_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error processing error queue: {e}")
    
    def save_error_report(self, error_info: Dict[str, Any], filename: Optional[str] = None) -> Path:
        """Save detailed error report to file."""
        if filename is None:
            filename = f"error_report_{error_info['error_id']}.json"
        
        report_path = self.log_directory / filename
        
        # Create a more user-friendly report
        report_data = {
            'error_summary': {
                'error_id': error_info['error_id'],
                'timestamp': error_info['details']['timestamp'],
                'context': error_info['context'],
                'error_type': error_info['details']['error_type'],
                'message': error_info['message'],
                'severity': error_info['severity']
            },
            'recovery_options': error_info['recovery_options'],
            'recovery_suggestion': error_info['recovery_suggestion'],
            'user_friendly_message': error_info['details']['user_friendly_message'],
            'system_info': {
                'python_version': error_info['details']['python_version'],
                'platform': error_info['details']['platform']
            }
        }
        
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        return report_path
    
    def get_error_reports(self) -> List[Path]:
        """Get list of all error report files."""
        return list(self.log_directory.glob("error_report_*.json"))
    
    def cleanup(self):
        """Cleanup resources."""
        self._running = False
        try:
            self.error_queue.put_nowait(None)
        except Exception:
            pass
        # Wait for error processor to finish
        try:
            self.error_processor.join(timeout=1.0)
        except RuntimeError as exc:
            self.logger.debug("Error processor join skipped during shutdown: %s", exc)
    
    @contextmanager
    def error_context(self, context: str, recovery_suggestion: Optional[str] = None,
                      user_friendly_message: Optional[str] = None):
        """Context manager for handling errors in a specific context."""
        try:
            yield
        except Exception as e:
            self.handle_error(
                e, 
                context, 
                recovery_suggestion=recovery_suggestion,
                user_friendly_message=user_friendly_message
            )
            # Re-raise the exception after logging
            raise e from None  # Keep original exception chain


# Global error handler instance
error_handler = ErrorHandler()


def safe_execute(func: Callable, *args, context: str = "", 
                recovery_suggestion: Optional[str] = None,
                user_friendly_message: Optional[str] = None, **kwargs) -> tuple[bool, Any, Optional[Dict[str, Any]]]:
    """
    Safely execute a function with error handling.
    
    Args:
        func: Function to execute
        *args: Arguments to pass to function
        context: Context information for error reporting
        recovery_suggestion: Suggested recovery steps
        user_friendly_message: User-friendly error message
        **kwargs: Keyword arguments to pass to function
        
    Returns:
        Tuple of (success, result, error_info)
    """
    try:
        result = func(*args, **kwargs)
        return True, result, None
    except Exception as e:
        error_info = error_handler.handle_error(
            e, 
            context, 
            recovery_suggestion=recovery_suggestion,
            user_friendly_message=user_friendly_message
        )
        return False, None, error_info


def retry_on_failure(max_retries: int = 3, delay: float = 1.0, 
                     backoff: float = 2.0, 
                     context: str = "",
                     recovery_suggestion: Optional[str] = None):
    """
    Decorator to retry a function on failure.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries
        backoff: Multiplier for delay after each retry
        context: Context information for error reporting
        recovery_suggestion: Suggested recovery steps
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries + 1):  # Include initial attempt
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:  # Don't sleep on the last attempt
                        error_handler.logger.warning(
                            f"Attempt {attempt + 1} failed for {func.__name__}: {str(e)}. "
                            f"Retrying in {current_delay}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        # All retries exhausted, log the final error
                        error_handler.handle_error(
                            e, 
                            f"{context} (after {max_retries} retries)", 
                            recovery_suggestion=recovery_suggestion
                        )
                        raise e from None  # Re-raise with original chain
            
            # This should never be reached, but just in case
            raise last_exception from None
        return wrapper
    return decorator


def log_exceptions(context: str = "", level: str = "error",
                  recovery_suggestion: Optional[str] = None,
                  user_friendly_message: Optional[str] = None):
    """
    Decorator to automatically log exceptions.
    
    Args:
        context: Context information for error reporting
        level: Logging level ('debug', 'info', 'warning', 'error', 'critical')
        recovery_suggestion: Suggested recovery steps
        user_friendly_message: User-friendly error message
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_handler.handle_error(
                    e, 
                    context, 
                    severity=level,
                    recovery_suggestion=recovery_suggestion,
                    user_friendly_message=user_friendly_message
                )
                raise e from None  # Re-raise with original chain
        return wrapper
    return decorator


def suppress_errors(default_return=None, log_error: bool = True, 
                   context: str = "",
                   recovery_suggestion: Optional[str] = None,
                   user_friendly_message: Optional[str] = None):
    """
    Decorator to suppress errors and return a default value.
    
    Args:
        default_return: Value to return if an error occurs
        log_error: Whether to log the error
        context: Context information for error reporting
        recovery_suggestion: Suggested recovery steps
        user_friendly_message: User-friendly error message
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_error:
                    error_handler.handle_error(
                        e, 
                        context, 
                        recovery_suggestion=recovery_suggestion,
                        user_friendly_message=user_friendly_message
                    )
                return default_return
        return wrapper
    return decorator


# Exception classes
class SecurityError(Exception):
    """Security-related exception."""
    pass


class ValidationError(Exception):
    """Validation-related exception."""
    pass


class OperationError(Exception):
    """Operation-related exception."""
    pass


from .security import SecurityValidator

# Utility functions for common operations
def validate_file_path(user_input: str, base_directory: Path) -> tuple[bool, Optional[Path], str]:
    """
    Securely validate and sanitize file paths.
    Returns (is_valid, safe_path, error_message)
    """
    return SecurityValidator.validate_file_path(user_input, base_directory)


def sanitize_filename(filename: str) -> tuple[bool, str, str]:
    """
    Sanitize filename for safe filesystem use.
    Returns (is_valid, sanitized_name, error_message)
    """
    return SecurityValidator.sanitize_filename(filename)


def validate_directory_path(directory_path: Path, must_exist: bool = True) -> tuple[bool, str]:
    """
    Validate directory path for security.
    Returns (is_valid, error_message)
    """
    return SecurityValidator.validate_directory_path(directory_path, must_exist)
