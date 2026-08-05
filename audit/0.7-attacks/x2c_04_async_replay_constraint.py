"""x2c-04 -- the new ``("partial", "node:<name>:async")`` replay constraint.

``_refresh_identity`` now adds the constraint for any node
``inspect.iscoroutinefunction`` recognises, and ``_start`` feeds it to
``_control.downgrade_many``.  Two questions.

CANCELLATION / TEARDOWN.  ``_cancelled()`` and every terminal carry
``self._control.replay.to_dict()`` -- the downgraded status.  The run HEADER
carries ``self._control.declared_replay.to_dict()`` -- the status as declared,
before any downgrade.  So the constraint lives only in the terminal.  A run
that never reaches a terminal therefore has nowhere to record it, and a reader
of that trace sees the declared claim instead.

DETECTION.  ``inspect.iscoroutinefunction`` is a narrow test.  Anything that
produces an awaitable without being a coroutine function -- a plain ``def``
that returns a coroutine, an ``async def ... yield`` generator function, an
unwrappable decorator -- executes fine under ``arun`` but is invisible to the
constraint, so the run keeps a replay claim ``ReplayEngine`` cannot honour.
Invariant IV requires replay capability to be an explicit recorded property.
"""

from __future__ import annotations

import asyncio
import functools

from vitruvyan_motus import GraphSpec, Runtime, State
from x2c_common import LINEAR, Report, anode, kinds, snode

R = Report("x2c_04 async replay constraint")

SOLO_DOC = {
    "schema_version": "1.0.0",
    "name": "x2c-solo",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}
SOLO = GraphSpec.from_dict(dict(SOLO_DOC))


async def _module_level_real(state: State) -> State:
    await asyncio.sleep(0)
    return state


def module_level_facade(state: State):
    """A plain ``def`` that hands back a coroutine: awaitable, not a
    coroutine function, and defined at module level so it is not opaque."""
    return _module_level_real(state)


def _replay_of(trace) -> tuple[dict, dict]:
    header = trace.run["replay"]
    terminal = trace.records[-1].get("replay") if trace.records else None
    return header, terminal


def _constraints(status) -> list[str]:
    if not status:
        return []
    return [c for c in status.get("constraints", [])]


def case_completed_async_run() -> None:
    rt = Runtime(SOLO, {"a": anode})
    result = asyncio.run(rt.arun(State.empty("done")))
    header, terminal = _replay_of(result.trace)
    R.record(
        "completed async run records the constraint",
        "node:a:async" in _constraints(terminal),
        f"header={header} terminal={terminal}",
    )


def case_cancelled_async_run() -> None:
    rt = Runtime(SOLO, {"a": anode})
    rt.cancel("queued")
    result = asyncio.run(rt.arun(State.empty("cancelled")))
    header, terminal = _replay_of(result.trace)
    R.record(
        "cancelled async run records the constraint",
        result.status == "cancelled"
        and "node:a:async" in _constraints(terminal),
        f"status={result.status} terminal={terminal}",
    )


def case_cancelled_midrun_async() -> None:
    box: dict = {}

    async def go() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt = Runtime(SOLO, {"a": gate})
        driver = rt.astream(State.empty("midrun"))
        task = asyncio.create_task(driver.__anext__())
        await task
        await driver.aclose("stopped mid-run")
        box["trace"] = driver.trace

    asyncio.run(go())
    header, terminal = _replay_of(box["trace"])
    R.record(
        "aclose()-cancelled async run records the constraint",
        "node:a:async" in _constraints(terminal),
        f"terminal_kind={box['trace'].records[-1]['kind']} terminal={terminal}",
    )


def case_torn_down_async_run() -> None:
    """CancelledError tears the run down: no terminal exists to carry it."""
    box: dict = {}

    async def go() -> None:
        async def cancelled(state: State) -> State:
            raise asyncio.CancelledError()

        rt = Runtime(SOLO, {"a": cancelled})
        try:
            await rt.arun(State.empty("torn"))
        except asyncio.CancelledError:
            pass
        box["trace"] = rt.trace

    asyncio.run(go())
    trace = box["trace"]
    header, terminal = _replay_of(trace)
    everywhere = _constraints(header) + _constraints(terminal)
    R.record(
        "torn-down async run records the constraint somewhere",
        "node:a:async" in everywhere,
        f"kinds={kinds(trace)} header_replay={header} terminal_replay={terminal}",
    )


