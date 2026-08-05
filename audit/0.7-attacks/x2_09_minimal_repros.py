"""Attack 9 -- minimal reproductions of the confirmed defects, plus the
synchronous-parity probes that decide whether each is new in 0.7 or inherited.

Run:  PYTHONPATH=.attack python .attack/x2_09_minimal_repros.py
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report, kinds, lifecycle

R = Report("x2_09 minimal reproductions")


async def anode(state: State) -> State:
    await asyncio.sleep(0)
    return state


def snode(state: State) -> State:
    return state


def _art() -> Runtime:
    return Runtime(LINEAR, {"a": anode, "b": anode, "c": anode})


def _srt() -> Runtime:
    return Runtime(LINEAR, {"a": snode, "b": snode, "c": snode})


class RecordingSink:
    def __init__(self) -> None:
        self.sessions: list[tuple[dict, list]] = []

    def open_run(self, header):
        session = _Session()
        self.sessions.append((header, session.records))
        return session


class _Session:
    def __init__(self) -> None:
        self.records: list = []

    def write(self, records) -> None:
        self.records.extend(records)


# --------------------------------------------------------------------------- #
# D1 -- a driver that is never advanced wedges the Runtime and opens an empty
#       durable session.
# --------------------------------------------------------------------------- #
def d1_never_advanced() -> None:
    sink = RecordingSink()

    async def repro() -> str:
        rt = Runtime(
            LINEAR, {"a": anode, "b": anode, "c": anode},
            durability_profile="synchronous", sink=sink,
        )
        rt.astream(State.empty("abandoned"), run_id="orphan")  # never advanced
        gc.collect()
        await asyncio.sleep(0)
        gc.collect()
        try:
            return (await rt.arun(State.empty("next"), run_id="next")).status
        except BaseException as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    status = asyncio.run(repro())
    opened = [(h["run_id"], len(recs)) for h, recs in sink.sessions]
    R.record(
        "D1 astream never advanced",
        status == "completed" and all(n > 0 for _, n in opened),
        f"second_run={status} durable_sessions={opened}",
    )


def d1_sync_parity() -> None:
    sink = RecordingSink()
    rt = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=sink,
    )
    rt.stream(State.empty("abandoned"), run_id="orphan")
    gc.collect()
    try:
        status = rt.run(State.empty("next"), run_id="next").status
    except BaseException as exc:  # noqa: BLE001
        status = f"{type(exc).__name__}: {exc}"
    opened = [(h["run_id"], len(recs)) for h, recs in sink.sessions]
    R.record(
        "D1 sync parity: stream never advanced",
        status == "completed" and all(n > 0 for _, n in opened),
        f"second_run={status} durable_sessions={opened}",
    )


# --------------------------------------------------------------------------- #
# D2 -- a stale AsyncStreamDriver cancels a newer, unrelated run.
# --------------------------------------------------------------------------- #
def d2_stale_driver() -> None:
    rt = _art()
    kept: list = []

    async def phase_one() -> None:
        driver = rt.astream(State.empty("one"), run_id="run-one")
        await driver.__anext__()
        kept.append(driver)

    asyncio.run(phase_one())  # loop teardown finalises _adrive, frees _running
    driver = kept[0]
    box: dict = {}

    async def phase_two() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt._nodes["a"] = gate
        task = asyncio.create_task(rt.arun(State.empty("two"), run_id="run-two"))
        await entered.wait()
        await driver.aclose("stale context exit")   # what __aexit__ also does
        release.set()
        result = await task
        box["status"] = result.status
        box["reason"] = result.trace.records[-1].get("reason")
        box["run_id"] = result.trace.run["run_id"]

    asyncio.run(phase_two())
    R.record(
        "D2 stale driver cancels an unrelated run",
        box["status"] == "completed",
        f"driver_closed_before={False} unrelated_run={box['run_id']} "
        f"status={box['status']} reason={box['reason']!r} "
        f"stale_trace={kinds(driver.trace)}",
    )


def d2_sync_parity() -> None:
    """Nothing in the synchronous world finalises the driver's generator for
    you, so the equivalent needs an explicit poke at a private attribute."""
    rt = _srt()
    driver = rt.stream(State.empty("one"))
    next(driver)
    driver._iterator.close()
    gc.collect()
    R.record(
        "D2 sync parity: no automatic finaliser",
        rt._running,
        f"running_after_generator_close={rt._running} "
        "(sync has no shutdown_asyncgens equivalent; _running stays held)",
    )


# --------------------------------------------------------------------------- #
# D3 -- graceful shutdown: aclose() while the consumer task is inside __anext__
# --------------------------------------------------------------------------- #
def d3_shutdown_race() -> None:
    box: dict = {}

    async def repro() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
        driver = rt.astream(State.empty("shutdown"))

        async def consumer() -> None:
            async for _ in driver:
                pass

        task = asyncio.create_task(consumer())
        await entered.wait()                      # consumer is inside __anext__
        try:
            await driver.aclose("graceful shutdown")
            box["aclose"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            box["aclose"] = f"{type(exc).__name__}: {exc}"
        release.set()
        try:
            await asyncio.wait_for(task, 2)
            box["consumer"] = "done"
        except BaseException as exc:  # noqa: BLE001
            box["consumer"] = type(exc).__name__
            task.cancel()
        box["trace"] = kinds(driver.trace)
        box["closed"] = driver._closed
        box["life"] = lifecycle(rt)
        try:
            box["second"] = (
                await asyncio.wait_for(rt.arun(State.empty("second")), 2)
            ).status
        except BaseException as exc:  # noqa: BLE001
            box["second"] = f"{type(exc).__name__}: {exc}"

    asyncio.run(repro())
    trace = box["trace"]
    R.record(
        "D3 aclose() during an in-flight __anext__",
        trace and trace[-1].startswith("run_") and box["second"] == "completed",
        f"aclose={box['aclose']} terminal={trace[-1] if trace else None} "
        f"driver_closed={box['closed']} cancel_reason={box['life']['_cancel_reason']!r} "
        f"running={box['life']['_running']} second={box['second']}",
    )


def d3_sync_parity() -> None:
    """The synchronous twin needs reentrancy -- a listener that pulls the
    driver it is being delivered from."""
    holder: dict = {}

    class Reentrant:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "hit" not in holder:
                holder["hit"] = True
                try:
                    next(holder["driver"])
                except BaseException as exc:  # noqa: BLE001
                    holder["err"] = f"{type(exc).__name__}: {exc}"

    rt = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(Reentrant(),)
    )
    driver = rt.stream(State.empty("reentrant"))
    holder["driver"] = driver
    try:
        for _ in driver:
            pass
        outer = "exhausted"
    except BaseException as exc:  # noqa: BLE001
        outer = f"{type(exc).__name__}: {exc}"
    try:
        driver.close("after the clash")
    except BaseException:  # noqa: BLE001
        pass
    trace = kinds(driver.trace)
    R.record(
        "D3 sync parity: reentrant next() latches _closed",
        trace and trace[-1].startswith("run_") and not rt._running,
        f"inner={holder.get('err')} outer={outer} "
        f"terminal={trace[-1] if trace else None} closed={driver._closed} "
        f"running={rt._running}",
    )


def main() -> int:
    d1_never_advanced()
    d1_sync_parity()
    d2_stale_driver()
    d2_sync_parity()
    d3_shutdown_race()
    d3_sync_parity()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
