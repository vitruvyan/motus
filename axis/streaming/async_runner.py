import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, List, Optional, Sequence

from axis.state import GraphState
from axis.events import Event, EventType, now
from axis.node import Node, node_name
from axis.policy import Policy
from axis.runner import NodeFailed, RunnerObserver, _state_from_exception

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
        # bus folds into _observers exactly like Runner — kept as an
        # attribute for compat (some callers check `runner.bus is not
        # None`), but it is never notified separately from _observers, so
        # bus= plus a later attach(bus) is the only way to double-notify,
        # and that's the caller asking for it twice.
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
        can't take down the run or silence the others — except a
        `critical` observer, which re-raises (see RunnerObserver)."""
        for observer in self._observers:
            try:
                observer.observe(event_type.value, state, **kwargs)
            except Exception:
                if getattr(observer, "critical", False):
                    raise
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
            name = node_name(node)

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
                current_state = _state_from_exception(exc, current_state)
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

        Under Policy.STRICT, a node failure does NOT yield — it raises
        NodeFailed instead, exactly like run(). Catch it and read
        `.state` for the trace up to and including the ERROR event; a
        consumer that only iterates `async for` never sees a failed run's
        trace, by design (the failure interrupts the generator).

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
            name = node_name(node)

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
                current_state = _state_from_exception(exc, current_state)
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


@dataclass
class _NodeOutcome:
    """Result of running one node under ConcurrentRunner. Never raises —
    success or failure is reported as data so run() can merge every
    branch deterministically (in self.nodes order) after gather()
    completes, instead of racing to append events from N coroutines at
    once."""

    name: str
    ok: bool
    state: Optional[GraphState]
    exc: Optional[Exception]
    duration_ms: int


class ConcurrentRunner(AsyncRunner):
    """
    Runner that executes multiple nodes concurrently against the same
    seed state, then merges their contributions back in one deterministic
    pass — NODE_STARTED/NODE_COMPLETED (or ERROR) per node, exactly like
    Runner, just without an ordering guarantee *between* branches (they
    ran in parallel; only the merge is sequential).

    Every node always runs to completion — concurrency doesn't compose
    with "stop early". Under STRICT, a failure raises NodeFailed after
    all branches have finished, not before the others start.

    Example:
        # Execute 3 independent nodes in parallel
        runner = ConcurrentRunner(nodes=[node1, node2, node3])
        result = await runner.run(state)
    """

    async def _run_node(self, node: Callable, seed_state: GraphState) -> _NodeOutcome:
        """Run one node against the shared seed. Never raises."""
        name = node_name(node)
        t0 = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(node):
                result_state = await node(seed_state)
            else:
                result_state = await asyncio.to_thread(node, seed_state)
        except Exception as exc:
            duration_ms = round((time.monotonic() - t0) * 1000)
            return _NodeOutcome(name=name, ok=False, state=None, exc=exc, duration_ms=duration_ms)
        duration_ms = round((time.monotonic() - t0) * 1000)
        return _NodeOutcome(name=name, ok=True, state=result_state, exc=None, duration_ms=duration_ms)

    def _merge_branch(
        self, current_state: GraphState, seed: GraphState, branch: GraphState
    ) -> GraphState:
        """Fold one branch's contribution into current_state.

        A branch's returned state was built by calling the node with
        `seed` — it therefore CONTAINS a full copy of everything `seed`
        already had (including GRAPH_START and any earlier-merged
        branch's facts, if the node just threads its argument through).
        Appending it whole, as the old _merge_states did, duplicates the
        seed for every branch: N nodes meant N copies of GRAPH_START and
        every seed fact. Slicing off exactly the seed's length from each
        collection takes only what THIS branch appended.
        """
        return GraphState(
            trace_id=current_state.trace_id,
            intent=branch.intent if branch.intent != seed.intent else current_state.intent,
            facts=current_state.facts + branch.facts[len(seed.facts):],
            decisions=current_state.decisions + branch.decisions[len(seed.decisions):],
            rejections=current_state.rejections + branch.rejections[len(seed.rejections):],
            events=current_state.events + branch.events[len(seed.events):],
        )

    async def run(self, state: GraphState) -> GraphState:
        """Execute all nodes concurrently against a shared seed, then
        merge and trace each branch's outcome in self.nodes order."""
        current_state = state.with_event(
            Event(
                event_type=EventType.GRAPH_START,
                description=f"Graph started under policy {self.policy.value} (concurrent)",
                timestamp=now(),
                metadata={"policy": self.policy.value},
            )
        )
        self._notify(EventType.GRAPH_START, current_state)

        seed = current_state
        outcomes: List[_NodeOutcome] = await asyncio.gather(
            *(self._run_node(node, seed) for node in self.nodes)
        )

        nodes_run = nodes_skipped = nodes_failed = 0

        for outcome in outcomes:
            current_state = current_state.with_event(
                Event(
                    event_type=EventType.NODE_STARTED,
                    description=f"Node {outcome.name} started",
                    timestamp=now(),
                    node_name=outcome.name,
                )
            )
            self._notify(EventType.NODE_STARTED, current_state, node_name=outcome.name)

            if outcome.ok:
                current_state = self._merge_branch(current_state, seed, outcome.state)
                current_state = current_state.with_event(
                    Event(
                        event_type=EventType.NODE_COMPLETED,
                        description=f"Node {outcome.name} completed",
                        timestamp=now(),
                        node_name=outcome.name,
                        metadata={"duration_ms": outcome.duration_ms},
                    )
                )
                self._notify(EventType.NODE_COMPLETED, current_state, node_name=outcome.name)
                nodes_run += 1
                continue

            nodes_failed += 1
            exc = outcome.exc
            current_state = _state_from_exception(exc, current_state)
            current_state = current_state.with_event(
                Event(
                    event_type=EventType.ERROR,
                    description=f"Node {outcome.name} failed: {exc}",
                    timestamp=now(),
                    node_name=outcome.name,
                    metadata={
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "duration_ms": outcome.duration_ms,
                    },
                )
            )
            self._notify(EventType.ERROR, current_state, node_name=outcome.name, error=exc)

            if self.policy == Policy.STRICT:
                failure = NodeFailed(exc)
                failure.state = current_state
                raise failure from exc

            current_state = current_state.with_event(
                Event(
                    event_type=EventType.NODE_SKIPPED,
                    description=f"Node {outcome.name} skipped due to error: {exc}",
                    timestamp=now(),
                    node_name=outcome.name,
                    metadata={"duration_ms": outcome.duration_ms},
                )
            )
            nodes_skipped += 1

        current_state = current_state.with_event(
            Event(
                event_type=EventType.GRAPH_END,
                description=(
                    f"Graph ended (concurrent): {nodes_run} run, "
                    f"{nodes_skipped} skipped, {nodes_failed} failed"
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
