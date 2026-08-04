"""Reproductions for the Motus 0.6 adversarial-audit findings.

Every test here asserts the behaviour the contract or the published claim
requires. They therefore FAIL against the audited implementation
(2a860a7c5c57f95dd471ccc1ff66bcf7bd10c7f6) and are the executable form of
audit/MOTUS-0.6-ADVERSARIAL-REPORT.md.

They are deliberately deterministic and bounded: nothing here sleeps, races,
or depends on wall-clock timing, so a failure is a defect and never noise.
No runtime file, contract file, frozen corpus, benchmark or existing test was
modified to produce them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest import mock

import pytest

from vitruvyan_motus import (
    Decision,
    DurabilityProfile,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    Policy,
    Rejection,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
TERMINALS = ("run_completed", "run_failed", "run_cancelled")


def _identity(state: State) -> State:
    return state


# --------------------------------------------------------------------------- #
# MOTUS-ADV-001 — a cyclic run whose attempts never commit is unbounded        #
# --------------------------------------------------------------------------- #

CYCLE = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "adv-cycle",
        "version": "1.0.0",
        "entry": "a",
        "max_transitions": 4,
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
            {"name": "z", "effect_class": "pure"},
        ],
        "transitions": {
            "a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
            "b": {"kind": "next", "to": "a"},
            "z": {"kind": "terminal"},
        },
    }
)
SEEDED_LOOP = State.new("adv-001", decisions=[Decision("k", "loop", NOW)])


def _always_raises(state: State) -> State:
    raise RuntimeError("this node is down")


def test_adv_001_exploration_cycle_reaches_a_terminal_within_the_declared_limit():
    """R11 makes max_transitions mandatory for a cyclic spec because it is the
    safety limit for that cycle. Under EXPLORATION a raised attempt takes
    disposition 'continue' and routes onward, but runtime.py only increments
    ``committed_transitions`` when an attempt RETURNS, so a cycle in which
    nothing ever commits is never bounded and never reaches a terminal.

    Bounded through ``stream()`` so the reproduction cannot hang the suite.
    """
    runtime = Runtime(
        CYCLE,
        {"a": _always_raises, "b": _always_raises, "z": _always_raises},
        policy=Policy.EXPLORATION,
    )
    budget = 200  # >> 4 transitions x 3 records + terminal
    kinds: list[str] = []
    with runtime.stream(SEEDED_LOOP) as driver:
        for record in driver:
            kinds.append(record["kind"])
            if record["kind"] in TERMINALS:
                break
            if len(kinds) > budget:
                break

    assert any(kind in TERMINALS for kind in kinds), (
        "a cyclic run under max_transitions=4 emitted "
        f"{len(kinds)} records without ever reaching a terminal outcome"
    )


def test_adv_001b_control_committing_cycle_is_bounded():
    """The same graph with committing nodes IS bounded — isolating the cause."""
    runtime = Runtime(
        CYCLE, {"a": _identity, "b": _identity, "z": _identity}, policy=Policy.EXPLORATION
    )
    result = runtime.run(SEEDED_LOOP)
    assert result.status == "failed"
    assert result.trace.records[-1]["cause"]["kind"] == "transition_limit_exceeded"


# --------------------------------------------------------------------------- #
# MOTUS-ADV-002 — an evidence-free Rejection poisons the trace                 #
# --------------------------------------------------------------------------- #

SINGLE = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "adv-rejection",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}},
    }
)


def _declines(state: State) -> State:
    # node-protocol section 2.3: "A node that declines to act SHOULD say so".
    return state.with_rejection(Rejection("the thing", "not applicable", NOW))


def test_adv_002_rejection_without_evidence_keeps_the_trace_serializable():
    """node-protocol 2.2: every written value MUST be a strict RFC 8259 JSON
    value, and "the runtime never carries what it cannot record".

    ``Rejection.evidence`` defaults to a module-level ``object()`` sentinel
    whose identity does not survive ``copy.deepcopy``; the copy stored by
    ``State.with_rejection`` therefore serialises the sentinel object itself.
    """
    result = Runtime(SINGLE, {"a": _declines}).run(State.empty("adv-002"))
    assert result.succeeded  # the run reports success ...

    transition = next(r for r in result.trace.records if r["kind"] == "transition")
    written = transition["writes"]["rejections"][0]
    assert "evidence" not in written or isinstance(
        written.get("evidence"), (str, int, float, bool, list, dict, type(None))
    ), f"trace carries a non-JSON {type(written.get('evidence')).__name__} in rejection evidence"

    # ... and its evidence must be persistable, validatable and replayable.
    json.loads(result.trace.to_json())
    result.trace.to_jsonl()
    TraceBundle(SINGLE, result.trace)


def test_adv_002b_seeded_rejection_without_evidence_keeps_the_trace_serializable():
    """Same defect through State.new()'s initial-state path."""
    seeded = State.new("adv-002b", rejections=[Rejection("w", "r", NOW)])
    result = Runtime(SINGLE, {"a": _identity}).run(seeded)
    json.loads(result.trace.to_json())


