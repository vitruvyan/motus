"""Run lifecycle coordination: who owns finishing a run, and who may read it.

`_managed_execute`'s ``finally`` treats "the generator unwound" as "the run is
over".  It is not: a run is over when its terminal record is written *and* its
result has been read by the caller it belongs to.  Between those two events the
lifecycle claim is already released, so a second run can begin and rebind the
instance attributes the first caller is about to read.

The stress form of this lives in `.attack/x2_06_two_loops_two_threads.py` and
shows it in the wild — two threads, own event loop each, ~2 cross-assigned
traces per 3000 `arun` attempts.  A race that rare cannot be a regression test,
so the tests here open the same window deliberately: the instrumentation only
widens a window the attack proves exists unaided, it does not create one.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from datetime import datetime, timezone
from typing import Any

import pytest

from vitruvyan_motus import Fact, GraphSpec, Runtime, State

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "lifecycle",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
    ],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))


def passthrough(state: State) -> State:
    return state


async def apassthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


class _GatedLock:
    """The runtime's lifecycle lock, with a hook on the release that matters.

    ``_running`` going back to False is precisely the moment the run stops
    being protected, and the caller has not read its result yet.  Firing there
    turns a rare interleaving into a certain one.
    """

    __slots__ = ("_inner", "_on_release", "_runtime")

    def __init__(self, inner, runtime, on_release) -> None:
        self._inner = inner
        self._runtime = runtime
        self._on_release = on_release

    def __enter__(self):
        return self._inner.__enter__()

    def __exit__(self, *exc):
        result = self._inner.__exit__(*exc)
        if not self._runtime._running:
            self._on_release()
        return result


def _park_until_a_second_run_completes(runtime, second_run):
    """Wire the runtime so the first run, having released its claim, waits
    inside that window while an entire second run executes and finishes."""
    parked = threading.Event()
    released = threading.Event()

    def on_release() -> None:
        if parked.is_set():
            return  # only the first run parks; the second must not deadlock
        parked.set()
        competitor = threading.Thread(target=second_run, daemon=True)
        competitor.start()
        competitor.join(10)
        released.set()

    runtime._lifecycle_lock = _GatedLock(
        runtime._lifecycle_lock, runtime, on_release
    )
    return parked, released


def test_a_synchronous_run_result_belongs_to_the_caller_that_asked_for_it():
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough})
    second: dict[str, Any] = {}

    def competitor() -> None:
        second["result"] = runtime.run(State.empty("second"), run_id="second")

    parked, released = _park_until_a_second_run_completes(runtime, competitor)

    first = runtime.run(State.empty("first"), run_id="first")

    assert parked.is_set() and released.is_set(), "the window was never opened"
    assert second["result"].trace.run["run_id"] == "second"
    assert first.trace.run["run_id"] == "first", (
        "run() returned the trace of a run that belongs to another caller"
    )


def test_an_asynchronous_run_result_belongs_to_the_caller_that_asked_for_it():
    """The same property for `arun`, which is where the attack actually
    reproduces it: the async driver's unwind is longer, so the window is wider.
    """
    runtime = Runtime(LINEAR, {"a": apassthrough, "b": apassthrough})
    second: dict[str, Any] = {}

    def competitor() -> None:
        second["result"] = asyncio.run(
            runtime.arun(State.empty("second"), run_id="second")
        )

    parked, released = _park_until_a_second_run_completes(runtime, competitor)

    first = asyncio.run(runtime.arun(State.empty("first"), run_id="first"))

    assert parked.is_set() and released.is_set(), "the window was never opened"
    assert second["result"].trace.run["run_id"] == "second"
    assert first.trace.run["run_id"] == "first", (
        "arun() returned the trace of a run that belongs to another caller"
    )


def test_a_run_result_state_belongs_to_the_same_run_as_its_trace():
    """A result carries two things.  Splitting them across runs would be worse
    than returning the wrong one, because the pair would look self-consistent.

    The node stamps the intent it is executing under, so the state names its own
    provenance and does not depend on which caller reads it.
    """

    def stamp(state: State) -> State:
        return state.with_fact(Fact("who", state.intent, "test", NOW))

    runtime = Runtime(LINEAR, {"a": stamp, "b": passthrough})
    second: dict[str, Any] = {}

    def competitor() -> None:
        second["result"] = runtime.run(State.empty("second"), run_id="second")

    _park_until_a_second_run_completes(runtime, competitor)

    first = runtime.run(State.empty("first"), run_id="first")

    assert second["result"].state.fact("who") == "second"
    assert first.trace.run["run_id"] == "first"
    assert first.state.fact("who") == "first", (
        "the returned state came from a different run than the returned trace"
    )


def test_a_listener_that_closes_its_own_stream_does_not_strand_the_run():
    """`aclose` refuses to drain a generator another caller is inside, because
    draining there raises and orphans the run with no terminal.  `close` had no
    such guard: a listener is delivered synchronously *from inside* the
    generator, so a listener that closes its own driver re-enters it, and the
    latch in `close`'s `finally` then makes every retry a silent no-op.
    """
    driver_box: dict[str, Any] = {}

    class ClosesOnFirstRecord:
        def __init__(self) -> None:
            self.raised: BaseException | None = None

        def on_record(self, record: dict[str, Any]) -> None:
            if record["kind"] != "run_started" or "driver" not in driver_box:
                return
            try:
                driver_box["driver"].close("listener stopped the stream")
            except BaseException as exc:  # the runtime isolates listener failures
                self.raised = exc
                raise

    listener = ClosesOnFirstRecord()
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough}, listeners=(listener,))
    driver = runtime.stream(State.empty("listener"))
    driver_box["driver"] = driver

    kinds = [record["kind"] for record in driver]

    assert not isinstance(listener.raised, ValueError), (
        "close() re-entered a generator that was already executing"
    )
    assert kinds[-1] == "run_cancelled", (
        f"the run must reach a terminal record, got {kinds}"
    )
    assert not runtime._running, "the Runtime is still claiming a finished run"
    assert runtime.run(State.empty("after")).status == "completed", (
        "the Runtime is wedged and cannot execute again"
    )


def test_a_second_close_does_not_rewrite_why_the_run_stopped():
    """The early return above leaves `_closed` unset, so `__exit__` will call
    `close` again after an explicit call.  The cancellation that actually
    stopped the run is the first one; the trace must attribute it to that
    reason, not to whichever call happened last.
    """
    driver_box: dict[str, Any] = {}

    def closer(record: dict[str, Any]) -> None:
        if record["kind"] == "run_started" and "driver" in driver_box:
            driver_box["driver"].close("the real reason")

    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough}, listeners=(closer,))
    driver = runtime.stream(State.empty("reason"))
    driver_box["driver"] = driver

    next(driver)                      # the listener closes from inside this call
    assert not driver._closed, "the guarded close must not latch the driver"
    driver.close("a later reason")    # the retry a caller makes when close looked inert

    # `close` drains the terminal rather than yielding it, so the evidence to
    # read is the trace, which is where the reason is recorded anyway.
    terminal = driver.trace.records[-1]
    assert terminal["kind"] == "run_cancelled"
    assert terminal["reason"] == "the real reason", (
        "a later close overwrote the reason the run actually stopped for"
    )


def test_dropping_a_driver_that_was_never_advanced_releases_the_run():
    """`stream()` claims the run before the consumer asks for anything, which is
    right: a second `stream()` must be refused.  But the claim was released only
    by `_managed_execute`'s `finally`, and an generator that was never started
    does not run one — so a driver created and dropped left the Runtime claiming
    a run that had not executed a single node, permanently.
    """
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough})

    driver = runtime.stream(State.empty("never-advanced"))
    assert runtime._running, "stream() must claim the run up front"
    del driver
    gc.collect()

    assert not runtime._running, "the abandoned run still holds the Runtime"
    assert runtime.run(State.empty("after")).status == "completed"


def test_dropping_an_async_driver_that_was_never_advanced_releases_the_run():
    runtime = Runtime(LINEAR, {"a": apassthrough, "b": apassthrough})

    driver = runtime.astream(State.empty("never-advanced"))
    assert runtime._running
    del driver
    gc.collect()

    assert not runtime._running, "the abandoned run still holds the Runtime"
    assert asyncio.run(runtime.arun(State.empty("after"))).status == "completed"


def test_an_abandoned_driver_cannot_release_a_later_run():
    """The release is scoped to the run the driver was made for, for the same
    reason cancellation is: a finaliser runs at an arbitrary later moment, and a
    Runtime that has since started another run must not be disarmed by it.
    """
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough})

    abandoned = runtime.stream(State.empty("abandoned"))
    list(abandoned)                      # this run finishes normally
    second = runtime.stream(State.empty("second"))
    assert runtime._running

    del abandoned
    gc.collect()

    assert runtime._running, "a stale driver's finaliser released a live run"
    list(second)
    assert not runtime._running


def test_the_abandoned_release_never_touches_a_run_that_has_begun():
    """The finaliser is narrowed to runs whose generator never started, and the
    narrowing is the load-bearing part.

    A driver dropped *mid-run* is already handled by its own generator: closing
    it raises `GeneratorExit` inside, and the `finally` both publishes the
    result and releases the claim.  Releasing that case here as well would free
    the claim while the generator is still unwinding, and the unwind would then
    publish another run's trace — and close another run's observation hub —
    through this handle.  That trades a wedge for a corruption.

    Exercised directly rather than through a dropped reference, because the
    damage needs a second thread to enter the window and the window lives
    inside object deallocation.
    """
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough})
    driver = runtime.stream(State.empty("begun"))
    handle = runtime._run
    next(driver)                      # the generator has now started

    assert handle.started and not handle.finished
    runtime._release_if_never_started(handle)

    assert runtime._running, (
        "the abandoned-driver path released a run whose generator is live"
    )
    list(driver)
    assert not runtime._running


def test_the_abandoned_driver_finaliser_does_not_keep_its_runtime_alive():
    """`weakref.finalize` keeps its callback in a module-global registry, and
    that registry is a strong root.

    A bound method as the callback puts the Runtime under that root, and the
    Runtime reaches the driver in exactly the shape this finaliser exists for —
    a listener that holds the Runtime and closes its own driver, which
    guarantees.md §6 explicitly blesses.  The driver then never becomes
    unreachable, so the finaliser never fires and its registry entry never
    leaves: an unbounded leak dragging the Trace, State, hub and the caller's
    sink along with every run.
    """
    alive: list[Any] = []

    for _ in range(20):
        holder: dict[str, Any] = {}

        # Bound as a default argument, not as a closure cell: `del holder`
        # below empties a cell, which would quietly break the very reference
        # path under test and let this pass against the defect.
        def listener(record: dict[str, Any], _held: dict[str, Any] = holder) -> None:
            _held.get("driver")

        runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough}, listeners=(listener,))
        driver = runtime.stream(State.empty("leak"))
        holder["driver"] = driver          # Runtime -> listener -> driver
        list(driver)
        alive.append(weakref.ref(runtime))
        del runtime, driver, holder, listener

    gc.collect()
    survivors = [reference for reference in alive if reference() is not None]

    assert survivors == [], (
        f"{len(survivors)}/20 Runtimes survived collection — the finaliser "
        "registry is rooting them, and every one holds a Trace, a State, an "
        "observation hub and the caller's sink"
    )


def test_a_close_whose_cancellation_is_rejected_does_not_disarm_the_driver():
    """The latch must be set only once the cancellation has actually bound.

    Burning it on a call that never cancelled leaves the driver armed-looking
    but inert, and the retry — or `__exit__` — then skips the cancel and
    *drains*: `close` would execute every remaining node, performing whatever
    external effects they carry, where guarantees.md §6 requires it to land a
    recorded `run_cancelled`.
    """
    executed: list[str] = []

    def record_a(state: State) -> State:
        executed.append("a")
        return state

    def record_b(state: State) -> State:
        executed.append("b")
        return state

    runtime = Runtime(LINEAR, {"a": record_a, "b": record_b})
    driver = runtime.stream(State.empty("rejected"))
    next(driver)
    executed.clear()

    with pytest.raises(TypeError):
        driver.close(None)                 # Runtime.cancel refuses a non-str

    driver.close("the retry that must still work")

    assert driver.trace.records[-1]["kind"] == "run_cancelled", (
        f"close() ran the graph instead of cancelling it; nodes executed: {executed}"
    )
    assert executed == [], f"close() executed pending nodes: {executed}"


@pytest.mark.asyncio
async def test_an_aclose_whose_cancellation_is_rejected_does_not_disarm_the_driver():
    """The asynchronous twin, which carried this defect from the round it was
    written in — the latch was copied to the synchronous side before it was
    correct on either."""
    executed: list[str] = []

    async def record_a(state: State) -> State:
        executed.append("a")
        return state

    runtime = Runtime(LINEAR, {"a": record_a, "b": apassthrough})
    driver = runtime.astream(State.empty("rejected"))
    await driver.__anext__()
    executed.clear()

    with pytest.raises(TypeError):
        await driver.aclose(None)

    await driver.aclose("the retry that must still work")

    assert driver.trace.records[-1]["kind"] == "run_cancelled", (
        f"aclose() ran the graph instead of cancelling it; nodes: {executed}"
    )
    assert executed == []
