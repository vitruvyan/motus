"""x3_07 — Runtime reuse after every terminal, sync driver vs async driver.

`_managed_execute`'s `finally` is what returns a Runtime to the idle state.
The inversion put two new generators between the caller and that `finally`
(`_drive` for sync, the async generator `_adrive` for async). This walks the
whole matrix and reports, for each exit path, whether the same Runtime can
start another run — and whether the two drivers agree.

Any row where sync says YES and async says NO is an asymmetry the
`AsyncStreamDriver` docstring's "Same contract, same cancellation semantics"
does not survive.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vitruvyan_motus import Fact, GraphSpec, NodeFailed, Runtime, State  # noqa: E402

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "x3-reuse", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"},
              {"name": "c", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"},
                    "b": {"kind": "next", "to": "c"},
                    "c": {"kind": "terminal"}},
})


def ok(name):
    return lambda s: s.with_fact(Fact(name, "d", "x3", NOW))


def aok(name):
    async def node(s):
        await asyncio.sleep(0)
        return s.with_fact(Fact(name, "d", "x3", NOW))
    return node


def bad(_s):
    raise ValueError("planned")


async def abad(_s):
    await asyncio.sleep(0)
    raise ValueError("planned")


SYNC = {"a": ok("a"), "b": ok("b"), "c": ok("c")}
ASYNC = {"a": aok("a"), "b": aok("b"), "c": aok("c")}
SYNC_BAD = {"a": bad, "b": ok("b"), "c": ok("c")}
ASYNC_BAD = {"a": abad, "b": aok("b"), "c": aok("c")}

rows: list[tuple[str, str, str]] = []


def reusable_sync(rt) -> str:
    try:
        rt.run(State.empty("next"))
        return "YES"
    except RuntimeError as exc:
        return f"NO ({exc})"
    except NodeFailed:
        return "YES"


async def reusable_async(rt) -> str:
    try:
        await rt.arun(State.empty("next"))
        return "YES"
    except RuntimeError as exc:
        return f"NO ({exc})"
    except NodeFailed:
        return "YES"


def sync_paths() -> dict[str, str]:
    out = {}

    rt = Runtime(SPEC, SYNC)
    rt.run(State.empty("first"))
    out["after run_completed"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC_BAD)
    try:
        rt.run(State.empty("first"))
    except NodeFailed:
        pass
    out["after run_failed (NodeFailed)"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("first"))
    next(d)
    next(d)
    d.close("stopped")
    out["after explicit close()"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC)
    with rt.stream(State.empty("first")) as d:
        next(d)
    out["after context-manager exit"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC)
    for _ in rt.stream(State.empty("first")):
        break
    out["after abandoning the driver"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC)
    d = rt.stream(State.empty("first"))
    for _ in d:
        pass
    out["after full exhaustion"] = reusable_sync(rt)

    rt = Runtime(SPEC, SYNC)
    _ = rt.stream(State.empty("first"))  # created, never advanced, never closed
    out["after stream() never advanced"] = reusable_sync(rt)

    return out


async def async_paths() -> dict[str, str]:
    out = {}

    rt = Runtime(SPEC, ASYNC)
    await rt.arun(State.empty("first"))
    out["after run_completed"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC_BAD)
    try:
        await rt.arun(State.empty("first"))
    except NodeFailed:
        pass
    out["after run_failed (NodeFailed)"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC)
    d = rt.astream(State.empty("first"))
    await d.__anext__()
    await d.__anext__()
    await d.aclose("stopped")
    out["after explicit close()"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC)
    async with rt.astream(State.empty("first")) as d:
        await d.__anext__()
    out["after context-manager exit"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC)
    async for _ in rt.astream(State.empty("first")):
        break
    out["after abandoning the driver"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC)
    d = rt.astream(State.empty("first"))
    async for _ in d:
        pass
    out["after full exhaustion"] = await reusable_async(rt)

    rt = Runtime(SPEC, ASYNC)
    _ = rt.astream(State.empty("first"))
    out["after stream() never advanced"] = await reusable_async(rt)

    return out


def main() -> int:
    s = sync_paths()
    a = asyncio.run(async_paths())
    order = list(s)
    width = max(len(k) for k in order)
    print(f"{'exit path':<{width}}  {'sync':<8}  async")
    print("-" * (width + 40))
    mismatches = 0
    for key in order:
        sv, av = s[key], a[key]
        same = (sv == "YES") == (av == "YES")
        if not same:
            mismatches += 1
        flag = "" if same else "   <-- ASYMMETRY"
        print(f"{key:<{width}}  {sv[:8]:<8}  {av}{flag}")
    print(f"\n{mismatches} asymmetry(ies) between the two drivers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
