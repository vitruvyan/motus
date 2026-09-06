from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus.context import ReplayStatus
from vitruvyan_motus.errors import NodeConfigurationError, NodeFailed, SinkFailed
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.observers import InMemoryTraceSink
from vitruvyan_motus.runtime import (
    DurabilityProfile, Policy, Runtime, _config_fingerprint, _config_material,
)
from vitruvyan_motus.state import State
from vitruvyan_motus.trace import Decision, Fact, Rejection, Trace, redact


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _validator():
    name = "motus_runtime_contract_validate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "contract" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = _validator()


def _spec(nodes, transitions, *, entry=None, max_transitions=None):
    document = {
        "schema_version": "1.0.0",
        "name": "runtime-test",
        "version": "1.0.0",
        "entry": entry or nodes[0]["name"],
        "nodes": nodes,
        "transitions": transitions,
    }
    if max_transitions is not None:
        document["max_transitions"] = max_transitions
    return GraphSpec.from_dict(document)


def _assert_clean(result, spec):
    violations = validate.validate_trace(result.trace.to_dict(), spec=spec.to_dict())
    assert violations == [], "\n".join(map(str, violations))


def establish(state):
    return state.with_fact(Fact("established", True, "test", NOW))


def finish(state):
    assert state.fact("established") is True
    return state


def choose_yes(state):
    return state.with_decision(Decision("path", "yes", NOW))


def choose_other(state):
    return state.with_decision(Decision("path", "other", NOW))


def choose_number(state):
    return state.with_decision(Decision("path", 7, NOW))


def unchanged(state):
    return state


def test_linear_run_is_validator_clean_and_captures_reads_writes():
    spec = _spec(
        [
            {"name": "establish", "effect_class": "pure", "writes_declared": ["established"]},
            {"name": "finish", "effect_class": "pure", "reads_declared": ["established"]},
        ],
        {"establish": {"kind": "next", "to": "finish"}, "finish": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"establish": establish, "finish": finish}).run(
        State.empty("linear"), run_id="linear", replay=ReplayStatus.declared("full")
    )
    assert result.state.fact("established") is True
    _assert_clean(result, spec)


@pytest.mark.parametrize(
    ("chooser", "policy", "expected_terminal", "expected_outcome"),
    [
        (choose_yes, Policy.STRICT, "run_completed", "matched"),
        (choose_other, Policy.STRICT, "run_completed", "default"),
        (choose_number, Policy.STRICT, "run_failed", "miss"),
        (choose_number, Policy.EXPLORATION, "run_completed", "miss"),
    ],
)
def test_routing_outcomes_are_causal_and_validator_clean(
    chooser, policy, expected_terminal, expected_outcome
):
    route = {"kind": "route", "on": "path", "map": {"yes": "done"}, "default": "done"}
    spec = _spec(
        [{"name": "choose", "effect_class": "pure"}, {"name": "done", "effect_class": "pure"}],
        {"choose": route, "done": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"choose": chooser, "done": unchanged}, policy=policy).run(
        State.empty("route"), run_id=f"route-{expected_outcome}"
    )
    records = result.trace.records
    routing = next(record for record in records if record["kind"] == "routing")
    assert routing["outcome"] == expected_outcome
    assert records[-1]["kind"] == expected_terminal
    _assert_clean(result, spec)


def test_retry_records_every_attempt_then_commits():
    calls = []

    class Flaky:
        def motus_config(self):
            return {"failures": 2}

        def __call__(self, state):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("again")
            return state.with_fact(Fact("ok", True, "test", NOW))

    spec = _spec(
        [{"name": "flaky", "effect_class": "external_effect"}],
        {"flaky": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"flaky": Flaky()}, max_attempts=3).run(run_id="retry")
    attempts = [record for record in result.trace.records if record["kind"] == "attempt_started"]
    assert [record["attempt"] for record in attempts] == [1, 2, 3]
    _assert_clean(result, spec)


def test_strict_node_failure_preserves_trace_and_state():
    def fails(state):
        assert state.intent == "failure"
        raise RuntimeError("boom")

    spec = _spec([{"name": "fails"}], {"fails": {"kind": "terminal"}})
    runtime = Runtime(spec, {"fails": fails})
    with pytest.raises(NodeFailed) as caught:
        runtime.run(State.empty("failure"), run_id="failure")
    assert caught.value.state is not None
    assert caught.value.trace.records[-1]["kind"] == "run_failed"
    violations = validate.validate_trace(caught.value.trace.to_dict(), spec=spec.to_dict())
    assert violations == []


def test_exploration_continues_past_failure_with_empty_transactional_writes():
    def fails(state):
        state.with_fact(Fact("discarded", True, "test", NOW))
        raise RuntimeError("degraded")

    spec = _spec(
        [{"name": "fails"}, {"name": "done"}],
        {"fails": {"kind": "next", "to": "done"}, "done": {"kind": "terminal"}},
    )
    result = Runtime(
        spec, {"fails": fails, "done": unchanged}, policy=Policy.EXPLORATION
    ).run(run_id="explore")
    failed = next(
        record for record in result.trace.records
        if record["kind"] == "transition" and record["node"] == "fails"
    )
    assert failed["outcome"] == "raised" and failed["disposition"] == "continue"
    assert failed["writes"] == {"facts": [], "decisions": [], "rejections": []}
    assert result.state.fact("discarded") is None
    _assert_clean(result, spec)


