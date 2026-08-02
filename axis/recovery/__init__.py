"""
Axis Recovery Layer - Error resilience patterns.

Provides:
- retry: Exponential backoff retry decorator
- CircuitBreaker: Circuit breaker pattern for fault tolerance
- timeout: Timeout decorator for long-running operations

Example:
    from axis.recovery import retry, CircuitBreaker, timeout_threading

    @retry(max_attempts=3, jitter=True)
    @timeout_threading(5.0)
    def my_node(state: GraphState) -> GraphState:
        # Node implementation
        return state

    breaker = CircuitBreaker(failure_threshold=5)

    @breaker
    def another_node(state: GraphState) -> GraphState:
        return state
"""

from axis.recovery.retry import (
    retry,
    retry_on_http_error,
    retry_on_io_error,
)

from axis.recovery.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    circuit_breaker,
)

from axis.recovery.timeout import (
    timeout,
    timeout_threading,
    time_limit,
    TimeoutError,
)

__all__ = [
    # Retry
    "retry",
    "retry_on_http_error",
    "retry_on_io_error",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "circuit_breaker",
    # Timeout
    "timeout",
    "timeout_threading",
    "time_limit",
    "TimeoutError",
]
