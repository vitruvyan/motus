"""Attack 2 -- cross-driver contamination.

Two questions.

(a) Does abandoning one driver leave the other surface usable?

(b) RA-001's exact shape, hunted on the async side: can a *stale* driver whose
    ``_closed`` flag is still False issue ``aclose()`` and have that
    cancellation land on a newer, unrelated run?

For (b) the sync driver needed someone to close the underlying generator
behind the driver's back -- rare.  Asyncio does it for you: ``asyncio.run``
ends with ``loop.shutdown_asyncgens()``, which finalises every live async
generator, including ``_adrive``.  That releases ``_running`` while leaving the
``AsyncStreamDriver`` object itself convinced it is still open.
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import State
from x2_common import LINEAR, Report, async_runtime, kinds, lifecycle, sync_runtime

R = Report("x2_02 cross-driver contamination")


def case_sync_stream_abandoned_then_arun() -> None:
    rt = sync_runtime()
    driver = rt.stream(State.empty("sync-first"))
    next(driver)
    next(driver)
    first_kinds = kinds(rt.trace)
    del driver
    gc.collect()
    life = lifecycle(rt)
    try:
        status = asyncio.run(rt.arun(State.empty("async-second"))).status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "sync stream abandoned -> arun",
        status == "completed",
        f"first_trace={first_kinds} running_after_gc={life['_running']} second={status}",
    )


def case_astream_abandoned_then_sync_run() -> None:
    rt = sync_runtime()

    async def phase() -> list[str]:
        driver = rt.astream(State.empty("async-first"))
        await driver.__anext__()
        await driver.__anext__()
        k = kinds(rt.trace)
        del driver
        gc.collect()
        await asyncio.sleep(0)
        return k

    first_kinds = asyncio.run(phase())
    gc.collect()
    life = lifecycle(rt)
    try:
        status = rt.run(State.empty("sync-second")).status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "astream abandoned -> run()",
        status == "completed",
        f"first_trace={first_kinds} running_after_loop={life['_running']} second={status}",
    )


def case_stale_async_driver_cancels_a_newer_run() -> None:
    """RA-001's shape: does a stale driver's aclose() hit an unrelated run?"""
    rt = async_runtime()
    stale: list = []

    async def phase_one() -> None:
        driver = rt.astream(State.empty("phase-one"))
        await driver.__anext__()          # run_started; run is live
        stale.append(driver)              # keep the driver, drop nothing
        # loop teardown at asyncio.run() exit finalises _adrive for us

    asyncio.run(phase_one())
    driver = stale[0]
    life_between = lifecycle(rt)
    first_kinds = kinds(driver.trace)

    outcome: dict = {}

    async def phase_two() -> None:
        release = asyncio.Event()
        entered = asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt._nodes["a"] = gate
        rt._uses_context["a"] = False
        task = asyncio.create_task(rt.arun(State.empty("phase-two")))
        await entered.wait()
        outcome["cancel_returned"] = None
        await driver.aclose("stale driver context exit")
        release.set()
        result = await task
        outcome["status"] = result.status
        outcome["reason"] = (
            result.trace.records[-1].get("reason")
            if result.status == "cancelled" else None
        )

    asyncio.run(phase_two())
    R.record(
        "stale async driver aclose() vs newer run",
        outcome.get("status") == "completed",
        f"first_trace={first_kinds} running_between={life_between['_running']} "
        f"driver_closed_between={driver._closed if False else 'n/a'} "
        f"second={outcome.get('status')} reason={outcome.get('reason')!r}",
    )


def case_stale_sync_driver_cancels_a_newer_run() -> None:
    """The synchronous control: same shape, but nothing closes the generator
    for you, so it takes an explicit poke at the private iterator."""
    rt = sync_runtime()
    driver = rt.stream(State.empty("sync-phase-one"))
    next(driver)
    driver._iterator.close()          # stands in for loop.shutdown_asyncgens()
    life_between = lifecycle(rt)
    import threading

    outcome: dict = {}
    release = threading.Event()
    entered = threading.Event()

    def gate(state: State) -> State:
        entered.set()
        release.wait(5)
        return state

    rt._nodes["a"] = gate
    rt._uses_context["a"] = False
    result: list = []
    worker = threading.Thread(target=lambda: result.append(rt.run(State.empty("sync-two"))))
    worker.start()
    entered.wait(5)
    driver.close("stale sync driver close")
    release.set()
    worker.join(5)
    outcome["status"] = result[0].status if result else "no-result"
    R.record(
        "stale sync driver close() vs newer run",
        outcome["status"] == "completed",
        f"running_between={life_between['_running']} second={outcome['status']}",
    )


def main() -> int:
    case_sync_stream_abandoned_then_arun()
    case_astream_abandoned_then_sync_run()
    case_stale_async_driver_cancels_a_newer_run()
    case_stale_sync_driver_cancels_a_newer_run()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