def test_foreign_returned_state_is_a_recorded_protocol_failure():
    def foreign(state):
        return State.empty("not-derived")

    spec = _spec([{"name": "foreign"}], {"foreign": {"kind": "terminal"}})
    with pytest.raises(NodeFailed) as caught:
        Runtime(spec, {"foreign": foreign}).run(run_id="foreign")
    transition = caught.value.trace.records[-2]
    assert transition["error"]["type"] == "ValueError"
    assert transition["writes"] == {"facts": [], "decisions": [], "rejections": []}
    assert validate.validate_trace(
        caught.value.trace.to_dict(), spec=spec.to_dict()
    ) == []


def test_fully_undeclared_node_records_null_violations_and_keeps_root():
    def untouched(state):
        return state

    spec = _spec([{"name": "untouched"}], {"untouched": {"kind": "terminal"}})
    result = Runtime(spec, {"untouched": untouched}).run(run_id="undeclared")
    transition = next(r for r in result.trace.records if r["kind"] == "transition")
    assert transition["violations"] is None
    assert result.trace.root is not None
    assert validate.validate_trace(result.trace.to_dict(), spec=spec.to_dict()) == []


def test_current_trace_null_and_empty_violations_round_trip_across_all_readers():
    def untouched(state):
        return state

    spec = _spec(
        [
            {"name": "untouched"},
            {"name": "clean", "reads_declared": [], "writes_declared": []},
        ],
        {"untouched": {"kind": "next", "to": "clean"}, "clean": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"untouched": untouched, "clean": untouched}).run(
        run_id="current-null-and-empty"
    )
    trace = result.trace
    transitions = [record for record in trace.records if record["kind"] == "transition"]
    assert [record["violations"] for record in transitions] == [None, []]

    document = trace.to_dict()
    assert document["schema_version"] == "3.2.0"
    round_trips = [
        Trace.from_json(trace.to_json()),
        Trace.from_dict(document),
    ]
    violations, jsonl_document = validate.validate_jsonl(trace.to_jsonl(), spec=spec.to_dict())
    assert violations == []
    round_trips.append(Trace.from_dict(jsonl_document))
    for loaded in round_trips:
        assert loaded.to_dict() == document
        assert loaded.root == trace.root

    unsupported = dict(document)
    unsupported["schema_version"] = "3.3.0"
    with pytest.raises(ValueError) as raised:
        Trace.from_json(json.dumps(unsupported))
    assert str(raised.value) == "unsupported trace schema version"


def test_declaration_violations_are_recomputed_from_every_captured_surface():
    def probes(state):
        state.fact("missing")
        state.metadata("actor")
        _ = state.events
        return state.with_fact(Fact("written", True, "test", NOW))

    spec = _spec(
        [{"name": "probes", "reads_declared": [], "writes_declared": []}],
        {"probes": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"probes": probes}, policy=Policy.EXPLORATION).run(
        State.empty(metadata={"actor": "davide"}), run_id="declarations"
    )
    transition = next(record for record in result.trace.records if record["kind"] == "transition")
    assert transition["violations"] == [
        {"kind": "undeclared_read", "key": "missing"},
        {"kind": "undeclared_read", "key": "actor"},
        {"kind": "undeclared_read", "key": "events"},
        {"kind": "undeclared_write", "key": "written"},
    ]
    _assert_clean(result, spec)


def test_a_declaration_violation_names_the_offending_keys_in_its_message():
    """The structured `violations` field is complete, but a developer reading a
    production traceback sees the string — and "some read or write was
    undeclared" without saying which one costs them the lookup the runtime
    already did. The first external integrator hit exactly this: four minutes
    to find out that the key was `question`, on the first line of every trace
    they wrote. ReplayMismatch names its node/record/field; this now matches.
    """
    from vitruvyan_motus import DeclarationViolation

    def leaks(state):
        state.metadata("question")
        return state.with_fact(Fact("x", 1, "test", NOW))

    spec = _spec(
        [{"name": "leaks", "reads_declared": ["sources"]}],
        {"leaks": {"kind": "terminal"}},
    )
    with pytest.raises(NodeFailed) as raised:
        Runtime(spec, {"leaks": leaks}).run(State.empty(metadata={"question": "?"}))

    cause = raised.value.cause
    assert isinstance(cause, DeclarationViolation)
    message = str(cause)
    assert "question" in message, (
        f"the message must name the offending key, not only the exception "
        f"attribute: {message!r}"
    )
    assert "undeclared_read" in message
    # The structured field stays authoritative and unchanged.
    assert cause.violations == ({"kind": "undeclared_read", "key": "question"},)


def test_transition_limit_fails_at_the_refused_selection():
    def loop_or_end(state):
        count = state.fact("count", 0)
        return state.with_fact(Fact("count", count + 1, "test", NOW)).with_decision(
            Decision("route", "loop", NOW)
        )

    spec = _spec(
        [{"name": "loop", "effect_class": "pure"}],
        {"loop": {"kind": "route", "on": "route", "map": {"loop": "loop"}, "default": "END"}},
        max_transitions=2,
    )
    result = Runtime(spec, {"loop": loop_or_end}).run(run_id="limit")
    assert result.trace.records[-1]["cause"]["kind"] == "transition_limit_exceeded"
    _assert_clean(result, spec)


