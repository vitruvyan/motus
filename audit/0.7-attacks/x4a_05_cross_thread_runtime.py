"""x4a-05 — hammer one Runtime (and its hub) from several threads at once.

Public surface only.  Four contentions:

  A  8 threads call ``rt.run()`` on the same Runtime.  Exactly one may execute;
     the rest must be refused, and the winner's trace must still validate.
  B  a background thread calls ``rt.cancel()`` in a tight loop for the whole
     run.  The trace must land on exactly one terminal and validate; a cancel
     that arrives after the terminal must not bleed into the NEXT run
     (ADR-008 §1).
  C  background readers pull ``rt.trace`` (and materialise it) while the
     buffered Timer thread is flushing.  No reader may see a document the
     contract validator rejects as in-flight evidence.
  D  background readers pull ``sink.records`` mid-run while the Timer writes.

Run: .venv/bin/python .attack/x4a_05_cross_thread_runtime.py
"""

from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, "/home/vitruvyan/motus/.attack")
sys.path.insert(0, "/home/vitruvyan/motus/contract")

import validate as contract_validate  # noqa: E402
from vitruvyan_motus import InMemoryTraceSink  # noqa: E402
from x4a_common import (  # noqa: E402
    Report, RecordingSink, buffered_runtime, chain_doc, seq_report,
)


def case_a(rep: Report, rounds: int = 20) -> None:
    N = 10
    spec_doc = chain_doc(N)
    bad = []
    for r in range(rounds):
        sink = RecordingSink()
        rt = buffered_runtime(N, sink=sink, chunk_records=3,
                              flush_interval_ms=1, pause_s=0.0004)
        outcomes: list[str] = []
        lock = threading.Lock()
        start = threading.Barrier(8)

        def worker():
            start.wait()
            try:
                res = rt.run()
            except RuntimeError as exc:
                with lock:
                    outcomes.append(f"refused:{exc}")
                return
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    outcomes.append(f"raised:{type(exc).__name__}")
                return
            with lock:
                outcomes.append(f"ran:{res.status}")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ran = [o for o in outcomes if o.startswith("ran:")]
        refused = [o for o in outcomes if o.startswith("refused:")]
        other = [o for o in outcomes if not (o.startswith("ran:") or o.startswith("refused:"))]
        trace = rt.trace
        problems = []
        if len(ran) != 1:
            problems.append(f"{len(ran)} runs executed: {ran}")
        if other:
            problems.append(f"unexpected outcomes {other}")
        if not all("overlapping" in o for o in refused):
            problems.append(f"wrong refusal text {refused[:2]}")
        if trace is not None:
            tseqs = [x["seq"] for x in trace.records]
            if not seq_report(tseqs)["gapless"]:
                problems.append(f"T1 gaps {tseqs[:12]}")
            v = contract_validate.validate_trace(trace.to_dict(), spec_doc, True)
            if v:
                problems.append(f"validate.py {[x.rule for x in v][:5]}")
            pseqs = [x["seq"] for _t, b in sink.batches for x in b]
            if pseqs != tseqs:
                problems.append(f"persisted!=trace {len(pseqs)}/{len(tseqs)}")
        if problems:
            bad.append((r, problems))
    rep.record("A/concurrent-run-exclusion", not bad,
               f"{rounds} rounds; {bad[:2]}")


