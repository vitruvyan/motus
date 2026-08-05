"""x4b-06 — how big is the finaliser leak, and does it also wedge?

Shape under test (contract guarantees.md §6, Listener: "Code that also holds
the Runtime can invoke its public cancellation surface"):

    box = {}
    def watchdog(record):
        if over_budget(record):
            box["d"].close("budget exceeded")
    rt = Runtime(spec, nodes, listeners=(watchdog,))
    d = rt.stream(state); box["d"] = d
    for record in d: ...

That makes the Runtime reach its own driver.  `weakref.finalize._registry` is
a module-global dict holding the finaliser's bound method — i.e. the Runtime —
so the driver stays reachable, its weakref never dies, the finaliser never
fires, the entry never leaves.  Nothing in the cycle is ever collected.
"""

from __future__ import annotations

import gc
import sys
import tracemalloc
import weakref
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import Runtime, State  # noqa: E402
from vitruvyan_motus.trace import Trace  # noqa: E402
from x4b_common import LINEAR, Report, passthrough  # noqa: E402


def one_request(consume: bool) -> None:
    """One service request: a Runtime whose watchdog listener holds the driver."""
    box: dict[str, Any] = {}

    def watchdog(record: dict[str, Any]) -> None:
        driver = box.get("d")
        if driver is not None and record["kind"] == "routing":
            driver.close("budget exceeded")

    rt = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(watchdog,),
    )
    d = rt.stream(State.empty("request"))
    box["d"] = d
    if consume:
        for _ in d:
            pass
    # the request ends: every local goes out of scope


def census() -> dict[str, int]:
    gc.collect()
    gc.collect()
    counts = {"Runtime": 0, "Trace": 0, "StreamDriver": 0}
    for obj in gc.get_objects():
        t = type(obj)
        if t is Runtime:
            counts["Runtime"] += 1
        elif t is Trace:
            counts["Trace"] += 1
        elif t.__name__ == "StreamDriver":
            counts["StreamDriver"] += 1
    counts["finalize_registry"] = len(weakref.finalize._registry)
    return counts


def main() -> int:
    report = Report("x4b-06 leak, quantified")
    print(f"# source: {sys.modules['vitruvyan_motus'].__file__}")
    print(f"# runtime.py mentions weakref: "
          f"{'weakref' in open(sys.modules[Runtime.__module__].__file__).read()}\n")

    for consume in (True, False):
        label = "consumed" if consume else "never-advanced"
        base = census()
        tracemalloc.start()
        snap0 = tracemalloc.take_snapshot()
        for _ in range(200):
            one_request(consume)
        after = census()
        snap1 = tracemalloc.take_snapshot()
        grew = sum(
            s.size_diff for s in snap1.compare_to(snap0, "filename")
        )
        tracemalloc.stop()
        delta = {k: after[k] - base[k] for k in base}
        report.record(
            f"200 {label} requests retain nothing",
            all(v == 0 for v in delta.values()),
            f"delta={delta} heap_growth={grew / 1024:.0f} KiB",
        )
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
