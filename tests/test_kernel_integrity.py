"""
Kernel integrity tests — pin the defects an adversarial review verified
empirically against this exact kernel: duplicated Event/EventType, trace
loss on STRICT failure, observer-inside-try misattribution, naive
timestamps, lost node identity, a silent EXPLORATION policy, an unstated
JSON contract, and the retry(0) landmine.

A SECOND adversarial review of this file's own fixes (below the "round 2"
marker) found five more MUST-FIX defects: FileTraceObserver path
traversal and silent trace loss, ConcurrentRunner dropping ERROR events
and duplicating GRAPH_START, and SSE/WebSocket frame mislabeling — plus
should-fix gaps in retry-exhaustion trace loss, node identity under
decoration, JSON round-trip vs. merely-not-raising, and naive-timestamp
normalization at the load boundary.

Each test below corresponds to one verified defect. If one of these goes
red, the kernel has regressed on "the trace survives failure."
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from axis.state import GraphState, Fact, Decision, Rejection
from axis.events import Event, EventType, now, Json, parse_timestamp
from axis.runner import Runner, NodeFailed
from axis.policy import Policy
from axis.persistence import FileTraceObserver
from axis.recovery.retry import retry
from axis.streaming.async_runner import AsyncRunner, ConcurrentRunner


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


# ===========================================================================
# ROUND 2 — a second adversarial review of the fixes above
# ===========================================================================

# ---------------------------------------------------------------------------
# 13. FileTraceObserver: path traversal via trace_id
# ---------------------------------------------------------------------------

def test_file_trace_observer_sanitizes_trace_id_for_filename():
    """GraphState.empty()/new() accept any string as trace_id. A trace_id
    of '../../escaped' must not let the observer write outside its
    directory — verified against the reviewer's exact reproduction."""
    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        evil_state = GraphState.empty("../../escaped-trace")

        observer.observe(EventType.GRAPH_END.value, evil_state)

        written = list(Path(d).iterdir())
        assert len(written) == 1
        assert written[0].parent.resolve() == Path(d).resolve()
        # Nothing escaped upward.
        assert not (Path(d).parent / "escaped-trace.json").exists()

        # The JSON *content* still carries the true, unflattened trace_id
        # — only the on-disk filename was sanitized.
        content = json.loads(written[0].read_text())
        assert content["trace_id"] == "../../escaped-trace"


def test_file_trace_observer_recreates_directory_per_write():
    """The directory may be removed after construction; the observer
    must self-heal on the next write instead of failing forever."""
    d = tempfile.mkdtemp()
    try:
        observer = FileTraceObserver(d)
        os.rmdir(d)
        assert not Path(d).exists()

        state = GraphState.new("recreate")
        observer.observe(EventType.GRAPH_END.value, state)

        assert Path(d).exists()
        assert list(Path(d).iterdir())
    finally:
        if Path(d).exists():
            for f in Path(d).iterdir():
                f.unlink()
            os.rmdir(d)


def test_file_trace_observer_writes_no_leftover_temp_file():
    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        state = GraphState.new("no-tmp-leftover")
        observer.observe(EventType.GRAPH_END.value, state)

        names = [p.name for p in Path(d).iterdir()]
        assert all(not n.endswith(".tmp") for n in names)


# ---------------------------------------------------------------------------
# 14. FileTraceObserver: critical=True re-raises instead of swallowing
# ---------------------------------------------------------------------------

def test_file_trace_observer_is_critical():
    assert FileTraceObserver.critical is True


def test_critical_observer_failure_propagates_from_runner():
    """A `critical` observer's exception must reach the caller instead of
    being logged and swallowed like a metrics observer's would be — the
    persistence observer IS the audit evidence; failing silently makes
    'the trace survives failure' unverifiable at runtime."""
    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        os.chmod(d, 0o500)  # read+execute only: the write will fail
        try:
            runner = Runner([lambda s: s], policy=Policy.STRICT)
            runner.attach(observer)

            with pytest.raises(Exception):
                runner.run(GraphState.new("perm-denied"))
        finally:
            os.chmod(d, 0o700)


