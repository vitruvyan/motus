"""x2c-02 -- attack ``_iterator_is_finished`` and re-verify the D-series.

    for attribute in ("gi_frame", "ag_frame", "cr_frame"):
        if hasattr(iterator, attribute):
            return getattr(iterator, attribute) is None
    return True  # not a generator: assume the exception ended it

Three questions.

(a) Does it still latch when it MUST?  ADR-008 §1 depends on exhaustion and
    execution failure closing the driver.  A probe that answers "not finished"
    too eagerly re-opens RA-001.

(b) Does it correctly refuse to latch on a clash?  That is the X2B-002 fix, and
    the synchronous twin (``ValueError: generator already executing`` from a
    reentrant listener) which was still failing at a72cdf1.

(c) What breaks for something that is not a native generator?  Both drivers are
    in ``__all__`` with public constructors that accept any
    ``Iterator``/``AsyncIterator``, so the fallback ``return True`` is
    reachable, and so is an attribute access that raises.
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import AsyncStreamDriver, NodeFailed, Runtime, State, StreamDriver
from vitruvyan_motus.observers import _iterator_is_finished
from x2c_common import LINEAR, Report, anode, async_runtime, kinds, lifecycle, snode, sync_runtime

R = Report("x2c_02 _iterator_is_finished")


# --------------------------------------------------------------------------- #
# (a) it must still latch                                                      #
# --------------------------------------------------------------------------- #
def case_latches_on_exhaustion() -> None:
    rt = sync_runtime()
    with rt.stream(State.empty("first")) as driver:
        for _ in driver:
            pass
        closed_at_exhaustion = driver._closed
    second = rt.run(State.empty("second")).status
    R.record(
        "latches on normal exhaustion (ADR-008 §1)",
        closed_at_exhaustion and second == "completed",
        f"closed={closed_at_exhaustion} second={second}",
    )


def case_latches_on_node_failure() -> None:
    def boom(state: State) -> State:
        raise RuntimeError("down")

    rt = Runtime(LINEAR, {"a": boom, "b": snode, "c": snode})
    driver = rt.stream(State.empty("first"))
    raised = "none"
    try:
        for _ in driver:
            pass
    except NodeFailed:
        raised = "NodeFailed"
    closed = driver._closed
    driver.__exit__(None, None, None)
    rt._nodes["a"] = snode
    second = rt.run(State.empty("second")).status
    R.record(
        "latches on execution failure",
        raised == "NodeFailed" and closed and second == "completed",
        f"raised={raised} closed={closed} second={second}",
    )


async def case_async_latches() -> None:
    rt = async_runtime()
    async with rt.astream(State.empty("first")) as driver:
        async for _ in driver:
            pass
        closed = driver._closed
    second = (await rt.arun(State.empty("second"))).status
    R.record(
        "async latches on exhaustion",
        closed and second == "completed",
        f"closed={closed} second={second}",
    )


def case_x2001_still_closed() -> None:
    """The full X2-001 reproduction, once more."""
    rt = async_runtime()
    kept: list = []

    async def phase_one() -> None:
        d = rt.astream(State.empty("one"), run_id="run-one")
        await d.__anext__()
        kept.append(d)

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
        box["result"] = await task

    asyncio.run(phase_two())
    R.record(
        "X2-001 stale driver still refused",
        box["result"].status == "completed",
        f"status={box['result'].status}",
    )


# --------------------------------------------------------------------------- #
# (b) it must NOT latch on a clash                                             #
# --------------------------------------------------------------------------- #
def case_sync_reentrant_clash() -> None:
    """The synchronous twin of X2-002, still failing at a72cdf1: a listener
    that pulls the driver it is being delivered from raises
    ``ValueError: generator already executing``."""
    holder: dict = {}

    class Reentrant:
        def on_record(self, record) -> None:
            if record["kind"] == "attempt_started" and "hit" not in holder:
                holder["hit"] = True
                try:
                    next(holder["driver"])
                except BaseException as exc:  # noqa: BLE001
                    holder["err"] = f"{type(exc).__name__}"

    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, listeners=(Reentrant(),))
    driver = rt.stream(State.empty("reentrant"))
    holder["driver"] = driver
    try:
        for _ in driver:
            pass
        outer = "exhausted"
    except BaseException as exc:  # noqa: BLE001
        outer = type(exc).__name__
    driver.close("after the clash")
    trace = kinds(driver.trace)
    life = lifecycle(rt)
    try:
        second = rt.run(State.empty("second")).status
    except BaseException as exc:  # noqa: BLE001
        second = type(exc).__name__
    R.record(
        "sync reentrant clash does not latch",
        trace and trace[-1].startswith("run_") and not life["_running"]
        and second == "completed",
        f"inner={holder.get('err')} outer={outer} terminal={trace[-1] if trace else None} "
        f"running={life['_running']} second={second}",
    )


async def case_async_two_readers() -> None:
    """X2B-002's minimal repro."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def gate(state: State) -> State:
        entered.set()
        await release.wait()
        return state

    rt = Runtime(LINEAR, {"a": gate, "b": anode, "c": anode})
    driver = rt.astream(State.empty("two-readers"))
    await driver.__anext__()
    await driver.__anext__()
    errors: list[str] = []

    async def reader(tag: str) -> None:
        try:
            await driver.__anext__()
        except BaseException as exc:  # noqa: BLE001
            errors.append(f"{tag}:{type(exc).__name__}")

    r1 = asyncio.create_task(reader("r1"))
    r2 = asyncio.create_task(reader("r2"))
    await entered.wait()
    await asyncio.sleep(0)
    closed_after_clash = driver._closed
    release.set()
    await asyncio.gather(r1, r2)
    await driver.aclose("consumer stopped")
    trace = kinds(driver.trace)
    try:
        second = (await asyncio.wait_for(rt.arun(State.empty("second")), 3)).status
    except BaseException as exc:  # noqa: BLE001
        second = type(exc).__name__
    R.record(
        "async two-reader clash does not latch",
        trace and trace[-1].startswith("run_") and second == "completed"
        and not closed_after_clash,
        f"errors={errors} closed_after_clash={closed_after_clash} "
        f"terminal={trace[-1] if trace else None} second={second}",
    )


