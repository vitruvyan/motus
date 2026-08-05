"""x4a-01 — put the buffered Timer thread and the runner thread in contention.

The hub's ``_lock`` is taken by three parties: ``persist`` (runner thread),
``_timer_flush`` (Timer thread) and ``close`` (runner thread, at the run's
terminal).  No existing test makes the Timer actually fire during a run, so
this script forces it to fire many times per run and then asks the persisted
stream the only questions that matter:

  * is it gapless in ``seq`` from 1 (trace rule T1)?
  * does it contain every record the in-memory trace contains?
  * are there duplicates (a record written by both the Timer and the runner)?
  * is the terminal record last?

Run: .venv/bin/python .attack/x4a_01_timer_contention.py
"""

from __future__ import annotations

import sys
import threading

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from x4a_common import (  # noqa: E402
    Report, RecordingSink, buffered_runtime, seq_report,
)


def one_round(rep: Report, *, label: str, n: int, chunk: int, interval_ms: int,
              pause_s: float, rounds: int) -> None:
    timer_writes_seen = 0
    for attempt in range(rounds):
        sink = RecordingSink()
        rt = buffered_runtime(
            n, sink=sink, chunk_records=chunk, flush_interval_ms=interval_ms,
            pause_s=pause_s,
        )
        result = rt.run()
        batches = sink.batches
        threads = {name for name, _ in batches}
        off = threads - {threading.main_thread().name}
        if off:
            timer_writes_seen += 1
        persisted = [r for _t, b in batches for r in b]
        pseqs = [r["seq"] for r in persisted]
        tseqs = [r["seq"] for r in result.trace.records]
        info = seq_report(pseqs)
        if not info["gapless"]:
            rep.record(f"{label}/T1-gapless", False,
                       f"attempt {attempt}: {info}")
            return
        if info["duplicates"]:
            rep.record(f"{label}/no-duplicate-write", False,
                       f"attempt {attempt}: dup seqs {info['duplicates']}")
            return
        if pseqs != tseqs:
            rep.record(f"{label}/persisted==trace", False,
                       f"attempt {attempt}: persisted {len(pseqs)} vs trace "
                       f"{len(tseqs)}; missing "
                       f"{sorted(set(tseqs) - set(pseqs))[:8]}")
            return
        if persisted[-1]["kind"] != result.trace.records[-1]["kind"]:
            rep.record(f"{label}/terminal-last", False,
                       f"attempt {attempt}: persisted tail "
                       f"{persisted[-1]['kind']}")
            return
        if not result.succeeded:
            rep.record(f"{label}/completed", False,
                       f"attempt {attempt}: status {result.status}")
            return
    rep.record(f"{label}/T1-gapless", True, f"{rounds} rounds, n={n}")
    rep.record(f"{label}/no-duplicate-write", True, f"{rounds} rounds")
    rep.record(f"{label}/persisted==trace", True, f"{rounds} rounds")
    rep.record(f"{label}/terminal-last", True, f"{rounds} rounds")
    rep.record(f"{label}/timer-thread-actually-wrote", timer_writes_seen > 0,
               f"{timer_writes_seen}/{rounds} rounds saw an off-thread write")


def main() -> int:
    rep = Report("x4a-01 buffered Timer vs runner contention")
    # Timer is the primary flusher: chunk never reached, interval short.
    one_round(rep, label="timer-driven", n=40, chunk=10_000, interval_ms=1,
              pause_s=0.001, rounds=12)
    # Chunk and timer both in play.
    one_round(rep, label="mixed", n=60, chunk=3, interval_ms=2,
              pause_s=0.0008, rounds=12)
    # Long interval: chunk does the flushing, the terminal force-flushes.
    one_round(rep, label="chunk-driven", n=60, chunk=5, interval_ms=60_000,
              pause_s=0.0, rounds=12)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
