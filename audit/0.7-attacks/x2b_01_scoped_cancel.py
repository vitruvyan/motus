"""x2b-01 -- attack ``Runtime._run_scoped_cancel``.

The fix makes the identity of the per-run ``_trace_ref`` list stand for the
identity of the run:

    def cancel(reason: str) -> bool:
        if self._trace_ref is not trace_ref:
            return False
        return self.cancel(reason)

Two directions of attack.

FALSE NEGATIVE -- a driver that SHOULD cancel its own run and now cannot.
That is the failure a scoping fix invites, so every legitimate cancellation
path is re-walked: sync ``close()`` mid-run with ``active_attempt``, async
``aclose()`` mid-run, cancel from a listener, cancel after a sink-failure
``_replace_trace``, and everything ADR-008 §1 fixed.

FALSE POSITIVE -- the identity test passing when it should not.  ``_start``
sets ``_running = True`` under the lifecycle lock but assigns
``self._trace_ref`` *much* later, after ``_RunController``, ``_ObservationHub``,
``_refresh_identity()``, state validation, run_id minting and header
construction.  For that whole window ``self._trace_ref`` still points at the
PREVIOUS run's list, while ``_running`` already describes the NEW one.  A stale
driver firing in that window passes the identity test and binds to a run it has
nothing to do with -- X2-001, through the fix.

``_refresh_identity()`` calls ``motus_config()``, which is user code (ADR-008
§4: "Motus evaluates it ... each run start"), so the window is not merely
theoretical: it contains a documented user hook.
"""

from __future__ import annotations

import asyncio
import gc
import threading

from vitruvyan_motus import GraphSpec, Runtime, State
from x2b_common import (
    LINEAR,
    Report,
    anode,
    async_runtime,
    kinds,
    lifecycle,
    snode,
    sync_runtime,
)

R = Report("x2b_01 run-scoped cancellation")


# --------------------------------------------------------------------------- #
# FALSE NEGATIVES: legitimate cancellations must still land                    #
# --------------------------------------------------------------------------- #
def case_sync_close_lands_cancelled() -> None:
    rt = sync_runtime()
    driver = rt.stream(State.empty("live"))
    next(driver)  # run_started
    next(driver)  # attempt_started -- attempt 'a' is open
    driver.close("consumer stopped")
    terminal = driver.trace.records[-1]
    R.record(
        "sync close() on a live run -> run_cancelled",
        terminal["kind"] == "run_cancelled"
        and terminal["reason"] == "consumer stopped"
        and terminal["active_attempt"] == {"node": "a", "attempt": 1}
        and not rt._running,
        f"terminal={terminal['kind']} reason={terminal['reason']!r} "
        f"active_attempt={terminal['active_attempt']} running={rt._running}",
    )


async def case_async_aclose_lands_cancelled() -> None:
    rt = async_runtime()
    driver = rt.astream(State.empty("live"))
    await driver.__anext__()
    await driver.__anext__()
    await driver.aclose("consumer stopped")
    terminal = driver.trace.records[-1]
    R.record(
        "async aclose() on a live run -> run_cancelled",
        terminal["kind"] == "run_cancelled"
        and terminal["active_attempt"] == {"node": "a", "attempt": 1}
        and not rt._running,
        f"terminal={terminal['kind']} active_attempt={terminal['active_attempt']} "
        f"running={rt._running}",
    )


def case_close_from_a_listener_of_its_own_run() -> None:
    """The driver is closed from inside delivery of its own run's record --
    ``_trace_ref`` is bound, so scoping must not refuse this."""
    holder: dict = {}

    class Closer:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "done" not in holder:
                holder["done"] = True
                holder["returned"] = holder["driver"]._cancel("listener close")

    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(Closer(),))
    driver = rt.stream(State.empty("listener"))
    holder["driver"] = driver
    for _ in driver:
        pass
    terminal = driver.trace.records[-1]
    R.record(
        "scoped cancel from a listener of its own run",
        holder.get("returned") is True and terminal["kind"] == "run_cancelled",
        f"cancel_returned={holder.get('returned')} terminal={terminal['kind']}",
    )