# --------------------------------------------------------------------------- #
# (c) non-generator iterators                                                  #
# --------------------------------------------------------------------------- #
def case_probe_truth_table() -> None:
    def gen():
        yield 1

    async def agen():
        yield 1

    async def coro():
        return 1

    g = gen()
    live_gen = (next(g), g)[1]
    done_gen = gen()
    list(done_gen)
    a = agen()
    c = coro()
    c.close()

    class Plain:
        def __iter__(self):
            return self

        def __next__(self):
            raise ValueError("transient")

    class Wrapping:
        """A decorator shape: delegates to a generator but is not one."""

        def __init__(self, inner):
            self.inner = inner

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.inner)

    class Hostile:
        @property
        def gi_frame(self):
            raise KeyError("gi_frame explodes")

        def __next__(self):
            raise ValueError("transient")

    table = {
        "live generator": _iterator_is_finished(live_gen),
        "exhausted generator": _iterator_is_finished(done_gen),
        "live async generator": _iterator_is_finished(a),
        "closed coroutine": _iterator_is_finished(c),
        "plain iterator (no frame)": _iterator_is_finished(Plain()),
        "wrapping a live generator": _iterator_is_finished(Wrapping(live_gen)),
    }
    hostile = "no-raise"
    try:
        _iterator_is_finished(Hostile())
    except BaseException as exc:  # noqa: BLE001
        hostile = type(exc).__name__
    a.aclose().close()
    R.record(
        "probe truth table",
        table["live generator"] is False
        and table["exhausted generator"] is True
        and table["live async generator"] is False
        and table["wrapping a live generator"] is False,
        f"{table} hostile_gi_frame={hostile}",
    )


def case_public_constructor_wrapped_iterator() -> None:
    """StreamDriver is public.  Wrap a live machine in a non-generator and the
    probe's fallback latches the driver on a transient error, reopening
    X2-002's shape for anyone who composes around the driver."""
    rt = sync_runtime()
    inner = rt.stream(State.empty("wrapped"))

    class Wrapping:
        def __init__(self, target):
            self.target = target
            self.clash = True

        def __iter__(self):
            return self

        def __next__(self):
            if self.clash:
                self.clash = False
                raise ValueError("generator already executing")
            return next(self.target)

    cancels: list[str] = []
    driver = StreamDriver(
        Wrapping(inner._iterator), lambda r: cancels.append(r) or True, lambda: rt.trace
    )
    try:
        next(driver)
    except ValueError:
        pass
    latched = driver._closed
    driver.close("after the clash")
    trace = kinds(rt.trace)
    R.record(
        "wrapped iterator: probe falls back to latching",
        not latched,
        f"latched_on_transient_error={latched} cancel_calls={cancels} "
        f"terminal={trace[-1] if trace else None} running={rt._running}",
    )


async def amain() -> None:
    await case_async_latches()
    await case_async_two_readers()


def main() -> int:
    case_latches_on_exhaustion()
    case_latches_on_node_failure()
    asyncio.run(amain())
    case_x2001_still_closed()
    case_sync_reentrant_clash()
    case_probe_truth_table()
    case_public_constructor_wrapped_iterator()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
