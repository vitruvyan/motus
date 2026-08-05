"""Attack 3 -- asyncio.Task.cancel() at every stage of arun/astream.

node-protocol.md §7.3: "An attempt interrupted by hard cancellation or crash
leaves its ``attempt_started`` unclosed -- visible, attributable evidence,
never an erased gap".

For each cancellation point the script reports: the final record kinds, whether
``_running`` was reset, whether ``_pending_cancel_reason`` is clean, and
whether the Runtime can be reused.  A Runtime that cannot be reused after a
task cancellation is a leak of exactly the kind RA-001 was about, in the other
direction.

Every gate is released before the reuse probe, so a hang here is the product's,
not the harness's.
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report, kinds, lifecycle

R = Report("x2_03 task cancellation")


async def passthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


async def _reusable(rt: Runtime) -> str:
    try:
        return (await asyncio.wait_for(rt.arun(State.empty("reuse")), 5)).status
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _plain_runtime(a=None, b=None, c=None) -> Runtime:
    return Runtime(
        LINEAR,
        {"a": a or passthrough, "b": b or passthrough, "c": c or passthrough},
    )


def _gate_node(gate: asyncio.Event):
    async def held(state: State) -> State:
        await gate.wait()
        return state

    return held


async def case_cancel_before_start() -> None:
    rt = _plain_runtime()
    task = asyncio.create_task(rt.arun(State.empty("t")))
    task.cancel()  # cancelled before its first step runs at all
    try:
        await task
        outcome = "no-raise"
    except asyncio.CancelledError:
        outcome = "CancelledError"
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    reuse = await _reusable(rt)
    R.record(
        "cancel before the coroutine's first step",
        outcome == "CancelledError" and not life["_running"] and reuse == "completed",
        f"trace={trace} running={life['_running']} pending={life['_pending']!r} "
        f"reuse={reuse}",
    )


async def case_cancel_during_node_await() -> None:
    gate = asyncio.Event()
    rt = _plain_runtime(a=_gate_node(gate))
    task = asyncio.create_task(rt.arun(State.empty("t")))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
        outcome = "no-raise"
    except asyncio.CancelledError:
        outcome = "CancelledError"
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    gate.set()
    reuse = await _reusable(rt)
    R.record(
        "cancel during a node's await",
        outcome == "CancelledError"
        and not life["_running"]
        and life["_pending"] is None
        and reuse == "completed"
        and trace == ["run_started", "attempt_started"],
        f"trace={trace} running={life['_running']} pending={life['_pending']!r} "
        f"reuse={reuse}",
    )


async def case_cancel_on_final_node() -> None:
    gate = asyncio.Event()
    rt = _plain_runtime(c=_gate_node(gate))
    task = asyncio.create_task(rt.arun(State.empty("t")))
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
        outcome = "no-raise"
    except asyncio.CancelledError:
        outcome = "CancelledError"
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    gate.set()
    reuse = await _reusable(rt)
    R.record(
        "cancel on the final node",
        outcome == "CancelledError"
        and not life["_running"]
        and reuse == "completed"
        and trace[-1] == "attempt_started",
        f"trace={trace[-3:]} running={life['_running']} reuse={reuse}",
    )


async def case_cancel_fully_synchronous_arun() -> None:
    """No node suspends, so arun completes inside a single Task step."""

    def quick(state: State) -> State:
        return state

    rt = Runtime(LINEAR, {"a": quick, "b": quick, "c": quick})
    task = asyncio.create_task(rt.arun(State.empty("t")))
    task.cancel()
    try:
        result = await task
        outcome = f"returned:{result.status}"
    except asyncio.CancelledError:
        outcome = "CancelledError"
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    reuse = await _reusable(rt)
    R.record(
        "cancel a fully-synchronous arun",
        not life["_running"] and reuse == "completed",
        f"outcome={outcome} terminal={trace[-1] if trace else None} "
        f"running={life['_running']} reuse={reuse}",
    )


async def case_cancel_inside_anext() -> None:
    """Cancellation delivered while awaiting ``driver.__anext__`` itself."""
    gate = asyncio.Event()
    rt = _plain_runtime(a=_gate_node(gate))
    driver = rt.astream(State.empty("t"))
    await driver.__anext__()  # run_started

    async def consumer() -> None:
        while True:
            await driver.__anext__()

    task = asyncio.create_task(consumer())
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    life = lifecycle(rt)
    trace = kinds(driver.trace)
    closed = driver._closed
    gate.set()
    reuse = await _reusable(rt)
    R.record(
        "cancel inside driver.__anext__",
        closed and not life["_running"] and reuse == "completed",
        f"trace={trace} driver_closed={closed} running={life['_running']} reuse={reuse}",
    )


async def case_wait_for_timeout() -> None:
    """The idiomatic timeout wrapper -- asyncio.wait_for cancels for you."""
    gate = asyncio.Event()
    rt = _plain_runtime(a=_gate_node(gate))
    try:
        await asyncio.wait_for(rt.arun(State.empty("t")), timeout=0.02)
        outcome = "no-raise"
    except asyncio.TimeoutError:
        outcome = "TimeoutError"
    life = lifecycle(rt)
    trace = kinds(rt.trace)
    gate.set()
    reuse = await _reusable(rt)
    R.record(
        "asyncio.wait_for timeout around arun",
        outcome == "TimeoutError" and not life["_running"] and reuse == "completed",
        f"outcome={outcome} trace={trace} running={life['_running']} reuse={reuse}",
    )


async def case_cancel_consumer_between_records() -> None:
    """The consumer task is cancelled while suspended in its OWN code, not
    inside ``__anext__``.  The async generator is then left suspended at its
    ``yield`` while ``_running`` is still True and nobody owns the driver."""
    rt = _plain_runtime()
    holder: list = [rt.astream(State.empty("t"))]
    ready = asyncio.Event()

    async def consumer() -> None:
        await holder[0].__anext__()
        ready.set()
        await asyncio.sleep(3600)  # suspended between records, in user code

    task = asyncio.create_task(consumer())
    await ready.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    closed_flag = holder[0]._closed
    life_immediately = lifecycle(rt)
    holder.clear()  # the consumer is gone; drop the last reference
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    life_after_gc = lifecycle(rt)
    trace = kinds(rt.trace)
    reuse = await _reusable(rt)
    R.record(
        "cancel consumer task between astream records",
        reuse == "completed",
        f"driver_closed={closed_flag} running_now={life_immediately['_running']} "
        f"running_after_gc={life_after_gc['_running']} abandoned_trace={trace} "
        f"reuse={reuse}",
    )


CASES = (
    case_cancel_before_start,
    case_cancel_during_node_await,
    case_cancel_on_final_node,
    case_cancel_fully_synchronous_arun,
    case_cancel_inside_anext,
    case_wait_for_timeout,
    case_cancel_consumer_between_records,
)


async def main() -> int:
    for case in CASES:
        await case()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
