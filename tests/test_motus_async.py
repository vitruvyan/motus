"""The asynchronous driver, and the invariant that makes it safe to have one.

guarantees.md invariant I forbids co-equal engines: "All physical
optimizations pass through the same executor and produce the same observable
trace." These tests hold that line the cheap way — by proving there is only
one state machine, not by comparing two.

``Runtime._execute`` no longer calls a node. It yields a request and receives
the outcome, so ``run``/``stream`` and ``arun``/``astream`` differ only in how
they answer. The central assertion below is therefore equality of the record
streams, byte for byte, once the declared nondeterminism sources are pinned.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import (
    Decision,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    NodeFailed,
    Policy,
    Runtime,
    State,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _validator():
    spec = importlib.util.spec_from_file_location(
        "motus_contract_validate_async", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate = _validator()

ROUTED_DOC = {
    "schema_version": "1.0.0",
    "name": "async-routed",
    "version": "1.0.0",
    "entry": "classify",
    "nodes": [
        {"name": "classify", "effect_class": "pure"},
        {"name": "review", "effect_class": "pure"},
        {"name": "reject", "effect_class": "pure"},
    ],
    "transitions": {
        "classify": {
            "kind": "route",
            "on": "risk",
            "map": {"review": "review"},
            "default": "reject",
        },
        "review": {"kind": "terminal"},
        "reject": {"kind": "terminal"},
    },
}
ROUTED = GraphSpec.from_dict(dict(ROUTED_DOC))

LINEAR_DOC = {
    "schema_version": "1.0.0",
    "name": "async-linear",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))


def classify(state: State) -> State:
    return state.with_decision(Decision("risk", "review", NOW))


def review(state: State) -> State:
    return state.with_fact(Fact("outcome", "escalated", "reviewer", NOW))


def reject(state: State) -> State:
    return state.with_fact(Fact("outcome", "rejected", "reviewer", NOW))


async def aclassify(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_decision(Decision("risk", "review", NOW))


async def areview(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("outcome", "escalated", "reviewer", NOW))


async def areject(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("outcome", "rejected", "reviewer", NOW))


def _comparable(trace) -> dict:
    """The trace with only the declared nondeterminism sources pinned.

    Timestamps come from the run clock, the run id from the identity source
    and the code fingerprint from the node registry — a sync and an async
    graph are necessarily written by different callables. Everything else is
    the interpreter's output and must match exactly.
    """
    document = json.loads(json.dumps(trace.to_dict()))
    document["run"].pop("created_ts")
    document["run"]["graph"]["code_fingerprint"] = "<pinned>"
    for record in document["records"]:
        record.pop("ts")
    return document


# --------------------------------------------------------------------------- #
# The invariant                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_async_driver_emits_the_interpreter_s_own_record_stream():
    synchronous = Runtime(
        ROUTED, {"classify": classify, "review": review, "reject": reject}
    ).run(State.empty("equivalence"), run_id="pinned")
    asynchronous = await Runtime(
        ROUTED, {"classify": aclassify, "review": areview, "reject": areject}
    ).arun(State.empty("equivalence"), run_id="pinned")

    assert _comparable(synchronous.trace) == _comparable(asynchronous.trace)
    assert synchronous.state.snapshot() == asynchronous.state.snapshot()
    assert asynchronous.status == "completed"


@pytest.mark.asyncio
async def test_a_graph_may_mix_sync_and_async_nodes():
    reference = Runtime(
        ROUTED, {"classify": classify, "review": review, "reject": reject}
    ).run(State.empty("mixed"), run_id="pinned")
    mixed = await Runtime(
        ROUTED, {"classify": classify, "review": areview, "reject": reject}
    ).arun(State.empty("mixed"), run_id="pinned")

    assert _comparable(mixed.trace) == _comparable(reference.trace)


@pytest.mark.asyncio
async def test_async_traces_satisfy_the_contract_in_both_encodings():
    result = await Runtime(
        ROUTED, {"classify": aclassify, "review": areview, "reject": areject}
    ).arun(State.empty("conformance"))

    document = result.trace.to_dict()
    assert validate.validate_trace(document, spec=ROUTED_DOC) == []
    violations, reassembled = validate.validate_jsonl(
        result.trace.to_jsonl(), spec=ROUTED_DOC
    )
    assert violations == []
    assert reassembled == document


# --------------------------------------------------------------------------- #
# Driver mismatch                                                              #
# --------------------------------------------------------------------------- #


def test_an_async_node_under_the_sync_driver_says_which_driver_to_use():
    runtime = Runtime(
        ROUTED, {"classify": aclassify, "review": review, "reject": reject}
    )
    with pytest.raises(NodeFailed) as raised:
        runtime.run(State.empty("mismatch"))

    assert isinstance(raised.value.cause, TypeError)
    assert "Runtime.arun()" in str(raised.value.cause)
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert transition["outcome"] == "raised"
    assert transition["writes"] == {"facts": [], "decisions": [], "rejections": []}
    assert validate.validate_trace(runtime.trace.to_dict(), spec=ROUTED_DOC) == []


# --------------------------------------------------------------------------- #
# Failure, retry, effects                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_async_node_that_raises_is_an_ordinary_raised_attempt():
    async def boom(state: State) -> State:
        await asyncio.sleep(0)
        raise ValueError("the dependency is down")

    runtime = Runtime(LINEAR, {"a": boom, "b": lambda s: s})
    with pytest.raises(NodeFailed) as raised:
        await runtime.arun(State.empty("failure"))

    assert isinstance(raised.value.cause, ValueError)
    transition = next(r for r in runtime.trace.records if r["kind"] == "transition")
    assert (transition["outcome"], transition["disposition"]) == ("raised", "abort")
    assert runtime.trace.records[-1]["cause"]["kind"] == "node_failure"
    assert validate.validate_trace(runtime.trace.to_dict(), spec=LINEAR_DOC) == []


@pytest.mark.asyncio
async def test_async_retry_records_every_attempt():
    attempts = []

    async def flaky(state: State) -> State:
        await asyncio.sleep(0)
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RuntimeError("transient")
        return state.with_fact(Fact("k", len(attempts), "s", NOW))

    result = await Runtime(
        LINEAR, {"a": flaky, "b": lambda s: s}, max_attempts={"a": 3, "b": 1}
    ).arun(State.empty("retry"))

    recorded = [
        (r["attempt"], r["outcome"], r["disposition"])
        for r in result.trace.records
        if r["kind"] == "transition" and r["node"] == "a"
    ]
    assert recorded == [
        (1, "raised", "retry"),
        (2, "raised", "retry"),
        (3, "returned", "commit"),
    ]
    assert result.state.fact("k") == 3
    assert validate.validate_trace(result.trace.to_dict(), spec=LINEAR_DOC) == []


@pytest.mark.asyncio
async def test_cancelled_error_tears_the_run_down_rather_than_becoming_a_failure():
    """``asyncio.CancelledError`` is a ``BaseException``: node-protocol §7.3
    says such an interruption leaves its ``attempt_started`` unclosed, visible
    and attributable — it is never laundered into a raised attempt."""

    async def cancelled(state: State) -> State:
        raise asyncio.CancelledError()

    runtime = Runtime(LINEAR, {"a": cancelled, "b": lambda s: s})
    with pytest.raises(asyncio.CancelledError):
        await runtime.arun(State.empty("torn"))

    kinds = [r["kind"] for r in runtime.trace.records]
    assert kinds == ["run_started", "attempt_started"]
    assert not runtime._running


# --------------------------------------------------------------------------- #
# astream                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_astream_is_consumer_paced_and_reaches_the_terminal():
    runtime = Runtime(
        ROUTED, {"classify": aclassify, "review": areview, "reject": areject}
    )
    async with runtime.astream(State.empty("streamed")) as driver:
        kinds = [record["kind"] async for record in driver]

    assert kinds[0] == "run_started"
    assert kinds[-1] == "run_completed"
    assert kinds == [r["kind"] for r in driver.trace.records]


@pytest.mark.asyncio
async def test_astream_close_mid_run_lands_a_recorded_cancellation():
    runtime = Runtime(
        ROUTED, {"classify": aclassify, "review": areview, "reject": areject}
    )
    driver = runtime.astream(State.empty("stopped"))
    await driver.__anext__()
    await driver.__anext__()
    await driver.aclose("consumer stopped")

    terminal = driver.trace.records[-1]
    assert terminal["kind"] == "run_cancelled"
    assert terminal["reason"] == "consumer stopped"
    assert terminal["active_attempt"] == {"node": "classify", "attempt": 1}


@pytest.mark.asyncio
async def test_an_exhausted_astream_cannot_cancel_a_later_run():
    """ADR-008 §1, carried to the asynchronous driver: normal exhaustion
    closes the driver, so context exit afterwards is a no-op."""
    runtime = Runtime(
        ROUTED, {"classify": aclassify, "review": areview, "reject": areject}
    )
    async with runtime.astream(State.empty("first")) as driver:
        async for _ in driver:
            pass

    second = await runtime.arun(State.empty("second"))
    assert second.status == "completed"


@pytest.mark.asyncio
async def test_pre_run_cancellation_is_honoured_by_the_async_driver():
    executed = []

    async def node(state: State) -> State:
        executed.append("a")
        return state

    runtime = Runtime(LINEAR, {"a": node, "b": node})
    assert runtime.cancel("queued before the run") is True
    result = await runtime.arun(State.empty("cancelled"))

    assert result.status == "cancelled"
    assert executed == []
    assert (await runtime.arun(State.empty("after"))).status == "completed"


# --------------------------------------------------------------------------- #
# The rest of the runtime is unchanged by the driver                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sinks_listeners_and_policy_behave_identically_under_arun():
    sink = InMemoryTraceSink()
    seen: list[str] = []

    class Recorder:
        def on_record(self, record):
            seen.append(record["kind"])

    async def boom(state: State) -> State:
        await asyncio.sleep(0)
        raise RuntimeError("down")

    result = await Runtime(
        LINEAR,
        {"a": boom, "b": lambda s: s},
        policy=Policy.EXPLORATION,
        durability_profile="synchronous",
        sink=sink,
        listeners=(Recorder(),),
    ).arun(State.empty("surfaces"))

    assert result.status == "completed"
    assert [r["kind"] for r in sink.records] == [r["kind"] for r in result.trace.records]
    assert seen == [r["kind"] for r in result.trace.records]
    assert validate.validate_trace(result.trace.to_dict(), spec=LINEAR_DOC) == []


@pytest.mark.asyncio
async def test_one_runtime_still_refuses_overlapping_async_runs():
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(state: State) -> State:
        started.set()
        await release.wait()
        return state

    runtime = Runtime(LINEAR, {"a": slow, "b": lambda s: s})
    first = asyncio.create_task(runtime.arun(State.empty("first")))
    await started.wait()
    with pytest.raises(RuntimeError, match="overlapping runs"):
        await runtime.arun(State.empty("second"))
    release.set()
    assert (await first).status == "completed"
