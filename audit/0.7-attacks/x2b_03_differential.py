"""x2b-03 -- differential: which x2b failures did the PATCH introduce?

Runs the same probes against whichever ``vitruvyan_motus`` is on sys.path, so
the caller can point PYTHONPATH at the pre-patch tree (51e2439), at v0.6.1, at
main (f5fcf84), or at the working tree (a72cdf1) and diff the answers.

Usage:
    PYTHONPATH=<tree>/src:.attack python .attack/x2b_03_differential.py <label>
"""

from __future__ import annotations

import asyncio
import gc
import sys

from vitruvyan_motus import GraphSpec, Runtime, State

LABEL = sys.argv[1] if len(sys.argv) > 1 else "working-tree"

LINEAR_DOC = {
    "schema_version": "1.0.0",
    "name": "x2b-linear",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))


async def anode(state: State) -> State:
    await asyncio.sleep(0)
    return state


def snode(state: State) -> State:
    return state


def emit(name: str, **facts) -> None:
    body = " ".join(f"{k}={v!r}" for k, v in facts.items())
    print(f"{LABEL:>12} | {name:<38} | {body}", flush=True)


def kinds(trace):
    return [] if trace is None else [r["kind"] for r in trace.records]


class RecordingSink:
    def __init__(self) -> None:
        self.sessions: list = []

    def open_run(self, header):
        session = _Session(header)
        self.sessions.append(session)
        return session


class _Session:
    def __init__(self, header) -> None:
        self.header = header
        self.records: list = []

    def write(self, records) -> None:
        self.records.extend(records)


# --------------------------------------------------------------------------- #
def probe_never_advanced_stream() -> None:
    """X2-004 / D1: still open by decision -- confirm it is identical here."""
    sink = RecordingSink()
    rt = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=sink,
    )
    rt.stream(State.empty("abandoned"), run_id="orphan")
    gc.collect()
    try:
        second = rt.run(State.empty("next"), run_id="next").status
    except BaseException as exc:  # noqa: BLE001
        second = f"{type(exc).__name__}"
    emit(
        "X2-004 stream never advanced",
        running_stuck=rt._running,
        second=second,
        sessions=[(s.header["run_id"], len(s.records)) for s in sink.sessions],
    )


def probe_never_advanced_astream() -> None:
    if not hasattr(Runtime, "astream"):
        emit("X2-004 astream never advanced", available=False)
        return
    sink = RecordingSink()

    async def go() -> str:
        rt = Runtime(
            LINEAR, {"a": anode, "b": anode, "c": anode},
            durability_profile="synchronous", sink=sink,
        )
        rt.astream(State.empty("abandoned"), run_id="orphan")
        gc.collect()
        await asyncio.sleep(0)
        gc.collect()
        try:
            return (await rt.arun(State.empty("next"), run_id="next")).status
        except BaseException as exc:  # noqa: BLE001
            return type(exc).__name__

    second = asyncio.run(go())
    emit(
        "X2-004 astream never advanced",
        second=second,
        sessions=[(s.header["run_id"], len(s.records)) for s in sink.sessions],
    )


def probe_reader_breaks_after_aclose() -> None:
    """x2b-02 case 3: aclose() binds, then the reader stops asking."""
    if not hasattr(Runtime, "astream"):
        emit("aclose then reader breaks", available=False)
        return

    box: dict = {}

    async def go() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
        driver = rt.astream(State.empty("stops"))
        stop = False

        async def consumer() -> None:
            nonlocal stop
            async for _ in driver:
                if stop:
                    break

        task = asyncio.create_task(consumer())
        await entered.wait()
        try:
            await driver.aclose("graceful shutdown")
            box["aclose"] = "returned"
        except BaseException as exc:  # noqa: BLE001
            box["aclose"] = type(exc).__name__
        stop = True
        release.set()
        try:
            await asyncio.wait_for(task, 5)
        except BaseException as exc:  # noqa: BLE001
            box["consumer"] = type(exc).__name__
        box["trace"] = kinds(driver.trace)
        box["closed"] = driver._closed
        box["running"] = rt._running
        try:
            box["second"] = (
                await asyncio.wait_for(rt.arun(State.empty("second")), 3)
            ).status
        except BaseException as exc:  # noqa: BLE001
            box["second"] = type(exc).__name__

    asyncio.run(go())
    emit(
        "aclose then reader breaks",
        aclose=box.get("aclose"),
        terminal=box["trace"][-1] if box.get("trace") else None,
        closed=box.get("closed"),
        running=box.get("running"),
        second=box.get("second"),
    )


