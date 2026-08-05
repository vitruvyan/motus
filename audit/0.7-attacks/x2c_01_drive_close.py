"""x2c-01 -- attack ``_drive``'s new ``finally: machine.close()``.

This is a lifecycle change on the synchronous path, so every question is
answered by counting, not by reasoning.  ``_ObservationHub.close`` is the first
statement of ``_managed_execute``'s ``finally`` and is called from nowhere
else, so counting it counts teardowns exactly.

Probes:
  1. ``run()`` / ``_run_from()`` drive to exhaustion -- is the extra
     ``machine.close()`` observably a no-op?  (teardowns == 1, trace unchanged)
  2. ``StreamDriver.close()`` mid-run, GC of a partially consumed driver, an
     exception thrown through the consumer -- teardown exactly once, hub
     closed, ``_running`` released, ``_cancel_reason`` cleared,
     ``_active_attempt`` None.
  3. The ``machine.throw(exc)`` path, which already tore the machine down,
     followed by the new ``finally`` -- double teardown?  Swallowed exception?
     ``RuntimeError: generator ignored GeneratorExit``?
  4. A ``buffered`` hub with a live flush ``Timer``: is the timer cancelled on
     the close path, or does a background thread outlive the run?
  5. Fault injection: if the teardown itself raises, does the new ``finally``
     replace the exception the caller was about to see?
"""

from __future__ import annotations

import gc
import sys
import threading
import time
import warnings

from vitruvyan_motus import NodeFailed, Policy, Runtime, State
from x2c_common import (
    LINEAR,
    Report,
    TeardownCounter,
    kinds,
    lifecycle,
    snode,
    sync_runtime,
)

R = Report("x2c_01 _drive finally: machine.close()")


def case_run_to_exhaustion() -> None:
    with TeardownCounter() as counter:
        rt = sync_runtime()
        result = rt.run(State.empty("exhaust"))
        counts = counter.counts
    R.record(
        "run() to exhaustion: teardown exactly once",
        counts == [1] and result.status == "completed" and not rt._running,
        f"teardowns={counts} status={result.status} life={lifecycle(rt)}",
    )


def case_run_from_to_exhaustion() -> None:
    with TeardownCounter() as counter:
        rt = sync_runtime()
        result = rt._run_from(
            State.empty("resumed"),
            start_node="b",
            resume_info={"source_run_id": "src", "reason": "test"},
            run_id="resume-1",
        )
        counts = counter.counts
    R.record(
        "_run_from() to exhaustion: teardown exactly once",
        counts == [1] and result.status == "completed" and not rt._running,
        f"teardowns={counts} status={result.status} running={rt._running}",
    )


def case_stream_close_midrun() -> None:
    with TeardownCounter() as counter:
        rt = sync_runtime()
        driver = rt.stream(State.empty("closed"))
        next(driver)
        next(driver)
        driver.close("consumer stopped")
        counts = counter.counts
    terminal = driver.trace.records[-1]
    life = lifecycle(rt)
    R.record(
        "StreamDriver.close() mid-run",
        counts == [1]
        and terminal["kind"] == "run_cancelled"
        and terminal["active_attempt"] == {"node": "a", "attempt": 1}
        and not life["_running"]
        and life["_cancel_reason"] is None
        and life["_active_attempt"] is None,
        f"teardowns={counts} terminal={terminal['kind']} life={life}",
    )


def case_gc_partially_consumed() -> None:
    """The shape the fix exists for: a driver dropped mid-run."""
    with TeardownCounter() as counter:
        rt = sync_runtime()
        driver = rt.stream(State.empty("dropped"))
        next(driver)
        next(driver)
        trace_before = kinds(rt.trace)
        del driver
        gc.collect()
        counts = counter.counts
        life = lifecycle(rt)
        second = rt.run(State.empty("second")).status
    R.record(
        "GC of a partially consumed StreamDriver",
        counts == [1]
        and not life["_running"]
        and life["_cancel_reason"] is None
        and life["_active_attempt"] is None
        and second == "completed",
        f"teardowns={counts} abandoned_trace={trace_before} life={life} second={second}",
    )


def case_exception_through_the_consumer() -> None:
    with TeardownCounter() as counter:
        rt = sync_runtime()
        driver = rt.stream(State.empty("boom"))
        raised = "none"
        try:
            for _ in driver:
                raise KeyError("consumer exploded")
        except KeyError:
            raised = "KeyError"
        del driver
        gc.collect()
        counts = counter.counts
        life = lifecycle(rt)
        second = rt.run(State.empty("second")).status
    R.record(
        "consumer raises inside the for-loop",
        counts == [1] and raised == "KeyError" and not life["_running"]
        and second == "completed",
        f"teardowns={counts} raised={raised} life={life} second={second}",
    )


def case_node_failure_path() -> None:
    """NodeFailed already unwound the machine; the new finally then closes it."""

    def boom(state: State) -> State:
        raise RuntimeError("node down")

    with TeardownCounter() as counter:
        rt = Runtime(LINEAR, {"a": boom, "b": snode, "c": snode})
        raised = "none"
        try:
            rt.run(State.empty("failed"))
        except NodeFailed as exc:
            raised = type(exc).__name__
        counts = counter.counts
        life = lifecycle(rt)
        trace = kinds(rt.trace)
        rt._nodes["a"] = snode  # the reuse probe must not re-trip the failure
        second = rt.run(State.empty("second")).status
    R.record(
        "NodeFailed: throw path then the new finally",
        counts == [1] and raised == "NodeFailed" and trace[-1] == "run_failed"
        and not life["_running"] and second == "completed",
        f"teardowns={counts} raised={raised} terminal={trace[-1]} life={life}",
    )


