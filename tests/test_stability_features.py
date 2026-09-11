"""
Test Suite for Circuit Breaker and Retry Strategy

Tests the stability enhancements for network operations.
"""

import pytest
import time
from unittest.mock import Mock, patch
from ut_vfx.core.infra.circuit_breaker import CircuitBreaker, CircuitState, CircuitBreakerError
from ut_vfx.core.infra.retry_strategy import RetryStrategy


class TestCircuitBreaker:
    """Test circuit breaker functionality"""
    
    def test_circuit_breaker_starts_closed(self):
        """Circuit breaker should start in CLOSED state"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=10)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0
    
    def test_circuit_breaker_opens_after_threshold(self):
        """Circuit breaker should open after failure threshold"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=10)
        
        # Simulate 3 failures
        for i in range(3):
            try:
                breaker.call(lambda: self._failing_function())
            except RuntimeError:
                pass  # Expected
        
        assert breaker.state == CircuitState.OPEN
    
    def test_circuit_breaker_rejects_when_open(self):
        """Circuit breaker should reject calls when OPEN"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=10)
        
        # Trigger failures to open circuit
        for _ in range(2):
            try:
                breaker.call(lambda: self._failing_function())
            except RuntimeError:
                pass
        
        # Next call should raise CircuitBreakerError
        with pytest.raises(CircuitBreakerError):
            breaker.call(lambda: "success")
    
    def test_circuit_breaker_half_open_after_timeout(self):
        """Circuit breaker should enter HALF_OPEN after timeout"""
        breaker = CircuitBreaker(failure_threshold=2, timeout=0.1)  # Short timeout for testing
        
        # Open the circuit
        for _ in range(2):
            try:
                breaker.call(lambda: self._failing_function())
            except RuntimeError:
                pass
        
        assert breaker.state == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(0.15)
        
        # Next call should transition to HALF_OPEN
        try:
            breaker.call(lambda: "success")
        except:
            pass
        
        # Should now be CLOSED after successful call
        assert breaker.state == CircuitState.CLOSED
    
    def test_circuit_breaker_success_resets_count(self):
        """Successful calls should reset failure count"""
        breaker = CircuitBreaker(failure_threshold=3, timeout=10)
        
        # One failure
        try:
            breaker.call(lambda: self._failing_function())
        except RuntimeError:
            pass
        
        assert breaker.failure_count == 1
        
        # Successful call
        breaker.call(lambda: "success")
        
        assert breaker.failure_count == 0
        assert breaker.state == CircuitState.CLOSED
    
    def test_circuit_breaker_stats(self):
        """Circuit breaker should provide statistics"""
        breaker = CircuitBreaker(failure_threshold=5, timeout=60, name="TestBreaker")
        
        stats = breaker.get_stats()
        
        assert stats["name"] == "TestBreaker"
        assert stats["state"] == "closed"
        assert stats["failure_threshold"] == 5
        assert stats["timeout"] == 60
    
    @staticmethod
    def _failing_function():
        """Helper function that always fails"""
        raise RuntimeError("Simulated failure")


class TestRetryStrategy:
    """Test retry strategy functionality"""
    
    def test_retry_succeeds_first_attempt(self):
        """Function succeeding on first attempt should not retry"""
        strategy = RetryStrategy(max_attempts=3, base_delay=0.1)
        
        mock_func = Mock(return_value="success")
        result = strategy.execute(mock_func)
        
        assert result == "success"
        assert mock_func.call_count == 1
    
    def test_retry_succeeds_after_failures(self):
        """Function should retry and eventually succeed"""
        strategy = RetryStrategy(max_attempts=3, base_delay=0.1)
        
        # Fail twice, then succeed
        mock_func = Mock(side_effect=[RuntimeError("fail"), RuntimeError("fail"), "success"])
        result = strategy.execute(mock_func)
        
        assert result == "success"
        assert mock_func.call_count == 3
    
    def test_retry_exhausts_attempts(self):
        """Function should fail after max attempts"""
        strategy = RetryStrategy(max_attempts=3, base_delay=0.1, exceptions=(RuntimeError,))
        
        mock_func = Mock(side_effect=RuntimeError("persistent failure"))
        
        with pytest.raises(RuntimeError, match="persistent failure"):
            strategy.execute(mock_func)
        
        assert mock_func.call_count == 3
    
    def test_retry_exponential_backoff(self):
        """Retry delays should increase exponentially"""
        strategy = RetryStrategy(
            max_attempts=3,
            base_delay=1.0,
            exponential_base=2.0,
            jitter=False  # Disable jitter for predictable test
        )
        
        delays = []
        for attempt in range(1, 3):  # Attempts 1-2 (3rd attempt doesn't get delay)
            delay = strategy._calculate_delay(attempt)
            delays.append(delay)
        
        # First retry: 1.0 * (2 ** 0) = 1.0
        # Second retry: 1.0 * (2 ** 1) = 2.0
        assert delays[0] == 1.0
        assert delays[1] == 2.0
    
    def test_retry_respects_max_delay(self):
        """Retry delay should be capped at max_delay"""
        strategy = RetryStrategy(
            max_attempts=5,
            base_delay=1.0,
            max_delay=5.0,
            exponential_base=2.0,
            jitter=False
        )
        
        # Attempt 4: 1.0 * (2 ** 3) = 8.0, but capped at 5.0
        delay = strategy._calculate_delay(4)
        assert delay == 5.0
    
    def test_retry_with_jitter(self):
        """Jitter should add randomness to delays"""
        strategy = RetryStrategy(
            max_attempts=3,
            base_delay=1.0,
            jitter=True
        )
        
        delays = set()
        for _ in range(10):
            delay = strategy._calculate_delay(1)
            delays.add(delay)
        
        # With jitter, delays should vary
        assert len(delays) > 1


class TestPostgresManagerIntegration:
    """Integration tests for PostgresManager with circuit breaker"""
    
    @patch('ut_vfx.core.infra.postgres_manager.pool.ThreadedConnectionPool')
    def test_execute_query_with_circuit_breaker(self, mock_pool):
        """execute_query should use circuit breaker"""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        # This test would require mocking the entire database connection
        # For now, just verify the circuit breaker exists
        assert hasattr(PostgresManager, '_circuit_breaker')
        assert hasattr(PostgresManager, '_retry_strategy')
    
    def test_circuit_breaker_configuration(self):
        """PostgresManager circuit breaker should be properly configured"""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        breaker = PostgresManager._circuit_breaker
        
        assert breaker.name == "PostgresCircuitBreaker"
        assert breaker.failure_threshold == 5
        assert breaker.timeout in (60, 120)
    
    def test_retry_strategy_configuration(self):
        """PostgresManager retry strategy should be properly configured"""
        from ut_vfx.core.infra.postgres_manager import PostgresManager
        
        retry = PostgresManager._retry_strategy
        
        assert retry.name == "PostgresRetry"
        assert retry.max_attempts in (3, 5)
        assert retry.base_delay == 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