def probe_aclose_then_reader_cancelled() -> None:
    if not hasattr(Runtime, "astream"):
        emit("aclose then reader cancelled", available=False)
        return
    box: dict = {}

    async def go() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
        driver = rt.astream(State.empty("abandoned"))

        async def consumer() -> None:
            async for _ in driver:
                pass

        task = asyncio.create_task(consumer())
        await entered.wait()
        try:
            await driver.aclose("graceful shutdown")
            box["aclose"] = "returned"
        except BaseException as exc:  # noqa: BLE001
            box["aclose"] = type(exc).__name__
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        release.set()
        await asyncio.sleep(0)
        box["trace"] = kinds(driver.trace)
        box["closed"] = driver._closed
        box["running"] = rt._running

    asyncio.run(go())
    emit(
        "aclose then reader cancelled",
        aclose=box.get("aclose"),
        terminal=box["trace"][-1] if box.get("trace") else None,
        closed=box.get("closed"),
        running=box.get("running"),
    )


def probe_double_aclose_reason() -> None:
    if not hasattr(Runtime, "astream"):
        emit("double aclose reason", available=False)
        return
    box: dict = {}

    async def go() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
        driver = rt.astream(State.empty("double"))

        async def consumer() -> None:
            async for _ in driver:
                pass

        task = asyncio.create_task(consumer())
        await entered.wait()
        for reason in ("first reason", "second reason"):
            try:
                await driver.aclose(reason)
                box.setdefault("calls", []).append("returned")
            except BaseException as exc:  # noqa: BLE001
                box.setdefault("calls", []).append(type(exc).__name__)
        release.set()
        try:
            await asyncio.wait_for(task, 5)
        except BaseException:  # noqa: BLE001
            pass
        records = driver.trace.records
        box["terminal"] = records[-1]["kind"]
        box["reason"] = records[-1].get("reason")

    asyncio.run(go())
    emit(
        "double aclose reason",
        calls=box.get("calls"),
        terminal=box.get("terminal"),
        recorded_reason=box.get("reason"),
    )


def probe_cancel_after_terminal() -> None:
    """X2-006, unchanged by decision."""
    box: dict = {}

    class Late:
        rt = None

        def on_record(self, record) -> None:
            if record["kind"] == "run_completed":
                box["ret"] = self.rt.cancel("too late")

    late = Late()
    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(late,))
    late.rt = rt
    first = rt.run(State.empty("x")).status
    emit(
        "X2-006 cancel after terminal",
        returned=box.get("ret"),
        first=first,
        pending=rt._pending_cancel_reason,
        second=rt.run(State.empty("y")).status,
    )


def probe_fa002() -> None:
    """X2-007 / FA-002, unchanged by decision."""
    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode})
    queued = rt.cancel("queued before the first run")
    try:
        rt.run(State.empty("x"), run_id="x" * 201)
        raised = "none"
    except BaseException as exc:  # noqa: BLE001
        raised = type(exc).__name__
    requeue = rt.cancel("re-queued")
    emit(
        "X2-007 FA-002",
        queued=queued,
        raised=raised,
        pending_after=rt._pending_cancel_reason,
        requeue=requeue,
        next_run=rt.run(State.empty("ok")).status,
    )


if __name__ == "__main__":
    probe_never_advanced_stream()
    probe_never_advanced_astream()
    probe_reader_breaks_after_aclose()
    probe_aclose_then_reader_cancelled()
    probe_double_aclose_reason()
    probe_cancel_after_terminal()
    probe_fa002()