def test_adv_002c_rejection_with_evidence_is_the_working_control():
    """The control: supplying evidence explicitly produces valid evidence."""

    def declines_with_evidence(state: State) -> State:
        return state.with_rejection(Rejection("w", "r", NOW, evidence={"n": 1}))

    result = Runtime(SINGLE, {"a": declines_with_evidence}).run(State.empty("adv-002c"))
    assert json.loads(result.trace.to_json())["records"]


# --------------------------------------------------------------------------- #
# MOTUS-ADV-003 — the published serialization ratio measures a cache hit       #
# --------------------------------------------------------------------------- #

BENCH = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "adv-bench",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure", "writes_declared": ["a"]}],
        "transitions": {"a": {"kind": "terminal"}},
    }
)


def test_adv_003_repeated_to_dict_actually_re_prepares_the_document():
    """benchmarks/bench_motus.py times ``result.trace.to_dict`` with 2 warmups
    and 7 measured samples on ONE Trace. ``Trace.to_json`` memoises its output
    and ``to_dict`` is ``json.loads(self.to_json())``, so every measured sample
    times a C-level parse of an already-built string.

    guarantees.md section 3 publishes the resulting quotient as "Motus trace
    preparation / json.dumps = 0.8x" and README.md as "0.788x trace preparation
    versus json.dumps"; the CI gate recomputes the same quotient. This test
    pins the methodology defect deterministically: it counts how many times the
    JSON encoder actually runs across the benchmark's own call pattern.
    """
    result = Runtime(BENCH, {"a": lambda s: s.with_fact(Fact("a", 1, "b", NOW))}).run(
        State.empty(), run_id="adv-003"
    )
    trace = result.trace

    import vitruvyan_motus.trace as trace_module

    real_dumps = trace_module.json.dumps
    calls = []

    def counting_dumps(*args, **kwargs):
        calls.append(1)
        return real_dumps(*args, **kwargs)

    with mock.patch.object(trace_module.json, "dumps", counting_dumps):
        for _ in range(2 + 7):  # bench_motus.py: WARMUPS + REPEATS
            trace.to_dict()

    assert len(calls) == 9, (
        "9 to_dict() calls invoked the JSON encoder "
        f"{len(calls)} time(s): the benchmark's 7 measured samples time a "
        "cached parse, not trace preparation"
    )


# --------------------------------------------------------------------------- #
# MOTUS-ADV-004 — a single state read scans the whole committed log            #
# --------------------------------------------------------------------------- #


class _CountingKey(str):
    """A str that counts how many equality comparisons it takes part in."""

    comparisons = 0

    def __eq__(self, other):  # noqa: D105
        type(self).comparisons += 1
        return str.__eq__(self, other)

    def __ne__(self, other):  # noqa: D105
        return not self.__eq__(other)

    def __hash__(self):  # noqa: D105
        return str.__hash__(self)


def _lookup_cost(size: int, key: str) -> int:
    state = State.new(
        "adv-004", facts=[Fact(f"k{index}", index, "s", NOW) for index in range(size)]
    )
    _CountingKey.comparisons = 0
    state.fact(_CountingKey(key))
    return _CountingKey.comparisons


def test_adv_004_one_state_read_does_not_grow_with_the_committed_log():
    """State._items rebuilds a filtered list over the entire committed log for
    every fact()/decision() lookup, so a run of n nodes that each read once
    costs O(n^2). The benchmark that backs "no positive superlinear term in the
    measured profile" uses a write-only node and never exercises this path,
    even though record-and-compare reads are node-protocol section 3's core.

    Comparisons are counted for the oldest key and for an absent key — the two
    cases a scanning lookup must walk end to end. (A newest-key hit short-
    circuits the comparison loop but still pays the O(n) ``_items()`` rebuild,
    which this counter cannot see; the growth below is the lower bound.)
    """
    small_oldest, large_oldest = _lookup_cost(64, "k0"), _lookup_cost(512, "k0")
    small_absent, large_absent = _lookup_cost(64, "absent"), _lookup_cost(512, "absent")

    assert large_oldest <= 2 * small_oldest, (
        f"an 8x larger log made one oldest-key read cost {large_oldest} comparisons "
        f"instead of {small_oldest}: the read path is linear in the committed log"
    )
    assert large_absent <= 2 * small_absent, (
        f"an 8x larger log made one absent-key read cost {large_absent} comparisons "
        f"instead of {small_absent}"
    )


# --------------------------------------------------------------------------- #
# MOTUS-ADV-005 — a defaulted second positional parameter is bound to ctx      #
# --------------------------------------------------------------------------- #