def test_non_critical_observer_failure_is_still_swallowed():
    """A plain (non-critical) observer keeps the old isolation guarantee
    — this is the metrics/logging/tracing case, and must not regress."""
    class Raising:
        def observe(self, event_type, state, **kwargs):
            raise RuntimeError("not critical")

    runner = Runner([lambda s: s], policy=Policy.STRICT)
    runner.attach(Raising())

    result = runner.run(GraphState.new("noncritical"))  # must not raise
    assert result is not None


# ---------------------------------------------------------------------------
# 15. FileTraceObserver: writes on ERROR too, so a STRICT abort persists
# ---------------------------------------------------------------------------

def test_file_trace_observer_persists_strict_abort_via_error_event():
    """GRAPH_END is never written on a STRICT abort (see NodeFailed's
    docstring) — without also writing on ERROR, the one persistence
    primitive this kernel ships couldn't persist the one class of run
    'the trace survives failure' is about."""
    def boom(state: GraphState) -> GraphState:
        raise ValueError("abort")

    with tempfile.TemporaryDirectory() as d:
        observer = FileTraceObserver(d)
        runner = Runner([boom], policy=Policy.STRICT)
        runner.attach(observer)

        with pytest.raises(NodeFailed):
            runner.run(GraphState.new("strict-abort-persists"))

        written = list(Path(d).iterdir())
        assert len(written) == 1
        content = json.loads(written[0].read_text())
        types = [e["event_type"] for e in content["events"]]
        assert "error" in types
        assert "graph_end" not in types  # the asymmetry, persisted honestly


# ---------------------------------------------------------------------------
# 16. axis.persistence lazy imports: no httpx/psycopg2 required
# ---------------------------------------------------------------------------

def test_persistence_lazy_imports_work_without_optional_deps():
    """from axis.persistence import FileTraceObserver must not require
    httpx (QdrantAdapter) or psycopg2 (PostgreSQLAdapter) to be
    importable — verified in a subprocess with both blocked at the
    import-hook level, since both happen to be installed in this dev
    environment."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    script = textwrap.dedent(
        """
        import sys, importlib.abc

        class Blocker(importlib.abc.MetaPathFinder):
            blocked = {"httpx", "psycopg2"}
            def find_spec(self, name, path, target=None):
                if name.split(".")[0] in self.blocked:
                    raise ImportError(f"blocked for test: {name}")
                return None

        sys.meta_path.insert(0, Blocker())

        from axis.persistence import FileTraceObserver
        assert FileTraceObserver is not None

        try:
            from axis.persistence import QdrantAdapter
            print("UNEXPECTED", file=sys.stderr)
            sys.exit(1)
        except ImportError:
            pass

        try:
            from axis.persistence import PostgreSQLAdapter
            print("UNEXPECTED", file=sys.stderr)
            sys.exit(1)
        except ImportError:
            pass

        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# 17. ConcurrentRunner: true twin-ness (reviewer's exact reproductions)
# ---------------------------------------------------------------------------

def test_concurrent_runner_event_vocabulary_matches_runner():
    """For 2 healthy nodes, ConcurrentRunner must emit the same event
    TYPES as Runner (graph_start, then a node_started/node_completed pair
    per node, then graph_end) — no ordering guarantee between branches,
    but no duplicated GRAPH_START and no missing node-level events
    either. Before the fix: ['graph_start', 'graph_start', 'graph_start',
    'graph_end'] — zero node-level events and N+1 graph_start."""
    async def node_a(state: GraphState) -> GraphState:
        return state.with_fact(Fact("a", 1, "node_a", now()))

    async def node_b(state: GraphState) -> GraphState:
        return state.with_fact(Fact("b", 2, "node_b", now()))

    async def main():
        runner = ConcurrentRunner([node_a, node_b], policy=Policy.STRICT)
        return await runner.run(GraphState.new("concurrent-vocab"))

    result = asyncio.run(main())
    types = [e.event_type for e in result.events]

    assert types[0] == EventType.GRAPH_START
    assert types[-1] == EventType.GRAPH_END
    assert types.count(EventType.GRAPH_START) == 1
    assert types.count(EventType.NODE_STARTED) == 2
    assert types.count(EventType.NODE_COMPLETED) == 2
    assert set(types) == {
        EventType.GRAPH_START,
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
        EventType.GRAPH_END,
    }


