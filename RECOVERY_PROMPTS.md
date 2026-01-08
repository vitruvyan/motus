# Axis Phase 2.3 Week 3-4 - Error Recovery Prompts

**Context:** You are implementing error recovery for Axis, a minimal cognitive graph kernel.  
**Architecture:** Immutable GraphState, frozen dataclasses, zero external dependencies.  
**Goal:** Enable resilient Node execution with retry, circuit breaker, and timeout patterns.

---

## PROMPT 1: Retry Decorator

**Task:** Implement exponential backoff retry decorator for Node execution.

**File to create:** `axis/recovery/retry.py` (~200 lines)

**Pattern:** Decorator that wraps Node callables to retry on failure with exponential backoff.

**Requirements:**

1. **Core retry function:**
```python
from functools import wraps
from typing import Callable, Type, Tuple, Optional
from axis.state import GraphState
from axis.node import Node
import time
import logging

logger = logging.getLogger(__name__)

def retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
) -> Callable[[Node], Node]:
    """
    Retry decorator with exponential backoff for Nodes.
    
    Args:
        max_attempts: Maximum number of attempts (default: 3)
        initial_delay: Initial delay in seconds (default: 1.0)
        backoff_factor: Multiplier for delay on each retry (default: 2.0)
        exceptions: Tuple of exception types to catch (default: all)
        on_retry: Optional callback(exception, attempt_number)
    
    Returns:
        Decorated Node that retries on failure
    
    Example:
        @retry(max_attempts=3, initial_delay=1.0)
        def my_node(state: GraphState) -> GraphState:
            # Node implementation
            return state
    """
    def decorator(node: Node) -> Node:
        @wraps(node)
        def wrapper(state: GraphState) -> GraphState:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return node(state)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        logger.error(
                            f"Node {node.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    
                    logger.warning(
                        f"Node {node.__name__} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {delay}s: {e}"
                    )
                    
                    if on_retry:
                        on_retry(e, attempt)
                    
                    time.sleep(delay)
                    delay *= backoff_factor
            
            # Should never reach here, but for type safety
            raise last_exception
        
        return wrapper
    return decorator
```

2. **Specialized retry decorators:**

```python
def retry_on_http_error(max_attempts: int = 3) -> Callable[[Node], Node]:
    """Retry only on HTTP-related errors."""
    import http.client
    return retry(
        max_attempts=max_attempts,
        exceptions=(http.client.HTTPException, ConnectionError, TimeoutError),
    )

def retry_on_io_error(max_attempts: int = 3) -> Callable[[Node], Node]:
    """Retry only on I/O errors."""
    return retry(
        max_attempts=max_attempts,
        exceptions=(IOError, OSError),
    )
```

3. **Retry with jitter (optional):**
```python
import random

def retry_with_jitter(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> Callable[[Node], Node]:
    """
    Retry with jitter to avoid thundering herd problem.
    
    Adds random jitter (0-50% of delay) to prevent simultaneous retries.
    """
    def decorator(node: Node) -> Node:
        @wraps(node)
        def wrapper(state: GraphState) -> GraphState:
            delay = initial_delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return node(state)
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    
                    actual_delay = delay
                    if jitter:
                        actual_delay = delay * (0.5 + 0.5 * random.random())
                    
                    actual_delay = min(actual_delay, max_delay)
                    
                    logger.warning(
                        f"Retry {attempt}/{max_attempts} after {actual_delay:.2f}s"
                    )
                    
                    time.sleep(actual_delay)
                    delay *= 2.0
            
            raise Exception("Max attempts reached")
        
        return wrapper
    return decorator
```

**Key principles:**
- Does NOT modify GraphState (immutability preserved)
- Logs all retry attempts
- Exponential backoff prevents overwhelming failed services
- Type hints for all functions
- Compatible with Node protocol (GraphState → GraphState)

**Acceptance Criteria:**
- [ ] retry() decorator works with any Node
- [ ] Exponential backoff: 1s, 2s, 4s (default)
- [ ] Logs warnings on retry, error on final failure
- [ ] Custom exceptions supported
- [ ] on_retry callback supported
- [ ] Specialized decorators (HTTP, I/O)
- [ ] Jitter support for distributed systems
- [ ] No external dependencies (stdlib only)

---

## PROMPT 2: Circuit Breaker Pattern

**Task:** Implement circuit breaker pattern for Node execution.

**File to create:** `axis/recovery/circuit_breaker.py` (~200 lines)

**Pattern:** Circuit breaker prevents cascading failures by stopping execution when error rate is high.

