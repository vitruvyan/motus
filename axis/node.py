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