def test_transition_limit_also_bounds_exploration_failures():
    def unavailable(state):
        raise RuntimeError("dependency unavailable")

    spec = _spec(
        [{"name": "loop", "effect_class": "pure"}],
        {"loop": {"kind": "route", "on": "route", "map": {"loop": "loop"}, "default": "END"}},
        max_transitions=2,
    )
    initial = State.new("limit-failure", decisions=[Decision("route", "loop", NOW)])
    result = Runtime(spec, {"loop": unavailable}, policy=Policy.EXPLORATION).run(initial)

    assert result.status == "failed"
    assert result.trace.records[-1]["cause"]["kind"] == "transition_limit_exceeded"
    transitions = [record for record in result.trace.records if record["kind"] == "transition"]
    assert [record["disposition"] for record in transitions] == ["continue", "continue"]
    _assert_clean(result, spec)


def test_evidence_free_rejection_remains_serializable_through_runtime():
    def declines(state):
        return state.with_rejection(Rejection("request", "not applicable", NOW))

    spec = _spec([{"name": "decline", "effect_class": "pure"}], {"decline": {"kind": "terminal"}})
    result = Runtime(spec, {"decline": declines}).run(run_id="rejection-without-evidence")

    written = next(record for record in result.trace.records if record["kind"] == "transition")["writes"]["rejections"][0]
    assert "evidence" not in written
    result.trace.to_json()
    result.trace.to_jsonl()
    _assert_clean(result, spec)


def test_pre_run_cancellation_is_consumed_without_executing_a_node():
    executed = []

    def node(state):
        executed.append("node")
        return state

    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    runtime.cancel("shutdown before start")

    cancelled = runtime.run(run_id="pre-cancelled")
    assert cancelled.status == "cancelled"
    assert executed == []
    _assert_clean(cancelled, spec)

    completed = runtime.run(run_id="after-pre-cancel")
    assert completed.status == "completed"
    assert executed == ["node"]
    _assert_clean(completed, spec)


def test_a_queued_cancellation_survives_a_start_that_never_began_a_run():
    """FA-002: `_start` latches the lifecycle before it can still fail.

    The claim is entered under the lock -- `_has_started` up, the queued reason
    consumed into `_cancel_reason` -- and only afterwards does the body run the
    checks that reject the call.  A rejected `run_id` therefore spent the
    Runtime's one queued cancellation on a run that wrote no header, executed
    no node and returned no result.

    The caller lodges the request ONCE, as a signal handler does.  Re-lodging
    it here would let the fix be deleted with this test still green: the second
    request satisfies every assertion below on its own.
    """
    executed = []

    def node(state):
        executed.append("node")
        return state

    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    assert runtime.cancel("shutdown before start") is True

    with pytest.raises(ValueError):
        runtime.run(run_id="x" * 201)

    cancelled = runtime.run(run_id="after-the-refused-start")
    assert cancelled.status == "cancelled"
    assert executed == []
    terminal = cancelled.trace.records[-1]
    assert terminal["reason"] == "shutdown before start"
    _assert_clean(cancelled, spec)


def test_a_refused_start_does_not_lock_the_caller_out_of_cancelling():
    """The other half of the same loss, and the one the caller can feel.

    `_has_started` staying up after a start that never ran made ADR-008 §1 read
    the Runtime as used, so every later `cancel()` returned False and there was
    no way back for a caller who had just been told True.
    """
    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": lambda state: state})

    with pytest.raises(ValueError):
        runtime.run(run_id="x" * 201)

    assert runtime.cancel("lodged after the refusal") is True
    assert runtime.run(run_id="honours-it").status == "cancelled"


def test_a_failed_start_does_not_reopen_the_queue_on_a_used_runtime():
    """The direction that must NOT change, and the reason the latch cannot be
    blanked.

    Rolling `_has_started` back to False unconditionally would let a Runtime
    that has already executed accept a queued cancellation again -- exactly the
    leak into "a later unrelated run" that ADR-008 §1 closed.  The failed start
    must restore the flag it found, not clear it.
    """
    def node(state):
        return state

    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    assert runtime.run(run_id="a-real-run").status == "completed"

    with pytest.raises(ValueError):
        runtime.run(run_id="x" * 201)

    assert runtime.cancel("idle, after a used runtime") is False
    assert runtime.run(run_id="unaffected").status == "completed"


class _CancelsWhileTheStartIsClaimed:
    """A node whose configuration cancels the run being started.

    `_refresh_identity` evaluates `motus_config()` inside `_start`, after the
    claim and before the `run_id` check, so this reaches the window a
    concurrent caller reaches -- deterministically, on one thread.  ADR-008 §4
    puts user code there by design; it is not an artificial hook.
    """

    def __init__(self, runtime_box, reason):
        self._box = runtime_box
        self._reason = reason
        self.armed = False

    def motus_config(self):
        if self.armed:
            self.armed = False
            self._box[0].cancel(self._reason)
        return {"armed": False}

    def __call__(self, state):
        return state


def test_a_cancel_bound_to_a_start_that_never_ran_is_not_retargeted_later():
    """Restoring `_cancel_reason` rather than what the claim took retargets it.

    A request the API reported as bound to THIS start becomes, on rollback, a
    queue entry -- and the next run is cancelled by it and records its reason
    as that run's own.  The trace then states, as a fact about run C, a reason
    that was a fact about run A.  Only the reason the claim consumed may go
    back into the queue.
    """
    box = []
    node = _CancelsWhileTheStartIsClaimed(box, "abort the start that is claimed")
    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    box.append(runtime)

    node.armed = True
    with pytest.raises(ValueError):
        runtime.run(run_id="x" * 201)

    later = runtime.run(run_id="a-different-run")
    assert later.status == "completed", (
        "a cancellation bound to a start that never ran was aimed at a later run"
    )
    assert [record["kind"] for record in later.trace.records].count("run_cancelled") == 0
    _assert_clean(later, spec)


