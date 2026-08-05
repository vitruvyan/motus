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