def case_scoped_cancel_survives_replace_trace() -> None:
    """``_replace_trace`` mutates ``trace_ref[0]`` on every single record.  If
    the fix had keyed on the Trace object rather than the list, every driver
    would be scoped out after its first record.  Prove the list identity is
    stable across an entire run."""
    rt = sync_runtime()
    driver = rt.stream(State.empty("stable"))
    ref = rt._trace_ref
    identities = []
    traces = set()
    for _ in range(4):
        next(driver)
        identities.append(rt._trace_ref is ref)
        traces.add(id(rt._trace_ref[0]))
    still_cancels = driver._cancel("late but legitimate")
    driver.close("drain")
    R.record(
        "list identity stable while Trace object churns",
        all(identities) and len(traces) > 1 and still_cancels is True,
        f"ref_stable={all(identities)} distinct_trace_objects={len(traces)} "
        f"cancel_returned={still_cancels}",
    )


async def case_adr008_still_fixed() -> None:
    """Everything ADR-008 §1 closed must still be closed."""
    checks = {}
    rt = async_runtime()
    async with rt.astream(State.empty("first")) as driver:
        async for _ in driver:
            pass
    checks["exhausted_context_exit_is_noop"] = (
        await rt.arun(State.empty("second"))
    ).status == "completed"

    rt2 = sync_runtime()
    with rt2.stream(State.empty("first")) as d2:
        for _ in d2:
            pass
    checks["sync_exhausted_context_exit_is_noop"] = (
        rt2.run(State.empty("second")).status == "completed"
    )

    rt3 = sync_runtime()
    rt3.run(State.empty("one"))
    checks["idle_cancel_returns_False"] = rt3.cancel("idle") is False
    checks["idle_cancel_does_not_leak"] = (
        rt3.run(State.empty("two")).status == "completed"
    )

    rt4 = sync_runtime()
    checks["pre_first_run_cancel_returns_True"] = rt4.cancel("queued") is True
    checks["pre_first_run_cancel_honoured"] = (
        rt4.run(State.empty("one")).status == "cancelled"
    )
    R.record(
        "ADR-008 §1 guarantees still hold",
        all(checks.values()),
        f"{ {k: v for k, v in checks.items() if not v} or 'all true'}",
    )


def case_stale_driver_is_now_refused() -> None:
    """X2-001's original reproduction, expected to be closed by the fix."""
    rt = async_runtime()
    kept: list = []

    async def phase_one() -> None:
        driver = rt.astream(State.empty("one"), run_id="run-one")
        await driver.__anext__()
        kept.append(driver)

    asyncio.run(phase_one())
    driver = kept[0]
    box: dict = {}

    async def phase_two() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(state: State) -> State:
            entered.set()
            await release.wait()
            return state

        rt._nodes["a"] = gate
        task = asyncio.create_task(rt.arun(State.empty("two"), run_id="run-two"))
        await entered.wait()
        await driver.aclose("stale context exit")
        release.set()
        result = await task
        box["status"] = result.status
        box["run_id"] = result.trace.run["run_id"]

    asyncio.run(phase_two())
    R.record(
        "X2-001 stale driver vs a settled newer run",
        box["status"] == "completed",
        f"unrelated_run={box['run_id']} status={box['status']}",
    )


def case_trace_ref_identity_reuse() -> None:
    """Could two runs ever be handed the same list object?  A driver pins its
    own ``trace_ref`` through the closure and the trace getter, so the address
    cannot be recycled underneath it -- but check that a *released* run's
    address really can come back, which is what would make the test unsound if
    a driver ever stopped pinning it."""
    rt = sync_runtime()
    seen: list[int] = []
    for i in range(400):
        rt.run(State.empty(f"r{i}"))
        seen.append(id(rt._trace_ref))
    recycled = len(seen) - len(set(seen))

    # And with a driver alive, is its own ref ever handed to a second run?
    rt2 = sync_runtime()
    d = rt2.stream(State.empty("held"))
    held = rt2._trace_ref
    d.close("drain")
    collisions = 0
    for i in range(400):
        rt2.run(State.empty(f"s{i}"))
        gc.collect()
        if rt2._trace_ref is held:
            collisions += 1
    R.record(
        "no two runs share a _trace_ref object",
        collisions == 0,
        f"address_reuse_across_released_runs={recycled}/400 "
        f"collisions_with_a_pinned_ref={collisions}/400",
    )


