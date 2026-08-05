"""x4b-05 — two threads.

1. Cross-run result attribution.  Commit 2d7a423 exists because the claim is
   released before `run()` reads `self._trace`/`self._state`, so a second run
   admitted in that window handed one caller another caller's evidence.  Two
   threads hammering `run()` should now never see a foreign run_id.  (Expect
   FAIL on main, PASS on HEAD.  If HEAD fails too, the fix is incomplete.)

2. A second thread calling gc.collect() continuously while a run is live —
   can the finaliser reach a run that has begun?

3. One thread in close() while another is in __next__.
"""

from __future__ import annotations

import gc
import sys
import threading
import time

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import Runtime, State  # noqa: E402
from x4b_common import LINEAR, Report, passthrough  # noqa: E402

sys.setswitchinterval(1e-6)


def attribution(report: Report, trials: int = 4000) -> None:
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    bad: list[str] = []
    overlaps = [0, 0]
    lock = threading.Lock()

    def worker(tag: str, index: int) -> None:
        for i in range(trials):
            run_id = f"{tag}-{i}"
            try:
                result = rt.run(State.empty(f"i-{tag}-{i}"), run_id=run_id)
            except RuntimeError:
                overlaps[index] += 1
                continue
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    bad.append(f"{tag}-{i}: escaped {type(exc).__name__}: {exc}")
                continue
            got = result.trace.run["run_id"]
            intent = result.state._intent
            if got != run_id or intent != f"i-{tag}-{i}":
                with lock:
                    bad.append(
                        f"{tag}-{i}: asked run_id={run_id!r} intent=i-{tag}-{i!r}, "
                        f"got run_id={got!r} intent={intent!r}"
                    )

    threads = [
        threading.Thread(target=worker, args=("A", 0)),
        threading.Thread(target=worker, args=("B", 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    report.record(
        "two threads never receive each other's evidence",
        not bad,
        f"{len(bad)} misattributions in {2 * trials} attempts "
        f"(overlaps refused: {sum(overlaps)}); first: {bad[0] if bad else '-'}",
    )


def gc_storm(report: Report, seconds: float = 2.0) -> None:
    """A second thread collecting continuously while runs execute."""
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    stop = threading.Event()
    problems: list[str] = []

    def collector() -> None:
        while not stop.is_set():
            gc.collect()

    t = threading.Thread(target=collector, daemon=True)
    t.start()
    deadline = time.monotonic() + seconds
    n = 0
    try:
        while time.monotonic() < deadline:
            n += 1
            d = rt.stream(State.empty(f"gc-{n}"))
            seen = [r["kind"] for r in d]
            if seen[-1] != "run_completed":
                problems.append(f"run {n}: terminal {seen[-1]}")
            if rt._running:
                problems.append(f"run {n}: claim survived a completed run")
            # every other run: abandon a fresh driver mid-flight
            d2 = rt.stream(State.empty(f"gc-mid-{n}"))
            next(d2)
            del d2
            gc.collect()
            if rt._running:
                problems.append(f"run {n}: mid-run abandon left the claim")
    except BaseException as exc:  # noqa: BLE001
        problems.append(f"escaped {type(exc).__name__}: {exc}")
    finally:
        stop.set()
        t.join()
    report.record(
        "gc storm from a second thread harms no run",
        not problems,
        f"{n} runs; {len(problems)} problems; first: {problems[0] if problems else '-'}",
    )


def close_vs_next(report: Report, trials: int = 300) -> None:
    escapes: list[str] = []
    wedged = 0
    for i in range(trials):
        rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
        d = rt.stream(State.empty(f"race-{i}"))
        errors: list[str] = []

        def reader() -> None:
            try:
                for _ in d:
                    pass
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"reader:{type(exc).__name__}:{exc}")

        def closer() -> None:
            try:
                d.close("racing close")
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"closer:{type(exc).__name__}:{exc}")

        tr = threading.Thread(target=reader)
        tc = threading.Thread(target=closer)
        tr.start()
        tc.start()
        tr.join(5)
        tc.join(5)
        if tr.is_alive() or tc.is_alive():
            escapes.append(f"trial {i}: thread hung")
            break
        if errors:
            escapes.extend(f"trial {i}: {e}" for e in errors)
        term = d.trace.records[-1]["kind"] if d.trace.records else "<none>"
        if not term.startswith("run_") or term == "run_started":
            escapes.append(f"trial {i}: no terminal, last={term}")
        if rt._running:
            wedged += 1
    report.record(
        "close() racing __next__ leaves no wedge and no escape",
        not escapes and wedged == 0,
        f"{len(escapes)} escapes, {wedged} wedged of {trials}; "
        f"first: {escapes[0] if escapes else '-'}",
    )


def main() -> int:
    report = Report("x4b-05 threads")
    attribution(report)
    gc_storm(report)
    close_vs_next(report)
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
