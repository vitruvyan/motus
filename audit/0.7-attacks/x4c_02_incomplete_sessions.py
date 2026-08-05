"""x4c-02 — The full enumeration: how can an opened session end incomplete?

`TraceSink.open_run(header)` is called from `Runtime._start`, i.e. BEFORE the
state machine has produced a single record, and unconditionally for every run
that reaches header construction.  This script walks every way the record
sequence that follows can fail to be a complete trace, and validates the
resulting file with contract/validate.py each time.

Each case reports:  open_calls / records persisted / last kind / validator rc.
"""

from __future__ import annotations

import asyncio
import gc
import json
from pathlib import Path

from vitruvyan_motus import DurabilityProfile, GraphSpec, Runtime, State

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, JsonlFileSink, Report, kinds_of_file,
    node, registry, validate_file,
)

CASES: list[dict] = []


def observe(name: str, sink: JsonlFileSink, note: str = "") -> dict:
    """Record what the sink ended up holding, and what the validator says."""
    if not sink.sessions:
        row = {
            "case": name, "opened": sink.open_calls, "file": None,
            "records": 0, "last": None, "rc": None, "verdict": "no file",
            "note": note,
        }
        CASES.append(row)
        return row
    path = sink.sessions[-1].path
    kinds = kinds_of_file(path)
    code, out = validate_file(path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    row = {
        "case": name, "opened": sink.open_calls, "file": path.name,
        "records": len(kinds) - 1, "last": kinds[-1] if len(kinds) > 1 else "(none)",
        "rc": code, "verdict": out.splitlines()[0][:150] if out else "VALID",
        "note": note,
    }
    CASES.append(row)
    return row


def sink_for(tag: str) -> JsonlFileSink:
    return JsonlFileSink(OUT / "02" / tag, prefix=tag)


# --------------------------------------------------------------------------- #
# A. sync stream, never advanced (the known starting point)                   #
# --------------------------------------------------------------------------- #
def case_a_never_advanced() -> None:
    s = sink_for("a-never-advanced")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    driver = rt.stream()
    del driver
    gc.collect()
    observe("A. stream() never advanced, dropped", s, "runtime released the claim")


def case_a2_never_advanced_kept() -> None:
    """The same, but the driver is still alive — no finaliser has run."""
    s = sink_for("a2-never-advanced-live")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    driver = rt.stream()  # noqa: F841 - deliberately kept alive
    observe("A2. stream() never advanced, still referenced", s, "driver alive")


def case_a3_astream_never_advanced() -> None:
    s = sink_for("a3-astream-never")

    async def go():
        rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
        driver = rt.astream()
        del driver
        gc.collect()

    asyncio.run(go())
    observe("A3. astream() never advanced, dropped", s)


# --------------------------------------------------------------------------- #
# B. stream advanced partway then dropped without close()                     #
# --------------------------------------------------------------------------- #
def case_b_dropped_midrun() -> None:
    s = sink_for("b-dropped-midrun")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    driver = rt.stream()
    for i, _ in enumerate(driver):
        if i >= 3:
            break
    del driver
    gc.collect()
    observe("B. stream() advanced 4 records, dropped (no close)", s)


def case_b2_astream_dropped_midrun() -> None:
    s = sink_for("b2-astream-dropped")

    async def go():
        rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
        driver = rt.astream()
        i = 0
        async for _ in driver:
            i += 1
            if i >= 4:
                break
        del driver
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(go())
    observe("B2. astream() advanced 4 records, dropped (no aclose)", s)


def case_b3_consumer_raises_no_with() -> None:
    """A consumer that raises out of the loop, with no context manager."""
    s = sink_for("b3-consumer-raises")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)

    def consume():
        driver = rt.stream()
        for i, _ in enumerate(driver):
            if i == 2:
                raise KeyError("consumer exploded")

    try:
        consume()
    except KeyError:
        pass
    gc.collect()
    observe("B3. consumer raises mid-iteration, driver not in `with`", s)


# --------------------------------------------------------------------------- #
# C. buffered profile: the buffer is dropped, not flushed                     #
# --------------------------------------------------------------------------- #
def case_c_buffered_dropped() -> None:
    s = sink_for("c-buffered-dropped")
    rt = Runtime(
        LINEAR, registry(), durability_profile=DurabilityProfile.BUFFERED, sink=s,
        chunk_records=64, flush_interval_ms=60_000,
    )
    driver = rt.stream()
    for i, _ in enumerate(driver):
        if i >= 6:
            break
    del driver
    gc.collect()
    observe("C. buffered stream dropped mid-run", s,
            "7 records were emitted; chunk 64 / interval 60s")


