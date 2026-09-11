"""
Circuit Breaker Pattern Implementation for Network Operations

Provides fault tolerance by preventing repeated calls to failing services.
Three states: CLOSED (normal), OPEN (failing), HALF_OPEN (testing recovery).
"""

import time
import logging
import threading
from enum import Enum
from typing import Callable, Any, Optional
from functools import wraps


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Service failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading failures.
    
    Usage:
        breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        
        @breaker
        def risky_operation():
            # ... network call ...
            pass
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exceptions: tuple = (Exception,),
        name: str = "CircuitBreaker"
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            timeout: Seconds to wait before trying again (OPEN -> HALF_OPEN)
            expected_exceptions: Exceptions that count as failures
            name: Name for logging purposes
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exceptions = expected_exceptions
        self.name = name
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()
        
        logging.info(f"Circuit breaker '{name}' initialized (threshold={failure_threshold}, timeout={timeout}s)")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state
    
    @property
    def failure_count(self) -> int:
        """Get current failure count"""
        return self._failure_count
    
    def __call__(self, func: Callable) -> Callable:
        """Decorate a function with circuit breaker protection"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Raises:
            CircuitBreakerError: If circuit is open
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    logging.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state")
                else:
                    logging.warning(f"Circuit breaker '{self.name}' is OPEN, rejecting call")
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Service unavailable. Retry after {self._time_until_retry():.1f}s"
                    )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
            
        except self.expected_exceptions:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self._last_failure_time is None:
            return False
        return (time.time() - self._last_failure_time) >= self.timeout
    
    def _time_until_retry(self) -> float:
        """Get seconds until next retry attempt"""
        if self._last_failure_time is None:
            return 0.0
        elapsed = time.time() - self._last_failure_time
        return max(0, self.timeout - elapsed)
    
    def _on_success(self):
        """Handle successful call"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logging.info(f"Circuit breaker '{self.name}' recovered, closing circuit")
            
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._last_failure_time = None
    
    def _on_failure(self):
        """Handle failed call"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # Failed during recovery test, go back to OPEN
                self._state = CircuitState.OPEN
                logging.warning(f"Circuit breaker '{self.name}' failed during recovery, reopening circuit")
                
            elif self._failure_count >= self.failure_threshold:
                # Too many failures, open circuit
                self._state = CircuitState.OPEN
                logging.error(
                    f"Circuit breaker '{self.name}' opened after {self._failure_count} failures "
                    f"(threshold={self.failure_threshold})"
                )
    
    def reset(self):
        """Manually reset circuit breaker to CLOSED state"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            logging.info(f"Circuit breaker '{self.name}' manually reset")
    
    def get_stats(self) -> dict:
        """Get circuit breaker statistics"""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "timeout": self.timeout,
            "last_failure_time": self._last_failure_time,
            "time_until_retry": self._time_until_retry() if self._state == CircuitState.OPEN else None
        }


# Pre-configured circuit breakers for common use cases
database_breaker = CircuitBreaker(
    failure_threshold=5,
    timeout=60,
    expected_exceptions=(Exception,),  # Catch all DB exceptions
    name="DatabaseCircuitBreaker"
)

network_breaker = CircuitBreaker(
    failure_threshold=3,
    timeout=30,
    expected_exceptions=(ConnectionError, TimeoutError, OSError),
    name="NetworkCircuitBreaker"
)
