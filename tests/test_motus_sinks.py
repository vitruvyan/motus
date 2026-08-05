"""The durable file sink, checked against the contract validator every time.

`JsonlTraceSink` is the first sink in the package that keeps a crash-persistence
promise, so the `buffered` and `synchronous` profiles stop being guarantees
nobody can reach. It is also the executable proof of ADR-011: it imports no
schema version and inspects no record kind, so if the protocol were not
sufficient this file could not be written.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from vitruvyan_motus import (
    DurabilityProfile, Fact, GraphSpec, JsonlTraceSink, NodeFailed, Runtime,
    State, TraceSink,
)
from datetime import datetime, timezone

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]

CHAIN = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "sink-suite",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
})


def note(state: State) -> State:
    return state.with_fact(Fact("seen", state.intent, "test", NOW))


def passthrough(state: State) -> State:
    return state


async def anote(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("seen", state.intent, "test", NOW))


def _validator():
    name = "motus_sinks_contract_validate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "contract" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def assert_valid(path: Path, *, complete: bool = True) -> dict[str, Any]:
    violations, document = _validator().validate_jsonl(
        path.read_text(encoding="utf-8"),
        spec=CHAIN.to_dict(),
        expect_complete=complete,
    )
    assert violations == [], "\n".join(map(str, violations))
    return document


@pytest.mark.parametrize(
    "profile",
    [DurabilityProfile.SYNCHRONOUS, DurabilityProfile.BUFFERED, DurabilityProfile.IN_MEMORY],
)
def test_a_completed_run_lands_as_a_valid_document(tmp_path, profile):
    sink = JsonlTraceSink(tmp_path, fsync=False)
    result = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=profile,
    ).run(State.empty("complete"), run_id="complete")

    assert len(sink.artifacts) == 1
    artifact = sink.artifacts[0]
    assert artifact.name.endswith(".jsonl")
    assert not artifact.name.endswith((".partial.jsonl", ".jsonl.part")), (
        f"a completed run must not be named as unfinished: {artifact.name}"
    )
    document = assert_valid(artifact)

    # The persisted account IS the trace, not a summary of it (guarantees.md §6).
    assert document == result.trace.to_dict()


def test_an_async_run_lands_the_same_way(tmp_path):
    sink = JsonlTraceSink(tmp_path, fsync=False)
    result = asyncio.run(
        Runtime(
            CHAIN, {"a": anote, "b": passthrough, "c": passthrough},
            sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
        ).arun(State.empty("async"), run_id="async")
    )

    assert assert_valid(sink.artifacts[0]) == result.trace.to_dict()


def test_a_failed_run_still_lands_a_whole_document(tmp_path):
    def boom(state: State) -> State:
        raise RuntimeError("node failed")

    sink = JsonlTraceSink(tmp_path, fsync=False)
    with pytest.raises(NodeFailed):
        Runtime(
            CHAIN, {"a": note, "b": boom, "c": passthrough},
            sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
        ).run(State.empty("failed"), run_id="failed")

    document = assert_valid(sink.artifacts[0])
    assert document["records"][-1]["kind"] == "run_failed"


def test_a_cancelled_run_lands_a_whole_document(tmp_path):
    sink = JsonlTraceSink(tmp_path, fsync=False)
    runtime = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    with runtime.stream(State.empty("cancelled"), run_id="cancelled") as driver:
        next(driver)
        next(driver)

    document = assert_valid(sink.artifacts[0])
    assert document["records"][-1]["kind"] == "run_cancelled"


def test_a_run_cut_short_is_named_for_what_it_is(tmp_path):
    """A prefix is still evidence — it just is not a whole account. The file
    says so in its name rather than looking like a finished run."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    runtime = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    driver = runtime.stream(State.empty("cut"), run_id="cut")
    next(driver)
    next(driver)
    del driver
    gc.collect()

    artifact = sink.artifacts[0]
    assert artifact.name.endswith(".partial.jsonl"), (
        f"a truncated account must not look like a finished one: {artifact.name}"
    )
    assert_valid(artifact, complete=False)