def test_concurrent_runner_strict_failure_carries_error_and_notifies():
    """Reviewer's exact reproduction: ConcurrentRunner([ok, failing],
    STRICT).run(seed) must produce a NodeFailed whose .state contains an
    ERROR event, and observers must be notified beyond just GRAPH_START.
    Before the fix: NodeFailed.state events == ['graph_start',
    'graph_start'], no ERROR, observer saw only ['graph_start'] — a
    regression against main, which at least called
    bus.observe('ERROR', ...)."""
    async def ok_node(state: GraphState) -> GraphState:
        return state.with_fact(Fact("ok", True, "ok_node", now()))

    async def failing_node(state: GraphState) -> GraphState:
        raise ValueError("concurrent kaboom")

    class Recorder:
        def __init__(self):
            self.seen = []

        def observe(self, event_type, state, **kwargs):
            self.seen.append(event_type)

    async def main():
        runner = ConcurrentRunner([ok_node, failing_node], policy=Policy.STRICT)
        recorder = Recorder()
        runner.attach(recorder)
        try:
            await runner.run(GraphState.new("concurrent-strict-fail"))
            return None, recorder
        except NodeFailed as e:
            return e, recorder

    failure, recorder = asyncio.run(main())

    assert failure is not None
    types = [e.event_type for e in failure.state.events]
    assert EventType.ERROR in types
    assert EventType.NODE_STARTED in types

    assert EventType.ERROR.value in recorder.seen
    assert len(recorder.seen) > 1  # not just GRAPH_START


def test_concurrent_runner_does_not_duplicate_seeded_facts():
    """A fact already on the seed state must appear exactly once in the
    merged result, no matter how many concurrent branches ran — before
    the fix, N branches meant N copies (each branch's returned state
    already contained a copy of the seed, and _merge_states concatenated
    whole states instead of only each branch's new contribution)."""
    async def node_a(state: GraphState) -> GraphState:
        return state.with_fact(Fact("from_a", True, "node_a", now()))

    async def node_b(state: GraphState) -> GraphState:
        return state.with_fact(Fact("from_b", True, "node_b", now()))

    async def main():
        seed = GraphState.new("concurrent-seed").with_fact(
            Fact("seed", True, "seed", now())
        )
        runner = ConcurrentRunner([node_a, node_b], policy=Policy.STRICT)
        return await runner.run(seed)

    result = asyncio.run(main())

    seed_facts = [f for f in result.facts if f.key == "seed"]
    assert len(seed_facts) == 1
    assert len(result.facts) == 3  # seed + from_a + from_b, no duplication


# ---------------------------------------------------------------------------
# 18. Retry exhaustion carries its trace instead of losing it
# ---------------------------------------------------------------------------

def test_retry_exhaustion_preserves_node_retried_events():
    """Reviewer's exact reproduction: @retry(max_attempts=3) on a node
    that always fails, under EXPLORATION, must show node_retried,
    node_retried, error, node_skipped — not silently drop the two
    retries the operator paid the latency for."""
    class AlwaysFails:
        def __call__(self, state: GraphState) -> GraphState:
            raise ConnectionError("down")

    decorated = retry(max_attempts=3, initial_delay=0.01)(AlwaysFails())
    runner = Runner([decorated], policy=Policy.EXPLORATION)

    result = runner.run(GraphState.new("retry-exhaustion"))

    types = [e.event_type for e in result.events]
    retried = [e for e in result.events if e.event_type == EventType.NODE_RETRIED]

    assert len(retried) == 2
    assert types.index(EventType.NODE_RETRIED) < types.index(EventType.ERROR)
    assert types.index(EventType.ERROR) < types.index(EventType.NODE_SKIPPED)


# ---------------------------------------------------------------------------
# 19. Node identity resolves through decoration
# ---------------------------------------------------------------------------

def test_node_name_resolves_through_retry_for_closure_and_class():
    """A @retry-decorated closure and a @retry-wrapped class instance
    must both report their TRUE name on node_started and node_completed
    — not 'wrapper' (functools.wraps sets __wrapped__ unconditionally
    even when it can't copy __name__ from a nameless class instance)."""
    def my_closure(state: GraphState) -> GraphState:
        return state
    my_closure.__name__ = "my_closure"

    class MyNode:
        def __call__(self, state: GraphState) -> GraphState:
            return state

    decorated_closure = retry(max_attempts=1)(my_closure)
    decorated_class = retry(max_attempts=1)(MyNode())

    runner = Runner([decorated_closure, decorated_class], policy=Policy.STRICT)
    result = runner.run(GraphState.new("names-through-retry"))

    started = [e.node_name for e in result.events if e.event_type == EventType.NODE_STARTED]
    completed = [e.node_name for e in result.events if e.event_type == EventType.NODE_COMPLETED]

    assert started == ["my_closure", "MyNode"]
    assert completed == ["my_closure", "MyNode"]
    assert "wrapper" not in started
    assert "wrapper" not in completed


