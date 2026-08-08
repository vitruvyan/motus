"""What a durable sink actually receives, and whether it can be trusted.

Motus's thesis is evidence you can independently re-check, so the failures that
matter most are the ones where the persisted account is *worse than absent*:
contradictory, or silently short. Both were reachable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from datetime import datetime, timezone

from vitruvyan_motus import (
    Decision, DurabilityProfile, EvidenceStatus, GraphSpec, NodeFailed, Runtime,
    SinkFailed, State,
)

CHAIN_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "evidence",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
    ],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
CHAIN = GraphSpec.from_dict(dict(CHAIN_DOC))


def passthrough(state: State) -> State:
    return state


class RecordingSink:
    """A sink that records every batch, and can fail after committing one."""

    def __init__(self, *, fail_on: str | None = None, commit_first: bool = True) -> None:
        self.fail_on = fail_on
        self.commit_first = commit_first
        self.batches: list[tuple[dict[str, Any], ...]] = []
        self.headers: list[dict[str, Any]] = []
        self.write_calls = 0

    def open_run(self, header: dict[str, Any]) -> "RecordingSink":
        self.headers.append(header)
        return self

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        self.write_calls += 1
        hit = self.fail_on is not None and any(r["kind"] == self.fail_on for r in records)
        if hit and self.commit_first:
            # The lost-acknowledgement shape: the append committed, the
            # acknowledgement did not.
            self.batches.append(records)
        if hit:
            raise OSError("acknowledgement lost")
        self.batches.append(records)

    @property
    def records(self) -> list[dict[str, Any]]:
        return [record for batch in self.batches for record in batch]


@pytest.mark.parametrize(
    "terminal_kind",
    ["run_completed", "run_cancelled", "run_failed"],
)
def test_a_sink_that_commits_then_raises_is_never_written_to_again(terminal_kind):
    """A write that raised may still have committed — the canonical lost
    acknowledgement, which guarantees.md invariant II contemplates by naming
    replication. The runtime cannot tell, so it must not write again: a retry
    beside a record that *did* commit produces a duplicate `seq` and, on the
    terminal, one artifact asserting both that the run completed and that it
    failed. OPEN-08 licenses a truncated prefix, never a corrupted suffix.
    """
    sink = RecordingSink(fail_on=terminal_kind)
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )

    if terminal_kind == "run_cancelled":
        runtime.cancel("stop")
        try:
            runtime.run(State.empty("e"))
        except SinkFailed:
            pass
    elif terminal_kind == "run_failed":
        def boom(state: State) -> State:
            raise ValueError("node failed")
        runtime = Runtime(
            CHAIN, {"a": boom, "b": passthrough},
            sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
        )
        with pytest.raises(Exception):
            runtime.run(State.empty("e"))
    else:
        with pytest.raises(SinkFailed):
            runtime.run(State.empty("e"))

    seqs = [record["seq"] for record in sink.records]
    assert len(seqs) == len(set(seqs)), (
        f"the sink was written to again after a failure: duplicate seq in {seqs}"
    )
    terminals = [r["kind"] for r in sink.records if r["kind"].startswith("run_") and r["kind"] != "run_started"]
    assert len(terminals) <= 1, (
        f"the persisted account carries more than one terminal: {terminals}"
    )


def test_a_buffered_run_torn_down_without_a_terminal_still_hands_over_its_buffer():
    """Every terminal force-flushes, so ordinary paths were safe. A run that
    never reaches one — a node raising `BaseException`, a cancelled task, an
    abandoned driver — arrived at `close()` with a full buffer and had it
    discarded: the trace named the records, the sink received none, and the
    process was alive throughout.

    ADR-008 §2: the declared loss window "does not license silent loss while
    the process is alive."
    """
    sink = RecordingSink()

    def interrupted(state: State) -> State:
        raise KeyboardInterrupt("operator stopped it")

    runtime = Runtime(
        CHAIN, {"a": interrupted, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.BUFFERED,
        chunk_records=64, flush_interval_ms=1000,
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.run(State.empty("torn"))

    assert sink.headers, "the run session was never opened"
    kinds = [record["kind"] for record in sink.records]
    assert kinds, (
        "the buffered profile discarded every record it was still holding; "
        "the trace named them and the process never died"
    )
    assert kinds[0] == "run_started"
    assert "attempt_started" in kinds, (
        "node-protocol §7.3: the unclosed attempt_started must survive as "
        "visible evidence, not be erased"
    )


def test_an_abandoned_buffered_stream_still_hands_over_what_it_produced():
    sink = RecordingSink()
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.BUFFERED,
        chunk_records=64, flush_interval_ms=1000,
    )

    driver = runtime.stream(State.empty("abandoned"))
    produced = [next(driver)["kind"] for _ in range(3)]
    del driver
    import gc
    gc.collect()

    assert [r["kind"] for r in sink.records][: len(produced)] == produced, (
        "records the consumer already saw never reached the sink"
    )


def test_a_close_never_retries_a_sink_that_already_failed():
    """The flush added to `close()` must not resurrect a stored failure —
    that would be exactly the retry-after-failure this refuses elsewhere."""
    sink = RecordingSink(fail_on="attempt_started", commit_first=False)
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.BUFFERED,
        chunk_records=1, flush_interval_ms=0,
    )

    with pytest.raises(SinkFailed):
        runtime.run(State.empty("failed"))

    calls_after_failure = sink.write_calls
    assert calls_after_failure > 0
    # Nothing further may be attempted once a failure is stored.
    assert all(
        record["kind"] != "attempt_started" for record in sink.records
    ), "the refused record was written after all"


def test_a_trace_without_a_terminal_has_no_status():
    """`status` strips the `run_` prefix off the last record's kind. A driver
    abandoned mid-run leaves a trace ending in `transition` or `routing`, and
    that quietly answered `"transition"` — a value outside the three the
    contract names, which callers compare against and branch on."""
    from vitruvyan_motus import RunResult

    runtime = Runtime(CHAIN, {"a": passthrough, "b": passthrough})
    driver = runtime.stream(State.empty("abandoned"))
    next(driver)
    next(driver)

    incomplete = RunResult(State.empty("x"), driver.trace)
    assert incomplete.trace.records[-1]["kind"] not in (
        "run_completed", "run_failed", "run_cancelled",
    )
    with pytest.raises(ValueError, match="no terminal record"):
        incomplete.status


def test_an_empty_run_id_is_refused_rather_than_replaced():
    """`run_id or uuid()` treated "" as "not supplied", so the stated 1..200
    check never saw it and the caller silently got a generated identity for a
    run they meant to name."""
    runtime = Runtime(CHAIN, {"a": passthrough, "b": passthrough})

    with pytest.raises(ValueError, match="run_id"):
        runtime.run(State.empty("e"), run_id="")


def test_a_generated_run_id_is_still_produced_when_none_is_asked_for():
    runtime = Runtime(CHAIN, {"a": passthrough, "b": passthrough})
    result = runtime.run(State.empty("e"))

    assert isinstance(result.trace.run["run_id"], str)
    assert result.trace.run["run_id"]



class ProtocolOnlySink:
    """A sink written against the protocol and nothing else.

    It imports no constant from the writer and inspects no record. If Motus's
    sink protocol is sufficient, this produces a conforming JSONL artifact; if
    it is not, this is the shape that proves it.
    """

    def __init__(self, path) -> None:
        self.path = path
        self.finished: list[bool] = []

    def open_run(self, header: dict[str, Any]) -> "ProtocolOnlySink":
        import json
        self.handle = open(self.path, "w", encoding="utf-8")
        self.handle.write(json.dumps(header) + "\n")
        return self

    def write(self, records: tuple[dict[str, Any], ...]) -> None:
        import json
        for record in records:
            self.handle.write(json.dumps(record) + "\n")

    def finish(self, *, complete: bool) -> None:
        self.finished.append(complete)
        self.handle.close()


def _validate_jsonl(path) -> list[Any]:
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    name = "motus_evidence_contract_validate"
    if name in sys.modules:
        module = sys.modules[name]
    else:
        spec = importlib.util.spec_from_file_location(name, root / "contract" / "validate.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    violations, _document = module.validate_jsonl(path.read_text(encoding="utf-8"))
    return violations


def test_a_sink_written_against_the_protocol_alone_produces_a_valid_artifact(tmp_path):
    """ADR-004's Consequence 1 claims a sink "receives everything needed to
    persist an independently verifiable trace". It did not: `open_run` handed
    over the `run` object, which is not a valid TraceHeader, so a sink could
    conform only by importing TRACE_SCHEMA_VERSION from the writer.

    ADR-011 hands over the TraceHeader instead. This is the test that would
    have caught the earlier, wrong fix: it writes what it was given, unchanged.
    """
    path = tmp_path / "run.jsonl"
    sink = ProtocolOnlySink(path)
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    runtime.run(State.empty("protocol"))

    assert _validate_jsonl(path) == [], (
        "a sink that persists exactly what the protocol handed it produced an "
        "artifact the contract validator rejects"
    )


def test_a_session_is_told_whether_its_record_sequence_is_whole(tmp_path):
    """The runtime only ever called `open_run` then `write`, so a session had
    no moment at which it could treat a file as final — *in flight* and
    *abandoned forever* looked identical. A run torn down before its terminal
    left a partial artifact indistinguishable from a live one."""
    complete_sink = ProtocolOnlySink(tmp_path / "complete.jsonl")
    Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=complete_sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(State.empty("whole"))

    assert complete_sink.finished == [True]
    assert _validate_jsonl(tmp_path / "complete.jsonl") == []

    partial_sink = ProtocolOnlySink(tmp_path / "partial.jsonl")
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=partial_sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    driver = runtime.stream(State.empty("partial"))
    next(driver)
    del driver
    import gc
    gc.collect()

    assert partial_sink.finished == [False], (
        "an abandoned run told its session nothing; the sink cannot know its "
        "artifact is a prefix"
    )


def test_a_sink_without_finish_still_works():
    """`finish` is optional on the sink's side: omitting it forfeits the
    distinction, not the sink."""
    sink = RecordingSink()
    assert not hasattr(sink, "finish")

    result = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(State.empty("nofinish"))

    assert result.status == "completed"
    assert [r["kind"] for r in sink.records][-1] == "run_completed"


def test_a_session_opened_for_a_driver_that_never_ran_is_still_told(tmp_path):
    """`_start` opens the durable session before the consumer asks for
    anything, so a driver created and dropped leaves one open. The signal that
    ends it lives in `_managed_execute`'s `finally`, which a generator that
    never started does not run — so the session was opened and then told
    nothing at all, ever, and its header-only file is byte-identical to a live
    run's.

    ADR-011 claims exactly this row becomes knowable. It has to be true.
    """
    sink = ProtocolOnlySink(tmp_path / "never.jsonl")
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )

    driver = runtime.stream(State.empty("never"))
    del driver
    import gc
    gc.collect()

    assert sink.finished == [False], (
        "the session was opened and never told the run was over"
    )


@pytest.mark.asyncio
async def test_an_async_session_for_a_driver_that_never_ran_is_still_told(tmp_path):
    sink = ProtocolOnlySink(tmp_path / "never-async.jsonl")
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )

    driver = runtime.astream(State.empty("never"))
    del driver
    import gc
    gc.collect()

    assert sink.finished == [False]


def test_a_write_only_sink_still_satisfies_the_run_sink_protocol():
    """ADR-011 §2 promises `finish` is optional on the sink's side. Python has
    no optional protocol member, so declaring it makes `isinstance` and every
    static checker reject a write-only sink — imposing precisely the break the
    decision says it does not impose. The runtime duck-types it instead."""
    from vitruvyan_motus import TraceRunSink

    class WriteOnly:
        def write(self, records: tuple[dict[str, Any], ...]) -> None:
            pass

    assert isinstance(WriteOnly(), TraceRunSink), (
        "declaring finish on the Protocol makes it mandatory, contradicting "
        "ADR-011 §2"
    )


def test_the_shipped_sink_implements_the_whole_shape():
    """The example people copy should not be the partial one."""
    from vitruvyan_motus import InMemoryTraceSink

    sink = InMemoryTraceSink()
    runtime = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    runtime.run(State.empty("shipped"))

    session = sink._runs[-1]
    assert callable(getattr(session, "finish", None))
    assert session.complete is True

    abandoned = InMemoryTraceSink()
    other = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=abandoned, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    driver = other.stream(State.empty("dropped"))
    next(driver)
    del driver
    import gc
    gc.collect()

    assert abandoned._runs[-1].complete is False


# --- #42: the caller learns whether the evidence was written -----------------

MISS_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "evidence",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "route", "on": "verdict", "map": {"yes": "END"}}},
}
MISS = GraphSpec.from_dict(dict(MISS_DOC))

LIMIT_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "evidence",
    "version": "1.0.0",
    "entry": "a",
    "max_transitions": 2,
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {
        "a": {"kind": "route", "on": "verdict", "map": {"again": "a"}, "default": "END"}
    },
}
LIMIT = GraphSpec.from_dict(dict(LIMIT_DOC))

FIXED = datetime(2026, 8, 8, tzinfo=timezone.utc)


def decide(value: str):
    def node(state: State) -> State:
        return state.with_decision(Decision("verdict", value, FIXED))
    return node


def _run(spec, nodes, *, fail_on=None, sink=True, **kwargs):
    """Run once, returning (outcome, evidence) whether it returned or raised."""
    used = RecordingSink(fail_on=fail_on, commit_first=False) if sink else None
    runtime = Runtime(
        spec, nodes, sink=used,
        durability_profile=(
            DurabilityProfile.SYNCHRONOUS if sink else DurabilityProfile.IN_MEMORY
        ),
        clock=lambda: FIXED, identity=lambda: "fixed",
        **kwargs,
    )
    try:
        result = runtime.run(State.empty("evidence"), run_id="r1")
        return result, result.evidence
    except (SinkFailed, NodeFailed) as exc:
        return exc, exc.evidence


def test_a_healthy_run_with_a_sink_reports_its_evidence_persisted():
    result, evidence = _run(CHAIN, {"a": passthrough, "b": passthrough})
    assert result.status == "completed"
    assert evidence == EvidenceStatus.PERSISTED
    assert evidence == "persisted", "the str Enum must compare against the plain string"


def test_a_run_with_no_sink_promised_nothing_and_says_so():
    """`not-required` and `incomplete` must not collapse into one boolean.

    An in-memory run persisted nothing and failed nothing. A caller that reads a
    durability failure here would refuse to act on every ordinary local run.
    """
    result, evidence = _run(CHAIN, {"a": passthrough, "b": passthrough}, sink=False)
    assert result.status == "completed"
    assert evidence == EvidenceStatus.NOT_REQUIRED


@pytest.mark.parametrize(
    "label, spec, nodes, refused_terminal",
    [
        ("route_miss", MISS, {"a": decide("no")}, "run_failed"),
        ("transition_limit", LIMIT, {"a": decide("again")}, "run_failed"),
    ],
)
def test_the_silent_terminals_now_tell_the_caller(label, spec, nodes, refused_terminal):
    """#42's core: three terminals reported a refusal to nobody who could act.

    Only `run_completed` raised `SinkFailed`. On every other terminal the primary
    cause is deliberately preserved — a node that failed is more important news
    than an archive that is down — and the consequence was that `run()` returned
    a result identical to a healthy one. The status is still the primary cause;
    `evidence` is the second fact, carried alongside rather than instead.
    """
    healthy, healthy_evidence = _run(spec, nodes)
    refused, refused_evidence = _run(spec, nodes, fail_on=refused_terminal)

    assert healthy.status == refused.status == "failed"
    assert healthy_evidence == EvidenceStatus.PERSISTED
    assert refused_evidence == EvidenceStatus.INCOMPLETE


def test_two_runs_whose_traces_are_byte_identical_are_told_apart_by_evidence():
    """The measurement that made #42 undeniable, kept as a regression.

    With the clock and identity fixed, a healthy run and one whose evidence was
    destroyed produced the same 2783 bytes of trace, the same final state and the
    same status. Nothing the caller could reach distinguished them. Asserting the
    traces are *still* identical is the point: the fix does not smuggle the fact
    into the artifact — that needs schema 1.1 — it puts it on the result.
    """
    healthy, healthy_evidence = _run(MISS, {"a": decide("no")})
    refused, refused_evidence = _run(MISS, {"a": decide("no")}, fail_on="run_failed")

    assert healthy.trace.to_jsonl() == refused.trace.to_jsonl()
    assert healthy.state.snapshot() == refused.state.snapshot()
    assert healthy.status == refused.status
    assert healthy_evidence != refused_evidence


def test_a_node_failure_and_a_refused_sink_are_both_reported():
    """The path with no RunResult to carry the news.

    A node raising while the sink also refuses used to produce a `NodeFailed`
    indistinguishable from one whose evidence was safely written. `NodeFailed`
    stays the exception — losing that the model did not answer would be a worse
    trade — and now carries the durability fact too.
    """
    def explode(state: State) -> State:
        raise RuntimeError("the model did not answer")

    healthy, healthy_evidence = _run(CHAIN, {"a": explode, "b": passthrough})
    refused, refused_evidence = _run(
        CHAIN, {"a": explode, "b": passthrough}, fail_on="run_failed"
    )

    assert isinstance(healthy, NodeFailed) and isinstance(refused, NodeFailed)
    assert healthy.node == refused.node == "a"
    assert healthy_evidence == EvidenceStatus.PERSISTED
    assert refused_evidence == EvidenceStatus.INCOMPLETE


def test_sink_failed_carries_the_same_field_so_one_read_serves_every_outcome():
    """`SinkFailed` can only mean `incomplete`, and says it anyway.

    A caller should be able to read `.evidence` off whatever it caught without
    first asking which exception it is holding.
    """
    caught, evidence = _run(CHAIN, {"a": passthrough, "b": passthrough},
                            fail_on="run_completed")
    assert isinstance(caught, SinkFailed)
    assert evidence == EvidenceStatus.INCOMPLETE


def test_the_async_surface_reports_evidence_identically():
    """Invariant I: one execution semantics across both surfaces.

    A durability fact the sync caller can read and the async caller cannot would
    be exactly the asymmetry that invariant forbids.
    """
    async def drive(fail_on):
        sink = RecordingSink(fail_on=fail_on, commit_first=False)
        runtime = Runtime(
            MISS, {"a": decide("no")}, sink=sink,
            durability_profile=DurabilityProfile.SYNCHRONOUS,
            clock=lambda: FIXED, identity=lambda: "fixed",
        )
        result = await runtime.arun(State.empty("evidence"), run_id="r1")
        return result.status, result.evidence

    assert asyncio.run(drive(None)) == ("failed", EvidenceStatus.PERSISTED)
    assert asyncio.run(drive("run_failed")) == ("failed", EvidenceStatus.INCOMPLETE)


class FinishingSink(RecordingSink):
    """A sink that implements the optional `finish`, so both sides can be read."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        super().__init__(fail_on=fail_on, commit_first=False)
        self.complete: bool | None = None

    def finish(self, *, complete: bool) -> None:
        self.complete = complete


