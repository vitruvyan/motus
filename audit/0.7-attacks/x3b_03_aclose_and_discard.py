"""x3b_03 — the residue of the other two fixes.

A. `AsyncStreamDriver.aclose` now early-returns when the generator is already
   running, deliberately WITHOUT setting `_closed`. That hands responsibility
   for reaching `run_cancelled` to the consumer. This measures what happens
   when the consumer does not accept it: stops after one record, is cancelled,
   or never resumes.

B. `_invoke_sync`'s awaitable guard moved inside the node-failure boundary, so
   a `close()` that itself raises is now a recorded attempt rather than an
   escape. A/B against `51e2439` (pre-fix) proves the change and shows what
   the trace records in each case.

C. For the record: `_drive` does not propagate close to the machine it wraps.
   Compared across v0.6.1 / 51e2439 / HEAD.

Run: python .attack/x3b_03_aclose_and_discard.py
"""

from __future__ import annotations

import asyncio
import gc
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from vitruvyan_motus import Fact, GraphSpec, Runtime, State  # noqa: E402

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "x3b-aclose", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"},
              {"name": "c", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"},
                    "b": {"kind": "next", "to": "c"},
                    "c": {"kind": "terminal"}},
})


def s(name):
    return lambda st: st.with_fact(Fact(name, "d", "x3b", NOW))


def gate(entered, release, name):
    async def node(st):
        entered.set()
        await release.wait()
        return st.with_fact(Fact(name, "d", "x3b", NOW))
    return node


def kinds(rt) -> list[str]:
    return [r["kind"] for r in rt._trace.to_dict()["records"]]


# =========================================================== A. aclose ======
async def a1_consumer_completes_the_cancellation() -> None:
    """The shape the fix is for, and the shape the new test asserts."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = Runtime(SPEC, {"a": gate(entered, release, "a"), "b": s("b"), "c": s("c")})
    driver = rt.astream(State.empty("shutdown"))

    async def consume():
        return [record["kind"] async for record in driver]

    task = asyncio.create_task(consume())
    await entered.wait()
    await driver.aclose("graceful shutdown")
    release.set()
    seen = await task
    print(f"  A1 consumer drives it out : terminal={seen[-1]} "
          f"_running={rt._running} _closed={driver._closed}")


async def a2_consumer_stops_after_one_record() -> None:
    """A consumer that is NOT a loop. aclose() early-returns, the consumer
    takes its one record and leaves, and nothing ever emits the terminal."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = Runtime(SPEC, {"a": gate(entered, release, "a"), "b": s("b"), "c": s("c")})
    driver = rt.astream(State.empty("one-shot"))
    await driver.__anext__()                      # run_started
    await driver.__anext__()                      # attempt_started
    # THIS __anext__ is the one that enters the node.
    task = asyncio.create_task(driver.__anext__())
    await entered.wait()
    await driver.aclose("supervisor stop")        # ag_running -> early return
    release.set()
    await task                                    # the consumer takes ONE record
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    print(f"  A2 consumer takes one     : records={kinds(rt)} "
          f"_running={rt._running} _closed={driver._closed}")
    try:
        await asyncio.wait_for(rt.arun(State.empty("later")), 3)
        print("     a later arun(): OK")
    except RuntimeError as exc:
        print(f"     a later arun(): REFUSED — {exc}")
    except asyncio.TimeoutError:
        print("     a later arun(): HUNG")


async def a3_consumer_task_cancelled_after_aclose() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    rt = Runtime(SPEC, {"a": gate(entered, release, "a"), "b": s("b"), "c": s("c")})
    driver = rt.astream(State.empty("cancelled-consumer"))

    async def consume():
        return [record["kind"] async for record in driver]

    task = asyncio.create_task(consume())
    await entered.wait()
    await driver.aclose("supervisor stop")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    print(f"  A3 consumer cancelled     : records={kinds(rt)} "
          f"_running={rt._running} _closed={driver._closed}")
    release.set()   # so the reuse probe below cannot block on the gate
    try:
        await asyncio.wait_for(rt.arun(State.empty("later")), 3)
        print("     a later arun(): OK")
    except RuntimeError as exc:
        print(f"     a later arun(): REFUSED — {exc}")
    except asyncio.TimeoutError:
        print("     a later arun(): HUNG")


async def a4_aclose_reports_success_while_leaving_the_driver_open() -> None:
    """`aclose()` awaited to completion, yet `_closed` is False and the run is
    still live — the caller has no signal that it must wait for someone else."""
    entered, release = asyncio.Event(), asyncio.Event()
    rt = Runtime(SPEC, {"a": gate(entered, release, "a"), "b": s("b"), "c": s("c")})
    driver = rt.astream(State.empty("signal"))

    async def consume():
        return [record["kind"] async for record in driver]

    task = asyncio.create_task(consume())
    await entered.wait()
    result = await driver.aclose("supervisor stop")
    print(f"  A4 aclose() returned      : {result!r}  _closed={driver._closed} "
          f"_running={rt._running} (run not yet terminal: {kinds(rt)})")
    release.set()
    await task


async def a5_double_aclose_from_two_supervisors() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    rt = Runtime(SPEC, {"a": gate(entered, release, "a"), "b": s("b"), "c": s("c")})
    driver = rt.astream(State.empty("double"))

    async def consume():
        return [record["kind"] async for record in driver]

    task = asyncio.create_task(consume())
    await entered.wait()
    await asyncio.gather(driver.aclose("stop one"), driver.aclose("stop two"))
    release.set()
    seen = await task
    print(f"  A5 two concurrent acloses : terminal={seen[-1]} "
          f"_running={rt._running}")


