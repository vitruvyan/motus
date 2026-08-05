"""x4a-07 — ``_timer_flush`` clears ``_timer`` unconditionally.

The hub tracks at most one pending flush::

    def _timer_flush(self):
        with self._lock:
            self._timer = None          # <-- unconditional
            ...

    def _schedule_timer_locked(self):
        if self._timer is not None or ...:
            return
        self._timer = threading.Timer(delay, self._timer_flush); ...start()

    def _cancel_timer_locked(self):
        timer, self._timer = self._timer, None
        if timer is not None: timer.cancel()

A Timer whose delay already elapsed is *running* — ``cancel()`` on it is a
no-op — and it then blocks on ``_lock``.  While it blocks, the runner thread
can flush inline (clearing ``_timer``) and arm a NEW Timer.  When the stale
callback finally gets the lock it writes ``self._timer = None`` over the new
Timer's handle.  The new Timer is still armed but no longer referenced, so:

  * ``_schedule_timer_locked`` immediately arms yet another one, and
  * ``close()`` — whose docstring is "Stop background scheduling after a run
    has reached a terminal" — can only cancel the one handle it can see.

This script measures how many Timer threads a single buffered run keeps alive,
and how many survive ``close()``.

  A  live Timer-thread high-water mark during one buffered run
  B  Timer threads still armed after run() returned (i.e. after close())
  C  flush racing the terminal: duplicate or missing writes (control)
  D  any sink write arriving after the run returned (control)

Run: .venv/bin/python .attack/x4a_07_timer_handle_loss.py
"""

from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from x4a_common import Report, RecordingSink, buffered_runtime  # noqa: E402


def _waiting(t: threading.Timer) -> bool:
    """True when this Timer is still counting down, not executing its callback.

    ``Timer.run`` sets ``finished`` only AFTER the callback returns, so
    ``finished.is_set()`` alone cannot tell a Timer that is still armed from
    one already blocked inside ``_timer_flush``.  Distinguishing the two is
    the whole point: only a *waiting* Timer is something ``close()`` was
    supposed to be able to cancel.
    """
    if t.finished.is_set() or t.ident is None:
        return False
    frame = sys._current_frames().get(t.ident)
    depth = 0
    while frame is not None and depth < 25:
        if frame.f_code.co_name == "_timer_flush":
            return False
        frame = frame.f_back
        depth += 1
    return True


def waiting_timers() -> list[threading.Timer]:
    return [t for t in threading.enumerate()
            if isinstance(t, threading.Timer) and _waiting(t)]


def case_ab(rep: Report) -> None:
    n, interval, chunk = 260, 20, 2
    peak_waiting = 0
    peak_orphans = 0
    idents: set[int] = set()
    stop = threading.Event()

    def watcher():
        nonlocal peak_waiting, peak_orphans
        while not stop.is_set():
            hub = rt._hub
            tracked = getattr(hub, "_timer", None)
            waiting = waiting_timers()
            peak_waiting = max(peak_waiting, len(waiting))
            orphans = [t for t in waiting if t is not tracked]
            peak_orphans = max(peak_orphans, len(orphans))
            for t in waiting:
                idents.add(t.ident)

    sink = RecordingSink()
    rt = buffered_runtime(n, sink=sink, chunk_records=chunk,
                          flush_interval_ms=interval, pause_s=0.0006)
    w = threading.Thread(target=watcher, daemon=True)
    w.start()
    result = rt.run()
    after = waiting_timers()
    stop.set()
    w.join(2.0)
    hub = rt._hub
    orphans_after = [t for t in after if t is not hub._timer]
    rep.record(
        "A/at-most-one-waiting-timer-per-hub", peak_waiting <= 1,
        f"high-water mark {peak_waiting} Timer threads counting down at once "
        f"for ONE hub (interval={interval}ms, chunk={chunk}, "
        f"{len(result.trace.records)} records, {len(idents)} distinct Timer "
        f"threads seen waiting)",
    )
    # The race-free discriminator: a hub tracks at most ONE handle, so two
    # Timers counting down at the same time proves one of them is unreachable
    # and therefore uncancellable by close().  Comparing a sampled
    # `hub._timer` against a separately sampled thread list is NOT proof —
    # the two reads are not atomic — so it is deliberately not used here.
    rep.record(
        "B/no-two-timers-counting-down-at-once",
        peak_waiting < 2 and len(orphans_after) == 0,
        f"peak_waiting={peak_waiting} (>=2 would prove a lost handle); "
        f"racy orphan sample peaked at {peak_orphans} and is NOT evidence; "
        f"{len(orphans_after)} still counting down after run() returned "
        f"(hub._closed={hub._closed})",
    )
    time.sleep(interval / 1000 * 3)


def case_cd(rep: Report, rounds: int = 30) -> None:
    """The flush that races the terminal record."""
    bad = []
    late_writes = 0
    for r in range(rounds):
        sink = RecordingSink()
        # interval ~= the time the last node takes, so the Timer is armed and
        # fires right around the terminal's forced flush.
        rt = buffered_runtime(12, sink=sink, chunk_records=10_000,
                              flush_interval_ms=2, pause_s=0.002)
        result = rt.run()
        before = len(sink.batches)
        pseqs = [x["seq"] for _t, b in sink.batches for x in b]
        tseqs = [x["seq"] for x in result.trace.records]
        if pseqs != tseqs:
            bad.append((r, f"persisted {pseqs[-4:]} vs trace {tseqs[-4:]}"))
        if len(set(pseqs)) != len(pseqs):
            bad.append((r, "duplicate seq written"))
        time.sleep(0.02)
        if len(sink.batches) != before:
            late_writes += 1
    rep.record("C/terminal-flush-race-clean", not bad, f"{rounds} rounds; {bad[:3]}")
    rep.record("D/no-write-after-run-returned", late_writes == 0,
               f"{late_writes}/{rounds} rounds saw a write land after run() returned")


def main() -> int:
    rep = Report("x4a-07 Timer handle loss in _ObservationHub")
    case_ab(rep)
    case_cd(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(0 if main() == 0 else 1)
