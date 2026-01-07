from datetime import datetime
from typing import Iterable, List

from axis.state import GraphState, Event, EventType
from axis.node import Node
from axis.policy import Policy


class Runner:
    """
    Executes a predefined sequence of Nodes against a GraphState.

    The runner:
    - does not modify graph structure
    - does not contain business logic
    - enforces execution order and policy
    """

    def __init__(self, nodes: Iterable[Node], policy: Policy = Policy.STRICT):
        self._nodes: List[Node] = list(nodes)
        self._policy = policy

    def run(self, state: GraphState) -> GraphState:
        current_state = state

        for i, node in enumerate(self._nodes):
            node_id = f"node_{i}"
            
            # Emit NODE_STARTED event
            current_state = current_state.with_event(
                Event(
                    type=EventType.NODE_STARTED,
                    description=f"Node {node_id} started",
                    timestamp=datetime.utcnow(),
                )
            )

            try:
                new_state = node(current_state)

                # Emit NODE_COMPLETED event
                current_state = new_state.with_event(
                    Event(
                        type=EventType.NODE_COMPLETED,
                        description=f"Node {node_id} completed",
                        timestamp=datetime.utcnow(),
                    )
                )

            except Exception as exc:
                # STRICT: stop execution
                if self._policy == Policy.STRICT:
                    raise

                # EXPLORATION: skip node, record event
                current_state = current_state.with_event(
                    Event(
                        type=EventType.NODE_SKIPPED,
                        description=f"Node {node_id} skipped due to error: {exc}",
                        timestamp=datetime.utcnow(),
                    )
                )

        return current_state
