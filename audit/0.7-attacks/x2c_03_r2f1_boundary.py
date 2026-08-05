"""x2c-03 -- the precise recoverability boundary of R2-F1 (open by decision).

R2-F1: ``aclose()`` early-returns while ``ag_running``, binding the
cancellation but leaving the terminal for a consumer that may never come.  The
question that decides code-vs-documentation is not "can it orphan a run" -- it
can -- but "once orphaned, what recovers it".

Each recovery route is tried against the identical orphaned state:
    A. call ``aclose()`` again
    B. ``async with`` / ``__aexit__``
    C. resume iterating the driver
    D. drop the driver and collect
    E. nothing at all

``_requested`` matters here: a second ``aclose`` no longer re-binds the reason,
but it does still fall through to the drain when ``ag_running`` has cleared.
"""

from __future__ import annotations

import asyncio
import gc
from typing import Any

from vitruvyan_motus import Runtime, State
from x2c_common import LINEAR, Report, anode, kinds, lifecycle

R = Report("x2c_03 R2-F1 recoverability boundary")


async def _orphan() -> tuple[Runtime, Any, asyncio.Event, dict]:
    """Build the exact R2-F1 state: aclose() bound while ag_running, then the
    consumer takes one record and stops."""
    entered, release = asyncio.Event(), asyncio.Event()
    facts: dict = {}

    async def gate(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
    driver = rt.astream(State.empty("r2f1"))
    took: list = []
    stop = False

    async def consumer() -> None:
        async for record in driver:
            took.append(record["kind"])
            if stop:
                break  # one more record after the shutdown, then stop

    task = asyncio.create_task(consumer())
    await entered.wait()  # the consumer is now suspended inside __anext__
    await driver.aclose("graceful shutdown")
    stop = True
    facts["aclose_returned"] = True
    facts["ag_running_at_aclose"] = True
    release.set()
    await asyncio.wait_for(task, 5)
    facts["consumer_took"] = took
    facts["closed"] = driver._closed
    facts["requested"] = driver._requested
    facts["running"] = rt._running
    facts["trace"] = kinds(driver.trace)
    facts["cancel_reason"] = rt._cancel_reason
    return rt, driver, release, facts


async def _reusable(rt: Runtime) -> str:
    try:
        return (await asyncio.wait_for(rt.arun(State.empty("after")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__


async def case_orphan_shape() -> None:
    rt, driver, _release, facts = await _orphan()
    R.record(
        "R2-F1 orphan state",
        facts["trace"] and facts["trace"][-1].startswith("run_"),
        f"consumer_took={facts['consumer_took']} closed={facts['closed']} "
        f"requested={facts['requested']} running={facts['running']} "
        f"trace={facts['trace']} bound_reason={facts['cancel_reason']!r}",
    )


async def case_recovery_second_aclose() -> None:
    rt, driver, _release, facts = await _orphan()
    await driver.aclose("second attempt")
    trace = kinds(driver.trace)
    reuse = await _reusable(rt)
    terminal = driver.trace.records[-1]
    R.record(
        "A. a second aclose() recovers it",
        trace[-1] == "run_cancelled" and driver._closed and reuse == "completed",
        f"terminal={trace[-1]} reason={terminal.get('reason')!r} "
        f"closed={driver._closed} running={rt._running} reuse={reuse}",
    )


async def case_recovery_aexit() -> None:
    rt, driver, _release, facts = await _orphan()
    async with driver:
        pass
    trace = kinds(driver.trace)
    reuse = await _reusable(rt)
    R.record(
        "B. __aexit__ recovers it",
        trace[-1] == "run_cancelled" and driver._closed and reuse == "completed",
        f"terminal={trace[-1]} closed={driver._closed} reuse={reuse}",
    )


async def case_recovery_resume_iteration() -> None:
    rt, driver, _release, facts = await _orphan()
    seen: list[str] = []
    async for record in driver:
        seen.append(record["kind"])
    trace = kinds(driver.trace)
    reuse = await _reusable(rt)
    R.record(
        "C. resuming iteration recovers it",
        trace[-1] == "run_cancelled" and driver._closed and reuse == "completed",
        f"consumer_saw={seen} terminal={trace[-1]} closed={driver._closed} reuse={reuse}",
    )


async def case_recovery_gc() -> None:
    rt, driver, _release, facts = await _orphan()
    trace_ref = driver._trace_getter
    del driver
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    trace = kinds(trace_ref())
    reuse = await _reusable(rt)
    R.record(
        "D. dropping the driver + GC recovers it",
        trace and trace[-1].startswith("run_") and reuse == "completed",
        f"terminal={trace[-1] if trace else None} running_after_gc={rt._running} "
        f"reuse={reuse} (a terminal here would need the machine to be driven, "
        "not merely closed)",
    )


async def case_no_recovery_attempt() -> None:
    rt, driver, _release, facts = await _orphan()
    reuse = await _reusable(rt)
    trace = kinds(driver.trace)
    R.record(
        "E. no recovery attempt: run stays orphaned",
        trace[-1].startswith("run_") and reuse == "completed",
        f"terminal={trace[-1]} running={rt._running} reuse={reuse} "
        "(driver still referenced)",
    )


CASES = (
    case_orphan_shape,
    case_recovery_second_aclose,
    case_recovery_aexit,
    case_recovery_resume_iteration,
    case_recovery_gc,
    case_no_recovery_attempt,
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
