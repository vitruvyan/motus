"""Attack 1 -- AsyncStreamDriver exhaustion, cleanup and abandonment.

RA-001 was: a finished StreamDriver was not marked closed, so context exit
cancelled the NEXT run.  The async twin claims to have fixed that by latching
``_closed`` in ``__anext__``'s ``except BaseException``.  This script tries
every way of finishing or abandoning an AsyncStreamDriver and asks, after each,
whether a later run on the same Runtime is still clean.

The last two cases are the interesting ones: a driver that is created and never
advanced at all.  ``Runtime._start`` sets ``_running = True`` *before* handing
back a generator that has not begun; a generator that never began does not run
its ``finally`` when it is collected.
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import State
from x2_common import Report, async_runtime, kinds, lifecycle, sync_runtime

R = Report("x2_01 AsyncStreamDriver lifecycle")


async def case_exhaust_then_aclose() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    async for _ in driver:
        pass
    life_after_exhaust = lifecycle(rt)
    await driver.aclose()  # explicit close AFTER normal exhaustion
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "exhaust -> aclose() -> arun",
        second.status == "completed" and life["_pending"] is None,
        f"pending={life['_pending']!r} status={second.status} "
        f"after_exhaust_running={life_after_exhaust['_running']}",
    )


async def case_context_exhaust_then_arun() -> None:
    rt = async_runtime()
    async with rt.astream(State.empty("first")) as driver:
        async for _ in driver:
            pass
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "async with + exhaust -> arun",
        second.status == "completed" and life["_pending"] is None,
        f"pending={life['_pending']!r} status={second.status}",
    )


async def case_context_exhaust_then_sync_run() -> None:
    """Cross-driver: an exhausted async driver must not poison a sync run."""
    rt = sync_runtime()
    async with rt.astream(State.empty("first")) as driver:
        async for _ in driver:
            pass
    life = lifecycle(rt)
    second = rt.run(State.empty("second"))
    R.record(
        "async with + exhaust -> run()",
        second.status == "completed" and life["_pending"] is None,
        f"pending={life['_pending']!r} status={second.status}",
    )


async def case_break_midrun_then_exit() -> None:
    rt = async_runtime()
    async with rt.astream(State.empty("first")) as driver:
        async for _ in driver:
            break
    first_kinds = kinds(driver.trace)
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "break mid-run -> __aexit__",
        first_kinds[-1] == "run_cancelled"
        and second.status == "completed"
        and life["_pending"] is None,
        f"first={first_kinds} pending={life['_pending']!r} second={second.status}",
    )


async def case_double_aclose() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    await driver.__anext__()
    await driver.aclose("first close")
    before = kinds(driver.trace)
    await driver.aclose("second close")
    after = kinds(driver.trace)
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "aclose() twice",
        before == after and second.status == "completed" and life["_pending"] is None,
        f"before={before[-1]} after={after[-1]} pending={life['_pending']!r} "
        f"second={second.status}",
    )


async def case_anext_after_aclose() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    await driver.__anext__()
    await driver.aclose()
    try:
        await driver.__anext__()
        raised = "none"
    except StopAsyncIteration:
        raised = "StopAsyncIteration"
    except BaseException as exc:  # noqa: BLE001
        raised = type(exc).__name__
    R.record("__anext__ after aclose", raised == "StopAsyncIteration", f"raised={raised}")


async def case_abandon_partially_consumed() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    await driver.__anext__()
    await driver.__anext__()
    del driver
    gc.collect()
    await asyncio.sleep(0)  # let the asyncgen finaliser hook run
    gc.collect()
    await asyncio.sleep(0)
    life = lifecycle(rt)
    try:
        second = await rt.arun(State.empty("second"))
        status = second.status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "abandon partially consumed + GC",
        status == "completed",
        f"running_after_gc={life['_running']} pending={life['_pending']!r} second={status}",
    )


async def case_abandon_never_advanced() -> None:
    """The driver is created and dropped without a single ``__anext__``."""
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    life_created = lifecycle(rt)
    del driver
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    life = lifecycle(rt)
    try:
        second = await rt.arun(State.empty("second"))
        status = second.status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "abandon NEVER-advanced astream + GC",
        status == "completed",
        f"running_at_create={life_created['_running']} "
        f"running_after_gc={life['_running']} second={status}",
    )


def case_abandon_never_advanced_sync() -> None:
    """The same shape on the synchronous driver, for comparison."""
    rt = sync_runtime()
    driver = rt.stream(State.empty("first"))
    del driver
    gc.collect()
    life = lifecycle(rt)
    try:
        second = rt.run(State.empty("second"))
        status = second.status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "abandon NEVER-advanced stream + GC (sync)",
        status == "completed",
        f"running_after_gc={life['_running']} second={status}",
    )


async def case_never_advanced_then_aclose() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("first"))
    await driver.aclose("closed before first record")
    life = lifecycle(rt)
    k = kinds(driver.trace)
    second = await rt.arun(State.empty("second"))
    R.record(
        "never advanced -> aclose()",
        k and k[-1] == "run_cancelled" and second.status == "completed",
        f"first={k} pending={life['_pending']!r} second={second.status}",
    )


async def case_never_advanced_context() -> None:
    rt = async_runtime()
    async with rt.astream(State.empty("first")):
        pass
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "async with, zero iterations",
        second.status == "completed" and life["_pending"] is None,
        f"pending={life['_pending']!r} second={second.status}",
    )


async def main() -> int:
    await case_exhaust_then_aclose()
    await case_context_exhaust_then_arun()
    await case_context_exhaust_then_sync_run()
    await case_break_midrun_then_exit()
    await case_double_aclose()
    await case_anext_after_aclose()
    await case_abandon_partially_consumed()
    await case_never_advanced_then_aclose()
    await case_never_advanced_context()
    await case_abandon_never_advanced()
    case_abandon_never_advanced_sync()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
