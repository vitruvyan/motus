"""ATTACK 3 -- can an `_Invoke` object ever reach a consumer?

Surfaces probed: stream()/astream() consumers, Listener.on_record, TraceSink,
RunResult.trace.records, and the raw machine itself (driven by hand so every
yielded object's type is enumerated, not just the ones the driver forwards).

Also probes the exact-type filter `type(item) is _Invoke` (runtime.py:190 and
runtime.py:238) with a synthetic machine, to establish whether the channel
exists at all and whether anything a node can do reaches it.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from x1_common import BEHAVIOURS, PINNED, Report, async_registry, sync_registry
import x1_matrix as M

from vitruvyan_motus import (
    GraphSpec, InMemoryTraceSink, Runtime, State,
)
from vitruvyan_motus.runtime import _Invoke, _adrive, _drive, _invoke_sync

LINEAR = {
    "schema_version": "1.0.0",
    "name": "leak",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}


class SpyListener:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def on_record(self, record):
        self.records.append(record)


def _looks_like_invoke(obj) -> bool:
    return isinstance(obj, _Invoke) or type(obj).__name__ == "_Invoke"


def enumerate_machine_yields(case_) -> list[str]:
    """Drive Runtime._execute by hand and record the type of every yield."""
    from vitruvyan_motus import State as S

    BEHAVIOURS.clear()
    BEHAVIOURS.update(case_["behaviours"]())
    rt = M.build(case_, sync_registry(case_["names"]))
    state = (case_["state"] or (lambda: S.empty(case_["name"])))()
    machine = rt._start(
        state, run_id="pinned", replay=None, copy_yields=False,
        start_node=rt._plan.entry, resume_info=None,
    )
    kinds: list[str] = []
    reply: Any = None
    while True:
        try:
            item = machine.send(reply)
        except StopIteration:
            break
        except BaseException:  # NodeFailed / SinkFailed terminate the machine
            break
        kinds.append(type(item).__name__)
        if type(item) is _Invoke:
            reply = _invoke_sync(item)
        else:
            reply = None
    return kinds


class _InvokeSubclass(_Invoke):
    """`_Invoke` is a frozen slots dataclass, but it is not final."""


def synthetic_machine(item):
    def gen():
        got = yield item
        yield {"kind": "sentinel", "reply": repr(got)}

    return gen()


async def main() -> int:
    report = Report("x1_leak -- _Invoke containment")

    # 1. Every consumer surface, over the whole case matrix.
    leaked: list[str] = []
    for case_ in M.CASES:
        name = case_["name"]
        state_factory = case_["state"] or (lambda: State.empty(name))
        names = case_["names"]

        for driver_name in ("run", "arun", "stream", "astream"):
            BEHAVIOURS.clear()
            BEHAVIOURS.update(case_["behaviours"]())
            spy = SpyListener()
            sink = InMemoryTraceSink()
            kw = dict(case_["kw"])
            kw.pop("_needs_sink", None)
            options = dict(durability_profile="synchronous")
            options.update(kw)
            options.update(sink=sink, listeners=(spy,))
            rt = Runtime(
                GraphSpec.from_dict(dict(case_["spec"])),
                sync_registry(names) if driver_name in ("run", "stream") else async_registry(names),
                **options, **PINNED,
            )
            yielded: list[Any] = []
            try:
                if driver_name == "run":
                    rt.run(state_factory(), run_id="pinned")
                elif driver_name == "arun":
                    await rt.arun(state_factory(), run_id="pinned")
                elif driver_name == "stream":
                    with rt.stream(state_factory(), run_id="pinned") as d:
                        yielded.extend(d)
                else:
                    async with rt.astream(state_factory(), run_id="pinned") as d:
                        async for rec in d:
                            yielded.append(rec)
            except BaseException:  # noqa: BLE001
                pass
            for surface, items in (
                ("yielded", yielded),
                ("listener", spy.records),
                ("sink", list(sink.records)),
                ("trace.records", list(rt.trace.records) if rt.trace else []),
            ):
                for obj in items:
                    if _looks_like_invoke(obj) or not isinstance(obj, dict):
                        leaked.append(f"{name}/{driver_name}/{surface}: {type(obj).__name__}")
    report.record("no _Invoke (and nothing non-dict) on any consumer surface",
                  not leaked, "; ".join(leaked[:5]))

    # 2. The machine itself: what types does it ever yield?
    seen_types: set[str] = set()
    for case_ in M.CASES:
        seen_types.update(enumerate_machine_yields(case_))
    report.record(
        "Runtime._execute yields only dict and _Invoke",
        seen_types <= {"dict", "_Invoke"},
        f"types={sorted(seen_types)}",
    )

    # 3. A node that returns an _Invoke instance.
    for label, returned in (
        ("_Invoke instance", _Invoke(lambda s: s, (), "a")),
        ("_Invoke subclass instance", _InvokeSubclass(lambda s: s, (), "a")),
    ):
        spy = SpyListener()
        rt = Runtime(
            GraphSpec.from_dict(dict(LINEAR)),
            {"a": (lambda s, _r=returned: _r), "b": lambda s: s},
            listeners=(spy,), **PINNED,
        )
        err = None
        try:
            rt.run(State.empty("ret"), run_id="pinned")
        except BaseException as exc:  # noqa: BLE001
            err = type(exc).__name__
        report.record(
            f"node returning an {label} does not leak it",
            not any(_looks_like_invoke(r) for r in spy.records)
            and not any(_looks_like_invoke(r) for r in rt.trace.records),
            "",
        )
        report.record(
            f"node returning an {label} becomes an ordinary raised attempt",
            err == "NodeFailed"
            and [r["kind"] for r in rt.trace.records][-1] == "run_failed",
            f"err={err} kinds={[r['kind'] for r in rt.trace.records]}",
        )

    # 4. Is `type(item) is _Invoke` defeatable *as a filter*?
    sub = _InvokeSubclass(lambda s: s, (), "a")
    out = list(_drive(synthetic_machine(sub), _invoke_sync))
    report.record(
        "sync driver filter is exact-type: a subclass would be forwarded as a record",
        out and out[0] is sub,
        f"forwarded={[type(o).__name__ for o in out]}",
    )
    aout = [x async for x in _adrive(synthetic_machine(sub), _ainvoke_noop)]
    report.record(
        "async driver filter is exact-type: same channel",
        aout and aout[0] is sub,
        f"forwarded={[type(o).__name__ for o in aout]}",
    )
    report.record(
        "…but nothing constructs an _Invoke subclass: the only construction "
        "site is runtime.py:728",
        _count_invoke_constructions() == 1,
        f"construction sites = {_count_invoke_constructions()}",
    )

    return report.emit()


async def _ainvoke_noop(request):
    return (None, None)


def _count_invoke_constructions() -> int:
    import pathlib
    import re

    text = (pathlib.Path(__file__).resolve().parents[1]
            / "src" / "vitruvyan_motus" / "runtime.py").read_text()
    return len(re.findall(r"(?<!class )\b_Invoke\(", text))


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
