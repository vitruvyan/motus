from datetime import datetime
from typing import Iterable, List, Protocol

from axis.state import GraphState
from axis.events import Event, EventType
from axis.node import Node
from axis.policy import Policy


class RunnerObserver(Protocol):
    """Protocol for observing runner execution."""
    
    def observe(self, event_type: str, state: GraphState, **kwargs) -> None:
        """Observe a runner event."""
        ...


class Runner:
    """
    Executes a predefined sequence of Nodes against a GraphState.

    The runner:
    - does not modify graph structure
    - does not contain business logic
    - enforces execution order and policy
    """

    def __init__(
        self, 
        nodes: Iterable[Node], 
        policy: Policy = Policy.STRICT,
        bus=None
    ):
        self._nodes: List[Node] = list(nodes)
        self._policy = policy
        self._bus = bus
        self._observers: List[RunnerObserver] = []
    
    def attach(self, observer: RunnerObserver) -> None:
        """Attach an observer to the runner."""
        self._observers.append(observer)

    def run(self, state: GraphState) -> GraphState:
        current_state = state

        # Notify observers
        for observer in self._observers:
            observer.observe("GRAPH_START", current_state)

        if self._bus:
            self._bus.observe("GRAPH_START", current_state)

        for i, node in enumerate(self._nodes):
            node_id = f"node_{i}"
            node_name = getattr(node, '__name__', node_id)  # Try to get function name
            
            # Notify observers
            for observer in self._observers:
                observer.observe("PRE_NODE", current_state, node_name=node_name)
            
            if self._bus:
                self._bus.observe("PRE_NODE", current_state, node_name=node_name)
            
            # Emit NODE_STARTED event
            current_state = current_state.with_event(
                Event(
                    event_type=EventType.NODE_STARTED,
                    description=f"Node {node_id} started",
                    timestamp=datetime.utcnow(),
                )
            )

            try:
                new_state = node(current_state)

                # Emit NODE_COMPLETED event
                current_state = new_state.with_event(
                    Event(
                        event_type=EventType.NODE_COMPLETED,
                        description=f"Node {node_id} completed",
                        timestamp=datetime.utcnow(),
                    )
                )

                # Notify observers
                for observer in self._observers:
                    observer.observe("POST_NODE", current_state, node_name=node_name)

                if self._bus:
                    self._bus.observe("POST_NODE", current_state, node_name=node_name)

            except Exception as exc:
                # Notify observers
                for observer in self._observers:
                    observer.observe("ERROR", current_state, node_name=node_name, error=exc)
                
                if self._bus:
                    self._bus.observe("ERROR", current_state, node_name=node_name, error=exc)
                
                # STRICT: stop execution
                if self._policy == Policy.STRICT:
                    raise

                # EXPLORATION: skip node, record event
                current_state = current_state.with_event(
                    Event(
                        event_type=EventType.NODE_SKIPPED,
                        description=f"Node {node_id} skipped due to error: {exc}",
                        timestamp=datetime.utcnow(),
                    )
                )

        # Notify observers
        for observer in self._observers:
            observer.observe("GRAPH_END", current_state)

        if self._bus:
            self._bus.observe("GRAPH_END", current_state)

        return current_state