def case_c2_buffered_timer_after_teardown() -> None:
    """Does the interval timer ever deliver what the teardown left buffered?"""
    import time

    s = sink_for("c2-buffered-timer")
    rt = Runtime(
        LINEAR, registry(), durability_profile=DurabilityProfile.BUFFERED, sink=s,
        chunk_records=64, flush_interval_ms=150,
    )
    driver = rt.stream()
    for i, _ in enumerate(driver):
        if i >= 6:
            break
    del driver
    gc.collect()
    time.sleep(0.6)  # three flush intervals
    observe("C2. buffered dropped, waited 4x flush_interval_ms", s,
            "does the declared loss window ever close?")


# --------------------------------------------------------------------------- #
# D. BaseException out of a node                                              #
# --------------------------------------------------------------------------- #
def case_d_baseexception_run() -> None:
    s = sink_for("d-baseexc-run")

    def boom(state: State) -> State:
        raise KeyboardInterrupt("hard stop")

    nodes = dict(registry())
    nodes["b"] = boom
    rt = Runtime(LINEAR, nodes, durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    observe("D. node raises BaseException under run()", s,
            "node-protocol 7.3: attempt_started stays unclosed")


def case_d2_baseexception_buffered() -> None:
    s = sink_for("d2-baseexc-buffered")

    def boom(state: State) -> State:
        raise KeyboardInterrupt("hard stop")

    nodes = dict(registry())
    nodes["c"] = boom
    rt = Runtime(
        LINEAR, nodes, durability_profile=DurabilityProfile.BUFFERED, sink=s,
        chunk_records=64, flush_interval_ms=60_000,
    )
    try:
        rt.run()
    except KeyboardInterrupt:
        pass
    observe("D2. node raises BaseException under buffered", s)


# --------------------------------------------------------------------------- #
# E. open_run refusal with a side effect already committed                    #
# --------------------------------------------------------------------------- #
def case_e_open_refusal_never_advanced() -> None:
    """A sink that refuses to open; the run is then never advanced."""

    class RefusingSink:
        def __init__(self) -> None:
            self.open_calls = 0
            self.sessions: list = []

        def open_run(self, header):
            self.open_calls += 1
            raise OSError("disk full at open_run")

    s = RefusingSink()
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    driver = rt.stream()
    del driver
    gc.collect()
    CASES.append({
        "case": "E. open_run refused, run never advanced", "opened": s.open_calls,
        "file": None, "records": 0, "last": None, "rc": None,
        "verdict": "no exception reached any caller",
        "note": "ADR-004: 'refusal is a sink failure' — observed by nobody",
    })


# --------------------------------------------------------------------------- #
# F. control: the cooperative paths that SHOULD be complete                   #
# --------------------------------------------------------------------------- #
def case_f_close_never_advanced() -> None:
    s = sink_for("f-close-never")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    driver = rt.stream()
    driver.close("explicit close before first advance")
    observe("F. stream() never advanced, close() called", s, "CONTROL: should be complete")


def case_f2_with_block() -> None:
    s = sink_for("f2-with-block")
    rt = Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s)
    with rt.stream() as driver:
        for i, _ in enumerate(driver):
            if i >= 3:
                break
    observe("F2. stream() in `with`, break at 4", s, "CONTROL: should be complete")


def case_f3_buffered_with_block() -> None:
    s = sink_for("f3-buffered-with")
    rt = Runtime(
        LINEAR, registry(), durability_profile=DurabilityProfile.BUFFERED, sink=s,
        chunk_records=64, flush_interval_ms=60_000,
    )
    with rt.stream() as driver:
        for i, _ in enumerate(driver):
            if i >= 3:
                break
    observe("F3. buffered stream in `with`, break at 4", s, "CONTROL: should be complete")


def main() -> int:
    for fn in (
        case_a_never_advanced, case_a2_never_advanced_kept,
        case_a3_astream_never_advanced,
        case_b_dropped_midrun, case_b2_astream_dropped_midrun,
        case_b3_consumer_raises_no_with,
        case_c_buffered_dropped, case_c2_buffered_timer_after_teardown,
        case_d_baseexception_run, case_d2_baseexception_buffered,
        case_e_open_refusal_never_advanced,
        case_f_close_never_advanced, case_f2_with_block, case_f3_buffered_with_block,
    ):
        fn()

    print("\n=== x4c-02: sessions opened vs. sequences delivered ===")
    print(f"{'case':<52} {'open':>4} {'recs':>5} {'last kind':<16} {'rc':>3}  verdict")
    for row in CASES:
        print(
            f"{row['case']:<52} {row['opened']:>4} {row['records']:>5} "
            f"{str(row['last']):<16} {str(row['rc']):>3}  {row['verdict']}"
        )
    bad = [r for r in CASES if r["rc"] not in (0, None)] + [
        r for r in CASES if r["rc"] is None and r["opened"]
    ]
    print(f"\n-- {len(CASES)} cases, {len(bad)} produced an invalid or absent artifact")
    (OUT / "02-summary.json").write_text(json.dumps(CASES, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