def case_resume_rebinds_scope() -> None:
    """``_run_from`` goes through ``_start`` too, so it must mint a fresh
    ``_trace_ref`` and scope a pre-resume driver out."""
    rt = sync_runtime()
    driver = rt.stream(State.empty("pre"))
    ref_before = rt._trace_ref
    driver.close("drain")
    result = rt._run_from(
        State.empty("resumed"),
        start_node="b",
        resume_info={"source_run_id": "whatever", "reason": "test"},
        run_id="resumed-1",
    )
    ref_after = rt._trace_ref
    refused = driver._cancel("stale after resume")
    R.record(
        "resume mints a fresh scope",
        ref_after is not ref_before and refused is False and result.status == "completed",
        f"ref_rebound={ref_after is not ref_before} stale_cancel={refused} "
        f"resume={result.status}",
    )


# --------------------------------------------------------------------------- #
# FALSE POSITIVE: the _start window where _running is new but _trace_ref is old
# --------------------------------------------------------------------------- #
class WindowNode:
    """A node whose documented ``motus_config()`` hook lets a test observe --
    and pause inside -- ``_start``'s pre-``_trace_ref`` window."""

    def __init__(self) -> None:
        self.inside = threading.Event()
        self.release = threading.Event()
        self.arm = False
        self.observations: list = []

    def motus_config(self):
        if self.arm:
            self.arm = False
            self.inside.set()
            self.release.wait(10)
        return {"v": 1}

    def __call__(self, state: State) -> State:
        return state


def case_start_window_lets_a_stale_driver_bind() -> None:
    node = WindowNode()
    rt = Runtime(LINEAR, {"a": node, "b": node, "c": node})
    kept: list = []

    async def phase_one() -> None:
        driver = rt.astream(State.empty("one"), run_id="run-one")
        await driver.__anext__()
        kept.append(driver)

    asyncio.run(phase_one())  # loop teardown ends the run; driver stays open
    driver = kept[0]
    ref_of_run_one = rt._trace_ref
    box: dict = {}

    def second_run() -> None:
        node.arm = True
        box["result"] = asyncio.run(rt.arun(State.empty("two"), run_id="run-two"))

    worker = threading.Thread(target=second_run)
    worker.start()
    node.inside.wait(10)  # thread is inside _start, before _trace_ref is bound
    box["running_in_window"] = rt._running
    box["ref_is_still_run_one"] = rt._trace_ref is ref_of_run_one
    box["cancel_returned"] = asyncio.run(driver.aclose("stale context exit")) or True
    box["bound_reason"] = rt._cancel_reason
    node.release.set()
    worker.join(20)
    result = box["result"]
    R.record(
        "stale driver inside _start's pre-_trace_ref window",
        result.status == "completed",
        f"running_in_window={box['running_in_window']} "
        f"ref_still_previous_run={box['ref_is_still_run_one']} "
        f"reason_bound_during_window={box['bound_reason']!r} "
        f"unrelated_run={result.trace.run['run_id']} status={result.status}",
    )


def case_start_window_unassisted(iterations: int = 300) -> None:
    """The same window without any gate: two threads, one holding a stale
    driver, one starting runs.  Quantifies the natural hit rate."""
    hits = 0
    attempts = 0
    for i in range(iterations):
        rt = async_runtime()
        kept: list = []

        async def phase_one() -> None:
            d = rt.astream(State.empty("one"))
            await d.__anext__()
            kept.append(d)

        asyncio.run(phase_one())
        driver = kept[0]
        box: dict = {}
        barrier = threading.Barrier(2)

        def closer() -> None:
            barrier.wait()
            try:
                asyncio.run(driver.aclose("stale"))
            except BaseException:  # noqa: BLE001
                pass

        worker = threading.Thread(target=closer)
        worker.start()

        async def go():
            barrier.wait()
            return await rt.arun(State.empty("two"))

        try:
            result = asyncio.run(go())
            attempts += 1
            if result.status == "cancelled":
                hits += 1
        except RuntimeError:
            pass
        worker.join(5)
    R.record(
        f"unassisted stale-driver/startup race x{iterations}",
        hits == 0,
        f"unrelated_runs_cancelled={hits}/{attempts}",
    )


async def amain() -> None:
    await case_async_aclose_lands_cancelled()
    await case_adr008_still_fixed()


def main() -> int:
    case_sync_close_lands_cancelled()
    asyncio.run(amain())
    case_close_from_a_listener_of_its_own_run()
    case_scoped_cancel_survives_replace_trace()
    case_trace_ref_identity_reuse()
    case_resume_rebinds_scope()
    case_stale_driver_is_now_refused()
    case_start_window_lets_a_stale_driver_bind()
    case_start_window_unassisted()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
