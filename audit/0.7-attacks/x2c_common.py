"""Shared fixtures for the x2c_* round (patch fb30b8d)."""

from __future__ import annotations

import asyncio
from typing import Any

from vitruvyan_motus import GraphSpec, Runtime, State

LINEAR_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x2c-linear",
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


def async_runtime(**kw: Any) -> Runtime:
    return Runtime(LINEAR, {"a": anode, "b": anode, "c": anode}, **kw)


def sync_runtime(**kw: Any) -> Runtime:
    return Runtime(LINEAR, {"a": snode, "b": snode, "c": snode}, **kw)


def lifecycle(runtime: Runtime) -> dict[str, Any]:
    return {
        "_running": runtime._running,
        "_has_started": runtime._has_started,
        "_pending": runtime._pending_cancel_reason,
        "_cancel_reason": runtime._cancel_reason,
        "_active_attempt": runtime._active_attempt,
    }


def kinds(trace: Any) -> list[str]:
    return [] if trace is None else [r["kind"] for r in trace.records]


class TeardownCounter:
    """Counts how many times ``_managed_execute``'s finally actually runs.

    ``_ObservationHub.close`` is the first statement in that finally and is
    called from nowhere else, so counting it counts teardowns. Installed by
    monkeypatching the class in this test process only -- no file is edited.
    """

    def __init__(self) -> None:
        from vitruvyan_motus import observers

        self.module = observers
        self.original = observers._ObservationHub.close
        self.per_hub: dict[int, int] = {}
        counter = self

        def counted(hub_self) -> None:
            counter.per_hub[id(hub_self)] = counter.per_hub.get(id(hub_self), 0) + 1
            return counter.original(hub_self)

        self.patched = counted

    def __enter__(self) -> "TeardownCounter":
        self.module._ObservationHub.close = self.patched
        return self

    def __exit__(self, *exc) -> None:
        self.module._ObservationHub.close = self.original

    @property
    def counts(self) -> list[int]:
        return sorted(self.per_hub.values())

    def reset(self) -> None:
        self.per_hub.clear()


class Report:
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