def case_b(rep: Report, rounds: int = 20) -> None:
    N = 12
    spec_doc = chain_doc(N)
    bad = []
    bled = 0
    for r in range(rounds):
        sink = RecordingSink()
        rt = buffered_runtime(N, sink=sink, chunk_records=3,
                              flush_interval_ms=1, pause_s=0.0003)
        stop = threading.Event()

        def canceller():
            while not stop.is_set():
                rt.cancel(f"x4a-05 hammer {r}")

        t = threading.Thread(target=canceller, daemon=True)
        t.start()
        result = rt.run()
        stop.set()
        t.join(2.0)
        trace = rt.trace
        records = trace.records
        problems = []
        terminals = [x["kind"] for x in records
                     if x["kind"] in ("run_completed", "run_failed", "run_cancelled")]
        if len(terminals) != 1:
            problems.append(f"{terminals} terminals")
        if records[-1]["kind"] not in ("run_completed", "run_cancelled"):
            problems.append(f"tail {records[-1]['kind']}")
        if not seq_report([x["seq"] for x in records])["gapless"]:
            problems.append("T1 gaps")
        v = contract_validate.validate_trace(trace.to_dict(), spec_doc, True)
        if v:
            problems.append(f"validate.py {[x.rule for x in v][:5]} {str(v[0])[:120]}")
        pseqs = [x["seq"] for _t, b in sink.batches for x in b]
        if pseqs != [x["seq"] for x in records]:
            problems.append(f"persisted!=trace {len(pseqs)}/{len(records)}")
        if problems:
            bad.append((r, result.status, problems))
        # A cancel that arrived after the terminal must not reach a later run.
        sink2 = RecordingSink()
        rt2_result = None
        try:
            rt2_result = rt.run()
        except RuntimeError as exc:
            bad.append((r, "second-run-refused", [str(exc)]))
        if rt2_result is not None and rt2_result.status == "cancelled":
            bled += 1
    rep.record("B/cancel-hammer-single-terminal", not bad,
               f"{rounds} rounds; {bad[:2]}")
    rep.record("B/no-cancel-bleed-into-next-run", bled == 0,
               f"{bled}/{rounds} later runs cancelled by a stale request")


def case_cd(rep: Report, rounds: int = 8) -> None:
    N = 25
    spec_doc = chain_doc(N)
    trace_problems: list[str] = []
    sink_problems: list[str] = []
    snapshots = 0
    sink_reads = 0
    for r in range(rounds):
        sink = InMemoryTraceSink()
        rt = buffered_runtime(N, sink=sink, chunk_records=1000,
                              flush_interval_ms=1, pause_s=0.002)
        stop = threading.Event()

        def trace_reader():
            nonlocal snapshots
            while not stop.is_set():
                tr = rt.trace
                if tr is None:
                    continue
                try:
                    doc = tr.to_dict()
                except BaseException as exc:  # noqa: BLE001
                    trace_problems.append(f"to_dict raised {type(exc).__name__}: {exc}")
                    return
                if not doc["records"]:
                    # A Trace exists from _start on, before run_started is
                    # stored.  An empty record list is a legitimate transient,
                    # not evidence; the schema requires non-empty.
                    continue
                snapshots += 1
                seqs = [x["seq"] for x in doc["records"]]
                if seqs != list(range(1, len(seqs) + 1)):
                    trace_problems.append(f"torn snapshot seqs={seqs[:10]}")
                    return
                v = contract_validate.validate_trace(doc, spec_doc, False)
                if v:
                    trace_problems.append(
                        f"in-flight snapshot rejected {[x.rule for x in v][:4]} "
                        f"{str(v[0])[:140]}")
                    return

        def sink_reader():
            nonlocal sink_reads
            while not stop.is_set():
                try:
                    recs = sink.records
                    hdr = sink.header
                    runs = sink.runs
                except BaseException as exc:  # noqa: BLE001
                    sink_problems.append(f"reader raised {type(exc).__name__}: {exc}")
                    return
                sink_reads += 1
                seqs = [x["seq"] for x in recs]
                if seqs != list(range(1, len(seqs) + 1)):
                    sink_problems.append(f"sink view not gapless {seqs[:10]}")
                    return
                if recs and hdr is None:
                    sink_problems.append("records without a header")
                    return
                if runs and len(runs[-1]["records"]) < len(recs) - 1:
                    sink_problems.append("runs view lags records view")
                    return

        readers = [threading.Thread(target=trace_reader, daemon=True),
                   threading.Thread(target=sink_reader, daemon=True)]
        for t in readers:
            t.start()
        rt.run()
        time.sleep(0.01)
        stop.set()
        for t in readers:
            t.join(3.0)
    rep.record("C/trace-reader-sees-valid-in-flight-evidence",
               not trace_problems, f"{snapshots} snapshots; {trace_problems[:2]}")
    rep.record("D/sink-reader-sees-consistent-view",
               not sink_problems, f"{sink_reads} reads; {sink_problems[:2]}")


def main() -> int:
    rep = Report("x4a-05 cross-thread contention on one Runtime")
    case_a(rep)
    case_b(rep)
    case_cd(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
