"""x2b-05 -- minimal reproductions of what the patch did NOT close.

M1: ``AsyncStreamDriver.__anext__`` still latches ``_closed`` on a transient
    ``RuntimeError: anext(): asynchronous generator is already running``.  Only
    ``aclose`` received the ``ag_running`` guard; the other half of X2-002 is
    untouched, so two readers still orphan the run and wedge the Runtime.

M2: ``_run_scoped_cancel``'s identity test reads ``self._trace_ref``, which
    ``_start`` rebinds long after it sets ``_running``.  A stale driver firing
    in that window is admitted.

M3: how far the M2 window reaches -- only the immediately preceding run's
    driver holds the list that ``self._trace_ref`` still points at, so the
    residual exposure is one run deep, not unbounded.  Stated so the fix can be
    scoped correctly rather than over-scoped.
"""

from __future__ import annotations

import asyncio
import threading

from vitruvyan_motus import Runtime, State
from x2b_common import LINEAR, Report, anode, kinds, lifecycle

R = Report("x2b_05 residual defects")


def m1_two_readers_latch_anext() -> None:
    box: dict = {}

    async def go() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
        driver = rt.astream(State.empty("two-readers"))
        await driver.__anext__()   # run_started
        await driver.__anext__()   # attempt_started; the next step enters 'a'

        errors: list[str] = []

        async def reader(tag: str) -> None:
            try:
                await driver.__anext__()
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"{tag}:{type(exc).__name__}")

        r1 = asyncio.create_task(reader("r1"))   # enters the node, suspends
        r2 = asyncio.create_task(reader("r2"))   # collides
        await entered.wait()
        await asyncio.sleep(0)
        box["closed_after_clash"] = driver._closed
        release.set()
        await asyncio.gather(r1, r2)

        # The consumer now does the documented thing: close the driver.
        await driver.aclose("consumer stopped")
        box["errors"] = errors
        box["trace"] = kinds(driver.trace)
        box["life"] = lifecycle(rt)
        try:
            box["second"] = (
                await asyncio.wait_for(rt.arun(State.empty("second")), 3)
            ).status
        except BaseException as exc:  # noqa: BLE001
            box["second"] = f"{type(exc).__name__}: {exc}"

    asyncio.run(go())
    trace = box["trace"]
    R.record(
        "M1 __anext__ latch on a transient RuntimeError",
        trace and trace[-1].startswith("run_") and box["second"] == "completed",
        f"errors={box['errors']} closed_after_clash={box['closed_after_clash']} "
        f"terminal={trace[-1] if trace else None} running={box['life']['_running']} "
        f"second={box['second']}",
    )


class WindowNode:
    """``motus_config()`` is documented as evaluated at every run start
    (ADR-008 §4); it therefore executes inside ``_start``'s window."""

    def __init__(self) -> None:
        self.inside = threading.Event()
        self.release = threading.Event()
        self.arm = False

    def motus_config(self):
        if self.arm:
            self.arm = False
            self.inside.set()
            self.release.wait(10)
        return {"v": 1}

    def __call__(self, state: State) -> State:
        return state


def _stale_async_driver(rt: Runtime, run_id: str):
    kept: list = []

    async def phase() -> None:
        driver = rt.astream(State.empty("stale"), run_id=run_id)
        await driver.__anext__()
        kept.append(driver)

    asyncio.run(phase())  # loop teardown ends the run; the driver stays open
    return kept[0]


def m2_start_window() -> None:
    node = WindowNode()
    rt = Runtime(LINEAR, {"a": node, "b": node, "c": node})
    driver = _stale_async_driver(rt, "run-one")
    box: dict = {}

    def second_run() -> None:
        node.arm = True
        box["result"] = asyncio.run(rt.arun(State.empty("two"), run_id="run-two"))

    worker = threading.Thread(target=second_run)
    worker.start()
    node.inside.wait(10)
    box["running"] = rt._running
    asyncio.run(driver.aclose("stale context exit"))
    box["bound"] = rt._cancel_reason
    node.release.set()
    worker.join(20)
    result = box["result"]
    R.record(
        "M2 stale driver admitted in _start's window",
        result.status == "completed",
        f"running_in_window={box['running']} reason_bound={box['bound']!r} "
        f"unrelated_run={result.trace.run['run_id']} status={result.status}",
    )


def m3_window_depth() -> None:
    """Two runs separate the stale driver from the new one: its list is no
    longer what ``self._trace_ref`` holds, so the window must refuse it."""
    node = WindowNode()
    rt = Runtime(LINEAR, {"a": node, "b": node, "c": node})
    driver = _stale_async_driver(rt, "run-one")
    asyncio.run(rt.arun(State.empty("interposed"), run_id="run-interposed"))
    box: dict = {}

    def third_run() -> None:
        node.arm = True
        box["result"] = asyncio.run(rt.arun(State.empty("three"), run_id="run-three"))

    worker = threading.Thread(target=third_run)
    worker.start()
    node.inside.wait(10)
    asyncio.run(driver.aclose("stale context exit"))
    box["bound"] = rt._cancel_reason
    node.release.set()
    worker.join(20)
    result = box["result"]
    R.record(
        "M3 window is one run deep, not unbounded",
        result.status == "completed",
        f"with_one_interposed_run: reason_bound={box['bound']!r} "
        f"status={result.status} (M2 shape is the exploitable depth)",
    )


def main() -> int:
    m1_two_readers_latch_anext()
    m2_start_window()
    m3_window_depth()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