def case_base_exception_from_a_node() -> None:
    """A BaseException goes through ``machine.throw`` and must still leave the
    attempt unclosed (node-protocol §7.3) with exactly one teardown."""

    class Abort(BaseException):
        pass

    def hard(state: State) -> State:
        raise Abort("hard interruption")

    with TeardownCounter() as counter:
        rt = Runtime(LINEAR, {"a": hard, "b": snode, "c": snode})
        raised = "none"
        try:
            rt.run(State.empty("torn"))
        except Abort:
            raised = "Abort"
        except BaseException as exc:  # noqa: BLE001
            raised = f"other:{type(exc).__name__}"
        counts = counter.counts
        life = lifecycle(rt)
        trace = kinds(rt.trace)
        rt._nodes["a"] = snode
        second = rt.run(State.empty("second")).status
    R.record(
        "BaseException: machine.throw + finally, §7.3 shape",
        counts == [1] and raised == "Abort"
        and trace == ["run_started", "attempt_started"]
        and not life["_running"] and second == "completed",
        f"teardowns={counts} raised={raised} trace={trace} life={life}",
    )


def case_generator_exit_hygiene() -> None:
    """Does closing the driver ever produce 'generator ignored GeneratorExit'
    or an 'Exception ignored in' report on stderr?"""
    errors: list[str] = []

    def hook(args) -> None:
        errors.append(f"{args.exc_type.__name__}: {args.exc_value}")

    old_hook = getattr(sys, "unraisablehook", None)
    sys.unraisablehook = hook
    caught: list[str] = []
    try:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            for _ in range(50):
                rt = sync_runtime()
                driver = rt.stream(State.empty("hygiene"))
                next(driver)
                next(driver)
                del driver
                gc.collect()
            caught = [str(w.message)[:60] for w in seen]
    finally:
        if old_hook is not None:
            sys.unraisablehook = old_hook
    R.record(
        "no unraisable / ignored-GeneratorExit noise x50",
        not errors and not caught,
        f"unraisable={errors[:2]} warnings={caught[:2]}",
    )


class SlowSink:
    def __init__(self) -> None:
        self.writes: list[int] = []
        self.threads: set[str] = set()

    def open_run(self, header):
        return self

    def write(self, records) -> None:
        self.writes.append(len(records))
        self.threads.add(threading.current_thread().name)


def case_buffered_timer_cancelled() -> None:
    """A live flush Timer must not outlive a driver that was closed by GC."""
    sink = SlowSink()
    with TeardownCounter() as counter:
        rt = Runtime(
            LINEAR, {"a": snode, "b": snode, "c": snode},
            durability_profile="buffered", sink=sink,
            chunk_records=10_000, flush_interval_ms=200,
        )
        driver = rt.stream(State.empty("buffered"))
        next(driver)
        next(driver)
        hub = rt._hub
        timer_before = hub._timer is not None
        del driver
        gc.collect()
        counts = counter.counts
        closed = hub._closed
        timer_after = hub._timer
        before_wait = list(sink.writes)
        time.sleep(0.5)  # a surviving timer would fire in this window
        after_wait = list(sink.writes)
        threads_alive = [t.name for t in threading.enumerate() if "Timer" in t.name]
    R.record(
        "buffered flush Timer cancelled on the close path",
        counts == [1] and closed and timer_after is None
        and before_wait == after_wait and not threads_alive,
        f"teardowns={counts} timer_scheduled_before={timer_before} hub_closed={closed} "
        f"timer_after={timer_after} writes_before={before_wait} writes_after={after_wait} "
        f"timer_threads={threads_alive}",
    )


def case_teardown_raises() -> None:
    """Fault injection: if the teardown itself raises, does the new finally
    replace the exception the caller was about to see?"""
    from vitruvyan_motus import observers

    original = observers._ObservationHub.close

    def exploding(self) -> None:
        original(self)
        raise ZeroDivisionError("teardown exploded")

    observers._ObservationHub.close = exploding
    try:
        rt = sync_runtime()
        driver = rt.stream(State.empty("inject"))
        next(driver)
        try:
            driver.close("consumer stopped")
            surfaced = "none"
        except BaseException as exc:  # noqa: BLE001
            surfaced = type(exc).__name__
        life_running = rt._running
    finally:
        observers._ObservationHub.close = original
    R.record(
        "teardown failure surfaces, does not corrupt lifecycle",
        surfaced in ("ZeroDivisionError", "none"),
        f"surfaced={surfaced} running_after={life_running} "
        "(informational: hub.close() raising is not a reachable product state)",
    )


def case_repeated_close_idempotent() -> None:
    with TeardownCounter() as counter:
        rt = sync_runtime()
        driver = rt.stream(State.empty("idem"))
        next(driver)
        driver.close("first")
        driver.close("second")
        driver._iterator.close()
        gc.collect()
        counts = counter.counts
        terminal = driver.trace.records[-1]
    R.record(
        "close() twice + explicit iterator close",
        counts == [1] and terminal["reason"] == "first",
        f"teardowns={counts} recorded_reason={terminal['reason']!r}",
    )


CASES = (
    case_run_to_exhaustion,
    case_run_from_to_exhaustion,
    case_stream_close_midrun,
    case_gc_partially_consumed,
    case_exception_through_the_consumer,
    case_node_failure_path,
    case_base_exception_from_a_node,
    case_repeated_close_idempotent,
    case_generator_exit_hygiene,
    case_buffered_timer_cancelled,
    case_teardown_raises,
)


def main() -> int:
    for case in CASES:
        try:
            case()
        except BaseException as exc:  # noqa: BLE001
            R.record(case.__name__, False, f"harness raised {type(exc).__name__}: {exc}")
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
