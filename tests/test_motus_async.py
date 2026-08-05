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
import functools
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
    ReplayMismatch,
    ReplayStatus,
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
        # Replay constraints derived from node IDENTITY are registry
        # properties, not driver properties — exactly like code_fingerprint.
        # An async node is executable but not verify-replayable, so it earns a
        # `node:<name>:async` constraint the synchronous twin cannot have. The
        # equivalence being asserted is of the state machine's decisions, and
        # this is not one of them.
        replay = record.get("replay")
        if isinstance(replay, dict):
            replay["constraints"] = [
                c for c in replay["constraints"] if not c.endswith(":async")
            ]
            if not replay["constraints"] and replay["capability"] == "partial":
                replay["capability"] = "<registry-derived>"
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


# --------------------------------------------------------------------------- #
# Regressions from the adversarial round on this branch                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_driver_outliving_its_run_cannot_cancel_a_later_one():
    """The RA-001 shape, in its asynchronous form.

    An event loop finalises abandoned async generators at shutdown, so a run
    can end without its driver noticing and ``_closed`` staying False. A bare
    ``Runtime.cancel`` would then bind to whatever run is live. ADR-008 §1:
    "an idle call ... cannot cancel a later unrelated run."
    """
    runtime = Runtime(LINEAR, {"a": lambda s: s, "b": lambda s: s})
    stale = runtime.astream(State.empty("stale"), run_id="stale")
    await stale.__anext__()

    # End the first run the way a loop shutdown would, behind the driver's back.
    await stale._iterator.aclose()
    assert stale._closed is False

    later = await runtime.arun(State.empty("later"), run_id="later")
    await stale.aclose("stale context exit")

    assert later.status == "completed"
    assert runtime._pending_cancel_reason is None
    assert (await runtime.arun(State.empty("after"))).status == "completed"


