"""Attack 6 -- one Runtime, two loops / two threads.

The overlapping-run guard is a ``threading.RLock`` plus a ``_running`` flag, so
it plainly anticipates being reached from more than one thread; ADR-008 §1
states the intent -- "a concurrent request either binds to the run or is
reported as missed".  The loser is supposed to get a clean RuntimeError.

The hypothesis under test is *not* that the guard admits two runs.  It is that
the guard is released too early.  ``_managed_execute``'s ``finally`` clears
``_running`` while the generator unwinds; only *afterwards* does ``arun`` /
``run`` read ``self._state`` and ``self._trace`` to build its RunResult:

    async for _ in _adrive(machine, _invoke_async):
        pass
    assert self._state is not None and self._trace is not None
    return RunResult(self._state, self._trace)          # <-- unguarded read

A second caller admitted in that window rebinds ``self._trace`` in ``_start``
before the first caller has read it.  Each run is given a distinct ``run_id``
here, so any mismatch is proof of a cross-assigned trace.

``sys.setswitchinterval`` is lowered to widen the interleaving; it changes only
the interpreter's scheduling granularity, never the product.
"""

from __future__ import annotations

import asyncio
import sys
import threading

from vitruvyan_motus import Runtime, State
from x2_common import LINEAR, Report

R = Report("x2_06 two loops / two threads")


async def apassthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


def spassthrough(state: State) -> State:
    return state


async def case_two_tasks_one_loop(iterations: int = 500) -> None:
    refused = 0
    mismatches = 0
    errors: list[str] = []
    rt = Runtime(LINEAR, {"a": apassthrough, "b": apassthrough, "c": apassthrough})
    for i in range(iterations):
        async def call(tag: str):
            try:
                result = await rt.arun(State.empty(tag), run_id=tag)
                return tag, result
            except RuntimeError as exc:
                return tag, exc

        out = await asyncio.gather(call(f"L{i}a"), call(f"L{i}b"))
        for tag, result in out:
            if isinstance(result, RuntimeError):
                refused += 1
                continue
            try:
                if result.trace.run["run_id"] != tag:
                    mismatches += 1
                result.status
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
    R.record(
        f"two tasks, one loop x{iterations}",
        mismatches == 0 and not errors,
        f"refused={refused} mismatched_traces={mismatches} errors={errors[:3]}",
    )


def _thread_worker(rt: Runtime, tag: str, iterations: int, stats: dict, use_async: bool) -> None:
    for i in range(iterations):
        run_id = f"{tag}-{i}"
        try:
            if use_async:
                result = asyncio.run(rt.arun(State.empty(tag), run_id=run_id))
            else:
                result = rt.run(State.empty(tag), run_id=run_id)
        except RuntimeError as exc:
            if "overlapping" in str(exc):
                stats["refused"] += 1
                continue
            stats["errors"].append(f"{type(exc).__name__}: {exc}")
            continue
        except BaseException as exc:  # noqa: BLE001
            stats["errors"].append(f"{type(exc).__name__}: {exc}")
            continue
        try:
            got = result.trace.run["run_id"]
            if got != run_id:
                stats["mismatch"] += 1
                stats["samples"].append(f"asked {run_id!r} got {got!r}")
            result.status
        except BaseException as exc:  # noqa: BLE001
            stats["errors"].append(f"{type(exc).__name__}: {exc}")


def case_two_threads(iterations: int, use_async: bool, label: str) -> None:
    rt = Runtime(
        LINEAR,
        {"a": apassthrough, "b": apassthrough, "c": apassthrough}
        if use_async
        else {"a": spassthrough, "b": spassthrough, "c": spassthrough},
    )
    stats = {"refused": 0, "mismatch": 0, "errors": [], "samples": []}
    threads = [
        threading.Thread(target=_thread_worker, args=(rt, tag, iterations, stats, use_async))
        for tag in ("T1", "T2")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(180)
    R.record(
        f"{label} x{iterations}/thread",
        stats["mismatch"] == 0 and not stats["errors"],
        f"refused={stats['refused']} mismatched_traces={stats['mismatch']} "
        f"errors={stats['errors'][:2]} sample={stats['samples'][:2]}",
    )


def main() -> int:
    old = sys.getswitchinterval()
    asyncio.run(case_two_tasks_one_loop())
    sys.setswitchinterval(1e-6)
    try:
        case_two_threads(2000, False, "two threads, sync run()")
        case_two_threads(1500, True, "two threads, own loop each, arun()")
    finally:
        sys.setswitchinterval(old)
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
