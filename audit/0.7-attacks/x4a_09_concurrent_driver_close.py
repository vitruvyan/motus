"""x4a-09 — two closers, one driver.

``StreamDriver.close`` guards the cancellation reason with a plain attribute
(observers.py as of sha256 a8a1db30…)::

    if self._closed: return
    if not self._requested:
        self._cancel(reason)
        self._requested = True      # not atomic with the test above
    if _iterator_is_running(self._iterator): return
    try:
        while True: next(self._iterator)
    except StopIteration: pass
    finally: self._closed = True

The comment above ``_requested`` says a second close "must not overwrite the
reason the trace will attribute it to".  Two threads closing at once can both
see ``_requested`` False, and both then enter the drain loop, where only
``StopIteration`` is caught — ``next()`` on a generator another thread is
already inside raises ``ValueError``.

  A  do two concurrent closes ever attribute the trace to the *second* reason?
  B  does ``close()`` ever raise out to its caller?
  C  is the run still driven to exactly one ``run_cancelled`` terminal?
  D  same three questions for a listener that closes while another thread does

Run: .venv/bin/python .attack/x4a_09_concurrent_driver_close.py
"""

from __future__ import annotations

import sys
import threading

sys.path.insert(0, "/home/vitruvyan/motus/.attack")
sys.path.insert(0, "/home/vitruvyan/motus/contract")

import validate as contract_validate  # noqa: E402
from x4a_common import Report, RecordingSink, buffered_runtime, chain_doc  # noqa: E402

N = 14
SPEC_DOC = chain_doc(N)
FIRST = "closer-A"
SECOND = "closer-B"


def one(rounds: int, with_listener: bool):
    wrong_reason = 0
    misattributed: list[str] = []
    escapes: list[str] = []
    bad_trace: list[str] = []
    for _ in range(rounds):
        sink = RecordingSink()
        holder: dict[str, object] = {}

        class Closer:
            def on_record(self, record):
                if with_listener and record["seq"] == 10:
                    d = holder.get("driver")
                    if d is not None:
                        try:
                            d.close("closer-listener")
                        except BaseException as exc:  # noqa: BLE001
                            escapes.append(f"listener: {type(exc).__name__}: {exc}")

        rt = buffered_runtime(N, sink=sink, chunk_records=4,
                              flush_interval_ms=2, pause_s=0.0006,
                              listeners=((Closer(),) if with_listener else ()))
        driver = rt.stream()
        holder["driver"] = driver
        for i, _rec in enumerate(driver):
            if i >= 5:
                break
        # Count how many closers get past the `not self._requested` arm.  The
        # comment there says the second one must not: "must not overwrite the
        # reason the trace will attribute it to".  Wrapping the driver's own
        # bound callable measures that without touching the product.
        inner_cancel = driver._cancel
        cancel_calls: list[str] = []
        cancel_lock = threading.Lock()

        def counting_cancel(reason: str):
            with cancel_lock:
                cancel_calls.append(reason)
            return inner_cancel(reason)

        object.__setattr__(driver, "_cancel", counting_cancel)
        closers = 8
        barrier = threading.Barrier(closers)

        def closer(reason):
            barrier.wait()
            try:
                driver.close(reason)
            except BaseException as exc:  # noqa: BLE001
                escapes.append(f"{reason}: {type(exc).__name__}: {exc}")

        threads = [
            threading.Thread(target=closer, args=(f"closer-{i}",))
            for i in range(closers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5.0)
            if t.is_alive():
                bad_trace.append("closer thread wedged")
        trace = rt.trace
        records = trace.records
        terminals = [r for r in records
                     if r["kind"] in ("run_completed", "run_failed", "run_cancelled")]
        if len(terminals) != 1:
            bad_trace.append(f"{[r['kind'] for r in terminals]} terminals")
        elif terminals[0]["kind"] == "run_cancelled":
            reason = terminals[0]["reason"]
            if cancel_calls and reason != cancel_calls[0]:
                misattributed.append(
                    f"trace says {reason!r}; the cancellation that actually "
                    f"stopped the run was {cancel_calls[0]!r} "
                    f"(calls: {cancel_calls})")
        seqs = [r["seq"] for r in records]
        if seqs != list(range(1, len(seqs) + 1)):
            bad_trace.append("T1 gaps")
        v = contract_validate.validate_trace(trace.to_dict(), SPEC_DOC, True)
        if v:
            bad_trace.append(f"validate.py {[x.rule for x in v][:4]}")
        pseqs = [r["seq"] for _t, b in sink.batches for r in b]
        if pseqs != seqs:
            bad_trace.append(f"persisted!=trace {len(pseqs)}/{len(seqs)}")
        # Both closers ran; whichever bound first owns the reason.  Two
        # threads entering the `not self._requested` arm means the second
        # cancel() call landed on the run too.
        if rt._cancel_reason is not None:
            bad_trace.append("cancel reason left behind after the run")
        if len(cancel_calls) > 1:
            wrong_reason += 1
    return wrong_reason, misattributed, escapes, bad_trace


def main() -> int:
    rep = Report("x4a-09 concurrent StreamDriver.close()")
    for label, with_listener in (("two-threads", False),
                                 ("two-threads+listener", True)):
        rounds = 120
        wrong, misattr, escapes, bad = one(rounds, with_listener)
        rep.record(f"{label}/close-never-raises", not escapes,
                   f"{len(escapes)} escapes; {escapes[:3]}")
        rep.record(f"{label}/trace-is-clean", not bad,
                   f"{len(bad)} problems; {bad[:3]}")
        rep.record(f"{label}/reason-guard-holds", wrong == 0,
                   f"{wrong}/{rounds} rounds had >1 closer reach _cancel()")
        rep.record(f"{label}/trace-names-the-cancel-that-stopped-it",
                   not misattr,
                   f"{len(misattr)}/{rounds} rounds misattributed; "
                   f"{misattr[:2]}")
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
