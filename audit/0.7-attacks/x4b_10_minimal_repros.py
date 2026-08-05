"""x4b-10 — the two findings, minimal and self-contained.

Run:  /home/vitruvyan/motus/.venv/bin/python .attack/x4b_10_minimal_repros.py
"""

from __future__ import annotations

import gc
import sys
import weakref
from typing import Any

from vitruvyan_motus import GraphSpec, Runtime, State

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "x4b-min", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"},
              {"name": "c", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"},
                    "b": {"kind": "next", "to": "c"},
                    "c": {"kind": "terminal"}},
})

FAILED = 0


def check(name: str, ok: bool, detail: str) -> None:
    global FAILED
    if not ok:
        FAILED += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}\n")


# ---------------------------------------------------------------- FINDING 1
def finding_1() -> None:
    """A Runtime that can reach its own driver is never collected."""
    def one() -> weakref.ref:
        box: dict[str, Any] = {}

        def watchdog(record: dict[str, Any]) -> None:      # guarantees.md §6
            d = box.get("d")
            if d is not None and record["kind"] == "routing":
                d.close("budget exceeded")

        def node(state: State) -> State:
            return state

        rt = Runtime(SPEC, {"a": node, "b": node, "c": node},
                     listeners=(watchdog,))
        d = rt.stream(State.empty("request"))
        box["d"] = d
        for _ in d:
            pass
        return weakref.ref(rt)

    refs = [one() for _ in range(50)]
    for _ in range(5):
        gc.collect()
    alive = sum(1 for r in refs if r() is not None)
    check(
        "FINDING 1 — 50 completed runs are fully collected",
        alive == 0,
        f"{alive}/50 Runtimes (with their Trace, State, hub and sink) are still "
        f"alive after 5x gc.collect(); weakref.finalize._registry holds "
        f"{len(weakref.finalize._registry)} entries",
    )


# ---------------------------------------------------------------- FINDING 2
def finding_2() -> None:
    """A close() whose reason is rejected burns the _requested latch, so the
    next close — including the one __exit__ makes — executes the graph instead
    of cancelling it."""
    executed: list[str] = []

    def make() -> Runtime:
        def a(state: State) -> State:
            executed.append("a")
            return state

        def b(state: State) -> State:
            executed.append("b")
            return state

        def c(state: State) -> State:
            executed.append("c")
            return state

        return Runtime(SPEC, {"a": a, "b": b, "c": c})

    # (i) explicit retry
    rt = make()
    d = rt.stream(State.empty("retry"))
    next(d)
    try:
        d.close(None)                       # type: ignore[arg-type]
    except TypeError:
        pass
    executed.clear()
    d.close("really stop")
    check(
        "FINDING 2a — close() after a rejected reason still cancels",
        d.trace.records[-1]["kind"] == "run_cancelled",
        f"terminal={d.trace.records[-1]['kind']!r}; "
        f"close() executed nodes {executed}",
    )

    # (ii) no retry at all — __exit__ does it
    rt = make()
    executed.clear()
    escaped = "none"
    try:
        with rt.stream(State.empty("ctx")) as d2:
            next(d2)
            d2.close(None)                  # type: ignore[arg-type]
    except TypeError as exc:
        escaped = f"{type(exc).__name__}: {exc}"
    check(
        "FINDING 2b — context exit after a rejected reason still cancels",
        d2.trace.records[-1]["kind"] == "run_cancelled",
        f"body raised {escaped}; terminal={d2.trace.records[-1]['kind']!r}; "
        f"__exit__ executed nodes {executed}",
    )


if __name__ == "__main__":
    print(f"# {sys.modules['vitruvyan_motus'].__file__}\n")
    finding_1()
    finding_2()
    print(f"-- {FAILED} FAIL")
    raise SystemExit(FAILED)
