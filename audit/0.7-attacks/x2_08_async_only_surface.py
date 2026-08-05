"""Attack 8 -- shapes that exist only because the driver is asynchronous.

A ``StreamDriver`` is pulled by one caller on one thread.  An
``AsyncStreamDriver`` sits on a loop where any number of tasks can touch it,
and where the generator it wraps is owned by a *loop*, not by a thread.  Three
consequences are probed:

1. Two tasks calling ``__anext__`` concurrently.  CPython answers with
   ``RuntimeError: anext(): asynchronous generator is already running`` -- a
   transient, caller-side mistake.  ``AsyncStreamDriver.__anext__`` catches
   ``BaseException`` and latches ``_closed = True``, which makes the subsequent
   ``aclose()``/``__aexit__`` a no-op.  If the run is still live at that point,
   nothing will ever give it a terminal.
2. ``aclose()`` racing an in-flight ``__anext__``.
3. Driving one driver from two successive event loops.

For each, the question is the same as always: does the run reach a terminal,
does ``_running`` come back, and is the Runtime reusable?
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report, kinds, lifecycle

R = Report("x2_08 async-only driver surface")


def _rt(first=None) -> Runtime:
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state

    return Runtime(LINEAR, {"a": first or node, "b": node, "c": node})


async def case_two_tasks_one_driver() -> None:
    gate = asyncio.Event()

    async def held(state: State) -> State:
        await gate.wait()
        return state

    rt = _rt(first=held)
    driver = rt.astream(State.empty("shared"))
    await driver.__anext__()  # run_started
    await driver.__anext__()  # attempt_started -- the NEXT step is the invoke

    errors: list[str] = []

    async def puller(tag: str) -> None:
        try:
            await driver.__anext__()
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"{tag}:{type(exc).__name__}")

    t1 = asyncio.create_task(puller("t1"))  # enters the node's await, suspends
    t2 = asyncio.create_task(puller("t2"))  # collides with a running asyncgen
    for _ in range(5):
        await asyncio.sleep(0)
    closed_after_clash = driver._closed
    gate.set()
    await asyncio.gather(t1, t2)

    # The consumer now does the correct thing: it closes the driver.
    await driver.aclose("consumer stopped")
    trace = kinds(driver.trace)
    life = lifecycle(rt)
    try:
        second = await asyncio.wait_for(rt.arun(State.empty("second")), 5)
        status = second.status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "two tasks pull one AsyncStreamDriver",
        status == "completed" and trace and trace[-1].startswith("run_"),
        f"errors={errors} closed_after_clash={closed_after_clash} "
        f"terminal={trace[-1] if trace else None} running={life['_running']} "
        f"second={status}",
    )


async def case_two_tasks_one_driver_context_managed() -> None:
    """The same clash, but the consumer used ``async with`` -- the idiom the
    contract recommends.  ``__aexit__`` sees ``_closed`` already True."""
    gate = asyncio.Event()

    async def held(state: State) -> State:
        await gate.wait()
        return state

    rt = _rt(first=held)
    holder: list = []
    trace_seen: list = []

    async def consume() -> None:
        async with rt.astream(State.empty("shared")) as driver:
            holder.append(driver)
            await driver.__anext__()  # run_started
            await driver.__anext__()  # attempt_started
            t1 = asyncio.create_task(driver.__anext__())
            t2 = asyncio.create_task(driver.__anext__())
            for _ in range(4):
                await asyncio.sleep(0)
            for t in (t1, t2):
                try:
                    await t
                except BaseException:  # noqa: BLE001
                    pass
            trace_seen.append(kinds(driver.trace))

    task = asyncio.create_task(consume())
    for _ in range(6):
        await asyncio.sleep(0)
    gate.set()
    await task
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    holder.clear()
    gc.collect()
    await asyncio.sleep(0)
    life_gc = lifecycle(rt)
    try:
        status = (await asyncio.wait_for(rt.arun(State.empty("second")), 5)).status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "clash inside `async with` -> terminal?",
        trace and trace[-1].startswith("run_") and status == "completed",
        f"trace_at_exit={trace} terminal={trace[-1] if trace else None} "
        f"running_at_exit={life['_running']} running_after_gc={life_gc['_running']} "
        f"second={status}",
    )


async def case_aclose_racing_anext() -> None:
    gate = asyncio.Event()

    async def held(state: State) -> State:
        await gate.wait()
        return state

    rt = _rt(first=held)
    driver = rt.astream(State.empty("raced"))
    await driver.__anext__()  # run_started
    await driver.__anext__()  # attempt_started
    errors: list[str] = []

    async def pull() -> None:
        try:
            await driver.__anext__()
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"anext:{type(exc).__name__}")

    async def closer() -> None:
        try:
            await driver.aclose("racing close")
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"aclose:{type(exc).__name__}")

    tasks = [asyncio.create_task(pull()), asyncio.create_task(closer())]
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)
    trace = kinds(driver.trace)
    life = lifecycle(rt)
    try:
        status = (await asyncio.wait_for(rt.arun(State.empty("second")), 5)).status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "aclose() racing an in-flight __anext__",
        status == "completed" and trace and trace[-1].startswith("run_"),
        f"errors={errors} terminal={trace[-1] if trace else None} "
        f"running={life['_running']} second={status}",
    )


def case_driver_across_two_loops() -> None:
    rt = _rt()
    holder: list = []

    async def phase_one() -> None:
        driver = rt.astream(State.empty("loop-one"))
        await driver.__anext__()
        holder.append(driver)

    asyncio.run(phase_one())
    driver = holder[0]
    outcome: dict = {}

    async def phase_two() -> None:
        try:
            await driver.__anext__()
            outcome["anext"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            outcome["anext"] = f"{type(exc).__name__}: {exc}"
        outcome["trace"] = kinds(driver.trace)
        outcome["closed"] = driver._closed

    asyncio.run(phase_two())
    life = lifecycle(rt)
    try:
        status = asyncio.run(rt.arun(State.empty("second"))).status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    R.record(
        "one driver, two successive event loops",
        status == "completed"
        and outcome["trace"]
        and outcome["trace"][-1].startswith("run_"),
        f"anext={outcome['anext']} trace={outcome['trace']} "
        f"closed={outcome['closed']} running={life['_running']} second={status}",
    )


async def amain() -> None:
    await case_two_tasks_one_driver()
    await case_two_tasks_one_driver_context_managed()
    await case_aclose_racing_anext()


def main() -> int:
    asyncio.run(amain())
    case_driver_across_two_loops()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
