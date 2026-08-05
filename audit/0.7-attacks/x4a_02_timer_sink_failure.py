"""x4a-02 — make the required sink fail on the *Timer* thread.

Invariant II: "A run cannot declare itself completed if its required sink has
not accepted the trace."  In the buffered profile the party that hands records
to the sink is frequently ``_ObservationHub._timer_flush``, running on a
``threading.Timer`` thread the runner never joins.  Its failure is stored in
``_async_failure`` and swallowed.  ``persist`` reads ``_async_failure`` once
*outside* the lock and again inside it, so this script asks whether a failure
raised off-thread can ever be missed by the run that must fail because of it.

Cases:
  A  timer-only failure, plenty of records left  -> must raise SinkFailed
  B  timer-only failure landing near the terminal -> must not complete
  C  failure on the *first* off-thread write with a slow sink (widest window)
  D  same, but the run also has a node failure racing it

Run: .venv/bin/python .attack/x4a_02_timer_sink_failure.py
"""

from __future__ import annotations

import sys
import threading

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import SinkFailed  # noqa: E402
from x4a_common import Report, FailingSink, buffered_runtime  # noqa: E402


def outcome(rt) -> tuple[str, object]:
    try:
        result = rt.run()
    except SinkFailed as exc:
        return "SinkFailed", exc
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, exc
    return result.status, result


def case(rep: Report, label: str, *, n: int, chunk: int, interval_ms: int,
         pause_s: float, fail_on: int, delay_s: float, rounds: int) -> None:
    completed_with_timer_failure = 0
    silent = []
    statuses: dict[str, int] = {}
    off_thread_failures = 0
    for _ in range(rounds):
        sink = FailingSink(fail_on=fail_on, only_timer_thread=True,
                           delay_s=delay_s)
        rt = buffered_runtime(
            n, sink=sink, chunk_records=chunk, flush_interval_ms=interval_ms,
            pause_s=pause_s,
        )
        status, _payload = outcome(rt)
        statuses[status] = statuses.get(status, 0) + 1
        failed_off = (
            sink.failed_on_thread is not None
            and sink.failed_on_thread != threading.main_thread().name
        )
        if failed_off:
            off_thread_failures += 1
            if status == "completed":
                completed_with_timer_failure += 1
                trace = rt.trace
                persisted = [r["seq"] for _t, b in sink.batches for r in b]
                silent.append({
                    "trace_records": len(trace.records),
                    "persisted": len(persisted),
                    "thread": sink.failed_on_thread,
                })
    rep.record(
        f"{label}/invariant-II-no-silent-completion",
        completed_with_timer_failure == 0,
        f"{completed_with_timer_failure}/{rounds} completed despite an "
        f"off-thread sink refusal; sample={silent[:2]}",
    )
    rep.record(
        f"{label}/timer-failure-actually-happened", off_thread_failures > 0,
        f"{off_thread_failures}/{rounds}; statuses={statuses}",
    )


def main() -> int:
    rep = Report("x4a-02 sink refusal raised on the Timer thread")
    case(rep, "A-early", n=40, chunk=10_000, interval_ms=1, pause_s=0.001,
         fail_on=1, delay_s=0.0, rounds=15)
    case(rep, "B-late", n=12, chunk=10_000, interval_ms=3, pause_s=0.001,
         fail_on=1, delay_s=0.0, rounds=25)
    case(rep, "C-slow-sink", n=25, chunk=10_000, interval_ms=1, pause_s=0.002,
         fail_on=1, delay_s=0.004, rounds=12)
    case(rep, "D-tight", n=6, chunk=10_000, interval_ms=1, pause_s=0.0005,
         fail_on=1, delay_s=0.0, rounds=40)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