# ---------------------------------------------------------------------------
# 20. JSON contract: round-trips, not just "doesn't raise"
# ---------------------------------------------------------------------------

def test_to_dict_whole_result_probe_catches_bad_decision_field():
    """The field probes only cover Fact.value and Event.metadata —
    Decision.description and Rejection fields are typed str but nothing
    enforces it at runtime. The whole-result json.dumps probe is the
    catch-all: an object() in a Decision must fail at to_dict() with a
    pointed message, not survive to an anonymous json.dumps failure at
    persist time."""
    state = GraphState.new("bad-decision").with_decision(
        Decision(description=object(), timestamp=now())
    )

    with pytest.raises(TypeError) as excinfo:
        state.to_dict()

    assert "GraphState" in str(excinfo.value)


def test_to_dict_whole_result_probe_catches_bad_rejection_field():
    state = GraphState.new("bad-rejection").with_rejection(
        Rejection(description="x", reason=object(), timestamp=now())
    )

    with pytest.raises(TypeError):
        state.to_dict()


def test_json_serializable_does_not_imply_round_trip_equal():
    """Documents the real contract (see Json's docstring): a tuple
    passes json.dumps unchanged but does NOT round-trip equal — the
    probe (and to_dict() itself) only guards against values json.dumps
    rejects outright, not silent structural mutation. to_dict() returns
    a plain nested structure (the tuple survives THAT boundary intact);
    it's the actual JSON text boundary — to_json()/from_json(), or
    json.dumps/json.loads directly — where a tuple becomes a list."""
    state = GraphState.new("tuple-value").with_fact(
        Fact("coords", (1.5, 2.5), "node", now())
    )

    state.to_dict()  # does not raise — tuples ARE json.dumps-able
    json.dumps(state.to_dict())  # ditto, the whole-result probe's own check

    reloaded = GraphState.from_json(state.to_json())  # the real JSON text boundary
    assert reloaded != state  # (1.5, 2.5) became [1.5, 2.5]
    assert reloaded.facts[0].value == [1.5, 2.5]


# ---------------------------------------------------------------------------
# 21. from_dict normalizes naive timestamps to UTC
# ---------------------------------------------------------------------------

def test_from_dict_normalizes_naive_timestamps_and_sorts_cleanly():
    """Load a literal old-format trace with naive timestamps (as all 83
    real terraveler traces on disk have), then merge it with a fresh
    tz-aware event and sort — must not raise "can't compare offset-naive
    and offset-aware datetimes", the exact failure 'one clock' claimed to
    have fixed but only fixed at the write boundary, not at load."""
    old_format = {
        "trace_id": "legacy-naive",
        "intent": None,
        "facts": [],
        "decisions": [],
        "rejections": [],
        "events": [
            {
                "event_type": "node_started",
                "description": "Node node_0 started",
                "timestamp": "2024-01-01T00:00:00",  # naive
                "metadata": None,
                "node_name": None,
            },
            {
                "event_type": "node_completed",
                "description": "Node node_0 completed",
                "timestamp": "2024-01-01T00:00:00.500000",  # naive
                "metadata": None,
                "node_name": None,
            },
        ],
    }

    loaded = GraphState.from_dict(old_format)
    assert all(e.timestamp.tzinfo is not None for e in loaded.events)

    fresh_event = Event(EventType.GRAPH_END, "fresh", now())
    merged = list(loaded.events) + [fresh_event]

    sorted(merged, key=lambda e: e.timestamp)  # must not raise TypeError


def test_parse_timestamp_preserves_aware_and_normalizes_naive():
    naive = parse_timestamp("2024-06-01T12:00:00")
    assert naive.tzinfo == timezone.utc

    aware = parse_timestamp("2024-06-01T12:00:00+02:00")
    assert aware.tzinfo is not None
    assert aware.utcoffset().total_seconds() == 2 * 3600