def test_a_cancel_bound_during_a_failed_start_does_not_displace_the_queued_one():
    """The lost update inside the same window.

    An operator queues a shutdown; a supervisor cancels during the doomed start
    and overwrites `_cancel_reason`.  Re-queueing THAT value destroys the
    operator's request and attributes the next run's cancellation to a cause
    that was never about it.
    """
    box = []
    node = _CancelsWhileTheStartIsClaimed(box, "supervisor: driver closed")
    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    box.append(runtime)

    assert runtime.cancel("operator: shutdown SIGTERM") is True
    node.armed = True
    with pytest.raises(ValueError):
        runtime.run(run_id="x" * 201)

    cancelled = runtime.run(run_id="the-first-run-that-begins")
    assert cancelled.status == "cancelled"
    assert cancelled.trace.records[-1]["reason"] == "operator: shutdown SIGTERM"
    _assert_clean(cancelled, spec)


class _ParkTheRollbackAfterItFreesTheClaim:
    """The lifecycle lock, holding the failed start open past its own release.

    The rollback frees the claim and only then clears `_active_attempt`.  A
    store ordered after that release is unsynchronised: it lands in whichever
    run has since begun.  Parking the release turns an interleaving measured at
    4 in 2000 rounds (`sys.setswitchinterval(5e-6)`) into a certain one.
    """

    __slots__ = ("_inner", "_exits", "_gate")

    def __init__(self, inner, gate) -> None:
        self._inner = inner
        self._gate = gate
        self._exits: dict[str, int] = {}

    def __enter__(self):
        return self._inner.__enter__()

    def __exit__(self, *exc):
        result = self._inner.__exit__(*exc)
        name = threading.current_thread().name
        count = self._exits[name] = self._exits.get(name, 0) + 1
        if name == "doomed-start" and count == 2:   # 1 is the claim, 2 the rollback
            self._gate.wait(10)
        return result