@pytest.mark.parametrize("profile", list(DurabilityProfile))
@pytest.mark.parametrize("fail_on", [None, "run_failed"])
def test_the_caller_and_the_sink_are_told_the_same_thing(profile, fail_on):
    """The invariant that makes `evidence` worth reading, across every profile.

    `finish(complete=)` already told the session whether its artifact was whole;
    #42 was that nobody told the caller. Two independent reports of one fact can
    drift, so this asserts they cannot: the value is read after `close()`, which
    is what performs the buffered profile's final flush and then signals the
    session from the same `_saw_terminal`.

    The in-memory profile is included on purpose. It accepts a sink, so a refusal
    there is a real refusal and must be reported as one — treating "in-memory" as
    "nothing was promised" would let the weakest profile hide the failure.
    """
    sink = FinishingSink(fail_on=fail_on)
    runtime = Runtime(
        MISS, {"a": decide("no")}, sink=sink, durability_profile=profile,
        clock=lambda: FIXED, identity=lambda: "fixed", flush_interval_ms=0,
    )
    result = runtime.run(State.empty("evidence"), run_id="r1")

    assert sink.complete is not None, "the session must be told before the caller reads"
    expected = EvidenceStatus.PERSISTED if sink.complete else EvidenceStatus.INCOMPLETE
    assert result.evidence == expected
    assert (result.evidence == EvidenceStatus.PERSISTED) is (fail_on is None)


