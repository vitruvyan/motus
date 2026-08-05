"""Shared fixtures for the x4b_* run-lifecycle adversarial round.

Same house style as x2_common: build graphs, report Runtime lifecycle
internals, accumulate PASS/FAIL rows.  Nothing here asserts.
"""

from __future__ import annotations

import asyncio
import gc
import weakref
from typing import Any

from vitruvyan_motus import GraphSpec, Runtime, State

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x4b-linear",
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


def passthrough(state: State) -> State:
    return state


async def apassthrough(state: State) -> State:
    await asyncio.sleep(0)
    return state


def sync_runtime(**kwargs: Any) -> Runtime:
    return Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough}, **kwargs
    )


def async_runtime(**kwargs: Any) -> Runtime:
    return Runtime(
        LINEAR, {"a": apassthrough, "b": apassthrough, "c": apassthrough}, **kwargs
    )


def lifecycle(runtime: Runtime) -> dict[str, Any]:
    handle = runtime._run
    return {
        "_running": runtime._running,
        "_has_started": runtime._has_started,
        "_pending": runtime._pending_cancel_reason,
        "_cancel_reason": runtime._cancel_reason,
        "handle.started": None if handle is None else handle.started,
        "handle.finished": None if handle is None else handle.finished,
    }


def kinds(trace: Any) -> list[str]:
    return [] if trace is None else [r["kind"] for r in trace.records]


def finalize_registry_size() -> int:
    return len(weakref.finalize._registry)


def live_runtimes() -> int:
    gc.collect()
    return sum(1 for obj in gc.get_objects() if type(obj) is Runtime)


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