def test_a_failed_start_cannot_blank_a_later_runs_active_attempt():
    """The rollback must not write to the Runtime after freeing it.

    Every participant here is doing what the contract blesses: ADR-008 §4 puts
    `motus_config()` failure inside the claim, and guarantees.md §6 permits a
    listener holding the Runtime to call its cancellation surface.  The stray
    store lands between the attempt being opened and the runtime reading it for
    `run_cancelled`, and the trace that results is one the project's OWN
    validator refuses under T6 -- an unclosed `attempt_started` followed by a
    `run_cancelled` that does not name it.
    """
    gate = threading.Event()
    box: dict[str, Runtime] = {}

    def listener(record):
        if record["kind"] == "attempt_started":
            box["runtime"].cancel("cancelled by a listener holding the Runtime")
            gate.set()
            time.sleep(0.2)          # the window the stray store must land in

    spec = _spec([{"name": "node", "effect_class": "pure"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": lambda state: state}, listeners=(listener,))
    box["runtime"] = runtime
    runtime._lifecycle_lock = _ParkTheRollbackAfterItFreesTheClaim(
        runtime._lifecycle_lock, gate
    )

    landed: list = []

    def doomed_start() -> None:
        with pytest.raises(ValueError):
            runtime.run(run_id="x" * 201)

    def the_run_that_follows() -> None:
        while True:
            try:
                landed.append(runtime.run(run_id="the-run-that-follows"))
                return
            except RuntimeError:                # the claim is not free yet
                time.sleep(0.001)

    doomed = threading.Thread(target=doomed_start, name="doomed-start")
    following = threading.Thread(target=the_run_that_follows, name="the-run")
    doomed.start()
    time.sleep(0.05)
    following.start()
    doomed.join(15)
    following.join(15)

    assert gate.is_set(), "the window was never opened"
    assert landed, "the following run never completed"
    result = landed[0]
    assert result.status == "cancelled"
    terminal = result.trace.records[-1]
    assert terminal["kind"] == "run_cancelled"
    assert terminal["active_attempt"] == {"node": "node", "attempt": 1}, (
        "a failed start blanked the attempt a later run was cancelled inside"
    )
    _assert_clean(result, spec)


def test_listener_failure_is_non_intervening_and_isolated():
    calls = []

    def noisy(record):
        calls.append(record["seq"])
        record["kind"] = "tampered"
        raise RuntimeError("listener")

    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    runtime = Runtime(spec, {"done": unchanged}, listeners=(noisy,))
    result = runtime.run(run_id="listener")
    assert calls
    assert all(record["kind"] != "tampered" for record in result.trace.records)
    _assert_clean(result, spec)


def test_required_sink_refusal_prevents_logical_success():
    class Refuses:
        def open_run(self, header):
            return self

        def write(self, records):
            raise OSError("disk full")

    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    runtime = Runtime(
        spec, {"done": unchanged}, durability_profile="synchronous", sink=Refuses()
    )
    with pytest.raises(SinkFailed) as caught:
        runtime.run(run_id="sink")
    assert caught.value.trace.records[-1]["cause"]["kind"] == "sink_failure"
    assert not any(record["kind"] == "run_completed" for record in caught.value.trace.records)


def test_synchronous_sink_receives_every_record_in_order():
    sink = InMemoryTraceSink()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    result = Runtime(
        spec, {"done": unchanged}, durability_profile="synchronous", sink=sink
    ).run(run_id="sync")
    # ADR-011: the sink receives the document's TraceHeader --
    # {schema_version, run} -- not the run object one level inside it, so
    # what it was handed is enough to write a conforming artifact. The
    # equality is tightened here, not relaxed: it now covers the version.
    assert sink.header == result.trace.header
    assert sink.records == result.trace.records


def test_stream_driver_close_records_active_attempt_without_running_node():
    called = []

    def node(state):
        called.append(True)
        return state

    spec = _spec([{"name": "node"}], {"node": {"kind": "terminal"}})
    driver = Runtime(spec, {"node": node}).stream(run_id="stream")
    assert next(driver)["kind"] == "run_started"
    assert next(driver)["kind"] == "attempt_started"
    driver.close("consumer stopped")
    assert called == []
    assert driver.trace.records[-1]["kind"] == "run_cancelled"
    assert driver.trace.records[-1]["active_attempt"] == {"node": "node", "attempt": 1}
    assert validate.validate_trace(driver.trace.to_dict(), spec=spec.to_dict()) == []


def test_reproducible_runs_differ_only_when_the_context_source_differs():
    instant = datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)

    def draws(state, ctx):
        return state.with_fact(Fact(
            "draws",
            {"time": ctx.now().isoformat(),
             # J4 (ADR-030): the draw itself is a float, so the fact carries
             # it at a scale the producer declares.
             "random": int(ctx.rand() * 1000), "id": ctx.uuid()},
            "context",
            instant,
        ))

    spec = _spec([{"name": "draws", "effect_class": "pure"}], {"draws": {"kind": "terminal"}})
    runtime = Runtime(
        spec, {"draws": draws}, clock=lambda: instant,
        identity=lambda: "same-id", random_source=lambda: 0.125,
    )
    first = runtime.run(run_id="same", replay=ReplayStatus.declared("full"))
    second = runtime.run(run_id="same", replay=ReplayStatus.declared("full"))
    assert first.trace.to_dict() == second.trace.to_dict()
    _assert_clean(first, spec)


def test_seeded_decision_routing_names_the_exact_initial_index():
    spec = _spec(
        [{"name": "route"}, {"name": "done"}],
        {
            "route": {"kind": "route", "on": "path", "map": {"yes": "done"}},
            "done": {"kind": "terminal"},
        },
    )
    initial = State.new(
        "seeded",
        decisions=[Decision("other", "x", NOW), Decision("path", "yes", NOW)],
    )
    result = Runtime(spec, {"route": unchanged, "done": unchanged}).run(
        initial, run_id="seeded"
    )
    routing = next(record for record in result.trace.records if record["kind"] == "routing")
    assert routing["origin"] == {"kind": "initial", "index": 1}
    _assert_clean(result, spec)


def test_buffered_profile_discloses_and_flushes_its_complete_loss_window():
    sink = InMemoryTraceSink()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    result = Runtime(
        spec,
        {"done": unchanged},
        durability_profile="buffered",
        sink=sink,
        chunk_records=100,
        flush_interval_ms=60_000,
    ).run(run_id="buffered")
    assert result.trace.run["sink"] == {
        "flush_interval_ms": 60_000,
        "chunk_records": 100,
    }
    assert sink.records == result.trace.records
    _assert_clean(result, spec)


def test_sink_refusal_of_completion_replaces_success_with_system_failure():
    class RefuseCompletion:
        def __init__(self):
            self.records = []

        def open_run(self, header):
            return self

        def write(self, records):
            if records[-1]["kind"] == "run_completed":
                raise OSError("terminal fsync failed")
            self.records.extend(records)

    sink = RefuseCompletion()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    with pytest.raises(SinkFailed) as caught:
        Runtime(
            spec, {"done": unchanged}, durability_profile="synchronous", sink=sink
        ).run(run_id="terminal-sink")
    assert caught.value.trace.records[-1]["cause"]["kind"] == "sink_failure"
    assert caught.value.trace.records[-1]["seq"] == 5
    assert validate.validate_trace(
        caught.value.trace.to_dict(), spec=spec.to_dict()
    ) == []


def test_redacted_metadata_and_jsonl_remain_validator_clean():
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    state = State.empty(
        "redacted", metadata={"credential": redact("secret", "policy:credential")}
    )
    result = Runtime(spec, {"done": unchanged}).run(state, run_id="redacted")
    document = result.trace.to_dict()
    assert document["run"]["metadata"]["credential"]["kind"] == "redacted"
    violations, reassembled = validate.validate_jsonl(
        result.trace.to_jsonl(), spec=spec.to_dict()
    )
    assert violations == []
    assert reassembled == document


class _MutableConfig:
    """A node whose configuration is mutable, which is the case ADR-008 §4
    evaluates `motus_config()` at every run start to keep honest."""

    def __init__(self, threshold):
        self.threshold = threshold

    def motus_config(self):
        return {"threshold": self.threshold}

    def __call__(self, state):
        return state


class _ConfigThatCanRefuse(_MutableConfig):
    def __init__(self):
        super().__init__(0)
        self.armed = False

    def motus_config(self):
        if self.armed:
            raise RuntimeError("configuration source unavailable")
        return {"threshold": self.threshold}


def test_a_refused_start_leaves_the_code_fingerprint_recomputable():
    """A refresh that raises must leave identity exactly as it found it.

    `_refresh_identity` walks the declarations in order and used to commit each
    node's new material into `_identity_cache` as it went, recomputing the
    fingerprint only after the loop.  A node that raised part-way -- ADR-008 §4
    puts a `motus_config()` failure exactly there -- left the nodes already
    visited cached against the OLD fingerprint.  On every later start they then
    compared equal to their own cache, `changed` stayed False, and the
    fingerprint was never recomputed again: the header recorded configuration
    the run did not use, for the life of the Runtime.

    Nothing downstream can catch this.  SB2 makes `code_fingerprint` the one
    identity a validator cannot recompute offline, so the runtime's honesty at
    run start is the whole of the guarantee.
    """
    first = _MutableConfig(1)
    second = _ConfigThatCanRefuse()
    spec = _spec(
        [{"name": "first", "effect_class": "pure"}, {"name": "second", "effect_class": "pure"}],
        {"first": {"kind": "next", "to": "second"}, "second": {"kind": "terminal"}},
    )
    runtime = Runtime(spec, {"first": first, "second": second})

    before = runtime.run(run_id="before").trace.run["graph"]["code_fingerprint"]

    first.threshold = 2
    second.armed = True
    with pytest.raises(NodeConfigurationError):
        runtime.run(run_id="refused")

    second.armed = False
    after = runtime.run(run_id="after").trace.run["graph"]["code_fingerprint"]

    assert after != before, (
        "the refused start left the identity cache half committed, and the "
        "fingerprint now describes configuration the run did not use"
    )

    control = Runtime(spec, {"first": _MutableConfig(1), "second": _ConfigThatCanRefuse()})
    control_before = control.run(run_id="control-before").trace.run["graph"]["code_fingerprint"]
    control.nodes["first"].threshold = 2
    control_after = control.run(run_id="control-after").trace.run["graph"]["code_fingerprint"]
    assert control_before == before and control_after == after, (
        "the same config change without a refused start produces a different "
        "fingerprint pair -- the test is not comparing what it claims to"
    )


def test_an_unchanged_configuration_is_not_refingerprinted_every_run(monkeypatch):
    """Staging the refresh must still commit it.

    Dropping the commit altogether keeps every correctness test green -- the
    fingerprint is simply recomputed from scratch on every start, which is
    right by accident. What it loses is the cache's whole purpose, and it loses
    it silently: `_refresh_identity` runs on the hot path of every run.
    """
    import vitruvyan_motus.runtime as runtime_module

    calls = []
    real = runtime_module._config_fingerprint

    def counted(material):
        calls.append(material)
        return real(material)

    monkeypatch.setattr(runtime_module, "_config_fingerprint", counted)

    node = _MutableConfig(1)
    spec = _spec([{"name": "first", "effect_class": "pure"}], {"first": {"kind": "terminal"}})
    runtime = Runtime(spec, {"first": node})

    runtime.run(run_id="first-run")
    after_first = len(calls)
    runtime.run(run_id="second-run")

    assert len(calls) == after_first, (
        "an unchanged configuration was fingerprinted again: the staged "
        f"refresh is never committed ({len(calls) - after_first} extra calls)"
    )
    assert set(runtime._identity_cache) == {"first"}

    node.threshold = 2
    runtime.run(run_id="third-run")
    assert len(calls) > after_first, "a changed configuration was not refingerprinted"


def test_runtime_configuration_cannot_diverge_from_recorded_fingerprints():
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    runtime = Runtime(spec, {"done": unchanged})
    with pytest.raises(TypeError):
        runtime.nodes["done"] = lambda state: state
    with pytest.raises(AttributeError):
        runtime.durability_profile = DurabilityProfile.SYNCHRONOUS
    result = runtime.run(run_id="frozen-config")
    assert result.succeeded and result.status == "completed"
    _assert_clean(result, spec)


def test_overlapping_runs_are_rejected_and_completed_driver_keeps_its_trace():
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    runtime = Runtime(spec, {"done": unchanged})
    first = runtime.stream(run_id="first")
    assert next(first)["kind"] == "run_started"
    with pytest.raises(RuntimeError, match="overlapping"):
        runtime.stream(run_id="second")
    first.close("finish first")
    first_document = first.trace.to_dict()
    second = runtime.run(run_id="second")
    assert first.trace.to_dict() == first_document
    assert first.trace.run["run_id"] == "first"
    assert second.trace.run["run_id"] == "second"


def test_stream_close_after_transition_is_cancellation_not_success():
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    driver = Runtime(spec, {"done": unchanged}).stream(run_id="late-cancel")
    assert [next(driver)["kind"] for _ in range(3)] == [
        "run_started", "attempt_started", "transition",
    ]
    driver.close("stop after commit")
    assert driver.trace.records[-1]["kind"] == "run_cancelled"
    assert driver.trace.records[-1]["active_attempt"] is None
    assert validate.validate_trace(driver.trace.to_dict(), spec=spec.to_dict()) == []


def test_listener_baseexception_is_structurally_non_intervening():
    class ListenerBomb(BaseException):
        pass

    def listener(record):
        raise ListenerBomb("must be isolated")

    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    runtime = Runtime(spec, {"done": unchanged}, listeners=(listener,))
    result = runtime.run(run_id="base-listener")
    assert result.succeeded
    assert runtime._hub.listener_failures == len(result.trace.records)


def test_hard_node_escape_resets_lifecycle_before_runtime_reuse():
    class HardEscape(BaseException):
        pass

    calls = []

    def node(state):
        calls.append(1)
        if len(calls) == 1:
            raise HardEscape("first run")
        return state

    spec = _spec([{"name": "node"}], {"node": {"kind": "terminal"}})
    runtime = Runtime(spec, {"node": node})
    with pytest.raises(HardEscape):
        runtime.run(run_id="escape")
    result = runtime.run(run_id="recovered")
    assert result.succeeded
    _assert_clean(result, spec)


def test_node_exception_content_is_not_automatically_persisted_as_plaintext():
    secret = "Bearer TOP-SECRET-123"

    def fails(state):
        raise RuntimeError(secret)

    spec = _spec([{"name": "fails"}], {"fails": {"kind": "terminal"}})
    with pytest.raises(NodeFailed) as caught:
        Runtime(spec, {"fails": fails}).run(run_id="safe-error")
    assert secret not in caught.value.trace.to_json()
    assert caught.value.cause.args == (secret,)


def test_transient_sink_refusal_leaves_a_valid_persisted_prefix():
    class RefuseTransitionOnce:
        def __init__(self):
            self.records = []
            self.refused = False

        def open_run(self, header):
            return self

        def write(self, records):
            if records[-1]["kind"] == "transition" and not self.refused:
                self.refused = True
                raise OSError("one refusal")
            self.records.extend(records)

    sink = RefuseTransitionOnce()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    with pytest.raises(SinkFailed):
        Runtime(
            spec, {"done": unchanged},
            durability_profile="synchronous", sink=sink,
        ).run(run_id="prefix")
    assert [record["seq"] for record in sink.records] == [1, 2]


def test_buffered_interval_flushes_without_waiting_for_another_record():
    sink = InMemoryTraceSink()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    driver = Runtime(
        spec, {"done": unchanged}, durability_profile="buffered", sink=sink,
        chunk_records=100, flush_interval_ms=10,
    ).stream(run_id="timed-flush")
    assert next(driver)["kind"] == "run_started"
    deadline = time.monotonic() + 1
    while not sink.records and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [record["kind"] for record in sink.records] == ["run_started"]
    driver.close("test complete")


def test_shared_sink_partitions_runs_and_cannot_mutate_trace_headers():
    class MutatingSink(InMemoryTraceSink):
        def open_run(self, header):
            session = super().open_run(header)
            header["run_id"] = "forged"
            return session

    sink = MutatingSink()
    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    first = Runtime(
        spec, {"done": unchanged}, durability_profile="synchronous", sink=sink
    ).run(run_id="first")
    second = Runtime(
        spec, {"done": unchanged}, durability_profile="synchronous", sink=sink
    ).run(run_id="second")
    assert [run["header"]["run"]["run_id"] for run in sink.runs] == ["first", "second"]
    assert first.trace.run["run_id"] == "first"
    assert second.trace.run["run_id"] == "second"
    assert all(run["records"][-1]["kind"] == "run_completed" for run in sink.runs)


def test_sink_open_refusal_prevents_logical_success():
    class RefuseOpen:
        def open_run(self, header):
            raise OSError("cannot bind run")

    spec = _spec([{"name": "done"}], {"done": {"kind": "terminal"}})
    with pytest.raises(SinkFailed) as caught:
        Runtime(
            spec, {"done": unchanged},
            durability_profile="synchronous", sink=RefuseOpen(),
        ).run(run_id="open-refusal")
    assert caught.value.trace.records[-1]["cause"]["kind"] == "sink_failure"
    assert not any(
        record["kind"] == "run_completed" for record in caught.value.trace.records
    )


def _seed_sensitive(state):
    return (
        state
        .with_fact(Fact("income", 42000, "registry", NOW))
        .with_fact(Fact("national_id", "BLDDVD80A01H501K", "registry", NOW))
    )


def _read_everything(state):
    seen = {item["key"]: item["value"] for item in state.snapshot()["facts"]}
    return state.with_fact(Fact("summary", sorted(seen), "test", NOW))


def _summary_graph(reads_declared):
    return _spec(
        [
            {
                "name": "seed", "effect_class": "pure",
                "reads_declared": [], "writes_declared": ["income", "national_id"],
            },
            {
                "name": "summarise", "effect_class": "pure",
                "reads_declared": reads_declared, "writes_declared": ["summary"],
            },
        ],
        {"seed": {"kind": "next", "to": "summarise"}, "summarise": {"kind": "terminal"}},
    )


def test_snapshot_cannot_launder_a_read_of_the_whole_state():
    """A loan file, and the failure mode that motivated the fix.

    `summarise` declared it reads nothing, called `snapshot()`, and received
    every fact in the file including the national id — which then appeared in
    its output while the trace recorded `reads: []` and no violation. The run
    completed. In a credit assessment that is the line that loses the audit:
    the identifier is in the product and the evidence says nobody looked at it.

    The declaration check was never broken. `snapshot()` simply did not report
    to it, so there was nothing to compare against.
    """
    with pytest.raises(NodeFailed) as caught:
        Runtime(
            _summary_graph([]), {"seed": _seed_sensitive, "summarise": _read_everything}
        ).run(State.empty("loan file"), run_id="undeclared")

    transitions = [
        record for record in caught.value.trace.records
        if record["kind"] == "transition" and record["node"] == "summarise"
    ]
    assert [read["key"] for read in transitions[0]["reads"]] == [
        "facts", "decisions", "rejections"
    ]
    assert [violation["kind"] for violation in transitions[0]["violations"]] == [
        "undeclared_read", "undeclared_read", "undeclared_read"
    ]


def test_a_node_that_needs_the_whole_state_declares_the_collections_and_passes():
    """The fix must not make a legitimate node impossible, only honest.

    Summarising everything known so far is a real thing to want, and it is the
    obvious shape for a node backed by a model. So reading the whole state stays
    permitted — it just has to be declared, and once declared a reviewer reading
    `reads_declared: ["facts", ...]` knows immediately that this node sees the
    entire file. That is strictly more information than the old `[]`, which was
    both smaller and false.
    """
    spec = _summary_graph(["facts", "decisions", "rejections"])
    result = Runtime(
        spec, {"seed": _seed_sensitive, "summarise": _read_everything}
    ).run(State.empty("loan file"), run_id="declared")

    assert result.status == "completed"
    assert result.state.fact("summary") == ["income", "national_id"]

    transitions = [
        record for record in result.trace.records
        if record["kind"] == "transition" and record["node"] == "summarise"
    ]
    assert transitions[0]["violations"] == []
    _assert_clean(result, spec)


def _nested_capturing_nothing():
    """A factory whose product captures no state at all."""
    def node(state):
        return state.with_fact(Fact("nested", True, "test", NOW))
    return node


def _module_level_equivalent(state):
    return state.with_fact(Fact("nested", True, "test", NOW))


def test_a_nested_function_that_captures_nothing_is_not_opaque():
    """Two functions with identical behaviour reported different capability.

    `_config_material` asks whether a callable carries CONFIGURATION worth
    fingerprinting. `__closure__ is None` is the interpreter's own statement
    that a function captured nothing, which answers that question completely.

    It used to also require the qualified name to be free of `<locals>`, so a
    `def` returned by a factory — capturing nothing — reported
    `node:<name>:opaque_config` while the module-level twin reported none. The
    recipe in node-protocol.md §6.3 names closures over non-JSON state and
    instances without `motus_config()`; it never says "lexically nested". The
    extra clause was stricter than the contract, undocumented, and untested, and
    graph-builder factories are the single most common shape in real code — so
    the false constraint was not rare, it was the norm.
    """
    spec = _spec(
        [{"name": "n", "effect_class": "pure", "writes_declared": ["nested"]}],
        {"n": {"kind": "terminal"}},
    )

    def constraints_for(node):
        result = Runtime(spec, {"n": node}).run(
            State.empty("nesting"), run_id="r1",
            replay=ReplayStatus.declared("full", ()),
        )
        terminal = [
            record for record in result.trace.records
            if record["kind"].startswith("run_")
        ][-1]
        return [
            constraint for constraint in terminal["replay"]["constraints"]
            if "opaque_config" in constraint
        ]

    assert constraints_for(_module_level_equivalent) == []
    assert constraints_for(_nested_capturing_nothing()) == []


def test_a_nested_function_that_does_capture_is_still_opaque():
    """The fix must not open the gate it narrowed.

    A closure over real configuration still has configuration this runtime
    cannot fingerprint, and still earns the constraint. Asserting only the
    negative case would let a later edit delete the whole branch and stay green.
    """
    def factory(threshold):
        def node(state):
            return state.with_fact(Fact("nested", threshold, "test", NOW))
        return node

    spec = _spec(
        [{"name": "n", "effect_class": "pure", "writes_declared": ["nested"]}],
        {"n": {"kind": "terminal"}},
    )
    result = Runtime(spec, {"n": factory(7)}).run(
        State.empty("nesting"), run_id="r1",
        replay=ReplayStatus.declared("full", ()),
    )
    terminal = [
        record for record in result.trace.records
        if record["kind"].startswith("run_")
    ][-1]
    assert "node:n:opaque_config" in terminal["replay"]["constraints"]


def test_default_arguments_are_configuration_and_are_fingerprinted():
    """The hole the previous fix opened, and why it is not closed by reverting.

    A function's configuration lives in exactly two places it can carry without
    a closure: `__defaults__` and `__kwdefaults__`. `def node(state, *,
    threshold=x)` keeps x there while `__closure__` stays None, so a factory can
    produce two nodes that behave differently and capture nothing.

    Removing the `<locals>` clause — a rule stricter than the contract — let both
    report `none` and share one configuration fingerprint. A false constraint is
    noise. A false IDENTITY is the defect class this project exists to prevent:
    two graphs that do different things claiming the same `code_fingerprint`.

    So defaults are fingerprinted the way a partial's arguments are, which is
    what the qualname check had been accidentally standing in for — and unlike
    that check, this distinguishes the nodes rather than refusing to look.
    """
    def factory(threshold):
        def node(state, *, threshold=threshold):
            return state.with_fact(Fact("t", threshold, "test", NOW))
        return node

    def positional_factory(threshold):
        def node(state, threshold=threshold):
            return state.with_fact(Fact("t", threshold, "test", NOW))
        return node

    def fingerprint(node):
        material, _ = _config_material(node)
        return _config_fingerprint(material)

    assert fingerprint(factory(7)) != fingerprint(factory(99))
    assert fingerprint(positional_factory(1)) != fingerprint(positional_factory(2))
    assert fingerprint(factory(7)) == fingerprint(factory(7))


def test_defaults_that_are_not_json_are_honestly_opaque():
    """Fingerprinting configuration requires configuration that reduces to JSON.

    A default that does not is not identifiable, and saying `none` about it
    would be the same false identity in a quieter form.
    """
    def factory(handle):
        def node(state, *, handle=handle):
            return state
        return node

    material, opaque = _config_material(factory(object()))
    assert opaque is True
    assert material == ("marker", "opaque")