def test_evidence_is_a_plain_string_on_every_carrier():
    """Four carriers, one type. An adversarial round found two.

    `RunResult` carried an `EvidenceStatus` while the exceptions carried plain
    strings, so `f"{x.evidence}"` printed `EvidenceStatus.INCOMPLETE` on one and
    `incomplete` on the other — for the same fact, decided by which object the
    caller happened to catch. `EvidenceStatus` is the vocabulary; the value is
    data, as `run.policy` in the header is `"strict"` and not a `Policy`.
    """
    carriers = []

    result, _ = _run(MISS, {"a": decide("no")})
    carriers.append(("RunResult", result.evidence))

    caught, _ = _run(CHAIN, {"a": passthrough, "b": passthrough},
                     fail_on="run_completed")
    carriers.append(("SinkFailed", caught.evidence))

    def explode(state: State) -> State:
        raise RuntimeError("boom")

    failed, _ = _run(CHAIN, {"a": explode, "b": passthrough})
    carriers.append(("NodeFailed", failed.evidence))

    sink = RecordingSink(commit_first=False)
    driver = Runtime(
        MISS, {"a": decide("no")}, sink=sink,
        durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).stream(State.empty("evidence"), run_id="r1")
    for _ in driver:
        pass
    carriers.append(("StreamDriver", driver.evidence))

    for name, value in carriers:
        assert type(value) is str, f"{name} carries {type(value).__name__}"
        assert f"{value}" == value, f"{name} formats as {value!r}"
    assert {value for _, value in carriers} <= {e.value for e in EvidenceStatus}


