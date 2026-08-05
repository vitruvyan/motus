"""Attack 4 -- Runtime.cancel() invoked from inside asynchronous contexts.

Four probes.

1. From a Listener during an async run.  guarantees.md §6 says listener
   delivery is synchronous on the runner thread and that "code that also holds
   the Runtime can invoke its public cancellation surface" -- so this must
   work under ``arun`` exactly as under ``run``.
2. From a coroutine node cancelling its own run.
3. From another task racing ``arun`` startup, 400 iterations, forced with an
   ``asyncio.Event`` and again with a ``threading.Barrier`` across a real
   thread boundary.  A leak is: ``_pending_cancel_reason`` still set after the
   run, or a later unrelated run coming back ``cancelled``.
4. Does ``cancel()`` ever return True for a run that then completes?
   ``Runtime.cancel``'s docstring: "``True`` means the request was bound to the
   active run or to the first run not yet begun."
"""

from __future__ import annotations

import asyncio
import threading

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report, kinds, lifecycle

R = Report("x2_04 cancel() from async contexts")


async def passthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


def _rt(**kw) -> Runtime:
    return Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough}, **kw)


async def case_cancel_from_listener() -> None:
    box: dict = {}

    class Canceller:
        def __init__(self) -> None:
            self.rt: Runtime | None = None

        def on_record(self, record):
            if record["kind"] == "transition" and "returned" not in box:
                box["returned"] = self.rt.cancel("cancelled by listener")

    canceller = Canceller()
    rt = _rt(listeners=(canceller,))
    canceller.rt = rt
    result = await rt.arun(State.empty("listener"))
    R.record(
        "cancel() from a listener under arun",
        result.status == "cancelled" and box.get("returned") is True,
        f"status={result.status} cancel_returned={box.get('returned')} "
        f"reason={result.trace.records[-1].get('reason')!r}",
    )


async def case_cancel_from_node() -> None:
    holder: dict = {}

    async def self_cancelling(state: State) -> State:
        holder["returned"] = holder["rt"].cancel("cancelled by its own node")
        await asyncio.sleep(0)
        return state

    rt = Runtime(LINEAR, {"a": self_cancelling, "b": passthrough, "c": passthrough})
    holder["rt"] = rt
    result = await rt.arun(State.empty("selfcancel"))
    R.record(
        "coroutine node cancels its own run",
        result.status == "cancelled" and holder.get("returned") is True,
        f"status={result.status} returned={holder.get('returned')} "
        f"trace={kinds(result.trace)}",
    )


async def case_cancel_returns_true_for_a_completing_run() -> None:
    """A listener watching for the terminal cancels after it was emitted."""
    box: dict = {}

    class LateCanceller:
        rt: Runtime | None = None

        def on_record(self, record):
            if record["kind"] == "run_completed":
                box["returned"] = self.rt.cancel("too late")

    late = LateCanceller()
    rt = _rt(listeners=(late,))
    late.rt = rt
    result = await rt.arun(State.empty("late"))
    life = lifecycle(rt)
    second = await rt.arun(State.empty("second"))
    R.record(
        "cancel() after run_completed reports True",
        box.get("returned") is False,
        f"cancel_returned={box.get('returned')} first={result.status} "
        f"pending_after={life['_pending']!r} second={second.status}",
    )


async def case_race_task_startup(iterations: int = 400) -> None:
    leaks = 0
    outcomes: dict[str, int] = {}
    for i in range(iterations):
        rt = _rt()
        if i % 2:
            await rt.arun(State.empty("warm"))  # half the runs are not the first
        armed = asyncio.Event()

        async def canceller() -> bool:
            armed.set()
            return rt.cancel(f"race-{i}")

        async def runner():
            await armed.wait()
            return await rt.arun(State.empty("raced"))

        cancel_task = asyncio.create_task(canceller())
        run_task = asyncio.create_task(runner())
        returned, result = await asyncio.gather(cancel_task, run_task)
        outcomes[f"{returned}/{result.status}"] = (
            outcomes.get(f"{returned}/{result.status}", 0) + 1
        )
        follow = await rt.arun(State.empty("follow"))
        if rt._pending_cancel_reason is not None or follow.status != "completed":
            leaks += 1
    R.record(
        f"cancel() racing arun startup, same loop x{iterations}",
        leaks == 0,
        f"leaks={leaks} outcomes={outcomes}",
    )


def case_race_across_threads(iterations: int = 400) -> None:
    leaks = 0
    outcomes: dict[str, int] = {}
    for i in range(iterations):
        rt = _rt()
        if i % 2:
            asyncio.run(rt.arun(State.empty("warm")))
        barrier = threading.Barrier(2)
        returned: list = []

        def cancel_thread() -> None:
            barrier.wait()
            returned.append(rt.cancel(f"thread-race-{i}"))

        worker = threading.Thread(target=cancel_thread)
        worker.start()

        async def go():
            barrier.wait()
            return await rt.arun(State.empty("raced"))

        result = asyncio.run(go())
        worker.join(5)
        key = f"{returned[0] if returned else '?'}/{result.status}"
        outcomes[key] = outcomes.get(key, 0) + 1
        follow = asyncio.run(rt.arun(State.empty("follow")))
        if rt._pending_cancel_reason is not None or follow.status != "completed":
            leaks += 1
    R.record(
        f"cancel() racing arun startup, real thread x{iterations}",
        leaks == 0,
        f"leaks={leaks} outcomes={outcomes}",
    )


async def main() -> int:
    await case_cancel_from_listener()
    await case_cancel_from_node()
    await case_cancel_returns_true_for_a_completing_run()
    await case_race_task_startup()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
    case_race_across_threads()
    raise SystemExit(1 if R.dump() else 0)
