"""x5-01 -- is `finish` called EXACTLY ONCE per opened session?

ADR-011 Decision 2: "Called exactly once, when the runtime will send that
session nothing more."  ADR-011 Consequences: "Rows 1-3 of ADR-010's twelve
(a session opened and given no records) remain possible, but are now
KNOWABLE: the sink is told complete=False."

This enumerates every way a session ends and counts the calls.  The unit of
account is `open_run` calls vs `finish` calls: one apiece, or the promise is
false.
"""

from __future__ import annotations

import asyncio
import gc
import threading

from vitruvyan_motus import Runtime, State
from vitruvyan_motus.errors import NodeFailed

from x5_common import (
    BUFFERED, INMEM, LINEAR, OUT, SYNC, ProtocolJsonlSink, Report, registry,
)


def arity(sink: ProtocolJsonlSink) -> str:
    return (
        f"open_run={sink.open_calls} sessions={len(sink.sessions)} "
        f"finish={[s.finish_calls for s in sink.sessions]}"
    )


def check(r: Report, name: str, sink: ProtocolJsonlSink, *, expect_opens: int,
          expect_complete: list[bool]) -> None:
    got = [s.finish_calls for s in sink.sessions]
    ok = (
        sink.open_calls == expect_opens
        and got == [[c] for c in expect_complete]
        and not sink.violations
    )
    detail = arity(sink)
    if sink.violations:
        detail += f" VIOLATIONS={sink.violations}"
    r.record(
        name, ok,
        detail + f"  (expected open_run={expect_opens} finish="
                 f"{[[c] for c in expect_complete]})",
    )


