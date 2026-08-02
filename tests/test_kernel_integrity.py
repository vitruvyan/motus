"""
Kernel integrity tests — pin the defects an adversarial review verified
empirically against this exact kernel: duplicated Event/EventType, trace
loss on STRICT failure, observer-inside-try misattribution, naive
timestamps, lost node identity, a silent EXPLORATION policy, an unstated
JSON contract, and the retry(0) landmine.

Each test below corresponds to one verified defect. If one of these goes
red, the kernel has regressed on "the trace survives failure."
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from axis.state import GraphState, Fact, Decision, Rejection
from axis.events import Event, EventType, now, Json
from axis.runner import Runner, NodeFailed
from axis.policy import Policy
from axis.persistence import FileTraceObserver
from axis.recovery.retry import retry


# ---------------------------------------------------------------------------
# 1. One Event/EventType: round-trip equality, every member
# ---------------------------------------------------------------------------

def test_round_trip_equality_every_event_type():
    """Every EventType member survives to_dict -> from_dict as an equal
    Event. Before the fix, GraphState.from_dict rebuilt events through a
    second, divergent EventType, so 8 of 11 members crashed with
    ValueError and even the 3 shared members failed equality (a different
    Event class entirely)."""
    ts = now()
    events = tuple(
        Event(
            event_type=member,
            description=f"{member.value} happened",
            timestamp=ts,
            metadata={"member": member.value},
            node_name="some_node",
        )
        for member in EventType
    )
    assert len(events) == len(list(EventType))  # sanity: didn't skip any

    state = GraphState(
        trace_id="round-trip",
        intent="testing",
        facts=(),
        decisions=(),
        rejections=(),
        events=events,
    )

    rebuilt = GraphState.from_dict(state.to_dict())

    assert rebuilt == state
    assert rebuilt.events == state.events


# ---------------------------------------------------------------------------
# 2. Backward compatibility with traces the OLD code emitted
# ---------------------------------------------------------------------------

def test_from_dict_loads_old_format_trace():
    """A trace serialized by the pre-fix Runner only ever contained
    node_started, node_completed, node_skipped — that literal shape must
    still load, unchanged, after the dedup."""
    old_format = {
        "trace_id": "legacy-trace-001",
        "intent": "answer_question",
        "facts": [
            {
                "key": "user_input",
                "value": "hello",
                "source": "test_node",
                "timestamp": "2024-01-01T00:00:00",
            }
        ],
        "decisions": [],
        "rejections": [],
        "events": [
            {
                "event_type": "node_started",
                "description": "Node node_0 started",
                "timestamp": "2024-01-01T00:00:00",
                "metadata": None,
                "node_name": None,
            },
            {
                "event_type": "node_completed",
                "description": "Node node_0 completed",
                "timestamp": "2024-01-01T00:00:00.100000",
                "metadata": None,
                "node_name": None,
            },
            {
                "event_type": "node_skipped",
                "description": "Node node_1 skipped due to error: boom",
                "timestamp": "2024-01-01T00:00:00.200000",
                "metadata": None,
                "node_name": None,
            },
        ],
    }

    state = GraphState.from_dict(old_format)

    assert state.trace_id == "legacy-trace-001"
    assert len(state.events) == 3
    assert [e.event_type for e in state.events] == [
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
        EventType.NODE_SKIPPED,
    ]
    assert state.facts[0].value == "hello"

    # And it round-trips right back out.
    assert GraphState.from_dict(state.to_dict()) == state


# ---------------------------------------------------------------------------
# 3. STRICT keeps the trace
# ---------------------------------------------------------------------------

def test_strict_failure_raises_node_failed_with_state():
    """Under STRICT, a node failure must not discard the accumulated
    trace. The Runner records an ERROR event, then raises NodeFailed(exc)
    from exc, carrying .state — the trace up to and including that ERROR
    event."""
    def ok_node(state: GraphState) -> GraphState:
        return state.with_fact(Fact("seen", True, "ok_node", now()))

    def boom_node(state: GraphState) -> GraphState:
        raise ValueError("kaboom")

    def never_runs(state: GraphState) -> GraphState:
        return state.with_fact(Fact("unreachable", True, "never_runs", now()))

    runner = Runner([ok_node, boom_node, never_runs], policy=Policy.STRICT)

    with pytest.raises(NodeFailed) as excinfo:
        runner.run(GraphState.new("strict"))

    failure = excinfo.value
    assert isinstance(failure.__cause__, ValueError)
    assert str(failure.__cause__) == "kaboom"

    trace = failure.state
    assert trace is not None
    assert len(trace.facts) == 1  # ok_node ran; never_runs did not

    error_events = [e for e in trace.events if e.event_type == EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].node_name == "boom_node"
    assert error_events[0].metadata["error_type"] == "ValueError"
    assert error_events[0].metadata["error"] == "kaboom"

    # No GRAPH_END: the run was aborted, not completed.
    assert not any(e.event_type == EventType.GRAPH_END for e in trace.events)


# ---------------------------------------------------------------------------
# 4. Observers can't alter outcomes
# ---------------------------------------------------------------------------

class RaisingObserver:
    def observe(self, event_type, state, **kwargs):
        raise RuntimeError("observer is broken")


def test_raising_observer_does_not_alter_healthy_run():
    """A raising observer must not abort a healthy run, and must not
    produce a self-contradictory trace. Before the fix, POST_NODE
    notification lived inside the node's try block: a raising observer
    aborted STRICT runs outright, and under EXPLORATION produced
    [node_started, node_completed, node_skipped] for a node that
    succeeded."""
    def ok_node(state: GraphState) -> GraphState:
        return state.with_fact(Fact("k", "v", "ok_node", now()))

    runner = Runner([ok_node], policy=Policy.STRICT)
    runner.attach(RaisingObserver())

    result = runner.run(GraphState.new("obs"))

    assert len(result.facts) == 1
    types = [e.event_type for e in result.events]
    # No contradictory NODE_SKIPPED after a successful NODE_COMPLETED.
    assert EventType.NODE_SKIPPED not in types
    assert types.count(EventType.NODE_COMPLETED) == 1


def test_raising_observer_does_not_block_other_observers():
    """One observer's exception must not stop later observers from being
    notified."""
    class RecordingObserver:
        def __init__(self):
            self.seen = []

        def observe(self, event_type, state, **kwargs):
            self.seen.append(event_type)

    recorder = RecordingObserver()
    runner = Runner([lambda s: s], policy=Policy.STRICT)
    runner.attach(RaisingObserver())
    runner.attach(recorder)

    runner.run(GraphState.new("obs2"))

    assert EventType.GRAPH_START.value in recorder.seen
    assert EventType.GRAPH_END.value in recorder.seen


# ---------------------------------------------------------------------------
# 5. Node identity in the trace
# ---------------------------------------------------------------------------

def test_node_name_propagates_for_closures_and_class_nodes():
    """Every event the Runner emits for a node carries that node's real
    identity — a closure's stamped __name__, or the class name when no
    __name__ was stamped — never a positional node_0 placeholder."""
    def make_closure_node():
        def inner(state: GraphState) -> GraphState:
            return state
        inner.__name__ = "stamped_closure"
        return inner

    class ClassNode:
        def __call__(self, state: GraphState) -> GraphState:
            return state

    runner = Runner([make_closure_node(), ClassNode()], policy=Policy.STRICT)
    result = runner.run(GraphState.new("names"))

    node_events = [e for e in result.events if e.node_name is not None]
    names = {e.node_name for e in node_events}

    assert "stamped_closure" in names
    assert "ClassNode" in names
    assert "node_0" not in names
    assert "node_1" not in names


# ---------------------------------------------------------------------------
# 6. Per-node duration
# ---------------------------------------------------------------------------

def test_node_completed_and_skipped_carry_duration_ms():
    def slow_ok(state: GraphState) -> GraphState:
        return state

    def fails(state: GraphState) -> GraphState:
        raise RuntimeError("nope")

    runner = Runner([slow_ok, fails], policy=Policy.EXPLORATION)
    result = runner.run(GraphState.new("duration"))

    completed = [e for e in result.events if e.event_type == EventType.NODE_COMPLETED]
    skipped = [e for e in result.events if e.event_type == EventType.NODE_SKIPPED]

    assert len(completed) == 1
    assert "duration_ms" in completed[0].metadata
    assert isinstance(completed[0].metadata["duration_ms"], int)

    assert len(skipped) == 1
    assert "duration_ms" in skipped[0].metadata


# ---------------------------------------------------------------------------
# 7. One clock: tz-aware everywhere
# ---------------------------------------------------------------------------

def test_all_runner_timestamps_are_tz_aware():
    """Before the fix, the Runner stamped events with naive
    datetime.utcnow() while every consumer node used aware
    datetime.now(timezone.utc) — sorting a merged trace raised TypeError.
    Every Event the Runner emits must be tz-aware."""
    def node_with_fact(state: GraphState) -> GraphState:
        return state.with_fact(Fact("k", "v", "node_with_fact", now()))

    runner = Runner([node_with_fact], policy=Policy.STRICT)
    result = runner.run(GraphState.new("tz"))

    for event in result.events:
        assert event.timestamp.tzinfo is not None, event.event_type

    for fact in result.facts:
        assert fact.timestamp.tzinfo is not None

    # The merged timeline is sortable without TypeError.
    sorted(result.events, key=lambda e: e.timestamp)

    assert now().tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# 8. Run identity
# ---------------------------------------------------------------------------

def test_graph_state_new_generates_unique_ids():
    ids = {GraphState.new().trace_id for _ in range(200)}
    assert len(ids) == 200  # no collisions

    prefixed = GraphState.new(prefix="ingest")
    assert prefixed.trace_id.startswith("ingest-")
    assert prefixed.facts == () and prefixed.events == ()

    bare = GraphState.new()
    assert "-" not in bare.trace_id or len(bare.trace_id) == 12


# ---------------------------------------------------------------------------
# 9. JSON contract stated and enforced at write time
# ---------------------------------------------------------------------------

def test_to_dict_raises_pointed_type_error_on_unserializable_value():
    """A datetime (or any non-JSON value) placed in Fact.value must fail
    at to_dict() time, with a message naming the offending key — not
    survive the whole run and kill json.dumps anonymously at persist
    time."""
    state = GraphState.new("json-contract").with_fact(
        Fact("bad", datetime.now(), "node", now())
    )

    with pytest.raises(TypeError) as excinfo:
        state.to_dict()

    assert "bad" in str(excinfo.value)
    assert "Fact" in str(excinfo.value)


def test_to_dict_raises_pointed_type_error_on_unserializable_metadata():
    state = GraphState.new("json-contract-2").with_event(
        Event(EventType.ERROR, "x", now(), metadata={"when": datetime.now()})
    )

    with pytest.raises(TypeError) as excinfo:
        state.to_dict()

    assert "Event" in str(excinfo.value)


def test_json_type_alias_covers_plain_values():
    valid: Json = {"a": 1, "b": [1, "two", 3.0, True, None]}
    json.dumps(valid)  # must not raise


# ---------------------------------------------------------------------------
# 10. FileTraceObserver
# ---------------------------------------------------------------------------

def test_file_trace_observer_writes_on_graph_end():
    def node(state: GraphState) -> GraphState:
        return state.with_intent("persisted")

    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        runner = Runner([node], policy=Policy.STRICT)
        runner.attach(observer)

        final = runner.run(GraphState.new("filetrace"))

        target = Path(d) / f"{final.trace_id}.json"
        assert target.exists()
        assert not target.with_suffix(".tmp").exists()  # atomic: no leftover temp

        loaded = GraphState.from_json(target.read_text())
        assert loaded == final


def test_file_trace_observer_ignores_non_graph_end_events():
    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        state = GraphState.new("ignored")
        observer.observe(EventType.NODE_STARTED.value, state, node_name="x")
        assert list(Path(d).iterdir()) == []


# ---------------------------------------------------------------------------
# 11. Retry fixes
# ---------------------------------------------------------------------------

def test_retry_zero_raises_at_decoration_time():
    """retry(max_attempts=0) must fail immediately and clearly, not with
    the old 'exceptions must derive from BaseException' from re-raising a
    None last_exception."""
    with pytest.raises(ValueError):
        retry(max_attempts=0)


# ---------------------------------------------------------------------------
# 12. EXPLORATION records the error, GRAPH_START/GRAPH_END carry counts
# ---------------------------------------------------------------------------

def test_exploration_records_error_before_skip_and_graph_end_counts():
    """Before the fix, EXPLORATION flattened an exception straight into a
    NODE_SKIPPED description string — no error type, no metadata, no
    node_name — and the run carried no overall status. A degraded run
    must be machine-detectable from the trace alone."""
    def ok1(state: GraphState) -> GraphState:
        return state.with_fact(Fact("a", 1, "ok1", now()))

    def fails(state: GraphState) -> GraphState:
        raise ConnectionError("upstream 504")

    def ok2(state: GraphState) -> GraphState:
        return state.with_fact(Fact("b", 2, "ok2", now()))

    runner = Runner([ok1, fails, ok2], policy=Policy.EXPLORATION)
    result = runner.run(GraphState.new("exploration"))

    types = [e.event_type for e in result.events]
    error_idx = types.index(EventType.ERROR)
    skipped_idx = types.index(EventType.NODE_SKIPPED)
    assert error_idx < skipped_idx  # ERROR recorded before the skip

    error_event = result.events[error_idx]
    assert error_event.node_name == "fails"
    assert error_event.metadata["error_type"] == "ConnectionError"
    assert error_event.metadata["error"] == "upstream 504"

    graph_end = [e for e in result.events if e.event_type == EventType.GRAPH_END]
    assert len(graph_end) == 1
    meta = graph_end[0].metadata
    assert meta["policy"] == "exploration"
    assert meta["nodes_run"] == 2
    assert meta["nodes_skipped"] == 1
    assert meta["nodes_failed"] == 1

    # The degraded run is detectable from the trace alone, not len(events).
    assert len(result.facts) == 2  # both ok1 and ok2 ran despite the failure