@pytest.mark.asyncio
async def test_aclose_during_an_in_flight_anext_still_lands_a_terminal():
    """The graceful-shutdown shape: a supervisor closes a driver that another
    task is reading. Draining from the supervisor would raise "asynchronous
    generator is already running" and orphan the run — guarantees.md §6 says
    cancellation lands "as the trace-recorded run_cancelled ... never as an
    abandoned generator"."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    runtime = Runtime(LINEAR, {"a": slow, "b": lambda s: s})
    driver = runtime.astream(State.empty("shutdown"))
    consumer = asyncio.create_task(_consume(driver))
    await entered.wait()

    await driver.aclose("graceful shutdown")
    release.set()
    kinds = await consumer

    assert kinds[-1] == "run_cancelled"
    assert not runtime._running
    assert (await runtime.arun(State.empty("after"))).status == "completed"


async def _consume(driver) -> list[str]:
    return [record["kind"] async for record in driver]


def test_a_failing_awaitable_cleanup_stays_inside_the_node_failure_boundary():
    """``_invoke_sync`` closes an awaitable it refuses. ``close()`` can itself
    raise — a coroutine that swallows ``GeneratorExit`` raises RuntimeError —
    and that must be an ordinary raised attempt, not an escape that leaves the
    attempt unclosed as though the run had crashed (node-protocol §7.3)."""

    async def swallows_generator_exit(state: State) -> State:
        try:
            await asyncio.sleep(0)
        except GeneratorExit:
            await asyncio.sleep(0)
        return state

    def node(state: State) -> State:
        coroutine = swallows_generator_exit(state)
        coroutine.send(None)  # start it, so close() has something to interrupt
        return coroutine

    runtime = Runtime(LINEAR, {"a": node, "b": lambda s: s})
    with pytest.raises(NodeFailed):
        runtime.run(State.empty("cleanup"))

    kinds = [r["kind"] for r in runtime.trace.records]
    assert kinds == ["run_started", "attempt_started", "transition", "run_failed"]
    assert validate.validate_trace(runtime.trace.to_dict(), spec=LINEAR_DOC) == []


@pytest.mark.asyncio
async def test_verify_refuses_an_async_node_instead_of_accusing_it_of_divergence():
    """``ReplayMismatch`` is the contract's signal that the code changed.
    Raising it for an async node — which ``verify`` simply cannot drive —
    would accuse unchanged code, and leak the coroutine besides."""
    from vitruvyan_motus import ReplayEngine, ReplayStatus, TraceBundle
    from vitruvyan_motus.errors import ReplayError, ReplayUnsupported

    async def pure(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact("k", 1, "s", NOW))

    result = await Runtime(LINEAR, {"a": pure, "b": lambda s: s}).arun(
        State.empty("verify"), replay=ReplayStatus.declared("full")
    )
    engine = ReplayEngine(TraceBundle(LINEAR, result.trace))

    with pytest.raises(ReplayUnsupported) as raised:
        engine.verify({"a": pure, "b": lambda s: s})
    assert "asynchronous" in str(raised.value)
    # Distinct from ReplayMismatch on purpose: "this engine cannot drive the
    # node" must be separable from "the recorded evidence and the code
    # disagree", which is the contract's tampering/drift signal.
    assert issubclass(ReplayUnsupported, ReplayError)
    assert not isinstance(raised.value, ReplayMismatch)


@pytest.mark.asyncio
async def test_a_stale_driver_cannot_cancel_a_run_that_is_still_starting():
    """The run-scoped cancel keys on the identity of the per-run handle. That
    identity must be published in the same critical section that claims
    `_running`: assigning it after the controller, the hub and
    `motus_config()` evaluation would leave a window where `_running` names
    the new run while the handle still names the old one, and the scope test
    would wave a stale driver through."""
    from vitruvyan_motus.runtime import _RunHandle
    runtime = Runtime(LINEAR, {"a": lambda s: s, "b": lambda s: s})
    stale = runtime.astream(State.empty("first"), run_id="first")
    await stale.__anext__()
    scoped_cancel = stale._cancel
    await stale._iterator.aclose()  # end run one behind the driver's back

    # Claim the next run's identity the way `_start` does, then check the
    # stale driver's scope from inside that window — before any record exists.
    with runtime._lifecycle_lock:
        runtime._running = True
        runtime._has_started = True
        runtime._run = _RunHandle()
        window_verdict = scoped_cancel("stale context exit")
        runtime._running = False
        runtime._cancel_reason = None

    assert window_verdict is False, "a stale driver was admitted mid-start"
    assert (await runtime.arun(State.empty("later"))).status == "completed"


@pytest.mark.asyncio
async def test_a_concurrency_clash_does_not_close_the_driver():
    """`__anext__` latches `_closed` so a finished driver cannot cancel a later
    run. But "asynchronous generator is already running" means the generator is
    *alive* and another task is inside it — latching there would strand that
    run with no terminal record, which guarantees.md §6 forbids."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    runtime = Runtime(LINEAR, {"a": slow, "b": lambda s: s})
    driver = runtime.astream(State.empty("clash"))
    consumer = asyncio.create_task(_consume(driver))
    await entered.wait()

    with pytest.raises(RuntimeError, match="already running"):
        await driver.__anext__()
    assert driver._closed is False  # transient, not termination

    await driver.aclose("after the clash")
    release.set()
    kinds = await consumer

    assert kinds[-1] == "run_cancelled"
    assert not runtime._running
    assert (await runtime.arun(State.empty("after"))).status == "completed"


@pytest.mark.asyncio
async def test_the_recorded_cancellation_reason_is_the_one_that_stopped_the_run():
    """A retried or context-manager-triggered `aclose` must not overwrite the
    reason the trace attributes the cancellation to."""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    runtime = Runtime(LINEAR, {"a": slow, "b": lambda s: s})
    driver = runtime.astream(State.empty("attribution"))
    consumer = asyncio.create_task(_consume(driver))
    await entered.wait()

    await driver.aclose("the reason that stopped it")
    await driver.aclose("a later, irrelevant reason")
    release.set()
    await consumer

    assert driver.trace.records[-1]["reason"] == "the reason that stopped it"


# --------------------------------------------------------------------------- #
# Evidence-shape assertions                                                    #
#                                                                              #
# A mutation probe found that deleting two of the fixes above left this suite  #
# green: it asserted behaviour thoroughly and the SHAPE OF THE EVIDENCE not at #
# all. These are the assertions that were missing.                             #
# --------------------------------------------------------------------------- #


def module_level_sync(state: State) -> State:
    return state


async def module_level_async(state: State) -> State:
    await asyncio.sleep(0)
    return state


def _sync_to_async(function):
    @functools.wraps(function)
    async def wrapper(state: State) -> State:
        return function(state)
    return wrapper


wrapped_async = _sync_to_async(module_level_sync)


def returns_a_coroutine(state: State) -> State:
    return module_level_async(state)


