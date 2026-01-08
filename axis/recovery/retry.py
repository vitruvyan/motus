"""
Axis Recovery Layer - Retry Patterns.

Provides exponential backoff retry decorators for resilient Node execution.
"""

from functools import wraps
from typing import Callable, Type, Tuple, Optional
from axis.state import GraphState
from axis.node import Node
import time
import logging
import random

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
                            f"Node {getattr(node, '__name__', str(node))} failed after {max_attempts} attempts: {e}"
                        )
                        raise
                    
                    logger.warning(
                        f"Node {getattr(node, '__name__', str(node))} failed (attempt {attempt}/{max_attempts}), "
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