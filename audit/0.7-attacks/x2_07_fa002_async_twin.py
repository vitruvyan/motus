"""Attack 7 -- FA-002's async twin.

FA-002 (open, issue #25): a cancellation queued before a run that then fails
inside ``_start`` is silently dropped AND can never be re-queued, because
``_has_started`` is latched before the ``try`` and the pending reason is
consumed and then cleared.

    with self._lifecycle_lock:
        ...
        self._has_started = True                      # latched here
        self._cancel_reason = self._pending_cancel_reason
        self._pending_cancel_reason = None            # consumed here
    try:
        ...                                            # can raise
    except BaseException:
        with self._lifecycle_lock:
            self._running = False
            self._cancel_reason = None                 # and discarded here

The question is whether the asynchronous entry points are worse, better, or
identical.  Three ways to fail inside ``_start`` are used: an over-long
``run_id`` (fails after the header is begun), a non-State state, and a
``motus_config()`` that raises (fails in ``_refresh_identity``, before any
run_id exists).  ``astream`` is included because it, unlike ``arun``, hands
back a driver -- a caller could plausibly believe cancellation survives there.
"""

from __future__ import annotations

import asyncio

from vitruvyan_motus import GraphSpec, Runtime, State
from x2_common import LINEAR, Report, lifecycle

R = Report("x2_07 FA-002 async twin")


async def apassthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


def spassthrough(state: State) -> State:
    return state


class ConfigNode:
    """A callable node whose motus_config() can be made to raise per run."""

    def __init__(self) -> None:
        self.explode = False

    def motus_config(self):
        if self.explode:
            raise RuntimeError("motus_config exploded")
        return {"k": 1}

    def __call__(self, state: State) -> State:
        return state


def _probe(label: str, start, fail, succeed) -> None:
    """`start` must raise; `succeed` must then run cleanly."""
    rt = start()
    queued = rt.cancel("queued before the first run")
    life_before = lifecycle(rt)
    try:
        fail(rt)
        raised = "none"
    except BaseException as exc:  # noqa: BLE001
        raised = type(exc).__name__
    life_after = lifecycle(rt)
    requeued = rt.cancel("re-queued after the failed start")
    life_requeue = lifecycle(rt)
    result = succeed(rt)
    R.record(
        label,
        result == "cancelled",
        f"cancel()->{queued} start_raised={raised} "
        f"pending_before={life_before['_pending']!r} "
        f"pending_after={life_after['_pending']!r} "
        f"requeue()->{requeued} pending_requeue={life_requeue['_pending']!r} "
        f"next_run={result}",
    )


def async_runtime() -> Runtime:
    return Runtime(LINEAR, {"a": apassthrough, "b": apassthrough, "c": apassthrough})


def sync_runtime() -> Runtime:
    return Runtime(LINEAR, {"a": spassthrough, "b": spassthrough, "c": spassthrough})


def config_runtime(nodes: dict) -> Runtime:
    return Runtime(LINEAR, nodes)


def main() -> int:
    # -- run_id too long ---------------------------------------------------
    _probe(
        "sync run(): pending cancel vs bad run_id",
        sync_runtime,
        lambda rt: rt.run(State.empty("x"), run_id="x" * 201),
        lambda rt: rt.run(State.empty("ok")).status,
    )
    _probe(
        "arun(): pending cancel vs bad run_id",
        async_runtime,
        lambda rt: asyncio.run(rt.arun(State.empty("x"), run_id="x" * 201)),
        lambda rt: asyncio.run(rt.arun(State.empty("ok"))).status,
    )
    _probe(
        "astream(): pending cancel vs bad run_id",
        async_runtime,
        lambda rt: rt.astream(State.empty("x"), run_id="x" * 201),
        lambda rt: asyncio.run(rt.arun(State.empty("ok"))).status,
    )
    # -- non-State state ---------------------------------------------------
    _probe(
        "arun(): pending cancel vs non-State state",
        async_runtime,
        lambda rt: asyncio.run(rt.arun({"not": "a state"})),
        lambda rt: asyncio.run(rt.arun(State.empty("ok"))).status,
    )
    # -- motus_config raising ----------------------------------------------
    def config_start() -> Runtime:
        a, b, c = ConfigNode(), ConfigNode(), ConfigNode()
        rt = config_runtime({"a": a, "b": b, "c": c})
        rt._x2_nodes = (a, b, c)  # type: ignore[attr-defined]
        return rt

    def config_fail(rt: Runtime) -> None:
        for node in rt._x2_nodes:  # type: ignore[attr-defined]
            node.explode = True
        asyncio.run(rt.arun(State.empty("x")))

    def config_succeed(rt: Runtime) -> str:
        for node in rt._x2_nodes:  # type: ignore[attr-defined]
            node.explode = False
        return asyncio.run(rt.arun(State.empty("ok"))).status

    _probe(
        "arun(): pending cancel vs raising motus_config",
        config_start,
        config_fail,
        config_succeed,
    )
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