def main() -> int:
    r = Report("x5-01 finish arity: exactly once per opened session")
    d = OUT / "x5_01"

    # ---------------------------------------------------------------- run() #
    for label, profile in (("sync", SYNC), ("buffered", BUFFERED),
                           ("inmem+sink", INMEM)):
        s = ProtocolJsonlSink(d, prefix=f"run-{label}")
        Runtime(LINEAR, registry(), durability_profile=profile, sink=s).run(
            State.empty(f"run-{label}")
        )
        check(r, f"run() completed [{label}]", s, expect_opens=1,
              expect_complete=[True])

    # ------------------------------------------------------- stream drained #
    s = ProtocolJsonlSink(d, prefix="stream-drained")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    for _ in rt.stream(State.empty("drained")):
        pass
    check(r, "stream() fully drained", s, expect_opens=1, expect_complete=[True])

    # ----------------------------------------- stream advanced then dropped #
    s = ProtocolJsonlSink(d, prefix="stream-dropped")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    it = rt.stream(State.empty("dropped"))
    next(it)
    next(it)
    del it
    gc.collect()
    check(r, "stream() advanced then dropped", s, expect_opens=1,
          expect_complete=[False])

    # ------------------------------------- stream NEVER advanced, dropped   #
    s = ProtocolJsonlSink(d, prefix="stream-never")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    it = rt.stream(State.empty("never"))
    del it
    gc.collect()
    check(r, "stream() created, NEVER advanced, dropped", s, expect_opens=1,
          expect_complete=[False])

    # ------------------------------------- stream never advanced, closed    #
    s = ProtocolJsonlSink(d, prefix="stream-never-closed")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    it = rt.stream(State.empty("neverclosed"))
    it.close()
    check(r, "stream() created, never advanced, close()d", s, expect_opens=1,
          expect_complete=[True])

    # -------------------------------------------- stream context-manager    #
    s = ProtocolJsonlSink(d, prefix="stream-ctx")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    with rt.stream(State.empty("ctx")) as drv:
        next(drv)
    check(r, "stream() context exit mid-run", s, expect_opens=1,
          expect_complete=[True])

    # ----------------------------------------------- astream never advanced #
    s = ProtocolJsonlSink(d, prefix="astream-never")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    drv = rt.astream(State.empty("anever"))
    del drv
    gc.collect()
    check(r, "astream() created, NEVER advanced, dropped", s, expect_opens=1,
          expect_complete=[False])

    # ------------------------------------------------ astream fully drained #
    s = ProtocolJsonlSink(d, prefix="astream-drained")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)

    async def drain():
        async for _ in rt.astream(State.empty("adrained")):
            pass

    asyncio.run(drain())
    check(r, "astream() fully drained", s, expect_opens=1,
          expect_complete=[True])

    # --------------------------------------------------------- node failure #
    def boom(state):
        raise ValueError("boom")

    s = ProtocolJsonlSink(d, prefix="nodefail")
    rt = Runtime(LINEAR, {**registry(), "b": boom}, durability_profile=SYNC,
                 sink=s)
    try:
        rt.run(State.empty("nodefail"))
    except NodeFailed:
        pass
    check(r, "node failure (NodeFailed)", s, expect_opens=1,
          expect_complete=[True])

    # ------------------------------------------------- node BaseException   #
    def hard(state):
        raise KeyboardInterrupt("hard stop")

    s = ProtocolJsonlSink(d, prefix="baseexc")
    rt = Runtime(LINEAR, {**registry(), "b": hard}, durability_profile=SYNC,
                 sink=s)
    try:
        rt.run(State.empty("baseexc"))
    except BaseException:
        pass
    check(r, "node raised BaseException", s, expect_opens=1,
          expect_complete=[False])

    # ------------------------------------------------------- cancellation   #
    s = ProtocolJsonlSink(d, prefix="cancel")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    drv = rt.stream(State.empty("cancel"))
    next(drv)
    drv.close("attacked")
    check(r, "stream close() -> run_cancelled", s, expect_opens=1,
          expect_complete=[True])

    # ------------------------------------------- two runs on one Runtime    #
    s = ProtocolJsonlSink(d, prefix="tworuns")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    rt.run(State.empty("first"))
    rt.run(State.empty("second"))
    check(r, "two sequential runs on one Runtime", s, expect_opens=2,
          expect_complete=[True, True])

    # ------------- abandoned never-advanced run FOLLOWED BY a real run ----- #
    s = ProtocolJsonlSink(d, prefix="orphan-then-run")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    it = rt.stream(State.empty("orphan"))
    del it
    gc.collect()
    rt.run(State.empty("after-orphan"))
    check(r, "never-advanced stream, then a real run", s, expect_opens=2,
          expect_complete=[False, True])

    # ----------------------------------------- _start fails AFTER binding?  #
    # run_id validation happens BEFORE bind, so this must open nothing.
    s = ProtocolJsonlSink(d, prefix="badrunid")
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    try:
        rt.run(State.empty("x"), run_id="")
    except ValueError:
        pass
    check(r, "_start rejects run_id before binding", s, expect_opens=0,
          expect_complete=[])

    # --------------------------------------- open_run itself raises         #
    s = ProtocolJsonlSink(d, prefix="openfail",
                          fail_open=RuntimeError("cannot open"))
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    try:
        rt.run(State.empty("openfail"))
    except BaseException:
        pass
    r.record(
        "open_run raised: no session, so no finish",
        s.open_calls == 1 and not s.sessions,
        arity(s),
    )

    # ------------------------------ a sink WITHOUT finish still works       #
    s = ProtocolJsonlSink(d, prefix="nofinish", no_finish=True)
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    res = rt.run(State.empty("nofinish"))
    r.record(
        "sink omitting finish is unaffected",
        res.status == "completed" and s.sessions[0].kinds[-1] == "run_completed",
        f"status={res.status} last={s.sessions[0].kinds[-1]}",
    )

    # ------------------------------ finish() that raises is swallowed       #
    s = ProtocolJsonlSink(d, prefix="finishraises",
                          finish_raises=RuntimeError("finish exploded"))
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s)
    try:
        res = rt.run(State.empty("finishraises"))
        escaped = None
    except BaseException as exc:  # noqa: BLE001
        res, escaped = None, exc
    r.record(
        "finish() raising does not escape",
        escaped is None and res is not None and res.status == "completed",
        f"escaped={escaped!r} status={getattr(res, 'status', None)}",
    )

    # ------------------------------ finish() runs on which thread?          #
    s = ProtocolJsonlSink(d, prefix="thread")
    rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED, sink=s,
                 flush_interval_ms=1, chunk_records=64)
    rt.run(State.empty("thread"))
    r.record(
        "finish() runs on the runner thread",
        s.sessions[0].finish_threads == [threading.get_ident()],
        f"{s.sessions[0].finish_threads} vs main {threading.get_ident()}",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
