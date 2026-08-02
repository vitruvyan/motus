"""
Axis Recovery Layer - Retry Patterns.

Provides exponential backoff retry decorators for resilient Node execution.
"""

from functools import wraps
from typing import Callable, Type, Tuple, Optional
from axis.state import GraphState
from axis.events import Event, EventType, now
from axis.node import Node, node_name
import time
import logging
import random

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    jitter: bool = False,
    max_delay: Optional[float] = None,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
) -> Callable[[Node], Node]:
    """
    Retry decorator with exponential backoff for Nodes.

    Args:
        max_attempts: Maximum number of attempts (default: 3). Must be >= 1;
            validated when the decorator is applied, not on first call.
        initial_delay: Initial delay in seconds (default: 1.0)
        backoff_factor: Multiplier for delay on each retry (default: 2.0)
        exceptions: Tuple of exception types to catch (default: all)
        jitter: Randomize each delay to 50-100% of its value, to avoid a
            thundering herd of simultaneous retries (default: False)
        max_delay: Cap on any single delay, in seconds (default: no cap)
        on_retry: Optional callback(exception, attempt_number)

    Returns:
        Decorated Node that retries on failure. Each retry appends a
        NODE_RETRIED event to the state handed to the next attempt, so
        retries are visible in the trace instead of only to on_retry.

    Example:
        @retry(max_attempts=3, initial_delay=1.0, jitter=True)
        def my_node(state: GraphState) -> GraphState:
            # Node implementation
            return state
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    def decorator(node: Node) -> Node:
        name = node_name(node)

        @wraps(node)
        def wrapper(state: GraphState) -> GraphState:
            current_state = state
            delay = initial_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return node(current_state)
                except exceptions as e:
                    if attempt == max_attempts:
                        logger.error(
                            f"Node {name} failed after {max_attempts} attempts: {e}"
                        )
                        # Carry the retry trace on the exception itself —
                        # current_state already holds every NODE_RETRIED
                        # event from the attempts that came before this
                        # one. Without this, the last `raise` discards it
                        # and the Runner's ERROR event is all that's left
                        # of 3x the latency the retries paid for.
                        e.__axis_state__ = current_state
                        raise

                    actual_delay = delay
                    if jitter:
                        actual_delay = delay * (0.5 + 0.5 * random.random())
                    if max_delay is not None:
                        actual_delay = min(actual_delay, max_delay)

                    logger.warning(
                        f"Node {name} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {actual_delay:.2f}s: {e}"
                    )

                    if on_retry:
                        on_retry(e, attempt)

                    current_state = current_state.with_event(
                        Event(
                            event_type=EventType.NODE_RETRIED,
                            description=f"Node {name} retry {attempt}/{max_attempts}",
                            timestamp=now(),
                            node_name=name,
                            metadata={
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                                "delay": actual_delay,
                                "error_type": type(e).__name__,
                                "error": str(e),
                            },
                        )
                    )

                    time.sleep(actual_delay)
                    delay *= backoff_factor

        return wrapper
    return decorator


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
