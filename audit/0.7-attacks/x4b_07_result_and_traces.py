"""x4b-07 — can a caller reach `_result_of` with an unfinished handle, and do
the new close/cancel paths ever emit a trace the contract validator refuses?

Part A: every exotic exception shape through run/arun/_run_from, checking that
an AssertionError from `_result_of` never escapes and that `handle.finished`
is always true when the caller reads it.

Part B: every terminal-producing shape, run through contract/validate.py.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus")
sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from contract import validate  # noqa: E402
from vitruvyan_motus import Runtime, State  # noqa: E402
from vitruvyan_motus.replay import ReplayEngine, TraceBundle  # noqa: E402
from x4b_common import LINEAR, LINEAR_DOC, Report, kinds, passthrough  # noqa: E402


class Weird(BaseException):
    pass


def run_and_report(report: Report, label: str, fn) -> Any:
    try:
        value = fn()
    except AssertionError as exc:
        report.record(f"A/{label}", False, f"AssertionError escaped: {exc}")
        return None
    except BaseException as exc:  # noqa: BLE001
        report.record(f"A/{label}", True, f"escaped {type(exc).__name__} (expected)")
        return exc
    report.record(f"A/{label}", True, f"returned {value}")
    return value


def part_a(report: Report) -> None:
    for exc_type in (KeyboardInterrupt, SystemExit, Weird, GeneratorExit,
                     asyncio.CancelledError, StopIteration, MemoryError):
        def boom(state: State, _e=exc_type) -> State:
            raise _e("from a node")

        rt = Runtime(LINEAR, {"a": boom, "b": passthrough, "c": passthrough})
        run_and_report(
            report, f"sync-run-node-raises-{exc_type.__name__}",
            lambda r=rt: r.run(State.empty("x")).status,
        )

        rt2 = Runtime(LINEAR, {"a": boom, "b": passthrough, "c": passthrough})
        run_and_report(
            report, f"arun-node-raises-{exc_type.__name__}",
            lambda r=rt2: asyncio.run(r.arun(State.empty("x"))).status,
        )

    # a node that raises during the *second* attempt of a retried node
    calls = {"n": 0}

    def flaky(state: State) -> State:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("first")
        raise KeyboardInterrupt("second")

    rt = Runtime(
        LINEAR, {"a": flaky, "b": passthrough, "c": passthrough}, max_attempts=3,
    )
    run_and_report(
        report, "retry-then-baseexception", lambda: rt.run(State.empty("x")).status
    )

    # a resume segment whose node raises BaseException
    good = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    ok = good.run(State.empty("seed"))
    bundle = TraceBundle(LINEAR, ok.trace)
    engine = ReplayEngine(bundle)

    def base_boom(state: State) -> State:
        raise KeyboardInterrupt("resume node")

    resumer = Runtime(LINEAR, {"a": base_boom, "b": base_boom, "c": base_boom})
    run_and_report(
        report, "resume-node-baseexception",
        lambda: engine.resume(resumer, run_id="resumed-1"),
    )


def collect_traces() -> dict[str, Any]:
    traces: dict[str, Any] = {}

    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    traces["plain-run"] = rt.run(State.empty("t")).trace

    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("t"))
    next(d)
    d.close("explicit close")
    traces["stream-close-midrun"] = d.trace

    # listener closes its own driver, consumer keeps iterating
    box: dict[str, Any] = {}

    def closer(record: dict[str, Any]) -> None:
        if record["kind"] == "run_started" and "d" in box:
            box["d"].close("listener stopped it")

    rt = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(closer,),
    )
    d = rt.stream(State.empty("t"))
    box["d"] = d
    list(d)
    traces["listener-close"] = d.trace

    # node closes its own driver
    nbox: dict[str, Any] = {}

    def canceller(state: State) -> State:
        if "d" in nbox:
            nbox["d"].close("node stopped it")
        return state

    rt = Runtime(LINEAR, {"a": canceller, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("t"))
    nbox["d"] = d
    list(d)
    traces["node-close"] = d.trace

    # close after a rejected reason (the latch defect)
    rt = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    d = rt.stream(State.empty("t"))
    next(d)
    try:
        d.close(None)  # type: ignore[arg-type]
    except TypeError:
        pass
    d.close("retry")
    traces["latch-then-retry"] = d.trace

    # async close from a supervisor while the consumer is inside __anext__
    async def supervised() -> Any:
        async def slow(state: State) -> State:
            await asyncio.sleep(0.05)
            return state

        art = Runtime(LINEAR, {"a": slow, "b": slow, "c": slow})
        ad = art.astream(State.empty("t"))

        async def consume() -> None:
            try:
                async for _ in ad:
                    pass
            except BaseException:  # noqa: BLE001
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.02)          # consumer is inside __anext__
        await ad.aclose("supervisor stop")
        await task
        return ad.trace

    traces["async-supervisor-aclose"] = asyncio.run(supervised())
    return traces


def part_b(report: Report) -> None:
    for label, trace in collect_traces().items():
        if trace is None:
            report.record(f"B/{label}", False, "no trace at all")
            continue
        doc = trace.to_dict()
        violations = validate.validate_trace(doc, spec=dict(LINEAR_DOC))
        report.record(
            f"B/{label}",
            not violations,
            f"kinds={kinds(trace)} violations="
            f"{[(v.rule, v.message[:70]) for v in violations]}",
        )


def main() -> int:
    report = Report("x4b-07 _result_of and trace validity")
    part_a(report)
    part_b(report)
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