def test_adv_005_defaulted_second_parameter_is_not_treated_as_run_context():
    """node-protocol 1.1 defines exactly two node shapes, ``node(state)`` and
    ``node(state, ctx)``. ``_accepts_context`` classifies on positional arity
    alone, so the common closure-by-default-argument idiom
    ``def node(state, cache={})`` silently receives the RunContext as ``cache``.
    """
    seen: dict[str, object] = {}
    default = {"call_count": 0}

    def node(state, cache=default):
        seen["got"] = type(cache).__name__
        return state

    Runtime(SINGLE, {"a": node}).run(State.empty("adv-005"))
    assert seen.get("got") == "dict", (
        f"the node's defaulted parameter was bound to a {seen.get('got')}"
    )


# --------------------------------------------------------------------------- #
# MOTUS-ADV-006 — code_fingerprint is captured once per Runtime, not per run   #
# --------------------------------------------------------------------------- #


class _ConfiguredNode:
    def __init__(self, value):
        self.value = value

    def motus_config(self):
        return {"value": self.value}

    def __call__(self, state):
        return state.with_fact(Fact("v", self.value, "s", NOW))


def test_adv_006_code_fingerprint_tracks_the_configuration_that_actually_ran():
    """node-protocol 6.3 binds config_fingerprint to "the callable's
    configuration". Runtime computes it once in __init__; a Runtime reused for
    a second run after the node's configuration changed attests the old
    identity for behaviour that is demonstrably different.
    """
    node = _ConfiguredNode(1)
    runtime = Runtime(SINGLE, {"a": node})
    first = runtime.run(State.empty("adv-006a"))
    node.value = 999
    second = runtime.run(State.empty("adv-006b"))

    assert first.state.fact("v") != second.state.fact("v")  # behaviour did change
    assert (
        first.trace.run["graph"]["code_fingerprint"]
        != second.trace.run["graph"]["code_fingerprint"]
    ), "two runs with different node configuration share one code_fingerprint"


# --------------------------------------------------------------------------- #
# MOTUS-ADV-007 — a TraceSink is silently discarded under the in-memory profile #
# --------------------------------------------------------------------------- #


def test_adv_007_attaching_a_sink_to_an_in_memory_run_is_not_silent():
    """_ObservationHub refuses ``buffered``/``synchronous`` without a sink but
    accepts ``in-memory`` WITH one and then never opens the run or writes a
    record. An operator who attached durable evidence and left the default
    profile gets no persistence and no diagnostic.
    """
    sink = InMemoryTraceSink()
    Runtime(
        SINGLE,
        {"a": _identity},
        durability_profile=DurabilityProfile.IN_MEMORY,
        sink=sink,
    ).run(State.empty("adv-007"))
    assert sink.runs, "the attached TraceSink never had open_run() called on it"


# --------------------------------------------------------------------------- #
# MOTUS-ADV-008 — a Listener can gate execution                                #
# --------------------------------------------------------------------------- #


def test_adv_008_listener_cannot_affect_execution():
    """guarantees.md section 6: on the Listener surface "nothing a listener
    does can affect execution, by construction rather than by convention".
    Delivery is synchronous on the runner thread and Runtime.cancel() is a
    public method, so a listener that holds the Runtime terminates the run
    before the entry node is ever invoked.
    """
    box: list[Runtime] = []
    executed: list[str] = []

    class CancellingListener:
        def on_record(self, record):
            if record["kind"] == "run_started":
                box[0].cancel("cancelled from a listener")

    def entry(state: State) -> State:
        executed.append("a")
        return state

    runtime = Runtime(SINGLE, {"a": entry}, listeners=(CancellingListener(),))
    box.append(runtime)
    result = runtime.run(State.empty("adv-008"))

    assert result.status != "cancelled", "a Listener cancelled the run"
    assert executed == ["a"], "a Listener prevented the entry node from executing"


# --------------------------------------------------------------------------- #
# MOTUS-ADV-009 — cancel() requested before run() is silently discarded        #
# --------------------------------------------------------------------------- #


def test_adv_009_cancellation_requested_before_the_run_starts_is_honoured():
    """``Runtime.cancel`` is public and carries no documented ordering
    constraint, but ``Runtime._start`` unconditionally resets
    ``self._cancel_reason = None`` before the first record. A caller that
    cancels between constructing the Runtime and calling run() therefore gets a
    complete execution — every node, and every external effect it performs —
    with no cancellation anywhere in the evidence.
    """
    executed: list[str] = []

    def entry(state: State) -> State:
        executed.append("a")
        return state

    runtime = Runtime(SINGLE, {"a": entry})
    runtime.cancel("shutdown signal arrived before the run started")
    result = runtime.run(State.empty("adv-009"))

    assert executed == [], "the cancelled run executed its node anyway"
    assert result.status == "cancelled", (
        f"a run cancelled before it started reported {result.status!r}"
    )
