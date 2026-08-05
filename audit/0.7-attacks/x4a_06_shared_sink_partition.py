"""x4a-06 — one TraceSink, two concurrent runs: who gets whose evidence?

``InMemoryTraceSink.open_run`` appends the new run session under a lock, but
``.records`` / ``.header`` answer from ``self._runs[-1]`` — the run that
opened LAST, not the run the caller is asking about.  Two Runtimes sharing one
sink is a supported shape (nothing in the contract binds a sink to one run;
ADR-004 makes the sink a factory precisely so it can serve many), so this
script runs two of them concurrently and asks whether a caller can read
another run's evidence through the accessor every test in the repository uses.

  A  two threads, two Runtimes, one sink: partitioning through ``.runs``
  B  the same, read through ``.records`` / ``.header``
  C  sequential runs on the same sink (the non-concurrent control)

Run: .venv/bin/python .attack/x4a_06_shared_sink_partition.py
"""

from __future__ import annotations

import sys
import threading

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import InMemoryTraceSink  # noqa: E402
from x4a_common import Report, buffered_runtime  # noqa: E402


def concurrent_pair(sink, n_a: int, n_b: int, pause_s: float):
    rt_a = buffered_runtime(n_a, sink=sink, chunk_records=2,
                            flush_interval_ms=1, pause_s=pause_s)
    rt_b = buffered_runtime(n_b, sink=sink, chunk_records=2,
                            flush_interval_ms=1, pause_s=pause_s)
    results: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def go(key, rt):
        barrier.wait()
        results[key] = rt.run()

    threads = [threading.Thread(target=go, args=("a", rt_a)),
               threading.Thread(target=go, args=("b", rt_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return rt_a, rt_b, results


def main() -> int:
    rep = Report("x4a-06 one sink, two concurrent runs")

    # --- A: partitioning through .runs ------------------------------------
    mismatches = []
    for r in range(15):
        sink = InMemoryTraceSink()
        rt_a, rt_b, results = concurrent_pair(sink, 6, 9, 0.001)
        runs = sink.runs
        by_id = {run["header"]["run_id"]: run for run in runs}
        for key, rt in (("a", rt_a), ("b", rt_b)):
            run_id = rt.trace.run["run_id"]
            if run_id not in by_id:
                mismatches.append((r, key, "no session for run_id"))
                continue
            got = [x["seq"] for x in by_id[run_id]["records"]]
            want = [x["seq"] for x in rt.trace.records]
            if got != want:
                mismatches.append((r, key, f"{len(got)} vs {len(want)}"))
        if len(runs) != 2:
            mismatches.append((r, "-", f"{len(runs)} sessions for 2 runs"))
    rep.record("A/runs-view-partitions-correctly", not mismatches,
               f"15 rounds; {mismatches[:3]}")

    # --- B: .records / .header answer for whom? ---------------------------
    wrong_records = 0
    wrong_header = 0
    rounds = 15
    detail = None
    for r in range(rounds):
        sink = InMemoryTraceSink()
        rt_a, rt_b, results = concurrent_pair(sink, 6, 9, 0.001)
        # Both runs are finished.  A caller of either Runtime who reads the
        # sink now gets exactly one answer; at most one of them is theirs.
        got = [x["seq"] for x in sink.records]
        hdr = sink.header
        a_seqs = [x["seq"] for x in rt_a.trace.records]
        b_seqs = [x["seq"] for x in rt_b.trace.records]
        if got != a_seqs:
            wrong_records += 1
            if detail is None:
                detail = (f"sink.records has {len(got)} records; run A's trace "
                          f"has {len(a_seqs)}; header.run_id="
                          f"{hdr['run_id'][:8]}.. vs A's "
                          f"{rt_a.trace.run['run_id'][:8]}..")
        if hdr["run_id"] not in (rt_a.trace.run["run_id"],):
            wrong_header += 1
    rep.record("B/records-accessor-answers-the-asking-run",
               wrong_records == 0,
               f"{wrong_records}/{rounds} rounds gave run A's caller someone "
               f"else's records; {detail}")
    rep.record("B/header-accessor-answers-the-asking-run",
               wrong_header == 0,
               f"{wrong_header}/{rounds} rounds gave run A's caller another "
               f"run's header")

    # --- C: sequential control -------------------------------------------
    sink = InMemoryTraceSink()
    rt1 = buffered_runtime(5, sink=sink, chunk_records=2, flush_interval_ms=1)
    rt1.run()
    first = [x["seq"] for x in sink.records]
    rt2 = buffered_runtime(7, sink=sink, chunk_records=2, flush_interval_ms=1)
    rt2.run()
    second = [x["seq"] for x in sink.records]
    rep.record("C/sequential-records-follow-the-latest-run",
               first != second and len(sink.runs) == 2,
               f"first={len(first)} then={len(second)}, sessions={len(sink.runs)}")
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
