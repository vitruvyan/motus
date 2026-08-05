"""x4b-09 — finaliser timing, and the shapes `_iterator_is_running` answers on.

1. Interpreter shutdown with a live never-advanced driver: does the atexit arm
   of `weakref.finalize` produce noise or harm?
2. `gc.disable()` plus a reference cycle: the release is best-effort — say how
   best-effort.
3. Can the finaliser reach a run whose generator is live?  (Forced: advance the
   underlying generator directly so the driver itself becomes droppable.)
4. `yield from` delegation — which generator reports `gi_running`?
5. Odd iterator shapes through the PUBLIC StreamDriver / AsyncStreamDriver
   constructors, which `__all__` exports.
6. Supervisor closes a driver whose reader thread is stuck inside a node:
   close() reports success — does anything ever write the terminal?
"""

from __future__ import annotations

import gc
import subprocess
import sys
import threading
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import Runtime, State  # noqa: E402
from vitruvyan_motus.observers import StreamDriver, _iterator_is_running  # noqa: E402
from x4b_common import LINEAR, Report, passthrough  # noqa: E402

SHUTDOWN_PROGRAM = """
import sys
sys.path.insert(0, "/home/vitruvyan/motus/.attack")
from vitruvyan_motus import State
from x4b_common import sync_runtime
rt = sync_runtime()
d = rt.stream(State.empty("shutdown"))       # never advanced, still alive at exit
KEEP = d
print("exiting with a live never-advanced driver")
"""


def shutdown(report: Report) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", SHUTDOWN_PROGRAM],
        capture_output=True, text=True, timeout=60,
    )
    report.record(
        "interpreter shutdown with a live never-advanced driver is quiet",
        proc.returncode == 0 and not proc.stderr.strip(),
        f"rc={proc.returncode} stderr={proc.stderr.strip()[:200]!r}",
    )


def gc_disabled(report: Report) -> None:
    gc.disable()
    try:
        rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
        d = rt.stream(State.empty("cycle"))
        cycle: dict[str, Any] = {"d": d}
        cycle["self"] = cycle              # refcounting alone cannot free it
        del d, cycle
        wedged_before = rt._running
    finally:
        gc.enable()
    still = rt._running
    gc.collect()
    after = rt._running
    report.record(
        "an abandoned driver in a cycle releases without gc",
        not still,
        f"_running: right after drop={wedged_before}, with gc disabled={still}, "
        f"after gc.collect()={after}",
    )


def live_generator(report: Report) -> None:
    """Force the worst case: advance the generator behind the driver's back so
    the driver becomes droppable while its run is live."""
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("behind"))
    inner = d._iterator                    # keep the machine alive independently
    next(inner)                            # the run is now live
    handle = rt._run
    started, finished = handle.started, handle.finished
    del d
    gc.collect()
    report.record(
        "finaliser cannot release a run whose generator is live",
        rt._running,
        f"handle.started={started} handle.finished={finished} "
        f"_running_after_drop={rt._running}",
    )
    inner.close()


def delegation(report: Report) -> None:
    """Which generator reports gi_running under `yield from`?"""
    seen: dict[str, Any] = {}

    def inner():
        seen["inner_running"] = _iterator_is_running(seen["inner"])
        seen["outer_running"] = _iterator_is_running(seen["outer"])
        yield 1

    def outer(i):
        yield from i

    i = inner()
    o = outer(i)
    seen["inner"], seen["outer"] = i, o
    next(o)
    report.record(
        "yield from: both the delegate and the delegator report running",
        seen["inner_running"] and seen["outer_running"],
        f"inner={seen['inner_running']} outer={seen['outer_running']}",
    )


def odd_shapes(report: Report) -> None:
    calls: list[str] = []

    class Proxy:
        """No gi_/ag_/cr_ attribute at all — the predicate must say 'not running'."""

        def __init__(self, inner):
            self._inner = inner

        def __next__(self):
            return next(self._inner)

        def __iter__(self):
            return self

    def cancel(reason: str) -> None:
        calls.append(reason)

    d = StreamDriver(Proxy(iter([{"kind": "x"}])), cancel, lambda: None)
    d.close("proxy close")
    report.record(
        "a non-generator iterator is drained, not skipped",
        d._closed and calls == ["proxy close"],
        f"closed={d._closed} cancels={calls}",
    )

    class Hostile:
        """__getattr__ that raises something hasattr does not swallow."""

        def __getattr__(self, name):
            raise RuntimeError(f"hostile attribute {name}")

        def __next__(self):
            raise StopIteration

    escaped = "none"
    try:
        _iterator_is_running(Hostile())
    except BaseException as exc:  # noqa: BLE001
        escaped = f"{type(exc).__name__}: {exc}"
    report.record(
        "_iterator_is_running survives a hostile iterator",
        escaped == "none",
        f"escaped={escaped}",
    )


def stuck_reader(report: Report) -> None:
    """A supervisor closes a driver whose reader is stuck inside a node."""
    gate = threading.Event()

    def blocker(state: State) -> State:
        gate.wait(3.0)
        return state

    rt = Runtime(LINEAR, {"a": blocker, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("stuck"))
    errors: list[str] = []

    def reader() -> None:
        try:
            for _ in d:                 # an ordinary consumer, blocks in node 'a'
                pass
        except BaseException as exc:    # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    threading.Event().wait(0.2)         # let the reader reach the node
    escaped = "none"
    try:
        d.close("supervisor stop")
    except BaseException as exc:        # noqa: BLE001
        escaped = f"{type(exc).__name__}: {exc}"
    terminal_now = d.trace.records[-1]["kind"] if d.trace else "<none>"
    closed_now = d._closed
    gate.set()
    t.join(5)
    terminal_after = d.trace.records[-1]["kind"] if d.trace else "<none>"
    report.record(
        "close() on a driver held by another thread reports honestly",
        escaped == "none" and terminal_after == "run_cancelled",
        f"close escaped={escaped}; at return: closed={closed_now} "
        f"terminal={terminal_now}; after the reader resumed: {terminal_after}; "
        f"reader errors={errors}; _running={rt._running}",
    )


def main() -> int:
    report = Report("x4b-09 timing and shapes")
    shutdown(report)
    gc_disabled(report)
    live_generator(report)
    delegation(report)
    odd_shapes(report)
    stuck_reader(report)
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
