"""x4a-03 — what does ``_ObservationHub.close()`` do with a non-empty buffer?

``close()`` is::

    with self._lock:
        self._closed = True
        self._cancel_timer_locked()

It cancels the pending Timer and latches ``_closed`` so a Timer that already
fired returns without flushing.  It does **not** flush.  Every terminal record
force-flushes, so on the ordinary paths the buffer is empty by then.  This
script hunts for run teardowns that reach ``_managed_execute``'s ``finally``
*without* a terminal, with the process still alive and the caller still
holding a Trace that names the dropped records.

Shapes tried, all under the buffered profile with the shipped defaults
(chunk_records=64, flush_interval_ms=1000):

  A  node raises BaseException (node-protocol §7.3 teardown)
  B  stream() driver abandoned mid-run and garbage-collected
  C  stream() driver closed mid-run (control: must cancel + flush)
  D  `with rt.stream()` broken out of (control)
  E  listener cancels the run (control)

Run: .venv/bin/python .attack/x4a_03_close_drops_buffer.py
"""

from __future__ import annotations

import gc
import sys

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from x4a_common import Report, RecordingSink, buffered_runtime  # noqa: E402

DEFAULT_CHUNK = 64
DEFAULT_INTERVAL = 1000


def gap(rt, sink: RecordingSink) -> dict[str, object]:
    trace = rt.trace
    tseqs = [r["seq"] for r in (trace.records if trace is not None else ())]
    pseqs = [r["seq"] for _t, b in sink.batches for r in b]
    missing = [s for s in tseqs if s not in set(pseqs)]
    return {
        "trace": len(tseqs),
        "persisted": len(pseqs),
        "missing": missing,
        "trace_tail": (trace.records[-1]["kind"] if tseqs else None),
        "persisted_tail": (
            [r for _t, b in sink.batches for r in b][-1]["kind"] if pseqs else None
        ),
        "hub_buffer_left": len(rt._hub._buffer) if rt._hub is not None else None,
        "hub_closed": rt._hub._closed if rt._hub is not None else None,
    }


class Boom(BaseException):
    """Not an Exception: node-protocol §7.3 says this tears the run down."""


def case_a(rep: Report) -> None:
    n = 30
    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=DEFAULT_CHUNK,
                          flush_interval_ms=DEFAULT_INTERVAL)
    nodes = rt.nodes
    target = "n20"

    def exploding(state):
        raise Boom("teardown")

    # Rebuild with one node that raises BaseException.
    registry = dict(nodes)
    registry[target] = exploding
    from vitruvyan_motus import DurabilityProfile, Runtime
    from x4a_common import chain
    sink = RecordingSink()
    rt = Runtime(
        chain(n), registry, durability_profile=DurabilityProfile.BUFFERED,
        sink=sink, chunk_records=DEFAULT_CHUNK,
        flush_interval_ms=DEFAULT_INTERVAL,
    )
    raised = None
    try:
        rt.run()
    except BaseException as exc:  # noqa: BLE001
        raised = type(exc).__name__
    info = gap(rt, sink)
    rep.record(
        "A-base-exception/no-evidence-dropped", not info["missing"],
        f"raised={raised}; {info}",
    )


def case_b(rep: Report) -> None:
    n = 30
    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=DEFAULT_CHUNK,
                          flush_interval_ms=DEFAULT_INTERVAL)
    driver = rt.stream()
    for i, _rec in enumerate(driver):
        if i >= 20:
            break
    del driver
    gc.collect()
    info = gap(rt, sink)
    rep.record(
        "B-abandoned-driver/no-evidence-dropped", not info["missing"], str(info),
    )


def case_c(rep: Report) -> None:
    n = 30
    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=DEFAULT_CHUNK,
                          flush_interval_ms=DEFAULT_INTERVAL)
    driver = rt.stream()
    for i, _rec in enumerate(driver):
        if i >= 20:
            break
    driver.close("x4a-03")
    info = gap(rt, sink)
    rep.record("C-driver-close/no-evidence-dropped", not info["missing"], str(info))


def case_d(rep: Report) -> None:
    n = 30
    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=DEFAULT_CHUNK,
                          flush_interval_ms=DEFAULT_INTERVAL)
    with rt.stream() as driver:
        for i, _rec in enumerate(driver):
            if i >= 20:
                break
    info = gap(rt, sink)
    rep.record("D-context-exit/no-evidence-dropped", not info["missing"], str(info))


def case_e(rep: Report) -> None:
    n = 30
    seen: list[str] = []

    class Canceller:
        def __init__(self) -> None:
            self.rt = None

        def on_record(self, record):
            seen.append(record["kind"])
            if len(seen) == 15 and self.rt is not None:
                self.rt.cancel("cancelled from a listener")

    listener = Canceller()
    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=DEFAULT_CHUNK,
                          flush_interval_ms=DEFAULT_INTERVAL,
                          listeners=(listener,))
    listener.rt = rt
    result = rt.run()
    info = gap(rt, sink)
    rep.record(
        "E-listener-cancel/no-evidence-dropped", not info["missing"],
        f"status={result.status}; {info}",
    )


def main() -> int:
    rep = Report("x4a-03 close() and the un-flushed buffer")
    case_a(rep)
    case_b(rep)
    case_c(rep)
    case_d(rep)
    case_e(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