**States:**
- **CLOSED**: Normal operation, requests pass through
- **OPEN**: Too many failures, requests fail immediately
- **HALF_OPEN**: Testing if service recovered, limited requests pass

**Requirements:**

1. **CircuitBreaker class:**
```python
from enum import Enum
from typing import Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open" # Testing recovery

@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 2          # Successes to close from half-open
    timeout: float = 30.0              # Seconds before trying half-open
    exceptions: tuple = (Exception,)    # Exceptions to count as failures

class CircuitBreaker:
    """
    Circuit breaker for Node execution.
    
    Prevents cascading failures by:
    1. CLOSED: Allow all requests, count failures
    2. OPEN: Reject requests immediately after threshold failures
    3. HALF_OPEN: After timeout, allow limited requests to test recovery
    
    Example:
        breaker = CircuitBreaker(failure_threshold=5, timeout=30.0)
        
        @breaker
        def my_node(state: GraphState) -> GraphState:
            # Node implementation
            return state
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 30.0,
        exceptions: tuple = (Exception,),
        name: Optional[str] = None,
    ):
        self.config = CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout=timeout,
            exceptions=exceptions,
        )
        self.name = name or "CircuitBreaker"
        
        # State
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
    
    def __call__(self, node: Callable) -> Callable:
        """Decorator to apply circuit breaker to a Node."""
        @wraps(node)
        def wrapper(state):
            return self._call_with_breaker(node, state)
        return wrapper
    
    def _call_with_breaker(self, node: Callable, state):
        """Execute node with circuit breaker logic."""
        # Check if circuit should transition from OPEN to HALF_OPEN
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                logger.info(f"{self.name}: Attempting reset (HALF_OPEN)")
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
            else:
                raise CircuitBreakerOpenError(
                    f"{self.name}: Circuit is OPEN, rejecting request"
                )
        
        try:
            result = node(state)
            self._on_success()
            return result
        
        except self.config.exceptions as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        """Handle successful execution."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.info(
                f"{self.name}: Success in HALF_OPEN "
                f"({self.success_count}/{self.config.success_threshold})"
            )
            
            if self.success_count >= self.config.success_threshold:
                logger.info(f"{self.name}: Closing circuit")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
        
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed execution."""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.state == CircuitState.HALF_OPEN:
            logger.warning(f"{self.name}: Failure in HALF_OPEN, re-opening circuit")
            self.state = CircuitState.OPEN
            self.success_count = 0
        
        elif self.state == CircuitState.CLOSED:
            logger.warning(
                f"{self.name}: Failure {self.failure_count}/{self.config.failure_threshold}"
            )
            
            if self.failure_count >= self.config.failure_threshold:
                logger.error(f"{self.name}: Opening circuit")
                self.state = CircuitState.OPEN
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.timeout
    
    def reset(self):
        """Manually reset circuit breaker to CLOSED state."""
        logger.info(f"{self.name}: Manual reset")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
    
    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        return self.state == CircuitState.HALF_OPEN

class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass
```

2. **Convenience decorator:**
```python
def circuit_breaker(
    failure_threshold: int = 5,
    timeout: float = 30.0,
    name: Optional[str] = None,
) -> Callable:
    """
    Convenience decorator for circuit breaker.
    
    Example:
        @circuit_breaker(failure_threshold=3, timeout=60.0)
        def my_node(state: GraphState) -> GraphState:
            return state
    """
    breaker = CircuitBreaker(
        failure_threshold=failure_threshold,
        timeout=timeout,
        name=name,
    )
    return breaker
```

**Key principles:**
- Does NOT modify GraphState
- State transitions: CLOSED → OPEN → HALF_OPEN → CLOSED
- Fails fast when circuit is OPEN (prevents cascading failures)
- Automatic recovery testing after timeout
- Thread-safe state management (use locks if needed)

**Acceptance Criteria:**
- [ ] CLOSED → OPEN after 5 failures (configurable)
- [ ] OPEN → HALF_OPEN after 30s timeout (configurable)
- [ ] HALF_OPEN → CLOSED after 2 successes (configurable)
- [ ] HALF_OPEN → OPEN on any failure
- [ ] CircuitBreakerOpenError raised when OPEN
- [ ] Manual reset() supported
- [ ] State properties: is_closed, is_open, is_half_open
- [ ] No external dependencies (stdlib only)

---

## PROMPT 3: Timeout Wrapper

**Task:** Implement timeout decorator for Node execution.

**File to create:** `axis/recovery/timeout.py` (~100 lines)

