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