"""x2b-02 -- attack ``AsyncStreamDriver.aclose``'s ``ag_running`` early return.

    self._cancel(reason)
    if getattr(self._iterator, "ag_running", False):
        return                      # <- binds, does NOT set _closed, no drain

The comment justifies it: "The cancellation is bound; the consumer drives it to
run_cancelled and closes this driver on the way out."  That sentence contains
the whole attack surface -- it is only true if a consumer *does* read again.

Probes:
  1. The intended shape: consumer keeps reading.  Must land run_cancelled.
  2. No consumer follows: ``ag_running`` was true but that task never reads
     again.  Is the run orphaned, and is ``_closed`` False forever?
  3. ``aclose`` twice while running -- which reason is recorded?
  4. ``__aexit__`` while ``ag_running`` -- does ``async with`` now exit over a
     live run?
  5. An iterator with no ``ag_running`` at all (``AsyncStreamDriver`` is in
     ``__all__``, so a caller may construct one).
  6. ``ag_running`` False at check time but a consumer's ``asend`` already
     pending: does the RuntimeError merely move from ``aclose`` to
     ``__anext__``?
  7. Does ``aclose()`` now return while ``_running`` is still True?  guarantees
     .md §6 says cancellation through the driver "lands as the trace-recorded
     run_cancelled ... never as an abandoned generator".
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import AsyncStreamDriver, Runtime, State
from x2b_common import LINEAR, Report, anode, kinds, lifecycle

R = Report("x2b_02 aclose ag_running early return")


def _gated(entered: asyncio.Event, release: asyncio.Event):
    async def gate(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    return gate


def _rt(first) -> Runtime:
    return Runtime(LINEAR, {"a": first, "b": anode, "c": anode})


async def case_intended_shape() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("graceful"))
    seen: list[str] = []

    async def consumer() -> None:
        async for record in driver:
            seen.append(record["kind"])

    task = asyncio.create_task(consumer())
    await entered.wait()
    running_at_aclose: list = []
    await driver.aclose("graceful shutdown")
    running_at_aclose.append(rt._running)
    closed_right_after = driver._closed
    release.set()
    await asyncio.wait_for(task, 5)
    life = lifecycle(rt)
    trace = kinds(driver.trace)
    second = (await asyncio.wait_for(rt.arun(State.empty("second")), 5)).status
    R.record(
        "graceful shutdown, consumer keeps reading",
        trace and trace[-1] == "run_cancelled" and driver._closed and second == "completed",
        f"terminal={trace[-1] if trace else None} closed_after_aclose={closed_right_after} "
        f"running_at_aclose_return={running_at_aclose[0]} closed_finally={driver._closed} "
        f"running={life['_running']} second={second}",
    )


async def case_no_consumer_follows() -> None:
    """``ag_running`` is true, but that consumer is cancelled and never reads
    again.  Pre-patch this raised loudly; now aclose() returns success."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("abandoned"))

    async def consumer() -> None:
        async for _ in driver:
            pass

    task = asyncio.create_task(consumer())
    await entered.wait()
    await driver.aclose("graceful shutdown")   # returns normally
    aclose_returned_cleanly = True
    task.cancel()                              # supervisor tears the reader down
    try:
        await task
    except asyncio.CancelledError:
        pass
    release.set()
    await asyncio.sleep(0)
    life = lifecycle(rt)
    trace = kinds(driver.trace)
    closed = driver._closed
    try:
        second = (await asyncio.wait_for(rt.arun(State.empty("second")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        second = f"{type(exc).__name__}: {exc}"
    R.record(
        "aclose() succeeded, then the reader is cancelled",
        trace and trace[-1].startswith("run_") and second == "completed",
        f"aclose_ok={aclose_returned_cleanly} terminal={trace[-1] if trace else None} "
        f"driver_closed={closed} cancel_reason={life['_cancel_reason']!r} "
        f"running={life['_running']} second={second}",
    )


async def case_reader_stops_without_cancel() -> None:
    """The reader simply breaks out of its loop after aclose() bound the
    cancellation -- no task cancellation, no exception, just a consumer that
    stops asking.  Nobody ever drives the terminal."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("stops"))
    stop = False

    async def consumer() -> None:
        nonlocal stop
        async for _ in driver:
            if stop:
                break

    task = asyncio.create_task(consumer())
    await entered.wait()
    await driver.aclose("graceful shutdown")
    stop = True
    release.set()
    await asyncio.wait_for(task, 5)
    life = lifecycle(rt)
    trace = kinds(driver.trace)
    closed = driver._closed
    try:
        second = (await asyncio.wait_for(rt.arun(State.empty("second")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        second = f"{type(exc).__name__}: {exc}"
    R.record(
        "reader breaks out after a bound aclose()",
        trace and trace[-1].startswith("run_") and second == "completed" and closed,
        f"terminal={trace[-1] if trace else None} driver_closed={closed} "
        f"running={life['_running']} second={second}",
    )


async def case_double_aclose_while_running() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("double"))

    async def consumer() -> None:
        async for _ in driver:
            pass

    task = asyncio.create_task(consumer())
    await entered.wait()
    await driver.aclose("first reason")
    await driver.aclose("second reason")
    release.set()
    await asyncio.wait_for(task, 5)
    terminal = driver.trace.records[-1]
    R.record(
        "aclose() twice while ag_running",
        terminal["kind"] == "run_cancelled" and terminal["reason"] == "first reason",
        f"terminal={terminal['kind']} recorded_reason={terminal['reason']!r} "
        "(the first caller's reason is what the evidence should attribute)",
    )


async def case_aexit_while_ag_running() -> None:
    """The supervisor owns the ``async with`` while another task reads."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("aexit"))
    holder: dict = {}

    async def consumer() -> None:
        async for _ in driver:
            pass

    task = asyncio.create_task(consumer())
    await entered.wait()

    async with driver:
        pass  # __aexit__ fires while a reader is inside __anext__
    holder["closed_at_exit"] = driver._closed
    holder["running_at_exit"] = rt._running
    release.set()
    await asyncio.wait_for(task, 5)
    trace = kinds(driver.trace)
    try:
        second = (await asyncio.wait_for(rt.arun(State.empty("second")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        second = f"{type(exc).__name__}: {exc}"
    R.record(
        "__aexit__ while ag_running",
        trace and trace[-1] == "run_cancelled" and second == "completed",
        f"closed_at_exit={holder['closed_at_exit']} running_at_exit={holder['running_at_exit']} "
        f"terminal={trace[-1] if trace else None} second={second}",
    )


async def case_non_asyncgen_iterator() -> None:
    """AsyncStreamDriver is public; a caller may wrap something that is not an
    async generator, so ``ag_running`` is absent and the old path returns."""

    class Stuck:
        def __init__(self) -> None:
            self.entered = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.entered.set()
            await asyncio.sleep(3600)

    stuck = Stuck()
    cancelled: list[str] = []
    driver = AsyncStreamDriver(stuck, lambda r: cancelled.append(r) or True, lambda: None)
    task = asyncio.create_task(driver.__anext__())
    await stuck.entered.wait()
    try:
        await asyncio.wait_for(driver.aclose("shutdown"), 0.3)
        outcome = "returned"
    except asyncio.TimeoutError:
        outcome = "TimeoutError (aclose never returns)"
    except BaseException as exc:  # noqa: BLE001
        outcome = f"{type(exc).__name__}: {exc}"
    task.cancel()
    R.record(
        "aclose() on a non-asyncgen iterator",
        outcome == "returned",
        f"has_ag_running={hasattr(stuck, 'ag_running')} outcome={outcome} "
        f"cancel_calls={cancelled}",
    )


async def case_pending_asend_moves_the_error() -> None:
    """``ag_running`` is False at the check, but a consumer task has already
    created its ``__anext__`` coroutine.  Does the RuntimeError merely move
    from aclose() to the consumer?"""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    driver = rt.astream(State.empty("pending"))
    await driver.__anext__()  # run_started
    await driver.__anext__()  # attempt_started; next step enters the node
    errors: list[str] = []

    async def consumer() -> None:
        try:
            while True:
                await driver.__anext__()
        except StopAsyncIteration:
            errors.append("StopAsyncIteration")
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    task = asyncio.create_task(consumer())          # created, not yet run
    ag = getattr(driver._iterator, "ag_running", None)
    closer = asyncio.create_task(driver.aclose("shutdown"))
    release.set()
    await asyncio.wait_for(asyncio.gather(task, closer), 5)
    trace = kinds(driver.trace)
    life = lifecycle(rt)
    try:
        second = (await asyncio.wait_for(rt.arun(State.empty("second")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        second = f"{type(exc).__name__}: {exc}"
    R.record(
        "pending asend vs an aclose that saw ag_running False",
        trace and trace[-1] == "run_cancelled"
        and errors == ["StopAsyncIteration"]
        and second == "completed",
        f"ag_running_at_check={ag} consumer_saw={errors} "
        f"terminal={trace[-1] if trace else None} running={life['_running']} "
        f"second={second}",
    )


async def case_closed_left_false_forever() -> None:
    """Direct measurement: after a bound-but-undrained aclose() with no reader
    left, does the driver ever become closed, and does GC recover _running?"""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = _rt(_gated(entered, release))
    holder: list = [rt.astream(State.empty("forever"))]

    async def consumer() -> None:
        async for _ in holder[0]:
            pass

    task = asyncio.create_task(consumer())
    await entered.wait()
    await holder[0].aclose("shutdown")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    release.set()
    closed_while_held = holder[0]._closed
    running_while_held = rt._running
    trace_while_held = kinds(holder[0].trace)
    holder.clear()
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    R.record(
        "_closed after an undrained aclose()",
        closed_while_held,
        f"closed_while_referenced={closed_while_held} running_while_referenced="
        f"{running_while_held} trace={trace_while_held} "
        f"running_after_gc={rt._running}",
    )


CASES = (
    case_intended_shape,
    case_no_consumer_follows,
    case_reader_stops_without_cancel,
    case_double_aclose_while_running,
    case_aexit_while_ag_running,
    case_non_asyncgen_iterator,
    case_pending_asend_moves_the_error,
    case_closed_left_false_forever,
)


async def main() -> int:
    for case in CASES:
        try:
            await case()
        except BaseException as exc:  # noqa: BLE001
            R.record(case.__name__, False, f"harness raised {type(exc).__name__}: {exc}")
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
