"""x4a-04 — re-enter the Runtime from inside ``_ObservationHub.notify``.

``notify`` delivers to listeners while holding NO lock, synchronously, from
inside the run generator.  guarantees.md §6 blesses one re-entry ("Code that
also holds the Runtime can invoke its public cancellation surface") and is
silent about the rest.  Silence is not permission, so every re-entry below is
judged by the trace it leaves: a terminal must exist, it must be last, seq
must be gapless, every started attempt must be closed, and contract/validate.py
must accept the document.

Re-entries tried, at every record index of the run:
  cancel        rt.cancel() from the callback
  close         driver.close() on the driver being consumed
  next          next(driver) re-entrantly
  run           rt.run() re-entrantly
  stream        rt.stream() re-entrantly
  base_exc      raise KeyboardInterrupt (a BaseException) out of the callback
  mutate        deep-mutate the delivered record
  thread_cancel spawn a thread that cancels, and join it
  slow          block past flush_interval_ms so the Timer fires during notify

Run: .venv/bin/python .attack/x4a_04_listener_reentry.py
"""

from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, "/home/vitruvyan/motus/.attack")
sys.path.insert(0, "/home/vitruvyan/motus/contract")

import validate as contract_validate  # noqa: E402
from vitruvyan_motus import NodeFailed, SinkFailed  # noqa: E402
from x4a_common import (  # noqa: E402
    Report, RecordingSink, buffered_runtime, chain_doc, seq_report,
)

N = 8
SPEC_DOC = chain_doc(N)


class Reentrant:
    """Fires ``action`` once, on the ``at``-th record delivered."""

    def __init__(self, action: str, at: int) -> None:
        self.action = action
        self.at = at
        self.count = 0
        self.rt = None
        self.driver = None
        self.errors: list[str] = []
        self.kinds: list[str] = []

    def on_record(self, record):
        self.count += 1
        self.kinds.append(record["kind"])
        if self.count != self.at:
            return
        try:
            self._fire(record)
        except BaseException as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise

    def _fire(self, record):
        a = self.action
        if a == "cancel":
            self.rt.cancel("x4a-04 listener cancel")
        elif a == "close":
            if self.driver is not None:
                self.driver.close("x4a-04 listener close")
        elif a == "next":
            if self.driver is not None:
                next(self.driver)
        elif a == "run":
            self.rt.run()
        elif a == "stream":
            self.rt.stream()
        elif a == "base_exc":
            raise KeyboardInterrupt("x4a-04 listener BaseException")
        elif a == "mutate":
            record.clear()
            record["seq"] = -1
            record["kind"] = "poisoned"
        elif a == "thread_cancel":
            t = threading.Thread(
                target=self.rt.cancel, args=("x4a-04 thread cancel",))
            t.start()
            t.join()
        elif a == "slow":
            time.sleep(0.02)
        else:  # pragma: no cover
            raise AssertionError(a)


def judge(rep: Report, label: str, rt, sink: RecordingSink, listener,
          terminal_expected: bool) -> None:
    trace = rt.trace
    if trace is None:
        rep.record(f"{label}/trace-exists", False, "runtime has no trace")
        return
    records = trace.records
    tseqs = [r["seq"] for r in records]
    pseqs = [r["seq"] for _t, b in sink.batches for r in b]
    problems = []
    info = seq_report(tseqs)
    if not info["gapless"]:
        problems.append(f"T1 trace gaps {info}")
    if terminal_expected:
        tail = records[-1]["kind"] if records else None
        if tail not in ("run_completed", "run_failed", "run_cancelled"):
            problems.append(f"no terminal, tail={tail}")
        else:
            terminals = [r for r in records
                         if r["kind"].startswith("run_") and r["kind"] != "run_started"]
            if len(terminals) != 1:
                problems.append(f"{len(terminals)} terminal records")
            if pseqs != tseqs:
                problems.append(
                    f"persisted!=trace ({len(pseqs)} vs {len(tseqs)}) "
                    f"missing={sorted(set(tseqs) - set(pseqs))[:6]}")
    # Every started attempt is closed.
    opened = [(r["node"], r["attempt"]) for r in records
              if r["kind"] == "attempt_started"]
    closed = [(r["node"], r["attempt"]) for r in records
              if r["kind"] == "transition"]
    if terminal_expected and records and records[-1]["kind"] != "run_cancelled":
        if opened != closed:
            problems.append(f"unclosed attempts opened={opened} closed={closed}")
    # Contract validator.
    violations = contract_validate.validate_trace(
        trace.to_dict(), SPEC_DOC, expect_complete=terminal_expected)
    if violations:
        problems.append(f"validate.py: {[v.rule for v in violations][:6]} "
                        f"{str(violations[0])[:160]}")
    rep.record(label, not problems,
               "; ".join(problems) if problems
               else f"{len(records)} records, tail="
                    f"{records[-1]['kind'] if records else None}")


def run_case(rep: Report, action: str, at: int, mode: str) -> None:
    listener = Reentrant(action, at)
    sink = RecordingSink()
    rt = buffered_runtime(N, sink=sink, chunk_records=4, flush_interval_ms=5,
                          listeners=(listener,))
    listener.rt = rt
    label = f"{mode}/{action}@{at}"
    if mode == "run":
        try:
            rt.run()
        except (NodeFailed, SinkFailed):
            pass
        except BaseException as exc:  # noqa: BLE001
            rep.record(label, False, f"escaped {type(exc).__name__}: {exc}")
            return
        judge(rep, label, rt, sink, listener, terminal_expected=True)
        return
    driver = rt.stream()
    listener.driver = driver
    try:
        for _rec in driver:
            pass
    except (NodeFailed, SinkFailed):
        pass
    except BaseException as exc:  # noqa: BLE001
        rep.record(label, False, f"escaped {type(exc).__name__}: {exc}")
        return
    judge(rep, label, rt, sink, listener, terminal_expected=True)


def main() -> int:
    rep = Report("x4a-04 listener re-entry into the Runtime")
    # run_started + attempt/transition/routing per node + the terminal.
    total_records = 1 + 3 * N + 1
    indices = (1, 2, 5, total_records - 1, total_records)
    for action in ("cancel", "run", "base_exc", "mutate", "thread_cancel", "slow"):
        for at in indices:
            run_case(rep, action, at, "run")
    for action in ("cancel", "close", "next", "stream", "base_exc", "mutate"):
        for at in indices:
            run_case(rep, action, at, "stream")
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
