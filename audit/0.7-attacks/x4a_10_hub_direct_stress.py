"""x4a-10 — the last three questions about ``_ObservationHub``.

A  (public path)  ``persist`` reads ``self._async_failure`` BEFORE taking the
   lock and again inside it.  After a refusal stored by the Timer thread, the
   sink must never be written to again, and the prefix it did receive must be
   a gapless prefix of the trace — anything else is a torn persisted account.

B  (white-box)    ``persist`` and ``close`` under direct contention.  The
   Runtime never does this — ``close()`` is called from ``_managed_execute``'s
   ``finally`` on the same thread that ran ``_execute`` — so a finding here is
   NOT reachable from the public API.  It is run to bound the damage: no
   duplicate write, no out-of-order write, no write after ``close()``
   returned.

C  (white-box)    ``_flush_locked`` does ``write(batch)`` then
   ``self._buffer.clear()`` — it clears the whole buffer, not the batch it
   wrote.  ``_lock`` is an RLock, so a sink that calls back into the hub from
   inside ``write`` re-enters on the same thread and its record is cleared
   without ever being written.  guarantees.md §6 forbids that sink ("MUST NOT
   feed anything back into execution"), so this is latent, not a defect.

Run: .venv/bin/python .attack/x4a_10_hub_direct_stress.py
"""

from __future__ import annotations

import random
import sys
import threading
import time

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus.observers import _ObservationHub  # noqa: E402
from x4a_common import (  # noqa: E402
    Report, FailingSink, RecordingSink, buffered_runtime,
)


def case_a(rep: Report, rounds: int = 25) -> None:
    torn = []
    observed = 0
    for r in range(rounds):
        sink = FailingSink(fail_on=1, only_timer_thread=True)
        rt = buffered_runtime(30, sink=sink, chunk_records=10_000,
                              flush_interval_ms=1, pause_s=0.001)
        try:
            rt.run()
        except BaseException:  # noqa: BLE001 - SinkFailed expected
            pass
        if sink.failed_on_thread is None:
            continue
        observed += 1
        time.sleep(0.01)
        pseqs = [x["seq"] for _t, b in sink.batches for x in b]
        tseqs = [x["seq"] for x in rt.trace.records]
        if pseqs != tseqs[:len(pseqs)]:
            torn.append((r, pseqs[:6], tseqs[:6]))
        if len(set(pseqs)) != len(pseqs):
            torn.append((r, "duplicate write after refusal"))
    rep.record("A/no-torn-account-after-an-off-thread-refusal", not torn,
               f"{observed}/{rounds} rounds refused off-thread; {torn[:2]}")


class SeqSink:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.written: list[int] = []
        self.after_close = 0
        self.closed = False

    def open_run(self, header):
        return self

    def write(self, records):
        with self.lock:
            if self.closed:
                self.after_close += 1
            self.written.extend(r["seq"] for r in records)


def case_b(rep: Report, rounds: int = 40) -> None:
    problems = []
    dropped_total = 0
    accepted_total = 0
    for r in range(rounds):
        sink = SeqSink()
        hub = _ObservationHub(profile="buffered", sink=sink, listeners=(),
                              chunk_records=5, flush_interval_ms=2)
        hub.bind({"run_id": f"x4a-10-{r}"})
        accepted: list[int] = []
        done = threading.Event()

        def producer():
            for seq in range(1, 400):
                try:
                    hub.persist({"seq": seq, "kind": "transition"})
                except BaseException:  # noqa: BLE001
                    break
                accepted.append(seq)
                if seq % 37 == 0:
                    time.sleep(0.0004)
            done.set()

        t = threading.Thread(target=producer)
        t.start()
        time.sleep(random.uniform(0.0005, 0.006))
        hub.close()
        with sink.lock:
            sink.closed = True
        done.wait(5.0)
        t.join(5.0)
        time.sleep(0.01)
        written = list(sink.written)
        if written != sorted(written):
            problems.append((r, "out of order"))
        if len(set(written)) != len(written):
            problems.append((r, "duplicate"))
        if sink.after_close:
            problems.append((r, f"{sink.after_close} write(s) after close()"))
        missing = [s for s in accepted if s not in set(written)]
        if missing and missing != accepted[-len(missing):]:
            problems.append((r, "drop is not a suffix of what persist accepted"))
        dropped_total += len(missing)
        accepted_total += len(accepted)
    rep.record("B/no-duplicate-reorder-or-write-after-close", not problems,
               f"{rounds} rounds; {problems[:3]}")
    rep.record("B/persist-after-close-still-writes",
               dropped_total > 0,
               f"{dropped_total}/{accepted_total} accepted records never "
               f"reached the sink (close() drops the buffer; probe only — "
               f"the Runtime never calls close() concurrently with persist)")


class ReentrantSink:
    """Contract-forbidden: feeds a record back into the hub from write()."""

    def __init__(self) -> None:
        self.written: list[int] = []
        self.hub = None
        self.fired = False

    def open_run(self, header):
        return self

    def write(self, records):
        self.written.extend(r["seq"] for r in records)
        if not self.fired and self.hub is not None:
            self.fired = True
            self.hub.persist({"seq": 999, "kind": "transition"})


def case_c(rep: Report) -> None:
    sink = ReentrantSink()
    hub = _ObservationHub(profile="buffered", sink=sink, listeners=(),
                          chunk_records=2, flush_interval_ms=0)
    hub.bind({"run_id": "x4a-10-c"})
    sink.hub = hub
    hub.persist({"seq": 1, "kind": "transition"})
    hub.persist({"seq": 2, "kind": "transition"})
    hub.close()
    written = sink.written
    clean = 999 in written and len(set(written)) == len(written)
    rep.record("C/reentrant-sink-writes-each-record-once", clean,
               f"sink saw {written} (duplicates="
               f"{sorted({s for s in written if written.count(s) > 1})}); hub "
               f"buffer left {hub._buffer} — LATENT ONLY: guarantees.md §6 "
               f"forbids a sink feeding back into execution, so no public "
               f"path reaches this")


def main() -> int:
    rep = Report("x4a-10 hub direct stress")
    case_a(rep)
    case_b(rep)
    case_c(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
