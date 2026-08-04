"""The inherited Axis 0.4.0 conformance corpus — guarantees.md §5.

Seven behaviors, each earned through adversarial review of the predecessor
kernel and each carried forward without weakening.  These are not the
implementation's unit tests (``tests/test_kernel_integrity.py`` keeps those, at
finer grain): they are the CONTRACT's statement of what any conforming runtime
must do, phrased so that they hold for Axis today and for Motus tomorrow
through the compatibility view.

**Frozen.** The implementing agent may not edit this file.  When the runtime
moves, ``tests/contract/kernel.py`` is the single line that changes; if a
behavior below cannot be satisfied, that is an amendment with its own ADR, not
an edit here.

Each test names the guarantee it descends from, and — where the behavior was
earned rather than designed — what went wrong before it existed.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from tests.contract.kernel import (
    ConcurrentRunner,
    Decision,
    Fact,
    FileTraceObserver,
    GraphState,
    NodeFailed,
    Policy,
    Rejection,
    Runner,
    retry,
)


def _now():
    """A timestamp of the shape the kernel's own records carry."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _fact(key, value, source="test"):
    return Fact(key, value, source, _now())


# --------------------------------------------------------------------------- #
# 1. State and trace survive node failure                                     #
# --------------------------------------------------------------------------- #


def test_strict_failure_carries_the_accumulated_trace():
    """§5.1 — a failed run must still hand back what it had established.

    Terraveler relies on this at three production sites: a STRICT run that
    dies mid-pipeline still writes its evidence to `ingestion_runs` and
    `chat_traces`.  Without it a failure erases its own investigation, which
    is the opposite of a trace-first runtime.
    """

    def establishes(state):
        return state.with_fact(_fact("established", "before the fall"))

    def fails(state):
        raise RuntimeError("the node raised")

    runner = Runner([establishes, fails], policy=Policy.STRICT)

    with pytest.raises(NodeFailed) as caught:
        runner.run(GraphState.new("conformance-failure"))

    failure = caught.value
    assert failure.state is not None, "NodeFailed must carry the state"
    assert failure.state.fact("established") == "before the fall"
    assert failure.state.events, "the lifecycle up to the failure must survive"
    assert any(
        "error" in str(getattr(event, "event_type", "")).lower()
        for event in failure.state.events
    ), "the failure itself must be on the trace, not merely raised"


def test_the_failed_state_still_serializes():
    """§5.1, second half — salvage is worthless if it cannot be persisted.

    Terraveler serializes the salvaged state into jsonb; a trace that survives
    in memory but cannot be written down has not survived.
    """

    def fails(state):
        raise RuntimeError("boom")

    with pytest.raises(NodeFailed) as caught:
        Runner([fails], policy=Policy.STRICT).run(GraphState.new("conformance"))

    document = caught.value.state.to_dict()
    assert json.loads(json.dumps(document))["trace_id"].startswith("conformance-")


# --------------------------------------------------------------------------- #
# 2. Retry exhaustion loses no trace; every attempt is recorded               #
# --------------------------------------------------------------------------- #


def test_retry_exhaustion_preserves_every_attempt():
    """§5.2 — retries are never hidden, and exhaustion keeps the evidence.

    A runtime that swallows the intermediate attempts reports a single
    failure where three things happened, and the operator cannot tell a flaky
    dependency from a broken one.
    """
    attempts = []

    @retry(max_attempts=3, initial_delay=0)
    def always_fails(state):
        attempts.append(1)
        raise RuntimeError("still failing")

    with pytest.raises(NodeFailed) as caught:
        Runner([always_fails], policy=Policy.STRICT).run(GraphState.new("retries"))

    assert len(attempts) == 3, "the configured attempts must actually happen"
    assert caught.value.state is not None, "exhaustion must not drop the trace"
    retried = [
        event
        for event in caught.value.state.events
        if "retr" in str(getattr(event, "event_type", "")).lower()
    ]
    assert retried, "each retry must appear in the trace, not only in the logs"


# --------------------------------------------------------------------------- #
# 3. A critical sink can abort the run; a listener never can                  #
# --------------------------------------------------------------------------- #


class _Exploding:
    """An observer that always raises.  ``critical`` decides what that means."""

    def __init__(self, critical: bool):
        self.critical = critical
        self.calls = 0

    def observe(self, event_type, state, **kwargs):
        self.calls += 1
        raise RuntimeError("observer exploded")


def test_a_critical_sink_failure_aborts_the_run():
    """§5.3, first half — the durable surface may stop execution.

    This is invariant II in miniature: if the record cannot be written, the
    run has no right to call itself successful.
    """
    sink = _Exploding(critical=True)
    runner = Runner([lambda state: state], policy=Policy.STRICT)
    runner.attach(sink)

    with pytest.raises(RuntimeError, match="observer exploded"):
        runner.run(GraphState.new("critical-sink"))
    assert sink.calls >= 1


def test_a_non_critical_listener_can_never_affect_the_run():
    """§5.3, second half — the live surface is structurally harmless.

    A listener that throws must not take the graph with it; observation that
    can break execution is not observation.
    """
    noisy = _Exploding(critical=False)
    runner = Runner([lambda state: state.with_fact(_fact("ran", True))],
                    policy=Policy.STRICT)
    runner.attach(noisy)

    final = runner.run(GraphState.new("noisy-listener"))

    assert final.fact("ran") is True, "the run completed despite the listener"
    assert noisy.calls >= 1, "the listener was in fact called"


# --------------------------------------------------------------------------- #
# 4. The file sink stays inside its directory                                 #
# --------------------------------------------------------------------------- #


