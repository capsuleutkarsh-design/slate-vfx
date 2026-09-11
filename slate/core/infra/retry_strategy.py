"""
Enhanced Retry Strategy with Exponential Backoff and Jitter

Provides intelligent retry logic for transient failures with configurable strategies.
"""

import time
import random
import logging
from typing import Callable, Any, Type, Tuple
from functools import wraps


class RetryStrategy:
    """
    Configurable retry strategy with exponential backoff and jitter.
    
    Usage:
        strategy = RetryStrategy(max_attempts=3, base_delay=1.0)
        
        @strategy.retry
        def unreliable_operation():
            # ... network call ...
            pass
    """
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        exceptions: Tuple[Type[Exception], ...] = (Exception,),
        name: str = "RetryStrategy"
    ):
        """
        Initialize retry strategy.
        
        Args:
            max_attempts: Maximum number of retry attempts
            base_delay: Initial delay in seconds
            max_delay: Maximum delay between retries
            exponential_base: Multiplier for exponential backoff
            jitter: Add random jitter to prevent thundering herd
            exceptions: Tuple of exceptions to retry on
            name: Name for logging purposes
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.exceptions = exceptions
        self.name = name
        
        logging.debug(
            f"Retry strategy '{name}' initialized "
            f"(attempts={max_attempts}, base_delay={base_delay}s, jitter={jitter})"
        )
    
    def retry(self, func: Callable) -> Callable:
        """Decorate a function with retry logic"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.execute(func, *args, **kwargs)
        return wrapper
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with retry logic.
        
        Raises:
            The last exception if all retries fail
        """
        last_exception = None
        
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = func(*args, **kwargs)
                
                if attempt > 1:
                    logging.info(
                        f"'{self.name}' succeeded on attempt {attempt}/{self.max_attempts}"
                    )
                
                return result
                
            except self.exceptions as e:
                last_exception = e
                
                if attempt < self.max_attempts:
                    delay = self._calculate_delay(attempt)
                    
                    logging.warning(
                        f"'{self.name}' attempt {attempt}/{self.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    time.sleep(delay)
                else:
                    logging.error(
                        f"'{self.name}' failed after {self.max_attempts} attempts: {e}"
                    )
        
        # All retries exhausted
        raise last_exception
    
    def _calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for given attempt using exponential backoff.
        
        Args:
            attempt: Current attempt number (1-indexed)
            
        Returns:
            Delay in seconds
        """
        # Exponential backoff: base_delay * (exponential_base ^ (attempt - 1))
        delay = self.base_delay * (self.exponential_base ** (attempt - 1))
        
        # Cap at max_delay
        delay = min(delay, self.max_delay)
        
        # Add jitter to prevent thundering herd
        if self.jitter:
            # Random jitter: ±25% of calculated delay
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
            delay = max(0, delay)  # Ensure non-negative
        
        return delay


# Pre-configured retry strategies for common scenarios

database_retry = RetryStrategy(
    max_attempts=3,
    base_delay=1.0,
    max_delay=10.0,
    exponential_base=2.0,
    jitter=True,
    exceptions=(ConnectionError, TimeoutError, OSError),
    name="DatabaseRetry"
)

network_retry = RetryStrategy(
    max_attempts=3,
    base_delay=0.5,
    max_delay=5.0,
    exponential_base=2.0,
    jitter=True,
    exceptions=(ConnectionError, TimeoutError, OSError),
    name="NetworkRetry"
)

file_operation_retry = RetryStrategy(
    max_attempts=5,
    base_delay=0.1,
    max_delay=2.0,
    exponential_base=2.0,
    jitter=True,
    exceptions=(OSError, IOError, PermissionError),
    name="FileOperationRetry"
)


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
) -> Callable:
    """
    Convenience decorator for quick retry setup.
    
    Usage:
        @retry_with_backoff(max_attempts=5, base_delay=2.0)
        def flaky_function():
            # ... code ...
            pass
    """
    strategy = RetryStrategy(
        max_attempts=max_attempts,
        base_delay=base_delay,
        exceptions=exceptions
    )
    return strategy.retry