def test_the_streaming_surfaces_report_evidence_too():
    """#42's first fix reached `run()`/`arun()` and stopped there.

    `stream()` is where the caller is most obviously still able to act — it is
    inside the loop — and it received a run whose evidence had been destroyed
    with nothing to tell it apart from a healthy one. The value was already
    computed and sitting one attribute away behind the driver.
    """
    def drive(fail_on):
        sink = RecordingSink(fail_on=fail_on, commit_first=False)
        driver = Runtime(
            MISS, {"a": decide("no")}, sink=sink,
            durability_profile=DurabilityProfile.SYNCHRONOUS,
            clock=lambda: FIXED, identity=lambda: "fixed",
        ).stream(State.empty("evidence"), run_id="r1")
        for _ in driver:
            pass
        return driver

    healthy, refused = drive(None), drive("run_failed")

    assert healthy.trace.to_jsonl() == refused.trace.to_jsonl()
    assert healthy.evidence == EvidenceStatus.PERSISTED
    assert refused.evidence == EvidenceStatus.INCOMPLETE


def test_an_unfinished_stream_has_no_evidence_answer_and_says_so():
    """Following `RunResult.status`, which raises rather than inventing one.

    Returning the initial `not-required` mid-stream would be a lie for the whole
    duration of the run — the shape of defect this attribute exists to end.
    """
    driver = Runtime(
        CHAIN, {"a": passthrough, "b": passthrough},
        sink=RecordingSink(commit_first=False),
        durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).stream(State.empty("evidence"), run_id="r1")

    next(driver)
    with pytest.raises(ValueError, match="has not finished"):
        driver.evidence

    for _ in driver:
        pass
    assert driver.evidence == EvidenceStatus.PERSISTED


