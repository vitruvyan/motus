import functools
import inspect
from typing import Protocol


from axis.state import GraphState


class Node(Protocol):
    """
    A Node represents a single, explicit responsibility in the graph.

    A Node:
    - receives a GraphState
    - returns a new GraphState
    - never mutates state in place
    - has no knowledge of other nodes, runner, or policy
    """

    def __call__(self, state: GraphState) -> GraphState:
        ...


def node_name(node) -> str:
    """
    Resolve a node's identity for the trace.

    Order: unwrap any @functools.wraps-decorated wrapper first (retry,
    timeout, circuit breaker all chain __wrapped__), so a decorated node
    reports its real name instead of "wrapper" — then __name__, then a
    functools.partial's inner function, then the type name for
    class-based nodes with no name of their own.
    """
    unwrapped = inspect.unwrap(node)
    if isinstance(unwrapped, functools.partial):
        return node_name(unwrapped.func)
    name = getattr(unwrapped, "__name__", None)
    return name or type(unwrapped).__name__
