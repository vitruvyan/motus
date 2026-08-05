"""What a durable sink actually receives, and whether it can be trusted.

Motus's thesis is evidence you can independently re-check, so the failures that
matter most are the ones where the persisted account is *worse than absent*:
contradictory, or silently short. Both were reachable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from vitruvyan_motus import (
    DurabilityProfile, GraphSpec, Runtime, SinkFailed, State,
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