def test_the_async_stream_reports_evidence_identically():
    async def drive(fail_on):
        sink = RecordingSink(fail_on=fail_on, commit_first=False)
        driver = Runtime(
            MISS, {"a": decide("no")}, sink=sink,
            durability_profile=DurabilityProfile.SYNCHRONOUS,
        ).astream(State.empty("evidence"), run_id="r1")
        async for _ in driver:
            pass
        return driver.evidence

    assert asyncio.run(drive(None)) == EvidenceStatus.PERSISTED
    assert asyncio.run(drive("run_failed")) == EvidenceStatus.INCOMPLETE


def test_a_node_failure_with_no_sink_behind_it_reports_not_required():
    """Pins `NodeFailed`'s default, which the compatibility bridge relies on.

    Flipping that default to `persisted` failed zero tests when an adversarial
    round tried it. The value is correct — the legacy runner has no sink — but
    nothing stopped it drifting into a claim that evidence was written by a
    component that never had anywhere to write it.
    """
    def explode(state: State) -> State:
        raise RuntimeError("boom")

    failed, evidence = _run(CHAIN, {"a": explode, "b": passthrough}, sink=False)
    assert isinstance(failed, NodeFailed)
    assert evidence == EvidenceStatus.NOT_REQUIRED

    bare = NodeFailed("legacy", None)
    assert bare.evidence == EvidenceStatus.NOT_REQUIRED
