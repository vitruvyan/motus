"""
Tests for Axis Recovery Layer.

Phase 2.3 - Week 3-4
Comprehensive test suite for retry, circuit breaker, and timeout patterns.
"""

import pytest
from axis.state import GraphState
from axis.events import EventType
from axis.recovery.retry import retry
from axis.recovery.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from axis.recovery.timeout import timeout_threading
import time


@pytest.fixture
def sample_state():
    return GraphState(
        trace_id="test-recovery",
        intent=None,
        facts=(),
        decisions=(),
        rejections=(),
        events=(),
    )


class FailingNode:
    """Node that fails N times then succeeds."""

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.attempts = 0

    def __call__(self, state: GraphState) -> GraphState:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise ValueError(f"Attempt {self.attempts} failed")
        return state


class SlowNode:
    """Node that takes N seconds to execute."""

    def __init__(self, duration: float):
        self.duration = duration

    def __call__(self, state: GraphState) -> GraphState:
        time.sleep(self.duration)
        return state


# Retry tests

def test_retry_success_on_third_attempt(sample_state):
    """Test retry succeeds on 3rd attempt."""
    node = FailingNode(fail_count=2)
    decorated = retry(max_attempts=3)(node)

    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"
    assert node.attempts == 3


def test_retry_exhausts_attempts(sample_state):
    """Test retry raises after max attempts."""
    node = FailingNode(fail_count=10)
    decorated = retry(max_attempts=3)(node)

    with pytest.raises(ValueError):
        decorated(sample_state)

    assert node.attempts == 3


def test_retry_with_custom_exceptions(sample_state):
    """Test retry only catches specified exceptions."""
    def node_with_type_error(state):
        raise TypeError("Wrong type")

    decorated = retry(max_attempts=3, exceptions=(ValueError,))(node_with_type_error)

    # TypeError not caught, raised immediately
    with pytest.raises(TypeError):
        decorated(sample_state)


def test_retry_with_jitter(sample_state):
    """Test retry(jitter=True) prevents thundering herd. jitter/max_delay
    are now parameters of retry() itself — retry_with_jitter is gone."""
    node = FailingNode(fail_count=2)
    decorated = retry(max_attempts=3, initial_delay=0.1, jitter=True)(node)

    start_time = time.time()
    result = decorated(sample_state)
    elapsed = time.time() - start_time

    assert result.trace_id == "test-recovery"
    assert node.attempts == 3
    # Should take at least 0.1 seconds with backoff (jitter may reduce it slightly)
    assert elapsed >= 0.05


def test_retry_max_delay_caps_backoff(sample_state):
    """Test max_delay caps exponential backoff."""
    node = FailingNode(fail_count=2)
    decorated = retry(
        max_attempts=3, initial_delay=0.05, backoff_factor=10.0, max_delay=0.1
    )(node)

    start_time = time.time()
    result = decorated(sample_state)
    elapsed = time.time() - start_time

    assert result.trace_id == "test-recovery"
    # Uncapped backoff would be 0.05 + 0.5 = 0.55s; capped it's 0.05 + 0.1.
    assert elapsed < 0.4


def test_retry_rejects_max_attempts_below_one():
    """Test retry(max_attempts=0) raises at decoration time, not with the
    old baffling 'exceptions must derive from BaseException'."""
    with pytest.raises(ValueError):
        retry(max_attempts=0)


def test_retry_records_node_retried_events(sample_state):
    """Test each retry appends a NODE_RETRIED event to the state passed
    into the next attempt, so retries are visible in the trace — not just
    to on_retry. FailingNode hands back whatever state it was called with,
    so a successful final attempt carries the two prior retries with it."""
    node = FailingNode(fail_count=2)
    decorated = retry(max_attempts=3, initial_delay=0.01)(node)

    result = decorated(sample_state)

    retried = [e for e in result.events if e.event_type == EventType.NODE_RETRIED]
    assert len(retried) == 2
    assert all(e.node_name == "FailingNode" for e in retried)
    assert [e.metadata["attempt"] for e in retried] == [1, 2]
    assert all(e.metadata["error_type"] == "ValueError" for e in retried)


# Circuit breaker tests