def test_file_sink_cannot_be_steered_outside_its_directory(tmp_path):
    """§5.4 — a trace id is untrusted input; it must not become a path.

    Found adversarially in the predecessor: a run whose id contained
    ``../..`` wrote its trace outside the directory the operator chose.  A
    trace-first runtime that can be aimed at arbitrary paths by naming a run
    is a file-write primitive wearing an audit trail's clothes.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    traces = tmp_path / "traces"

    runner = Runner([lambda state: state], policy=Policy.STRICT)
    runner.attach(FileTraceObserver(str(traces)))
    runner.run(GraphState.empty("../../escaped-trace"))

    assert not list(outside.iterdir()), "nothing may be written outside"
    written = list(traces.iterdir())
    assert written, "the trace must still be written, under a safe name"
    for path in written:
        # Containment is the guarantee, and it is checked on the resolved
        # path — a literal '..' surviving INSIDE a filename is harmless once
        # it can no longer act as a path segment.
        assert path.resolve().parent == traces.resolve(), (
            f"{path} escaped the directory the operator chose"
        )
        assert re.fullmatch(r"[A-Za-z0-9._-]+", path.name), (
            f"unsafe characters survived into the filename: {path.name!r}"
        )
        assert "/" not in path.name and "\\" not in path.name


def test_file_sink_writes_when_a_strict_run_aborts(tmp_path):
    """§5.4, companion — the failure path is the one that matters most.

    A STRICT abort never reaches the end of the graph, so a sink that only
    writes on completion loses exactly the traces someone will come looking
    for.
    """
    traces = tmp_path / "traces"

    def fails(state):
        raise RuntimeError("boom")

    runner = Runner([fails], policy=Policy.STRICT)
    runner.attach(FileTraceObserver(str(traces)))
    with pytest.raises(NodeFailed):
        runner.run(GraphState.empty("aborted-run"))

    written = list(traces.iterdir())
    assert written, "an aborted run must leave its trace on disk"
    document = json.loads(written[0].read_text())
    assert document["trace_id"] == "aborted-run"


# --------------------------------------------------------------------------- #
# 5. Concurrent merge semantics (compatibility view)                          #
# --------------------------------------------------------------------------- #


def test_concurrent_branches_all_run_and_merge_in_declared_order():
    """§5.5 — the proven merge: every branch runs, contributions concatenate.

    Scoped to the compatibility view by decision: fan-out has no
    representation in GraphSpec v1 or trace schema v1, deliberately deferred
    until a consumer needs declared concurrent topology.  What must not
    change is what the predecessor already guaranteed.
    """
    def branch(name):
        def node(state):
            return state.with_fact(_fact(f"branch_{name}", name))

        node.__name__ = f"branch_{name}"
        return node

    runner = ConcurrentRunner([branch("a"), branch("b"), branch("c")],
                              policy=Policy.STRICT)
    final = asyncio.run(runner.run(GraphState.new("concurrent")))

    for name in ("a", "b", "c"):
        assert final.fact(f"branch_{name}") == name, "every branch must run"

    keys = [fact.key for fact in final.facts]
    assert keys == ["branch_a", "branch_b", "branch_c"], (
        "the merge is in DECLARED order, not completion order — a trace whose "
        "ordering depends on the scheduler is not reproducible evidence"
    )


def test_concurrent_seed_facts_are_not_duplicated_by_the_merge():
    """§5.5, companion — merging N branches must not multiply the seed.

    Each branch starts from the same state; a merge that concatenates whole
    states instead of contributions reports the seed N times and inflates
    every count downstream.
    """
    seeded = GraphState.new("concurrent-seed").with_fact(_fact("seed", 1))
    final = asyncio.run(
        ConcurrentRunner([lambda s: s, lambda s: s], policy=Policy.STRICT).run(seeded)
    )

    assert [fact.key for fact in final.facts].count("seed") == 1


# --------------------------------------------------------------------------- #
# 6. STRICT and EXPLORATION keep their contract-defined divergence            #
# --------------------------------------------------------------------------- #


def test_strict_stops_and_exploration_continues_past_the_same_failure():
    """§5.6 — the two policies differ in exactly one way, and it is recorded.

    STRICT fails the run; EXPLORATION records the error and carries on.  A
    runtime where the difference is a log level rather than a trace record
    cannot answer "did this run skip anything?".
    """
    def fails(state):
        raise RuntimeError("boom")

    def afterwards(state):
        return state.with_fact(_fact("reached", True))

    with pytest.raises(NodeFailed):
        Runner([fails, afterwards], policy=Policy.STRICT).run(GraphState.new("strict"))

    explored = Runner([fails, afterwards], policy=Policy.EXPLORATION).run(
        GraphState.new("exploration")
    )
    assert explored.fact("reached") is True, "exploration continues past failure"
    kinds = [str(getattr(event, "event_type", "")).lower() for event in explored.events]
    assert any("error" in kind for kind in kinds), "the failure is on the record"
    assert any("skip" in kind for kind in kinds), "and so is the skip it caused"


# --------------------------------------------------------------------------- #
# 7. Old traces load, with documented defaults                                #
# --------------------------------------------------------------------------- #


def test_a_trace_written_before_a_field_existed_still_loads():
    """§5.7 — the format's own history must remain readable.

    Terraveler's stored traces predate later optional fields; a runtime that
    can only read what it currently writes has orphaned its own archive.  The
    real production artifact is exercised in
    ``tests/compat/terraveler/`` — this is the minimal statement of the rule.
    """
    old = {
        "trace_id": "written-before",
        "intent": "an older run",
        "facts": [],
        "decisions": [],
        "rejections": [],
        "events": [],
    }

    state = GraphState.from_dict(old)

    assert state.trace_id == "written-before"
    assert state.intent == "an older run"
    assert state.facts == () and state.decisions == ()
    assert state.rejections == () and state.events == ()
