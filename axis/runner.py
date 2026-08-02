import logging
import time
from typing import Iterable, List, Optional, Protocol

from axis.state import GraphState
from axis.events import Event, EventType, now
from axis.node import Node
from axis.policy import Policy

logger = logging.getLogger(__name__)


class RunnerObserver(Protocol):
    """Protocol for observing runner execution."""

    def observe(self, event_type: str, state: GraphState, **kwargs) -> None:
        """Observe a runner event."""
        ...


class NodeFailed(Exception):
    """
    Raised by the Runner under Policy.STRICT when a node fails.

    Chains the original exception (`raise ... from exc`) and carries
    `.state` — the GraphState accumulated through the ERROR event for the
    failing node — so a caller can still persist the trace of the run that
    failed. A caller that only does `except Exception` keeps working
    unchanged; one that wants the trace reads `.state`.
    """

    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original
        self.state: Optional[GraphState] = None


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
        bus=None,
    ):
        self._nodes: List[Node] = list(nodes)
        self._policy = policy
        self._observers: List[RunnerObserver] = []
        if bus is not None:
            self._observers.append(bus)

    def attach(self, observer: RunnerObserver) -> None:
        """Attach an observer to the runner."""
        self._observers.append(observer)

    def _notify(self, event_type: EventType, state: GraphState, **kwargs) -> None:
        """Tell observers what happened. Called outside the node's try
        block, always — a broken observer must never be mistaken for a
        broken node, and must never stop other observers from hearing."""
        for observer in self._observers:
            try:
                observer.observe(event_type.value, state, **kwargs)
            except Exception:
                logger.exception(
                    "Observer %r raised handling %s", observer, event_type.value
                )

    def run(self, state: GraphState) -> GraphState:
        current_state = state.with_event(
            Event(
                event_type=EventType.GRAPH_START,
                description=f"Graph started under policy {self._policy.value}",
                timestamp=now(),
                metadata={"policy": self._policy.value},
            )
        )
        self._notify(EventType.GRAPH_START, current_state)

        nodes_run = nodes_skipped = nodes_failed = 0

        for node in self._nodes:
            name = getattr(node, "__name__", type(node).__name__)

            current_state = current_state.with_event(
                Event(
                    event_type=EventType.NODE_STARTED,
                    description=f"Node {name} started",
                    timestamp=now(),
                    node_name=name,
                )
            )
            self._notify(EventType.NODE_STARTED, current_state, node_name=name)

            t0 = time.monotonic()
            try:
                new_state = node(current_state)
            except Exception as exc:
                nodes_failed += 1
                duration_ms = round((time.monotonic() - t0) * 1000)
                current_state = current_state.with_event(
                    Event(
                        event_type=EventType.ERROR,
                        description=f"Node {name} failed: {exc}",
                        timestamp=now(),
                        node_name=name,
                        metadata={
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "duration_ms": duration_ms,
                        },
                    )
                )
                self._notify(EventType.ERROR, current_state, node_name=name, error=exc)

                # STRICT: stop execution, but hand the caller the trace.
                if self._policy == Policy.STRICT:
                    failure = NodeFailed(exc)
                    failure.state = current_state
                    raise failure from exc

                # EXPLORATION: record the failure, skip the node, continue.
                current_state = current_state.with_event(
                    Event(
                        event_type=EventType.NODE_SKIPPED,
                        description=f"Node {name} skipped due to error: {exc}",
                        timestamp=now(),
                        node_name=name,
                        metadata={"duration_ms": duration_ms},
                    )
                )
                nodes_skipped += 1
                continue

            duration_ms = round((time.monotonic() - t0) * 1000)
            current_state = new_state.with_event(
                Event(
                    event_type=EventType.NODE_COMPLETED,
                    description=f"Node {name} completed",
                    timestamp=now(),
                    node_name=name,
                    metadata={"duration_ms": duration_ms},
                )
            )
            self._notify(EventType.NODE_COMPLETED, current_state, node_name=name)
            nodes_run += 1

        current_state = current_state.with_event(
            Event(
                event_type=EventType.GRAPH_END,
                description=(
                    f"Graph ended: {nodes_run} run, {nodes_skipped} skipped, "
                    f"{nodes_failed} failed"
                ),
                timestamp=now(),
                metadata={
                    "policy": self._policy.value,
                    "nodes_run": nodes_run,
                    "nodes_skipped": nodes_skipped,
                    "nodes_failed": nodes_failed,
                },
            )
        )
        self._notify(EventType.GRAPH_END, current_state)

        return current_state