def case_torn_down_baseline_constraint() -> None:
    """The same shape with a pre-existing constraint (opaque_config), to show
    whether the loss is new with :async or inherent to header/terminal split."""

    class Opaque:
        async def __call__(self, state: State) -> State:
            raise asyncio.CancelledError()

    box: dict = {}

    async def go() -> None:
        rt = Runtime(SOLO, {"a": Opaque()})
        try:
            await rt.arun(State.empty("torn"))
        except asyncio.CancelledError:
            pass
        box["trace"] = rt.trace
        box["constraints"] = rt._identity_constraints

    asyncio.run(go())
    header, terminal = _replay_of(box["trace"])
    R.record(
        "torn-down run records opaque_config either",
        "node:a:opaque_config" in _constraints(header) + _constraints(terminal),
        f"runtime_constraints={box['constraints']} header_replay={header} "
        "(pre-existing constraint, same header/terminal split)",
    )


def case_detection_coverage() -> None:
    """Which async shapes does ``_is_async_node`` actually see?"""

    async def plain_async(state: State) -> State:
        return state

    def wrapper_returning_coroutine(state: State):
        return plain_async(state)

    @functools.wraps(plain_async)
    def wrapped_with_wraps(state: State):
        return plain_async(state)

    async def async_gen(state: State):
        yield state

    class AsyncCallable:
        async def __call__(self, state: State) -> State:
            return state

    async def two_arg(state: State, ctx) -> State:
        return state

    shapes = {
        "async def": plain_async,
        "def returning a coroutine": wrapper_returning_coroutine,
        "functools.wraps around async": wrapped_with_wraps,
        "async def with yield": async_gen,
        "callable with async __call__": AsyncCallable(),
        "functools.partial(async)": functools.partial(plain_async),
        "plain sync node": snode,
        "async def (state, ctx)": two_arg,
    }
    detected: dict[str, bool] = {}
    for label, node in shapes.items():
        try:
            rt = Runtime(SOLO, {"a": node})
            detected[label] = any(
                c == "node:a:async" for _, c in rt._identity_constraints
            )
        except BaseException as exc:  # noqa: BLE001
            detected[label] = f"rejected:{type(exc).__name__}"
    expected_true = (
        "async def", "functools.wraps around async",
        "callable with async __call__", "functools.partial(async)",
        "async def (state, ctx)",
    )
    misses = [k for k in expected_true if detected.get(k) is not True]
    undetected_but_runnable = [
        k for k in ("def returning a coroutine",) if detected.get(k) is not True
    ]
    R.record(
        "async detection covers every awaitable-producing shape",
        not misses and not undetected_but_runnable,
        f"{detected} misses={misses + undetected_but_runnable}",
    )


def case_undetected_shape_actually_runs() -> None:
    """A module-level ``def`` that returns a coroutine executes fine under
    arun -- the run is genuinely async and genuinely not verify-replayable --
    while the trace carries no degradation at all.  Module level matters: a
    closure would be ``opaque_config``-degraded for an unrelated reason."""
    rt = Runtime(SOLO, {"a": module_level_facade})
    result = asyncio.run(rt.arun(State.empty("facade")))
    header, terminal = _replay_of(result.trace)
    R.record(
        "undetected async node degrades the claim",
        result.status == "completed"
        and "node:a:async" in _constraints(terminal),
        f"status={result.status} terminal_replay={terminal} "
        "(the node awaited successfully, so the run IS async)",
    )


def case_sync_run_not_degraded() -> None:
    rt = Runtime(SOLO, {"a": snode})
    result = rt.run(State.empty("sync"))
    header, terminal = _replay_of(result.trace)
    R.record(
        "a purely synchronous run keeps its claim",
        "node:a:async" not in _constraints(terminal),
        f"terminal_replay={terminal}",
    )


CASES = (
    case_completed_async_run,
    case_cancelled_async_run,
    case_cancelled_midrun_async,
    case_torn_down_async_run,
    case_torn_down_baseline_constraint,
    case_sync_run_not_degraded,
    case_detection_coverage,
    case_undetected_shape_actually_runs,
)


def main() -> int:
    for case in CASES:
        try:
            case()
        except BaseException as exc:  # noqa: BLE001
            R.record(case.__name__, False, f"harness raised {type(exc).__name__}: {exc}")
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
