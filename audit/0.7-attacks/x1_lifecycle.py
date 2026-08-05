"""INVENTED ATTACK -- driver finalization asymmetry.

`_drive` is a synchronous generator: when the last reference to a StreamDriver
goes away, CPython closes it immediately, the `machine` local dies with the
frame, and `_managed_execute`'s finally releases `_running` and closes the
observation hub.

`_adrive` is an ASYNC generator. Its finalization goes through the event
loop's async-generator hooks, not refcounting. If that defers or skips the
machine's finally, an abandoned `astream()` leaves the Runtime permanently
"running" where the synchronous twin does not -- an observable difference
between two surfaces that are documented as twins (observers.py:295-302:
"Same contract, same cancellation semantics").
"""

from __future__ import annotations

import asyncio
import gc
import sys

from x1_common import PINNED, Report

from vitruvyan_motus import GraphSpec, InMemoryTraceSink, Runtime, State

LINEAR = {
    "schema_version": "1.0.0",
    "name": "lifecycle",
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


def build(**kw):
    return Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": lambda s: s, "b": lambda s: s, "c": lambda s: s},
        **kw, **PINNED,
    )


async def main() -> int:
    report = Report("x1_lifecycle -- abandoned driver finalization")

    # ---- 1. abandoned StreamDriver ---------------------------------------
    rt = build()
    driver = rt.stream(State.empty("abandon-sync"), run_id="pinned")
    next(driver)
    del driver
    running_no_gc = rt._running
    gc.collect()
    running_after_gc = rt._running
    later = None
    try:
        later = rt.run(State.empty("later"), run_id="later").status
    except BaseException as exc:  # noqa: BLE001
        later = f"{type(exc).__name__}: {exc}"
    report.record(
        "abandoned stream(): _running released without an explicit gc",
        running_no_gc is False, f"_running={running_no_gc}",
    )
    report.record(
        "abandoned stream(): a later run() succeeds",
        later == "completed", f"later={later}",
    )

    # ---- 2. abandoned AsyncStreamDriver -----------------------------------
    rta = build()
    adriver = rta.astream(State.empty("abandon-async"), run_id="pinned")
    await adriver.__anext__()
    del adriver
    arunning_no_gc = rta._running
    gc.collect()
    arunning_after_gc = rta._running
    # The release is NOT permanent: asyncio finalizes an abandoned async
    # generator through its finalizer hook, which needs the loop to run the
    # scheduled callback and then the aclose() task -- two ticks. Measure it
    # instead of asserting either extreme.
    ticks = None
    for i in range(1, 51):
        await asyncio.sleep(0)
        if not rta._running:
            ticks = i
            break
    alater = None
    try:
        alater = (await rta.arun(State.empty("later"), run_id="later")).status
    except BaseException as exc:  # noqa: BLE001
        alater = f"{type(exc).__name__}: {exc}"
    report.record(
        "abandoned astream(): _running released as promptly as stream() "
        "(i.e. before the caller can touch the Runtime again)",
        arunning_no_gc is False,
        f"_running(no gc)={arunning_no_gc} after gc={arunning_after_gc}; "
        f"released only after {ticks} event-loop tick(s) -- stream() releases in 0",
    )
    report.record(
        "abandoned astream(): the immediately following arun() succeeds",
        alater == "completed", f"later={alater} (after {ticks} ticks it would succeed)",
    )

    # ---- 3. never-iterated drivers ---------------------------------------
    # NB: this leak is PRE-EXISTING and symmetric -- verified identical on
    # bb886e6 (pre-inversion). Recorded here for completeness, not as a
    # regression of the inversion.
    rt2 = build()
    d2 = rt2.stream(State.empty("never"), run_id="pinned")
    del d2
    gc.collect()
    report.record(
        "a never-iterated stream() still releases the Runtime "
        "[PRE-EXISTING: identical on bb886e6]",
        rt2._running is False, f"_running={rt2._running}",
    )

    rt3 = build()
    d3 = rt3.astream(State.empty("never"), run_id="pinned")
    del d3
    gc.collect()
    for _ in range(20):
        await asyncio.sleep(0)
    report.record(
        "a never-iterated astream() still releases the Runtime "
        "[same pre-existing shape]",
        rt3._running is False, f"_running={rt3._running}",
    )

    # ---- 4. observation hub / timer thread --------------------------------
    sink = InMemoryTraceSink()
    rt4 = build(durability_profile="buffered", sink=sink,
                chunk_records=64, flush_interval_ms=5000)
    d4 = rt4.astream(State.empty("hub"), run_id="pinned")
    await d4.__anext__()
    hub = rt4._hub
    del d4
    gc.collect()
    await asyncio.sleep(0)
    immediate = (hub._closed, hub._timer)
    for _ in range(50):
        await asyncio.sleep(0)
        if hub._closed:
            break
    report.record(
        "abandoned astream() closes the observation hub immediately "
        "(no window with an orphan flush timer)",
        immediate[0] is True and immediate[1] is None,
        f"one tick after abandonment: closed={immediate[0]} timer={immediate[1]}; "
        f"eventually closed={hub._closed} timer={hub._timer}",
    )

    sink2 = InMemoryTraceSink()
    rt5 = build(durability_profile="buffered", sink=sink2,
                chunk_records=64, flush_interval_ms=5000)
    d5 = rt5.stream(State.empty("hub"), run_id="pinned")
    next(d5)
    hub5 = rt5._hub
    del d5
    gc.collect()
    report.record(
        "abandoned stream() closes the observation hub",
        hub5._closed is True and hub5._timer is None,
        f"closed={hub5._closed} timer={hub5._timer}",
    )

    # ---- 5. a live driver still blocks an overlapping run -----------------
    rt6 = build()
    live = rt6.astream(State.empty("live"), run_id="pinned")
    await live.__anext__()
    blocked = None
    try:
        await rt6.arun(State.empty("overlap"))
    except BaseException as exc:  # noqa: BLE001
        blocked = f"{type(exc).__name__}"
    report.record(
        "a live astream() blocks an overlapping run",
        blocked == "RuntimeError", f"{blocked}",
    )
    await live.aclose()

    rt7 = build()
    live7 = rt7.stream(State.empty("live"), run_id="pinned")
    next(live7)
    blocked7 = None
    try:
        rt7.run(State.empty("overlap"))
    except BaseException as exc:  # noqa: BLE001
        blocked7 = f"{type(exc).__name__}"
    report.record(
        "a live stream() blocks an overlapping run",
        blocked7 == "RuntimeError", f"{blocked7}",
    )
    live7.close()

    # ---- 6. terminal recorded by an abandoned driver ----------------------
    rt8 = build()
    d8 = rt8.stream(State.empty("terminal"), run_id="pinned")
    next(d8)
    del d8
    gc.collect()
    rt9 = build()
    d9 = rt9.astream(State.empty("terminal"), run_id="pinned")
    await d9.__anext__()
    trace9 = rt9.trace
    del d9
    gc.collect()
    await asyncio.sleep(0)
    report.record(
        "abandoned stream() and astream() record the same (absence of a) terminal",
        [r["kind"] for r in rt8.trace.records] == [r["kind"] for r in trace9.records],
        f"sync={[r['kind'] for r in rt8.trace.records]} "
        f"async={[r['kind'] for r in trace9.records]}",
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
