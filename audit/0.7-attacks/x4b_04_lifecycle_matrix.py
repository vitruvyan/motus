"""x4b-04 — a wide lifecycle matrix, printed as data so HEAD and main can be
diffed line by line.

For each scenario: what escaped, what the trace looks like, whether the Runtime
was left claiming the run, and whether it can execute again.  Nothing here
decides what is right; the diff against main does.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import traceback
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import GraphSpec, NodeFailed, Runtime, State  # noqa: E402
from x4b_common import LINEAR, kinds, passthrough  # noqa: E402

ROWS: list[tuple[str, str]] = []


def row(name: str, value: Any) -> None:
    ROWS.append((name, str(value)))
    print(f"{name:<52} {value}", flush=True)


def probe(name: str, fn) -> None:
    try:
        fn(name)
    except BaseException as exc:  # noqa: BLE001 - the matrix records escapes
        row(f"{name}/UNCAUGHT", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


def reusable(runtime: Runtime) -> str:
    try:
        return runtime.run(State.empty("after")).status
    except BaseException as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- sync shapes


def s_plain_run(name: str) -> None:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    result = rt.run(State.empty("plain"))
    row(name, f"status={result.status} running={rt._running} reuse={reusable(rt)}")


def s_node_baseexception(name: str) -> None:
    def boom(state: State) -> State:
        raise KeyboardInterrupt("from a node")

    rt = Runtime(LINEAR, {"a": boom, "b": passthrough, "c": passthrough})
    escaped = "none"
    try:
        rt.run(State.empty("base"))
    except BaseException as exc:  # noqa: BLE001
        escaped = type(exc).__name__
    row(name, f"escaped={escaped} running={rt._running} "
              f"trace={kinds(rt.trace)} reuse={reusable(rt)}")


def s_node_failure(name: str) -> None:
    def boom(state: State) -> State:
        raise ValueError("nope")

    rt = Runtime(LINEAR, {"a": boom, "b": passthrough, "c": passthrough})
    escaped = "none"
    try:
        rt.run(State.empty("fail"))
    except NodeFailed as exc:
        escaped = f"NodeFailed(trace={kinds(exc.trace)})"
    row(name, f"escaped={escaped} running={rt._running} reuse={reusable(rt)}")


def s_stream_dropped_never_advanced(name: str) -> None:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("dropped"))
    claimed = rt._running
    del d
    gc.collect()
    row(name, f"claimed={claimed} after_drop_running={rt._running} "
              f"reuse={reusable(rt)}")


def s_stream_dropped_midrun(name: str) -> None:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("mid"))
    next(d)
    tr = d.trace
    del d
    gc.collect()
    row(name, f"after_drop_running={rt._running} trace={kinds(tr)} "
              f"reuse={reusable(rt)}")


def s_stream_kept_in_traceback(name: str) -> None:
    """A driver captured by a live traceback frame is not collectable."""
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})

    def make() -> None:
        d = rt.stream(State.empty("tb"))     # noqa: F841 - held by the frame
        raise RuntimeError("carry the frame")

    held = None
    try:
        make()
    except RuntimeError as exc:
        held = exc
    gc.collect()
    row(name, f"running_with_tb={rt._running} reuse={reusable(rt)}")
    del held
    gc.collect()
    row(f"{name}/after-tb-dropped", f"running={rt._running} reuse={reusable(rt)}")


def s_stream_consumer_stops_after_terminal(name: str) -> None:
    """Consumer reads every record including the terminal but never asks the
    one extra time that lets the generator return."""
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("stop"))
    seen = []
    while True:
        rec = next(d)
        seen.append(rec["kind"])
        if rec["kind"].startswith("run_") and rec["kind"] != "run_started":
            break
    row(name, f"seen={seen} running={rt._running} closed={d._closed} "
              f"reuse={reusable(rt)}")


def s_listener_closes_own_driver(name: str) -> None:
    box: dict[str, Any] = {}

    def closer(record: dict[str, Any]) -> None:
        if record["kind"] == "run_started" and "d" in box:
            box["d"].close("listener stopped it")

    rt = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(closer,),
    )
    d = rt.stream(State.empty("listener"))
    box["d"] = d
    seen = [r["kind"] for r in d]
    row(name, f"seen={seen} trace={kinds(d.trace)} running={rt._running}")


def s_listener_closes_then_consumer_abandons(name: str) -> None:
    """close() returned normally from inside the generator; the consumer then
    stops.  Who writes the terminal?"""
    box: dict[str, Any] = {}

    def closer(record: dict[str, Any]) -> None:
        if record["kind"] == "run_started" and "d" in box:
            box["d"].close("listener stopped it")

    rt = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(closer,),
    )
    d = rt.stream(State.empty("abandon"))
    box["d"] = d
    next(d)                                # listener closes from inside
    tr = d.trace
    closed = d._closed
    box.clear()
    del d
    gc.collect()
    row(name, f"close_latched={closed} trace={kinds(tr)} running={rt._running} "
              f"reuse={reusable(rt)}")


def s_node_closes_own_driver(name: str) -> None:
    box: dict[str, Any] = {}

    def canceller(state: State) -> State:
        if "d" in box:
            box["d"].close("node stopped it")
        return state

    rt = Runtime(LINEAR, {"a": canceller, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("nodeclose"))
    box["d"] = d
    escaped = "none"
    try:
        seen = [r["kind"] for r in d]
    except BaseException as exc:  # noqa: BLE001
        seen = "<raised>"
        escaped = f"{type(exc).__name__}"
    row(name, f"seen={seen} escaped={escaped} trace={kinds(d.trace)} "
              f"running={rt._running}")


# --------------------------------------------------------------- async shapes


async def _a_dropped_never_advanced() -> str:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.astream(State.empty("adrop"))
    claimed = rt._running
    del d
    gc.collect()
    await asyncio.sleep(0)
    reuse = (await rt.arun(State.empty("after"))).status if not rt._running else "WEDGED"
    return f"claimed={claimed} after_drop_running={rt._running} reuse={reuse}"


async def _a_dropped_midrun() -> str:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.astream(State.empty("amid"))
    await d.__anext__()
    del d
    gc.collect()
    states = [rt._running]
    for _ in range(6):
        await asyncio.sleep(0)
        states.append(rt._running)
    reuse = "WEDGED" if rt._running else (await rt.arun(State.empty("after"))).status
    return f"running_over_time={states} reuse={reuse}"


async def _a_task_cancelled_midrun() -> str:
    async def slow(state: State) -> State:
        await asyncio.sleep(5)
        return state

    rt = Runtime(LINEAR, {"a": slow, "b": passthrough, "c": passthrough})
    task = asyncio.ensure_future(rt.arun(State.empty("acancel")))
    await asyncio.sleep(0.02)
    task.cancel()
    escaped = "none"
    try:
        await task
    except BaseException as exc:  # noqa: BLE001
        escaped = type(exc).__name__
    states = [rt._running]
    for _ in range(6):
        await asyncio.sleep(0)
        states.append(rt._running)
    reuse = "WEDGED" if rt._running else (await rt.arun(State.empty("after"))).status
    return (f"escaped={escaped} running_over_time={states} "
            f"trace={kinds(rt.trace)} reuse={reuse}")


def a_shapes(name: str) -> None:
    row(f"{name}/dropped-never-advanced", asyncio.run(_a_dropped_never_advanced()))
    row(f"{name}/dropped-midrun", asyncio.run(_a_dropped_midrun()))
    row(f"{name}/task-cancelled-midrun", asyncio.run(_a_task_cancelled_midrun()))


def main() -> int:
    print(f"# build: {Runtime.__module__} from {GraphSpec.__module__}")
    import vitruvyan_motus
    print(f"# source: {vitruvyan_motus.__file__}\n")
    probe("sync/plain-run", s_plain_run)
    probe("sync/node-baseexception", s_node_baseexception)
    probe("sync/node-failure", s_node_failure)
    probe("sync/stream-dropped-never-advanced", s_stream_dropped_never_advanced)
    probe("sync/stream-dropped-midrun", s_stream_dropped_midrun)
    probe("sync/stream-in-traceback", s_stream_kept_in_traceback)
    probe("sync/consumer-stops-after-terminal", s_stream_consumer_stops_after_terminal)
    probe("sync/listener-closes-own-driver", s_listener_closes_own_driver)
    probe("sync/listener-closes-consumer-abandons",
          s_listener_closes_then_consumer_abandons)
    probe("sync/node-closes-own-driver", s_node_closes_own_driver)
    probe("async", a_shapes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
