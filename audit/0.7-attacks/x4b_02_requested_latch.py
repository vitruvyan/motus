"""x4b-02 — the new `_requested` latch on StreamDriver.

    if not self._requested:
        self._requested = True      # latched BEFORE the cancel is attempted
        self._cancel(reason)

`Runtime.cancel` validates its reason: a non-str raises TypeError.  So the
latch can be set by a call whose cancellation never bound.  A caller who sees
the TypeError and retries with a good reason now skips the cancel entirely and
drains the run to COMPLETION.

Also probes: latch vs exhaustion, latch vs __exit__, latch vs failure, and
whether a second close can still rewrite the recorded reason.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import State  # noqa: E402
from x4b_common import Report, kinds, sync_runtime  # noqa: E402


def probe_typeerror_retry(report: Report) -> None:
    runtime = sync_runtime()
    driver = runtime.stream(State.empty("latch"))
    next(driver)                       # run_started; run is live

    try:
        driver.close(123)              # type: ignore[arg-type]
    except TypeError as exc:
        detail = f"first close raised {type(exc).__name__}"
    else:
        detail = "first close did NOT raise"
    latched = getattr(driver, "_requested", "<absent on this build>")
    print(f"    {detail}; _requested={latched} _closed={driver._closed}")

    driver.close("really stop now")    # the retry any caller makes
    trace = driver.trace
    terminal = trace.records[-1]["kind"]
    report.record(
        "close() retry after a rejected reason still cancels",
        terminal == "run_cancelled",
        f"terminal={terminal!r} reason={trace.records[-1].get('reason')!r} "
        f"kinds={kinds(trace)}",
    )
    report.record(
        "runtime released after the retry",
        not runtime._running,
        f"_running={runtime._running}",
    )


def probe_context_exit_after_typeerror(report: Report) -> None:
    """The same shape, but the retry is __exit__ — the guaranteed one."""
    runtime = sync_runtime()
    driver = runtime.stream(State.empty("ctx"))
    with driver:
        next(driver)
        try:
            driver.close(object())     # type: ignore[arg-type]
        except TypeError:
            pass
    trace = driver.trace
    terminal = trace.records[-1]["kind"]
    report.record(
        "context exit after a rejected reason still cancels",
        terminal == "run_cancelled",
        f"terminal={terminal!r} kinds={kinds(trace)}",
    )


def probe_normal_double_close(report: Report) -> None:
    runtime = sync_runtime()
    driver = runtime.stream(State.empty("double"))
    next(driver)
    driver.close("the real reason")
    driver.close("a later reason")
    terminal = driver.trace.records[-1]
    report.record(
        "a second close does not rewrite the reason",
        terminal["kind"] == "run_cancelled"
        and terminal["reason"] == "the real reason",
        f"{terminal['kind']}/{terminal.get('reason')!r}",
    )


def probe_close_after_exhaustion(report: Report) -> None:
    runtime = sync_runtime()
    driver = runtime.stream(State.empty("exhaust"))
    list(driver)
    driver.close("too late")
    report.record(
        "close after exhaustion is inert",
        driver.trace.records[-1]["kind"] == "run_completed"
        and not runtime._running,
        f"terminal={driver.trace.records[-1]['kind']} _running={runtime._running}",
    )


def main() -> int:
    report = Report("x4b-02 the _requested latch")
    probe_typeerror_retry(report)
    probe_context_exit_after_typeerror(report)
    probe_normal_double_close(report)
    probe_close_after_exhaustion(report)
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
