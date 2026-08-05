"""x5-04 -- can the finish/close ordering be raced into a wrong state?

Targets:
  * `close()` nulls `_run_sink` inside the lock and calls `finish` outside it.
    Can a Timer flush or a concurrent `persist` then trip
    `_flush_locked`'s `assert self._run_sink is not None`, or `persist`'s?
  * Can `finish` be observed more than once under GC pressure / threads?
  * Can anything be written to a session AFTER its `finish`?
  * Does an exception ever escape where the contract says it must not?
"""

from __future__ import annotations

import gc
import random
import sys
import threading
import time
import traceback

from vitruvyan_motus import Runtime, State
from vitruvyan_motus.errors import NodeFailed

from x5_common import BUFFERED, LINEAR, OUT, SYNC, ProtocolJsonlSink, Report, registry

D = OUT / "x5_04"


class CountingRunSink:
    """Minimal session: counts, and screams if written to after finish."""

    def __init__(self, owner) -> None:
        self.owner = owner
        self.batches = 0
        self.finishes: list[bool] = []
        self.after_finish = 0
        self.lock = threading.Lock()

    def write(self, records):
        with self.lock:
            if self.finishes:
                self.after_finish += 1
                self.owner.violations.append("write after finish")
            self.batches += 1
        if self.owner.slow_write:
            time.sleep(self.owner.slow_write)

    def finish(self, *, complete: bool) -> None:
        with self.lock:
            if self.finishes:
                self.owner.violations.append(
                    f"finish called {len(self.finishes) + 1} times"
                )
            self.finishes.append(complete)
        if self.owner.slow_finish:
            time.sleep(self.owner.slow_finish)


class CountingSink:
    def __init__(self, *, slow_write: float = 0.0, slow_finish: float = 0.0) -> None:
        self.sessions: list[CountingRunSink] = []
        self.violations: list[str] = []
        self.slow_write = slow_write
        self.slow_finish = slow_finish
        self._lock = threading.Lock()

    def open_run(self, header):
        s = CountingRunSink(self)
        with self._lock:
            self.sessions.append(s)
        return s


