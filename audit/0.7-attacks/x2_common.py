"""Shared fixtures for the x2_* asynchronous adversarial round.

Nothing here asserts anything; it only builds graphs and reports Runtime
lifecycle internals so each attack can print the same three facts:
``_running``, ``_has_started``, ``_pending_cancel_reason``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vitruvyan_motus import Decision, GraphSpec, Runtime, State

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x2-linear",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))


async def anode(state: State) -> State:
    await asyncio.sleep(0)
    return state


def snode(state: State) -> State:
    return state


def async_runtime(**kwargs: Any) -> Runtime:
    return Runtime(LINEAR, {"a": anode, "b": anode, "c": anode}, **kwargs)


def sync_runtime(**kwargs: Any) -> Runtime:
    return Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, **kwargs)


def lifecycle(runtime: Runtime) -> dict[str, Any]:
    return {
        "_running": runtime._running,
        "_has_started": runtime._has_started,
        "_pending": runtime._pending_cancel_reason,
        "_cancel_reason": runtime._cancel_reason,
    }


def kinds(trace: Any) -> list[str]:
    return [] if trace is None else [r["kind"] for r in trace.records]


class Report:
    """Accumulate PASS/FAIL rows so every script prints one table."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "FAIL", detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    def dump(self) -> int:
        width = max(len(r[0]) for r in self.rows)
        print(f"\n=== {self.title} ===")
        for name, verdict, detail in self.rows:
            print(f"{name.ljust(width)}  {verdict}  {detail}")
        failures = sum(1 for r in self.rows if r[1] == "FAIL")
        print(f"-- {len(self.rows)} checks, {failures} FAIL")
        return failures