def test_a_run_that_produced_nothing_leaves_no_artifact(tmp_path):
    """A header-only file cannot be a valid document — the schema requires at
    least one record — so publishing one would publish something no reader can
    accept. ADR-011: `complete=False` with nothing written is what lets the sink
    decline to publish it."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    runtime = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    driver = runtime.stream(State.empty("nothing"), run_id="nothing")
    del driver
    gc.collect()

    assert sink.artifacts == ()
    assert list(tmp_path.iterdir()) == [], (
        f"a header-only artifact was published: {list(tmp_path.iterdir())}"
    )


def test_a_run_still_in_flight_is_not_named_as_finished(tmp_path):
    """Nothing has declared this run over, so neither final name may appear.
    A process that dies here leaves exactly this state, and it is honest."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    runtime = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    )
    driver = runtime.stream(State.empty("inflight"), run_id="inflight")
    next(driver)

    names = sorted(path.name for path in tmp_path.iterdir())
    assert len(names) == 1, names
    assert names[0].endswith(".jsonl.part"), (
        f"nothing has declared this run over; it must not carry a final name: {names[0]}"
    )
    driver.close("done with it")
    assert sink.artifacts[0].name.endswith(".jsonl")


def test_several_runs_on_one_sink_stay_separate(tmp_path):
    sink = JsonlTraceSink(tmp_path, fsync=False)
    for tag in ("first", "second", "third"):
        Runtime(
            CHAIN, {"a": note, "b": passthrough, "c": passthrough},
            sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
        ).run(State.empty(tag), run_id=tag)

    assert len(sink.artifacts) == 3
    landed = set()
    for artifact in sink.artifacts:
        document = assert_valid(artifact)
        landed.add(document["run"]["run_id"])
    assert landed == {"first", "second", "third"}


def test_a_hostile_run_id_cannot_escape_the_directory(tmp_path):
    """`run_id` is caller-supplied and may be any string of 1..200 characters,
    separators included. It names a file here, so it must not be able to choose
    which one."""
    target = tmp_path / "sink"
    sink = JsonlTraceSink(target, fsync=False)
    Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(State.empty("hostile"), run_id="../../escaped")

    artifact = sink.artifacts[0]
    assert artifact.parent == target, f"the artifact escaped to {artifact}"
    assert not (tmp_path.parent / "escaped.jsonl").exists()
    assert_valid(artifact)


def test_two_run_ids_that_sanitise_alike_do_not_collide(tmp_path):
    """Sanitising alone would map distinct runs onto one file and silently
    overwrite evidence, which is the failure this sink exists to prevent."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    for run_id in ("a/b", "a:b"):
        Runtime(
            CHAIN, {"a": note, "b": passthrough, "c": passthrough},
            sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
        ).run(State.empty("collide"), run_id=run_id)

    assert len(set(sink.artifacts)) == 2
    assert {assert_valid(path)["run"]["run_id"] for path in sink.artifacts} == {"a/b", "a:b"}


def test_the_sink_reads_nothing_from_the_writer_but_the_protocol():
    """ADR-011's claim, enforced. If this sink needed the schema version or the
    record kinds, the protocol would not be sufficient and the claim would be
    false — as it was before ADR-011."""
    source = (ROOT / "src" / "vitruvyan_motus" / "sinks.py").read_text(encoding="utf-8")

    assert "TRACE_SCHEMA_VERSION" not in source
    assert "run_completed" not in source
    assert "vitruvyan_motus" not in source.split('"""', 2)[2], (
        "the reference sink must not import from the package it persists for"
    )


def test_the_written_bytes_are_the_trace_the_runtime_would_have_written(tmp_path):
    """The file is not a re-serialisation with its own opinions; it is the same
    document, line for line."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    result = Runtime(
        CHAIN, {"a": note, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(State.empty("bytes"), run_id="bytes")

    written = [json.loads(line) for line in sink.artifacts[0].read_text().splitlines()]
    expected = [json.loads(line) for line in result.trace.to_jsonl().splitlines()]
    assert written == expected
