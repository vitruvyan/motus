"""Attack 5 -- the threading locks under asyncio.

Two claims are tested.

(a) Deadlock.  ``_managed_execute``'s ``finally`` takes ``_lifecycle_lock``.
    That ``finally`` can run on an arbitrary thread (generator finalisation) or
    inside an event-loop callback.  Is there a lock-ordering inversion, or a
    holder that can block long enough to wedge the loop?

(b) Stall.  ``_ObservationHub`` owns a second ``threading.RLock`` and a
    ``threading.Timer`` that flushes in the background while holding it.  Under
    ``arun`` the runner thread IS the event loop thread, so any contention on
    that lock blocks *every other task on the loop*, not just this run.
    guarantees.md §6 only ever promised that a listener "can delay the runner".

Heartbeat gaps are measured, so the answer is a number, not an opinion.
"""

from __future__ import annotations

import asyncio
import threading
import time

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report

R = Report("x2_05 locks vs the event loop")

STALL_BUDGET_S = 0.25


class Heartbeat:
    def __init__(self) -> None:
        self.max_gap = 0.0
        self._stop = False

    async def run(self) -> None:
        last = time.perf_counter()
        while not self._stop:
            await asyncio.sleep(0.001)
            now = time.perf_counter()
            self.max_gap = max(self.max_gap, now - last)
            last = now

    def stop(self) -> None:
        self._stop = True


class TimerStallSink:
    """Fast on the loop thread, glacial on the hub's background Timer thread."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.timer_writes = 0
        self.main = threading.current_thread()

    def open_run(self, header):
        return self

    def write(self, records) -> None:
        if threading.current_thread() is not self.main:
            self.timer_writes += 1
            time.sleep(self.delay)


class LoopThreadStallSink:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    def open_run(self, header):
        return self

    def write(self, records) -> None:
        self.calls += 1
        time.sleep(self.delay)


async def slow_node(state: State) -> State:
    await asyncio.sleep(0.02)
    return state


def _rt(**kw) -> Runtime:
    return Runtime(LINEAR, {"a": slow_node, "b": slow_node, "c": slow_node}, **kw)


async def case_background_timer_stalls_the_loop() -> None:
    sink = TimerStallSink(1.0)
    rt = _rt(
        durability_profile="buffered",
        sink=sink,
        chunk_records=10_000,
        flush_interval_ms=5,
    )
    beat = Heartbeat()
    beat_task = asyncio.create_task(beat.run())
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    result = await rt.arun(State.empty("stall"))
    elapsed = time.perf_counter() - started
    beat.stop()
    await beat_task
    R.record(
        "hub Timer thread stalls the event loop",
        beat.max_gap < STALL_BUDGET_S,
        f"max_heartbeat_gap={beat.max_gap:.3f}s arun_wall={elapsed:.3f}s "
        f"timer_writes={sink.timer_writes} status={result.status}",
    )


async def case_sink_write_on_loop_thread() -> None:
    sink = LoopThreadStallSink(0.15)
    rt = _rt(durability_profile="synchronous", sink=sink)
    beat = Heartbeat()
    beat_task = asyncio.create_task(beat.run())
    await asyncio.sleep(0.01)
    result = await rt.arun(State.empty("syncsink"))
    beat.stop()
    await beat_task
    R.record(
        "synchronous sink write on the loop thread",
        beat.max_gap < STALL_BUDGET_S,
        f"max_heartbeat_gap={beat.max_gap:.3f}s writes={sink.calls} "
        f"status={result.status}",
    )


async def case_listener_blocks_the_loop() -> None:
    class Sleeper:
        def on_record(self, record) -> None:
            time.sleep(0.05)

    rt = _rt(listeners=(Sleeper(),))
    beat = Heartbeat()
    beat_task = asyncio.create_task(beat.run())
    await asyncio.sleep(0.01)
    await rt.arun(State.empty("listener"))
    beat.stop()
    await beat_task
    R.record(
        "blocking listener on the loop thread",
        beat.max_gap < STALL_BUDGET_S,
        f"max_heartbeat_gap={beat.max_gap:.3f}s",
    )


def case_finalisation_on_a_foreign_thread() -> None:
    """The astream generator is finalised by GC on a thread that is not the
    loop's.  ``_managed_execute``'s finally then takes ``_lifecycle_lock``
    there.  Does anything deadlock, and does the Runtime come back clean?"""
    import gc

    rt = _rt()
    holder: list = []

    async def make() -> None:
        driver = rt.astream(State.empty("foreign"))
        await driver.__anext__()
        holder.append(driver)

    asyncio.run(make())

    done = threading.Event()
    outcome: dict = {}

    def reaper() -> None:
        holder.clear()
        gc.collect()
        outcome["running"] = rt._running
        done.set()

    worker = threading.Thread(target=reaper)
    worker.start()
    finished = done.wait(10)
    worker.join(5)
    reuse = "n/a"
    if finished:
        try:
            reuse = asyncio.run(rt.arun(State.empty("after"))).status
        except BaseException as exc:  # noqa: BLE001
            reuse = f"{type(exc).__name__}: {exc}"
    R.record(
        "generator finalised by GC on a foreign thread",
        finished and reuse == "completed",
        f"reaper_finished={finished} running_after={outcome.get('running')} reuse={reuse}",
    )


def case_lock_ordering() -> None:
    """Look for an inversion: does any path hold _lifecycle_lock and then want
    the hub lock, while another holds the hub lock and wants _lifecycle_lock?"""
    import inspect

    from vitruvyan_motus import runtime as runtime_module

    source = inspect.getsource(runtime_module.Runtime._managed_execute)
    hub_first = source.index("_hub.close()") < source.index("_lifecycle_lock")
    R.record(
        "no _lifecycle_lock -> hub lock nesting",
        hub_first,
        "hub.close() completes before _lifecycle_lock is taken" if hub_first
        else "INVERSION: hub lock acquired while holding _lifecycle_lock",
    )


async def amain() -> None:
    await case_listener_blocks_the_loop()
    await case_sink_write_on_loop_thread()
    await case_background_timer_stalls_the_loop()


def main() -> int:
    case_lock_ordering()
    asyncio.run(amain())
    case_finalisation_on_a_foreign_thread()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