@pytest.mark.asyncio
async def test_an_async_node_degrades_the_recorded_replay_capability():
    """Invariant IV: replay capability is an explicit recorded property. An
    async node is executable but not verify-replayable — `verify` refuses it
    and `resume` cannot drive it — so a run containing one may not keep a
    `full` claim it cannot honour."""
    synchronous = Runtime(LINEAR, {"a": module_level_sync, "b": module_level_sync}).run(
        State.empty("sync"), replay=ReplayStatus.declared("full")
    )
    assert synchronous.trace.records[-1]["replay"] == {
        "capability": "full",
        "constraints": [],
    }

    asynchronous = await Runtime(
        LINEAR, {"a": module_level_async, "b": module_level_sync}
    ).arun(State.empty("async"), replay=ReplayStatus.declared("full"))
    terminal = asynchronous.trace.records[-1]["replay"]
    assert terminal["capability"] == "partial"
    assert "node:a:async" in terminal["constraints"]
    assert validate.validate_trace(
        asynchronous.trace.to_dict(), spec=LINEAR_DOC
    ) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node,expected",
    [
        pytest.param(module_level_async, True, id="async-def"),
        pytest.param(wrapped_async, True, id="functools-wraps-asyncified"),
        pytest.param(returns_a_coroutine, True, id="def-returning-a-coroutine"),
        pytest.param(module_level_sync, False, id="plain-sync"),
    ],
)
async def test_every_async_shape_reaches_the_recorded_capability(node, expected):
    """`inspect.unwrap` walks straight past the coroutine function in the
    standard asyncify decorator, and a plain `def` that returns a coroutine is
    not statically knowable at all — so static detection alone is not enough
    and the runtime also records what it observes."""
    result = await Runtime(LINEAR, {"a": node, "b": module_level_sync}).arun(
        State.empty("shapes"), replay=ReplayStatus.declared("full")
    )
    constraints = result.trace.records[-1]["replay"]["constraints"]
    assert ("node:a:async" in constraints) is expected


@pytest.mark.asyncio
async def test_an_async_node_never_reached_still_constrains_the_run():
    """Two nets guard this, and only one of them covers this case.

    Observation at await time is precise but sees only what ran. Static
    detection is conservative and sees the whole registry — which is what a
    consumer holds when they ask whether this graph is replayable. A run that
    could have taken the async branch is not fully verify-replayable evidence
    of a graph that contains one.
    """
    result = await Runtime(
        ROUTED,
        {"classify": classify, "review": module_level_sync, "reject": module_level_async},
    ).arun(State.empty("unreached"), replay=ReplayStatus.declared("full"))

    executed = {r["node"] for r in result.trace.records if r["kind"] == "transition"}
    assert "reject" not in executed, "the async node must not have run"
    assert "node:reject:async" in result.trace.records[-1]["replay"]["constraints"]


def test_closing_a_stream_driver_releases_the_run_it_drives():
    """`_drive` wraps the state machine, so closing the driver must close the
    machine — otherwise the run, and `_running` with it, is stranded. The async
    twin gets this from the loop's generator finalisation; the synchronous one
    has to say so."""
    runtime = Runtime(LINEAR, {"a": module_level_sync, "b": module_level_sync})
    driver = runtime.stream(State.empty("stranded"))
    next(driver)
    assert runtime._running is True

    driver._iterator.close()

    assert runtime._running is False
    assert runtime.run(State.empty("after")).status == "completed"


@pytest.mark.asyncio
async def test_verify_survives_a_cleanup_that_raises():
    """`verify` closes the awaitable it declines. That cleanup can itself
    raise, and the failure must stay inside the re-execution boundary — an
    arbitrary third-party exception escaping `verify()` would defeat
    `except ReplayError` around it."""
    from vitruvyan_motus import ReplayEngine, TraceBundle
    from vitruvyan_motus.errors import ReplayError

    class HostileAwaitable:
        def __await__(self):
            yield
            return None

        def close(self):
            raise RuntimeError("cleanup refused")

    def pure(state: State) -> State:
        return HostileAwaitable()  # type: ignore[return-value]

    result = await Runtime(
        LINEAR, {"a": module_level_async, "b": module_level_sync}
    ).arun(State.empty("hostile"))
    engine = ReplayEngine(TraceBundle(LINEAR, result.trace))

    with pytest.raises(ReplayError):
        engine.verify({"a": pure, "b": module_level_sync})


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
