import asyncio
import logging
import time
from typing import AsyncIterator, Callable, List, Optional, Sequence

from axis.state import GraphState
from axis.events import Event, EventType, now
from axis.node import Node
from axis.policy import Policy
from axis.runner import NodeFailed, RunnerObserver

logger = logging.getLogger(__name__)


class AsyncRunner:
    """
    Async runner for concurrent Node execution.

    Twin of Runner: same event shape (NODE_STARTED/COMPLETED/SKIPPED,
    ERROR, GRAPH_START/END), same clock, same trace-on-failure and
    observer-isolation guarantees — just async. Adds:
    - Async nodes (async def)
    - Concurrent execution (asyncio.gather, via ConcurrentRunner)
    - Streaming updates (async generator)

    Example:
        async def async_node(state: GraphState) -> GraphState:
            await asyncio.sleep(0.1)  # Async I/O
            return state.with_fact(...)

        runner = AsyncRunner(nodes=[async_node])
        async for state in runner.stream(initial_state):
            print(f"Progress: {len(state.events)} events")

        # Or just get final result:
        result = await runner.run(initial_state)
    """

    def __init__(
        self,
        nodes: Sequence[Callable],
        policy: Policy = Policy.STRICT,
        bus: Optional['SynapticBus'] = None,
    ):
        """
        Args:
            nodes: Sequence of async or sync Node callables
            policy: Execution policy (STRICT or EXPLORATION)
            bus: Optional observer (e.g. SynapticBus) for event notification
        """
        self.nodes = list(nodes)
        self.policy = policy
        self.bus = bus
        self._observers: List[RunnerObserver] = []
        if bus is not None:
            self._observers.append(bus)

    def attach(self, observer: RunnerObserver) -> None:
        """Attach an observer to the runner."""
        self._observers.append(observer)

    def _notify(self, event_type: EventType, state: GraphState, **kwargs) -> None:
        """Same isolation guarantee as Runner._notify: called outside any
        node's try block, each observer wrapped so one broken observer
        can't take down the run or silence the others."""
        for observer in self._observers:
            try:
                observer.observe(event_type.value, state, **kwargs)
            except Exception:
                logger.exception(
                    "Observer %r raised handling %s", observer, event_type.value
                )

    async def run(self, state: GraphState) -> GraphState:
        """
        Execute all nodes asynchronously and return final state.

        Args:
            state: Initial GraphState

        Returns:
            Final GraphState after all nodes
        """
        current_state = state.with_event(
            Event(
                event_type=EventType.GRAPH_START,
                description=f"Graph started under policy {self.policy.value}",
                timestamp=now(),
                metadata={"policy": self.policy.value},
            )
        )
        self._notify(EventType.GRAPH_START, current_state)

        nodes_run = nodes_skipped = nodes_failed = 0

        for node in self.nodes:
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
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
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

                if self.policy == Policy.STRICT:
                    failure = NodeFailed(exc)
                    failure.state = current_state
                    raise failure from exc

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
            current_state = next_state.with_event(
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
                    "policy": self.policy.value,
                    "nodes_run": nodes_run,
                    "nodes_skipped": nodes_skipped,
                    "nodes_failed": nodes_failed,
                },
            )
        )
        self._notify(EventType.GRAPH_END, current_state)

        return current_state

    async def stream(self, state: GraphState) -> AsyncIterator[GraphState]:
        """
        Stream GraphState updates during execution.

        Yields GraphState after GRAPH_START, after each node, and after
        GRAPH_END.

        Args:
            state: Initial GraphState

        Yields:
            GraphState after each lifecycle event

        Example:
            async for current_state in runner.stream(initial_state):
                print(f"Events: {len(current_state.events)}")
        """
        current_state = state.with_event(
            Event(
                event_type=EventType.GRAPH_START,
                description=f"Graph started under policy {self.policy.value}",
                timestamp=now(),
                metadata={"policy": self.policy.value},
            )
        )
        self._notify(EventType.GRAPH_START, current_state)
        yield current_state

        nodes_run = nodes_skipped = nodes_failed = 0

        for node in self.nodes:
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
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
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

                if self.policy == Policy.STRICT:
                    failure = NodeFailed(exc)
                    failure.state = current_state
                    raise failure from exc

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
                yield current_state
                continue

            duration_ms = round((time.monotonic() - t0) * 1000)
            current_state = next_state.with_event(
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
            yield current_state

        current_state = current_state.with_event(
            Event(
                event_type=EventType.GRAPH_END,
                description=(
                    f"Graph ended: {nodes_run} run, {nodes_skipped} skipped, "
                    f"{nodes_failed} failed"
                ),
                timestamp=now(),
                metadata={
                    "policy": self.policy.value,
                    "nodes_run": nodes_run,
                    "nodes_skipped": nodes_skipped,
                    "nodes_failed": nodes_failed,
                },
            )
        )
        self._notify(EventType.GRAPH_END, current_state)
        yield current_state


class ConcurrentRunner(AsyncRunner):
    """
    Runner that executes multiple nodes concurrently.

    Example:
        # Execute 3 independent nodes in parallel
        runner = ConcurrentRunner(nodes=[node1, node2, node3])
        result = await runner.run(state)
    """

    async def run(self, state: GraphState) -> GraphState:
        """Execute all nodes concurrently using asyncio.gather."""
        current_state = state.with_event(
            Event(
                event_type=EventType.GRAPH_START,
                description=f"Graph started under policy {self.policy.value} (concurrent)",
                timestamp=now(),
                metadata={"policy": self.policy.value},
            )
        )
        self._notify(EventType.GRAPH_START, current_state)

        tasks = []
        for node in self.nodes:
            if asyncio.iscoroutinefunction(node):
                tasks.append(node(current_state))
            else:
                # Wrap sync function in async
                tasks.append(asyncio.to_thread(node, current_state))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        nodes_run = nodes_failed = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                nodes_failed += 1
                if self.policy == Policy.STRICT:
                    failure = NodeFailed(result)
                    failure.state = current_state
                    raise failure from result
                logger.warning(f"Node {i} failed: {result}")
            else:
                # Merge state (combine facts, decisions, etc.)
                current_state = self._merge_states(current_state, result)
                nodes_run += 1

        current_state = current_state.with_event(
            Event(
                event_type=EventType.GRAPH_END,
                description=(
                    f"Graph ended (concurrent): {nodes_run} run, {nodes_failed} failed"
                ),
                timestamp=now(),
                metadata={
                    "policy": self.policy.value,
                    "nodes_run": nodes_run,
                    "nodes_skipped": 0,
                    "nodes_failed": nodes_failed,
                },
            )
        )
        self._notify(EventType.GRAPH_END, current_state)

        return current_state

    def _merge_states(self, state1: GraphState, state2: GraphState) -> GraphState:
        """
        Merge two GraphStates (simple union of collections).

        Note: This is a naive merge. Production may need conflict resolution.
        """
        return GraphState(
            trace_id=state1.trace_id,
            intent=state1.intent,
            facts=state1.facts + state2.facts,
            decisions=state1.decisions + state2.decisions,
            rejections=state1.rejections + state2.rejections,
            events=state1.events + state2.events,
        )
