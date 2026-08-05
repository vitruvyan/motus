"""x3_02 — how long an abandoned AsyncStreamDriver holds the Runtime.

x3_01 showed `async for ... break` leaves the Runtime rejecting the next run
while the synchronous `for ... break` does not. This quantifies it: how many
event-loop turns are needed before the run is released, whether it is ever
released without an explicit `aclose()`, and what is still open while it is
not (the buffered sink's flush timer, `_running`, the active attempt).
"""

from __future__ import annotations

import asyncio
import gc
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus import (  # noqa: E402
    DurabilityProfile,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    Runtime,
    State,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "x3-release",
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
)


def anode(name):
    async def node(state: State) -> State:
        await asyncio.sleep(0)
        return state.with_fact(Fact(name, "done", "x3", NOW))

    return node


NODES = {k: anode(k) for k in ("a", "b", "c")}


async def turns_until_released() -> None:
    runtime = Runtime(SPEC, NODES)
    async for _ in runtime.astream(State.empty("abandoned")):
        break
    for turn in range(0, 12):
        if not runtime._running:
            print(f"  released after {turn} loop turn(s) (no gc.collect)")
            return
        await asyncio.sleep(0)
    print("  STILL HELD after 12 loop turns (no gc.collect)")


async def turns_until_released_with_gc() -> None:
    runtime = Runtime(SPEC, NODES)
    async for _ in runtime.astream(State.empty("abandoned")):
        break
    gc.collect()
    for turn in range(0, 12):
        if not runtime._running:
            print(f"  released after gc.collect + {turn} loop turn(s)")
            return
        await asyncio.sleep(0)
    print("  STILL HELD after gc.collect + 12 loop turns")


async def sink_timer_left_running() -> None:
    """Buffered profile: `_hub.close()` cancels the flush timer. If the run
    is never finalised, the timer thread outlives the abandoned stream."""
    sink = InMemoryTraceSink()
    runtime = Runtime(
        SPEC,
        NODES,
        sink=sink,
        durability_profile=DurabilityProfile.BUFFERED,
        flush_interval_ms=50_000,
        chunk_records=1000,
    )
    before = {t.name for t in threading.enumerate()}
    async for _ in runtime.astream(State.empty("abandoned")):
        break
    timers = [t for t in threading.enumerate() if t.name not in before]
    print(f"  threads created by the abandoned buffered astream: {len(timers)} {[t.name for t in timers]}")
    print(f"  runtime._running immediately after break: {runtime._running}")
    print(f"  hub closed: {runtime._hub._closed if runtime._hub else 'n/a'}")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    print(f"  after two loop turns: _running={runtime._running} "
          f"hub_closed={runtime._hub._closed if runtime._hub else 'n/a'} "
          f"live_timers={[t.name for t in threading.enumerate() if t.name not in before]}")


def sync_control() -> None:
    runtime = Runtime(SPEC, {k: (lambda s, _n=k: s.with_fact(Fact(_n, "d", "x3", NOW))) for k in ("a", "b", "c")})
    for _ in runtime.stream(State.empty("abandoned")):
        break
    print(f"  sync control: _running immediately after break = {runtime._running}")


async def released_only_at_loop_shutdown() -> None:
    """The nastiest shape: abandon the stream, then do real awaited work.
    Does the Runtime come back before the loop ends?"""
    runtime = Runtime(SPEC, NODES)
    async for _ in runtime.astream(State.empty("abandoned")):
        break
    await asyncio.sleep(0.05)  # a real await, not a bare turn
    print(f"  after a 50ms sleep: _running={runtime._running}")
    try:
        await runtime.arun(State.empty("next"))
        print("  a later arun() succeeded")
    except RuntimeError as exc:
        print(f"  a later arun() FAILED: {exc}")


async def main() -> None:
    print("sync control (StreamDriver):")
    sync_control()
    print("async, no gc:")
    await turns_until_released()
    print("async, with gc.collect:")
    await turns_until_released_with_gc()
    print("async, buffered sink:")
    await sink_timer_left_running()
    print("async, real await after abandonment:")
    await released_only_at_loop_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