**Pattern:** Decorator that enforces maximum execution time for Nodes.

**Requirements:**

1. **Timeout decorator (signal-based, Unix only):**
```python
import signal
from functools import wraps
from typing import Callable, Optional
from axis.state import GraphState
from axis.node import Node
import logging

logger = logging.getLogger(__name__)

class TimeoutError(Exception):
    """Raised when execution exceeds timeout."""
    pass

def timeout(seconds: float, error_message: Optional[str] = None) -> Callable[[Node], Node]:
    """
    Timeout decorator using SIGALRM (Unix only).
    
    Args:
        seconds: Maximum execution time in seconds
        error_message: Optional custom error message
    
    Returns:
        Decorated Node that raises TimeoutError if exceeded
    
    Example:
        @timeout(5.0)
        def my_node(state: GraphState) -> GraphState:
            # Must complete within 5 seconds
            return state
    
    Note:
        - Unix/Linux only (uses signal.SIGALRM)
        - Not thread-safe (signal handlers are process-wide)
        - Use timeout_threading() for cross-platform support
    """
    def decorator(node: Node) -> Node:
        @wraps(node)
        def wrapper(state: GraphState) -> GraphState:
            def _timeout_handler(signum, frame):
                msg = error_message or f"Node execution exceeded {seconds}s timeout"
                raise TimeoutError(msg)
            
            # Set signal handler
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, seconds)
            
            try:
                result = node(state)
            finally:
                # Restore old handler
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)
            
            return result
        
        return wrapper
    return decorator
```

2. **Timeout decorator (threading-based, cross-platform):**
```python
import threading
from typing import Callable, Optional, Any
from axis.state import GraphState
from axis.node import Node

class TimeoutThread(threading.Thread):
    """Thread that stores return value or exception."""
    
    def __init__(self, target: Callable, args: tuple):
        super().__init__(target=target, args=args, daemon=True)
        self.result: Optional[Any] = None
        self.exception: Optional[Exception] = None
    
    def run(self):
        try:
            self.result = self._target(*self._args)
        except Exception as e:
            self.exception = e

def timeout_threading(seconds: float) -> Callable[[Node], Node]:
    """
    Timeout decorator using threading (cross-platform).
    
    Args:
        seconds: Maximum execution time in seconds
    
    Returns:
        Decorated Node that raises TimeoutError if exceeded
    
    Example:
        @timeout_threading(5.0)
        def my_node(state: GraphState) -> GraphState:
            # Must complete within 5 seconds
            return state
    
    Note:
        - Works on all platforms (Unix, Windows)
        - Thread-safe
        - Slight overhead from thread creation
    """
    def decorator(node: Node) -> Node:
        @wraps(node)
        def wrapper(state: GraphState) -> GraphState:
            thread = TimeoutThread(target=node, args=(state,))
            thread.start()
            thread.join(timeout=seconds)
            
            if thread.is_alive():
                # Thread still running after timeout
                logger.error(
                    f"Node {node.__name__} exceeded {seconds}s timeout"
                )
                raise TimeoutError(
                    f"Node {node.__name__} execution exceeded {seconds}s"
                )
            
            if thread.exception:
                raise thread.exception
            
            return thread.result
        
        return wrapper
    return decorator
```

3. **Context manager for timeout:**
```python
from contextlib import contextmanager
import signal

@contextmanager
def time_limit(seconds: float):
    """
    Context manager for timeout.
    
    Example:
        with time_limit(5.0):
            # Code must complete within 5 seconds
            result = expensive_operation()
    """
    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Operation exceeded {seconds}s")
    
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
```

**Key principles:**
- Does NOT modify GraphState
- Two implementations: signal (Unix) and threading (cross-platform)
- Logs timeout events
- Clean resource cleanup (signal handlers restored)
- Compatible with Node protocol

**Acceptance Criteria:**
- [ ] timeout() decorator works (Unix/Linux)
- [ ] timeout_threading() decorator works (cross-platform)
- [ ] TimeoutError raised after N seconds
- [ ] Signal handlers properly restored
- [ ] time_limit() context manager works
- [ ] No resource leaks
- [ ] No external dependencies (stdlib only)

---

## PROMPT 4: Tests & Integration

**Task:** Comprehensive test suite for recovery layer.

**File to create:** `tests/test_recovery.py` (~150 lines)

**Requirements:**

1. **Test fixtures:**
```python
import pytest
from axis.state import GraphState
from axis.recovery.retry import retry, retry_with_jitter
from axis.recovery.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from axis.recovery.timeout import timeout_threading
import time

@pytest.fixture
def sample_state():
    return GraphState(trace_id="test-recovery")

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
```

