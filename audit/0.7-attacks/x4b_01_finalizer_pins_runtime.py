"""x4b-01 — does `weakref.finalize(driver, self._release_if_never_started, ...)`
make a Runtime that can reach its own driver immortal?

`weakref.finalize` stores its callback in the module-global
`weakref.finalize._registry`.  The callback here is a BOUND METHOD of the
Runtime, so the registry holds a strong reference to the Runtime for as long
as the driver is alive.  If the Runtime can reach the driver back — a listener
that closes its own driver, a node that cancels its own stream, a sink that
holds it — then:

    registry (global root) -> finalize -> bound method -> Runtime
                           -> listener/node closure -> driver

the driver is永 reachable, its weakref never dies, the finaliser never fires,
the registry entry never leaves.  Nothing is ever collected.

Before this commit there was no finaliser, so the Runtime<->driver cycle was
an ordinary cycle and gc reclaimed it.
"""

from __future__ import annotations

import gc
import sys
import weakref
from typing import Any

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import Runtime, State  # noqa: E402
from x4b_common import LINEAR, Report, passthrough  # noqa: E402


def build_listener_cycle() -> weakref.ref:
    """The canonical shape the new close() guard exists FOR: a listener that
    closes its own driver.  Returns a weakref to the Runtime."""
    box: dict[str, Any] = {}

    def closer(record: dict[str, Any]) -> None:
        driver = box.get("driver")
        if driver is not None and record["kind"] == "run_started":
            driver.close("listener stopped it")

    runtime = Runtime(
        LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough},
        listeners=(closer,),
    )
    driver = runtime.stream(State.empty("cycle"))
    box["driver"] = driver
    list(driver)                     # run to completion; driver latches closed
    return weakref.ref(runtime)


def build_node_cycle() -> weakref.ref:
    """A node that cancels its own stream — the other documented shape."""
    box: dict[str, Any] = {}

    def canceller(state: State) -> State:
        driver = box.get("driver")
        if driver is not None:
            driver.close("node stopped it")
        return state

    runtime = Runtime(
        LINEAR, {"a": canceller, "b": passthrough, "c": passthrough},
    )
    driver = runtime.stream(State.empty("cycle"))
    box["driver"] = driver
    list(driver)
    return weakref.ref(runtime)


def build_plain() -> weakref.ref:
    """Control: no Runtime -> driver edge at all."""
    runtime = Runtime(LINEAR, {"a": passthrough, "b": passthrough, "c": passthrough})
    driver = runtime.stream(State.empty("plain"))
    list(driver)
    del driver
    return weakref.ref(runtime)


def main() -> int:
    report = Report("x4b-01 finaliser pins the Runtime")

    before = len(weakref.finalize._registry)

    ref = build_plain()
    gc.collect()
    report.record(
        "control: runtime with no back-edge to its driver is collected",
        ref() is None,
        f"alive={ref() is not None}",
    )

    ref = build_listener_cycle()
    for _ in range(3):
        gc.collect()
    alive = ref() is not None
    report.record(
        "listener-closes-own-driver cycle is collected",
        not alive,
        f"runtime alive after 3x gc.collect(): {alive}",
    )

    ref = build_node_cycle()
    for _ in range(3):
        gc.collect()
    alive_node = ref() is not None
    report.record(
        "node-closes-own-driver cycle is collected",
        not alive_node,
        f"runtime alive after 3x gc.collect(): {alive_node}",
    )

    # Unbounded growth: build N of them and count what survives.
    refs = [build_listener_cycle() for _ in range(25)]
    for _ in range(3):
        gc.collect()
    survivors = sum(1 for r in refs if r() is not None)
    after = len(weakref.finalize._registry)
    report.record(
        "repeated listener cycles do not accumulate",
        survivors == 0,
        f"{survivors}/25 Runtimes still alive; "
        f"weakref.finalize._registry {before} -> {after}",
    )

    if survivors:
        # Show WHAT is holding it, to prove the registry is the root.
        victim = next(r() for r in refs if r() is not None)
        holders = gc.get_referrers(victim)
        kinds = sorted({type(h).__name__ for h in holders})
        print(f"\n    referrers of a surviving Runtime: {kinds}")
        for h in holders:
            if type(h).__name__ == "method":
                fin_holders = gc.get_referrers(h)
                print(f"    the bound method is held by: "
                      f"{sorted({type(x).__name__ for x in fin_holders})}")
        print(f"    weakref.finalize._registry size: {len(weakref.finalize._registry)}")
        print("    sample registry entries pointing at a Runtime: ", end="")
        n = sum(
            1
            for info in weakref.finalize._registry.values()
            if getattr(getattr(info.func, "__self__", None), "__class__", None) is Runtime
        )
        print(n)

    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