def main() -> int:
    r = Report("x5-04 races around close/finish")

    # ------------------------------------------------------------------- 1  #
    # Buffered profile with an aggressive timer: the Timer thread and the
    # runner's close() contend for the lock on every run.
    errors: list[str] = []
    sink = CountingSink()
    for i in range(300):
        rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED, sink=sink,
                     chunk_records=2, flush_interval_ms=1)
        try:
            if i % 3 == 0:
                drv = rt.stream(State.empty(f"t{i}"))
                next(drv)
                time.sleep(0.0015)   # let the timer fire mid-run
                del drv
                gc.collect()
            else:
                rt.run(State.empty(f"t{i}"))
        except BaseException:
            errors.append(traceback.format_exc(limit=3))
    ok = (
        not sink.violations and not errors
        and len(sink.sessions) == 300
        and all(len(s.finishes) == 1 for s in sink.sessions)
    )
    r.record(
        "300 buffered runs, 1ms timer, mixed abandon: exactly one finish each",
        ok,
        f"sessions={len(sink.sessions)} "
        f"finish_counts={sorted({len(s.finishes) for s in sink.sessions})} "
        f"violations={sink.violations[:3]} errors={len(errors)}"
        + (f" :: {errors[0].splitlines()[-1]}" if errors else ""),
    )

    # ------------------------------------------------------------------- 2  #
    # A slow `finish` while a Timer is still armed: the timer thread wakes
    # while finish() is running OUTSIDE the lock and _run_sink is already None.
    sink = CountingSink(slow_finish=0.05)
    errors = []
    for i in range(30):
        rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED, sink=sink,
                     chunk_records=1000, flush_interval_ms=2)
        try:
            drv = rt.stream(State.empty(f"s{i}"))
            next(drv)
            next(drv)
            del drv
            gc.collect()
        except BaseException:
            errors.append(traceback.format_exc(limit=3))
    time.sleep(0.2)
    r.record(
        "slow finish() while a 2ms timer is armed: no assert, no double call",
        not sink.violations and not errors
        and all(len(s.finishes) == 1 for s in sink.sessions),
        f"violations={sink.violations[:3]} errors={len(errors)} "
        f"counts={sorted({len(s.finishes) for s in sink.sessions})}",
    )

    # ------------------------------------------------------------------- 3  #
    # Two threads closing one driver while a third drives it.
    sink = CountingSink()
    errors = []
    for i in range(40):
        rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=sink)
        drv = rt.stream(State.empty(f"c{i}"))
        next(drv)
        barrier = threading.Barrier(3)

        def closer(d=drv, b=barrier):
            try:
                b.wait()
                d.close("racing close")
            except BaseException:
                errors.append(traceback.format_exc(limit=3))

        def driver(d=drv, b=barrier):
            try:
                b.wait()
                for _ in d:
                    pass
            except BaseException:
                errors.append(traceback.format_exc(limit=3))

        ts = [threading.Thread(target=closer), threading.Thread(target=closer),
              threading.Thread(target=driver)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert not any(t.is_alive() for t in ts), "a thread wedged"
        del drv
        gc.collect()
    r.record(
        "2 closers + 1 driver on one StreamDriver, 40x",
        not sink.violations and not errors
        and all(len(s.finishes) == 1 for s in sink.sessions),
        f"violations={sink.violations[:3]} errors={len(errors)} "
        f"counts={sorted({len(s.finishes) for s in sink.sessions})}"
        + (f" :: {errors[0].splitlines()[-1]}" if errors else ""),
    )

    # ------------------------------------------------------------------- 4  #
    # Cancellation from another thread while the run is mid-node.
    sink = CountingSink()
    errors = []
    for i in range(40):
        rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED, sink=sink,
                     chunk_records=1000, flush_interval_ms=1)

        def slow(state, _rt=rt):
            time.sleep(0.002)
            return state

        rt2 = Runtime(LINEAR, {k: slow for k in registry()},
                      durability_profile=BUFFERED, sink=sink,
                      chunk_records=1000, flush_interval_ms=1)

        def canceller(_rt=rt2):
            time.sleep(random.uniform(0.0, 0.006))
            try:
                _rt.cancel("cross-thread cancel")
            except BaseException:
                errors.append(traceback.format_exc(limit=3))

        t = threading.Thread(target=canceller)
        t.start()
        try:
            rt2.run(State.empty(f"x{i}"))
        except BaseException:
            errors.append(traceback.format_exc(limit=3))
        t.join(5)
    r.record(
        "cross-thread cancel during buffered run, 40x",
        not sink.violations and not errors
        and all(len(s.finishes) == 1 for s in sink.sessions),
        f"violations={sink.violations[:3]} errors={len(errors)} "
        f"counts={sorted({len(s.finishes) for s in sink.sessions})}"
        + (f" :: {errors[0].splitlines()[-1]}" if errors else ""),
    )

    # ------------------------------------------------------------------- 5  #
    # A `finish` that re-enters the Runtime.
    reentry: list[str] = []

    class ReentrantRunSink:
        def __init__(self, rt) -> None:
            self.rt = rt

        def write(self, records):
            pass

        def finish(self, *, complete):
            for label, call in (
                ("run", lambda: self.rt.run(State.empty("reentrant"))),
                ("stream", lambda: self.rt.stream(State.empty("reentrant"))),
                ("cancel", lambda: self.rt.cancel("from finish")),
            ):
                try:
                    call()
                    reentry.append(f"{label}:OK")
                except BaseException as exc:  # noqa: BLE001
                    reentry.append(f"{label}:{type(exc).__name__}")

    class ReentrantSink:
        def __init__(self) -> None:
            self.rt = None

        def open_run(self, header):
            return ReentrantRunSink(self.rt)

    rs = ReentrantSink()
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=rs)
    rs.rt = rt
    escaped = None
    try:
        res = rt.run(State.empty("reenter"))
    except BaseException as exc:  # noqa: BLE001
        res, escaped = None, exc
    followup = rt.run(State.empty("after-reenter"))
    r.record(
        "finish() re-entering the Runtime neither wedges nor escapes",
        escaped is None and res is not None and res.status == "completed"
        and followup.status == "completed",
        f"escaped={escaped!r} inside_finish={reentry} "
        f"followup={followup.status}",
    )

    # ------------------------------------------------------------------- 6  #
    # Two threads, two Runtimes, one shared sink: partitions and arity.
    sink = CountingSink()
    errors = []

    def worker(n: int):
        try:
            for i in range(25):
                rt = Runtime(LINEAR, registry(), durability_profile=BUFFERED,
                             sink=sink, chunk_records=3, flush_interval_ms=1)
                if i % 4 == 0:
                    d = rt.stream(State.empty(f"w{n}-{i}"))
                    next(d)
                    del d
                    gc.collect()
                else:
                    rt.run(State.empty(f"w{n}-{i}"))
        except BaseException:
            errors.append(traceback.format_exc(limit=3))

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    gc.collect()
    time.sleep(0.05)
    r.record(
        "4 threads x 25 runs on one shared sink",
        not sink.violations and not errors
        and len(sink.sessions) == 100
        and all(len(s.finishes) == 1 for s in sink.sessions),
        f"sessions={len(sink.sessions)} "
        f"counts={sorted({len(s.finishes) for s in sink.sessions})} "
        f"violations={sink.violations[:3]} errors={len(errors)}"
        + (f" :: {errors[0].splitlines()[-1]}" if errors else ""),
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