2. **Retry tests:**
```python
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
```

3. **Circuit breaker tests:**
```python
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
```

4. **Timeout tests:**
```python
def test_timeout_raises_on_slow_node(sample_state):
    """Test timeout raises TimeoutError."""
    from axis.recovery.timeout import TimeoutError, timeout_threading
    
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
```

5. **Integration test:**
```python
def test_retry_with_circuit_breaker(sample_state):
    """Test combining retry and circuit breaker."""
    breaker = CircuitBreaker(failure_threshold=3)
    node = FailingNode(fail_count=2)
    
    decorated = retry(max_attempts=3)(breaker(node))
    
    # Should succeed after 3 attempts total
    result = decorated(sample_state)
    assert result.trace_id == "test-recovery"
```

**Acceptance Criteria:**
- [ ] All 10+ tests pass
- [ ] Retry tests: success, exhaustion, custom exceptions
- [ ] Circuit breaker tests: open, half-open, reset
- [ ] Timeout tests: raises on slow, allows fast
- [ ] Integration tests: combining decorators
- [ ] No external dependencies (stdlib only)

---

## Module Integration

**File to create:** `axis/recovery/__init__.py` (~50 lines)

```python
"""
Axis Recovery Layer - Error resilience patterns.

Provides:
- retry: Exponential backoff retry decorator
- CircuitBreaker: Circuit breaker pattern for fault tolerance
- timeout: Timeout decorator for long-running operations

Example:
    from axis.recovery import retry, CircuitBreaker, timeout_threading
    
    @retry(max_attempts=3)
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
    retry_with_jitter,
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
    "retry_with_jitter",
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
```

---

## Common Guidelines

1. **Immutability:** Never modify GraphState
2. **Stdlib only:** No external dependencies
3. **Logging:** Use stdlib `logging` for observability
4. **Type hints:** Full type annotations
5. **Documentation:** Docstrings with examples
6. **Thread safety:** Document thread-safety guarantees
7. **Testing:** Minimum 10 tests for full coverage
8. **Error messages:** Clear, actionable error messages

---

## Deliverables

**Agent 1 (Retry):**
- `axis/recovery/retry.py` (~200 lines)
- 4 tests in `test_recovery.py`

**Agent 2 (Circuit Breaker):**
- `axis/recovery/circuit_breaker.py` (~200 lines)
- 4 tests in `test_recovery.py`

**Agent 3 (Timeout):**
- `axis/recovery/timeout.py` (~100 lines)
- 3 tests in `test_recovery.py`

**Agent 4 (Integration):**
- `axis/recovery/__init__.py` (~50 lines)
- `tests/test_recovery.py` complete (~150 lines)
- Integration tests (2+)

**Integration:**
```python
# Verify all exports
from axis.recovery import (
    retry, CircuitBreaker, timeout_threading,
    CircuitBreakerOpenError, TimeoutError,
)
```

**Timeline:** Each agent works in parallel. Total: 1-2 days for all 4.

---

## Validation Checklist

After all agents complete:

- [ ] All existing tests still pass (33 passing, 5 skipped)
- [ ] 10+ new recovery tests pass
- [ ] retry() works with exponential backoff
- [ ] CircuitBreaker state transitions correct
- [ ] timeout_threading() enforces time limits
- [ ] Decorators stackable (retry + circuit + timeout)
- [ ] No regressions in core
- [ ] Documentation updated
- [ ] Copilot instructions note Week 3-4 complete

**Success metric:**
```bash
cd /home/caravaggio/axis
python3 -m pytest tests/ -v
# Expected: 43+ tests passing (33 existing + 10 recovery)
```

---

## Architecture Notes

**Design Philosophy:**
- **Composability:** All decorators stack cleanly
- **Explicitness:** No magic, clear behavior
- **Minimalism:** Solve 80% of use cases simply
- **Production-ready:** Battle-tested patterns (Netflix Hystrix inspiration)

**NOT Included (intentional):**
- Async support (Phase 2.3 Week 7-8: Streaming)
- Metrics/monitoring (Phase 2.3 Week 5-6: Observability)
- Configuration files (keep it code-first)
- Distributed coordination (out of scope)

**Vitruvyan Context:**
- 8 months production experience with retry patterns
- 70K+ lines validated these patterns work
- Circuit breaker prevented cascade failures in Codex Hunter
- Timeout essential for LLM calls (prevent hanging)

---
