"""x5-06 -- `finish` under asynchronous teardown.

An async generator is finalised by the event loop (`shutdown_asyncgens`),
possibly after the consumer is gone and possibly on another task.  Does the
session still get exactly one `finish`, with the right `complete`, and does
nothing escape?
"""

from __future__ import annotations

import asyncio
import gc
import threading
import traceback

from vitruvyan_motus import Runtime, State

from x5_common import BUFFERED, LINEAR, OUT, SYNC, ProtocolJsonlSink, Report, registry

D = OUT / "x5_06"


def report(r: Report, name: str, sink: ProtocolJsonlSink, *, expect: list[bool],
           errors: list[str] | None = None) -> None:
    got = [s.finish_calls for s in sink.sessions]
    agree = all(
        (s.finish_calls[0] == s.file_has_terminal) if s.finish_calls else True
        for s in sink.sessions
    )
    ok = got == [[e] for e in expect] and agree and not sink.violations and not errors
    r.record(
        name, ok,
        f"finish={got} file_terminal={[s.file_has_terminal for s in sink.sessions]} "
        f"expected={[[e] for e in expect]} violations={sink.violations[:2]}"
        + (f" errors={errors[:1]}" if errors else ""),
    )


def main() -> int:
    r = Report("x5-06 finish under asynchronous teardown")

    # ---- 1. abandoned async driver, finalised by shutdown_asyncgens ------- #
    s = ProtocolJsonlSink(D, prefix="agen-abandoned")

    async def abandon():
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
        drv = rt.astream(State.empty("agen"))
        await drv.__anext__()
        await drv.__anext__()
        del drv
        gc.collect()

    asyncio.run(abandon())
    gc.collect()
    report(r, "abandoned async driver (loop finalises the agen)", s,
           expect=[False])

    # ---- 2. task cancelled mid-run --------------------------------------- #
    s = ProtocolJsonlSink(D, prefix="agen-cancelled")
    errors: list[str] = []

    async def cancelled():
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)

        async def consume():
            async for _ in rt.astream(State.empty("cancelme")):
                await asyncio.sleep(0.01)

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.015)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(cancelled())
    except BaseException:
        errors.append(traceback.format_exc(limit=3))
    gc.collect()
    report(r, "task cancelled mid-run", s, expect=[False], errors=errors)

    # ---- 3. explicit aclose then context exit ----------------------------- #
    s = ProtocolJsonlSink(D, prefix="agen-aclose")

    async def double_close():
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
        drv = rt.astream(State.empty("aclose"))
        await drv.__anext__()
        await drv.aclose("first")
        await drv.aclose("second")
        async with drv:
            pass

    asyncio.run(double_close())
    report(r, "aclose twice + async context exit: still one finish", s,
           expect=[True])

    # ---- 4. arun in a thread-run loop, buffered with a live timer --------- #
    s = ProtocolJsonlSink(D, prefix="arun-buffered")
    errors = []

    async def many():
        for i in range(20):
            rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED,
                         sink=s, chunk_records=2, flush_interval_ms=1)
            await rt.arun(State.empty(f"a{i}"))

    try:
        asyncio.run(many())
    except BaseException:
        errors.append(traceback.format_exc(limit=3))
    report(r, "20 buffered arun()s with a 1ms timer", s,
           expect=[True] * 20, errors=errors)

    # ---- 5. astream never advanced, inside a loop ------------------------- #
    s = ProtocolJsonlSink(D, prefix="agen-never")

    async def never():
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
        drv = rt.astream(State.empty("never"))
        del drv
        gc.collect()

    asyncio.run(never())
    gc.collect()
    report(r, "astream never advanced, inside a live loop", s, expect=[False])

    # ---- 6. two loops in two threads, one shared sink --------------------- #
    s = ProtocolJsonlSink(D, prefix="two-loops")
    errors = []

    def loop_worker(n):
        async def go():
            for i in range(10):
                rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED,
                             sink=s, chunk_records=3, flush_interval_ms=1)
                await rt.arun(State.empty(f"L{n}-{i}"))
        try:
            asyncio.run(go())
        except BaseException:
            errors.append(traceback.format_exc(limit=3))

    ts = [threading.Thread(target=loop_worker, args=(n,)) for n in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    report(r, "3 event loops in 3 threads, 10 runs each, one sink", s,
           expect=[True] * 30, errors=errors)

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
