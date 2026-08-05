"""x4b-08 — falsification pass on the finaliser leak.

Things that could make x4b-01/06 wrong:
  (a) gc simply needs more passes;
  (b) tracemalloc or the census itself is holding the objects;
  (c) it is my `box` closure, not the finaliser;
  (d) it would go away without the listener back-edge.

The decisive test: clear `weakref.finalize._registry` (nothing else) and collect
again.  If everything is reclaimed, the registry IS the root and the finaliser
is the cause.  Also checks astream, and whether the leaked Runtime drags its
TraceSink along.
"""

from __future__ import annotations

import gc
import sys
import weakref
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import InMemoryTraceSink, Runtime, State  # noqa: E402
from x4b_common import LINEAR, Report, passthrough  # noqa: E402


def make(kind: str) -> tuple[weakref.ref, weakref.ref, weakref.ref]:
    """One Runtime whose listener holds its own driver.  Returns weakrefs to
    the Runtime, the driver and the sink."""
    box: dict[str, Any] = {}

    def watchdog(record: dict[str, Any]) -> None:
        d = box.get("d")
        if d is not None and record["kind"] == "routing":
            d.close("budget exceeded")

    class Sink:                      # weak-referenceable stand-in for a real sink
        def __init__(self): self.inner = InMemoryTraceSink()
        def open_run(self, header): return self.inner.open_run(header)

    sink = Sink()
    rt = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(watchdog,), sink=sink,
    )
    if kind == "sync":
        d = rt.stream(State.empty("req"))
        box["d"] = d
        for _ in d:
            pass
    else:
        d = rt.astream(State.empty("req"))
        box["d"] = d
    return weakref.ref(rt), weakref.ref(d), weakref.ref(sink)


def main() -> int:
    report = Report("x4b-08 falsifying the finaliser leak")
    print(f"# source: {sys.modules['vitruvyan_motus'].__file__}\n")

    for kind in ("sync", "async"):
        rt_ref, d_ref, sink_ref = make(kind)
        for _ in range(10):                      # (a) more passes
            gc.collect()
        alive = (rt_ref() is not None, d_ref() is not None, sink_ref() is not None)
        report.record(
            f"{kind}: 10x gc.collect() reclaims runtime/driver/sink",
            not any(alive),
            f"(runtime, driver, sink) alive = {alive}",
        )

        # (c)+(d): the same cycle WITHOUT any Runtime->driver back-edge
        def plain() -> tuple[weakref.ref, weakref.ref]:
            rt = Runtime(
                LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough}
            )
            d = rt.stream(State.empty("plain"))
            for _ in d:
                pass
            return weakref.ref(rt), weakref.ref(d)

        p_rt, p_d = plain()
        gc.collect()
        report.record(
            f"{kind}: control without the back-edge is reclaimed",
            p_rt() is None and p_d() is None,
            f"(runtime, driver) alive = ({p_rt() is not None}, {p_d() is not None})",
        )

        # (b): the decisive one — drop ONLY the registry entries and re-collect.
        if any(alive):
            victims = [
                f for f, info in list(weakref.finalize._registry.items())
                if getattr(info.func, "__self__", None) is rt_ref()
            ]
            print(f"    registry entries whose bound method IS this Runtime: "
                  f"{len(victims)}")
            for f in victims:
                weakref.finalize._registry.pop(f, None)
            gc.collect()
            gc.collect()
            now = (rt_ref() is not None, d_ref() is not None, sink_ref() is not None)
            report.record(
                f"{kind}: clearing ONLY the finalize registry frees everything",
                not any(now),
                f"after registry purge, (runtime, driver, sink) alive = {now} "
                f"-> the registry was the root",
            )
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