# ================================================ B. _discard_awaitable =====
PROBE = r'''
import sys, json
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timezone
from vitruvyan_motus import Fact, GraphSpec, Runtime, State
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
SPEC = GraphSpec.from_dict({"schema_version":"1.0.0","name":"p","version":"1.0.0",
 "entry":"a","nodes":[{"name":"a","effect_class":"pure"},{"name":"b","effect_class":"pure"}],
 "transitions":{"a":{"kind":"next","to":"b"},"b":{"kind":"terminal"}}})
out = {}

def probe(label, awaitable_factory):
    rt = Runtime(SPEC, {"a": lambda s: awaitable_factory(), "b": lambda s: s.with_fact(Fact("b","d","p",NOW))})
    escaped = None
    try:
        rt.run(State.empty(label))
    except BaseException as exc:
        escaped = type(exc).__name__
    doc = rt._trace.to_dict()
    transition = next((r for r in doc["records"] if r["kind"] == "transition"), None)
    out[label] = {
        "escaped_as": escaped,
        "records": [r["kind"] for r in doc["records"]],
        "recorded_error": (transition or {}).get("error"),
        "attempt_left_unclosed": transition is None,
    }

class CloseRaisesException:
    def __await__(self):
        yield
    def close(self):
        raise RuntimeError("cleanup exploded")

class CloseRaisesBaseException:
    def __await__(self):
        yield
    def close(self):
        raise KeyboardInterrupt("cleanup interrupted")

class CloseIsFine:
    closed = False
    def __await__(self):
        yield
    def close(self):
        type(self).closed = True

probe("close_raises_Exception", CloseRaisesException)
probe("close_raises_BaseException", CloseRaisesBaseException)
probe("close_is_fine", CloseIsFine)
out["close_is_fine"]["close_was_called"] = CloseIsFine.closed
print(json.dumps(out, sort_keys=True))
'''


def discard_ab() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="x3b-"))
    script = tmp / "probe.py"
    script.write_text(PROBE)
    trees = {}
    for rev in ("51e2439", "HEAD"):
        if rev == "HEAD":
            src = REPO / "src"
        else:
            dest = tmp / rev
            dest.mkdir()
            subprocess.run(f"git -C {REPO} archive {rev} src | tar -x -C {dest}",
                           shell=True, check=True)
            src = dest / "src"
        proc = subprocess.run([sys.executable, str(script), str(src)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  {rev}: probe failed\n{proc.stderr[-1500:]}")
            return
        trees[rev] = json.loads(proc.stdout)
    for case in sorted(trees["HEAD"]):
        before, after = trees["51e2439"][case], trees["HEAD"][case]
        mark = "CHANGED" if before != after else "same   "
        print(f"  {mark}  {case}")
        print(f"           51e2439: {json.dumps(before, sort_keys=True)}")
        print(f"           HEAD   : {json.dumps(after, sort_keys=True)}")


# ================================================ C. _drive close leak ======
LEAK_PROBE = r'''
import sys, gc
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timezone
from vitruvyan_motus import Fact, GraphSpec, Runtime, State
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
SPEC = GraphSpec.from_dict({"schema_version":"1.0.0","name":"p","version":"1.0.0",
 "entry":"a","nodes":[{"name":"a","effect_class":"pure"},{"name":"b","effect_class":"pure"}],
 "transitions":{"a":{"kind":"next","to":"b"},"b":{"kind":"terminal"}}})
S = {k: (lambda st, _k=k: st.with_fact(Fact(_k,"d","p",NOW))) for k in ("a","b")}
rt = Runtime(SPEC, S); d = rt.stream(State.empty("one")); next(d)
d._iterator.close(); gc.collect()
print("running=%s closed=%s records=%s" % (
    rt._running, d._closed, [r["kind"] for r in rt._trace.to_dict()["records"]]))
'''


def drive_close_leak() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="x3b-leak-"))
    script = tmp / "leak.py"
    script.write_text(LEAK_PROBE)
    for rev in ("v0.6.1", "51e2439", "HEAD"):
        if rev == "HEAD":
            src = REPO / "src"
        else:
            dest = tmp / rev.replace(".", "_")
            dest.mkdir()
            subprocess.run(f"git -C {REPO} archive {rev} src | tar -x -C {dest}",
                           shell=True, check=True)
            src = dest / "src"
        proc = subprocess.run([sys.executable, str(script), str(src)],
                              capture_output=True, text=True)
        print(f"  {rev:<9} {proc.stdout.strip() or proc.stderr.strip()[-200:]}")


async def section_a() -> None:
    await a1_consumer_completes_the_cancellation()
    await a2_consumer_stops_after_one_record()
    await a3_consumer_task_cancelled_after_aclose()
    await a4_aclose_reports_success_while_leaving_the_driver_open()
    await a5_double_aclose_from_two_supervisors()


if __name__ == "__main__":
    print("=== A. AsyncStreamDriver.aclose early-return ===")
    asyncio.run(section_a())
    print("\n=== B. _discard_awaitable inside the node-failure boundary ===")
    discard_ab()
    print("\n=== C. closing _drive does not close the machine it wraps ===")
    drive_close_leak()
