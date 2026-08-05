"""x2c-06 -- the guard asymmetry the patch left between the two drivers.

``AsyncStreamDriver.aclose`` gained an ``ag_running`` early return so a close
issued while the iterator is being driven does not raise and orphan the run.
``StreamDriver.close`` gained nothing, and its ``finally`` is unconditional:

    def close(self, reason=...):
        if self._closed: return
        self._cancel(reason)
        try:
            while True: next(self._iterator)
        except StopIteration: pass
        finally:
            self._closed = True          # <- latches even on a clash

``__next__`` is now careful (``_iterator_is_finished``); ``close`` is not.  The
reachable trigger is the one guarantees.md §6 explicitly blesses: "Code that
also holds the Runtime can invoke its public cancellation surface" from a
listener.  A listener that calls ``driver.close()`` is that code, and delivery
is synchronous on the runner thread, so the driver is being driven at that
moment.
"""

from __future__ import annotations

import gc

from vitruvyan_motus import Runtime, State
from x2c_common import LINEAR, Report, kinds, lifecycle, snode, sync_runtime

R = Report("x2c_06 close() vs aclose() guard asymmetry")


def case_listener_closes_the_driver() -> None:
    holder: dict = {}

    class Shutdown:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "hit" not in holder:
                holder["hit"] = True
                try:
                    holder["driver"].close("shutdown from a listener")
                    holder["close"] = "returned"
                except BaseException as exc:  # noqa: BLE001
                    holder["close"] = f"{type(exc).__name__}: {exc}"

    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(Shutdown(),))
    driver = rt.stream(State.empty("listener-close"))
    holder["driver"] = driver
    try:
        for _ in driver:
            pass
        outer = "exhausted"
    except BaseException as exc:  # noqa: BLE001
        outer = type(exc).__name__
    closed = driver._closed
    trace = kinds(driver.trace)
    life = lifecycle(rt)
    # The consumer now does the documented thing a second time.
    driver.close("second attempt")
    trace_after = kinds(driver.trace)
    try:
        second = rt.run(State.empty("second")).status
    except BaseException as exc:  # noqa: BLE001
        second = type(exc).__name__
    R.record(
        "listener calls driver.close() during delivery",
        trace and trace[-1].startswith("run_") and second == "completed",
        f"close={holder.get('close')} outer={outer} latched={closed} "
        f"terminal={trace[-1] if trace else None} running={life['_running']} "
        f"terminal_after_retry={trace_after[-1] if trace_after else None} "
        f"second={second}",
    )


def case_listener_closes_recoverable_by_gc() -> None:
    """If it does orphan, is the Runtime at least recoverable?"""
    holder: dict = {}

    class Shutdown:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "hit" not in holder:
                holder["hit"] = True
                try:
                    holder["driver"].close("shutdown from a listener")
                except BaseException:  # noqa: BLE001
                    pass

    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(Shutdown(),))
    driver = rt.stream(State.empty("gc"))
    holder["driver"] = driver
    try:
        for _ in driver:
            pass
    except BaseException:  # noqa: BLE001
        pass
    trace_before = kinds(rt.trace)
    holder.clear()
    del driver
    gc.collect()
    try:
        second = rt.run(State.empty("second")).status
    except BaseException as exc:  # noqa: BLE001
        second = type(exc).__name__
    R.record(
        "orphan from close() is recoverable by GC",
        second == "completed",
        f"orphan_trace={trace_before} running_after_gc={rt._running} second={second}",
    )


def case_async_equivalent_is_guarded() -> None:
    """The same shape on the async driver, to show the asymmetry directly."""
    import asyncio

    from x2c_common import anode

    holder: dict = {}
    box: dict = {}

    class Shutdown:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "hit" not in holder:
                holder["hit"] = True
                holder["ag_running"] = getattr(
                    holder["driver"]._iterator, "ag_running", None
                )

    async def go() -> None:
        rt = Runtime(
            LINEAR, {"a": anode, "b": anode, "c": anode}, listeners=(Shutdown(),)
        )
        driver = rt.astream(State.empty("async-listener"))
        holder["driver"] = driver
        await driver.__anext__()
        await driver.__anext__()
        # A supervisor closing while the iterator is being driven is the
        # guarded path; the listener above only samples ag_running.
        await driver.aclose("shutdown")
        box["trace"] = kinds(driver.trace)
        box["running"] = rt._running
        box["second"] = (await asyncio.wait_for(rt.arun(State.empty("s")), 3)).status

    asyncio.run(go())
    R.record(
        "async driver: same shape stays clean",
        box["trace"][-1].startswith("run_") and box["second"] == "completed",
        f"ag_running_seen_in_listener={holder.get('ag_running')} "
        f"terminal={box['trace'][-1]} running={box['running']} second={box['second']}",
    )


def main() -> int:
    case_listener_closes_the_driver()
    case_listener_closes_recoverable_by_gc()
    case_async_equivalent_is_guarded()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
