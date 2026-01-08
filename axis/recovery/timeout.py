import signal
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Callable, Optional, Any
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
                    f"Node {getattr(node, '__name__', str(node))} exceeded {seconds}s timeout"
                )
                raise TimeoutError(
                    f"Node {getattr(node, '__name__', str(node))} execution exceeded {seconds}s"
                )
            
            if thread.exception:
                raise thread.exception
            
            return thread.result
        
        return wrapper
    return decorator

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