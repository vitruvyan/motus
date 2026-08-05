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
import threading
from datetime import datetime, timezone
from typing import Any

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