def test_circuit_breaker_opens_after_failures(sample_state):
    """Test circuit opens after threshold failures."""
    breaker = CircuitBreaker(failure_threshold=3, timeout=1.0)
    node = FailingNode(fail_count=10)
    decorated = breaker(node)

    # First 3 attempts fail, circuit opens
    for _ in range(3):
        with pytest.raises(ValueError):
            decorated(sample_state)

    assert breaker.is_open

    # 4th attempt rejected by circuit
    with pytest.raises(CircuitBreakerOpenError):
        decorated(sample_state)


def test_circuit_breaker_half_open_recovery(sample_state):
    """Test circuit transitions to half-open after timeout."""
    breaker = CircuitBreaker(
        failure_threshold=2,
        success_threshold=2,
        timeout=0.5,
    )
    node = FailingNode(fail_count=2)
    decorated = breaker(node)

    # Fail twice, circuit opens
    for _ in range(2):
        with pytest.raises(ValueError):
            decorated(sample_state)

    assert breaker.is_open

    # Wait for timeout
    time.sleep(0.6)

    # Next attempts succeed (node no longer failing)
    decorated(sample_state)
    assert breaker.is_half_open

    decorated(sample_state)
    assert breaker.is_closed


def test_circuit_breaker_manual_reset(sample_state):
    """Test manual reset closes circuit."""
    breaker = CircuitBreaker(failure_threshold=1)
    node = FailingNode(fail_count=10)
    decorated = breaker(node)

    with pytest.raises(ValueError):
        decorated(sample_state)

    assert breaker.is_open

    breaker.reset()
    assert breaker.is_closed


def test_circuit_breaker_success_resets_failure_count(sample_state):
    """Test successful calls reset failure count."""
    breaker = CircuitBreaker(failure_threshold=3)
    node = FailingNode(fail_count=1)  # Fail once, then succeed
    decorated = breaker(node)

    # First call fails
    with pytest.raises(ValueError):
        decorated(sample_state)

    # Second call succeeds
    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"

    # Failure count should be reset
    assert breaker.failure_count == 0


# Timeout tests

def test_timeout_raises_on_slow_node(sample_state):
    """Test timeout raises TimeoutError."""
    from axis.recovery.timeout import TimeoutError

    node = SlowNode(duration=2.0)
    decorated = timeout_threading(0.5)(node)

    with pytest.raises(TimeoutError):
        decorated(sample_state)


def test_timeout_allows_fast_node(sample_state):
    """Test timeout allows fast execution."""
    node = SlowNode(duration=0.1)
    decorated = timeout_threading(1.0)(node)

    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"


def test_timeout_preserves_exceptions(sample_state):
    """Test timeout preserves original exceptions."""
    def failing_node(state):
        raise RuntimeError("Original error")

    decorated = timeout_threading(1.0)(failing_node)

    with pytest.raises(RuntimeError, match="Original error"):
        decorated(sample_state)


# Integration tests

def test_retry_with_circuit_breaker(sample_state):
    """Test combining retry and circuit breaker."""
    breaker = CircuitBreaker(failure_threshold=3)
    node = FailingNode(fail_count=2)

    decorated = retry(max_attempts=3)(breaker(node))

    # Should succeed after 3 attempts total
    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"


def test_retry_with_timeout(sample_state):
    """Test combining retry and timeout."""
    node = FailingNode(fail_count=1)  # Fail once, succeed on retry
    decorated = retry(max_attempts=3)(timeout_threading(1.0)(node))

    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"
    assert node.attempts == 2  # One failure, one success


def test_circuit_breaker_with_timeout(sample_state):
    """Test combining circuit breaker and timeout."""
    breaker = CircuitBreaker(failure_threshold=2)
    node = SlowNode(duration=2.0)  # Always slow
    decorated = breaker(timeout_threading(0.5)(node))

    # First attempt times out
    with pytest.raises(Exception):  # TimeoutError from timeout_threading
        decorated(sample_state)

    # Second attempt times out, circuit opens
    with pytest.raises(Exception):
        decorated(sample_state)

    assert breaker.is_open

    # Third attempt rejected by circuit
    with pytest.raises(CircuitBreakerOpenError):
        decorated(sample_state)


def test_all_three_combined(sample_state):
    """Test combining retry, circuit breaker, and timeout."""
    breaker = CircuitBreaker(failure_threshold=5)
    node = FailingNode(fail_count=1)  # Fail once, then succeed
    decorated = retry(max_attempts=3)(breaker(timeout_threading(1.0)(node)))

    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"