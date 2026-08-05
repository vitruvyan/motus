"""INVENTED ATTACKS -- the surfaces where the two drivers really could differ:
durable-sink failure paths, listener misbehaviour, and *concurrent*
cancellation (a thread for `run`, a task for `arun`).

Cancellation is the one place the drivers do not share a mechanism: the sync
driver can only be cancelled from another OS thread, the async driver from
another task on the same loop. If the machine's cancellation checkpoints were
sensitive to that difference, the record streams would diverge here.
"""

from __future__ import annotations

import asyncio
import sys
import threading

from x1_common import FIXED_TS, PINNED, Report, diff, doc, load_validator

from vitruvyan_motus import Fact, GraphSpec, Runtime, SinkFailed, State

validate = load_validator()

LINEAR = {
    "schema_version": "1.0.0",
    "name": "sinks",
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


class FailAtNth:
    """A required sink that refuses the Nth record it is given."""

    def __init__(self, n: int, fail_open: bool = False) -> None:
        self.n = n
        self.fail_open = fail_open
        self.count = 0
        self.kinds: list[str] = []

    def open_run(self, header):
        if self.fail_open:
            raise OSError("cannot open the run session")
        return self

    def write(self, records):
        for record in records:
            self.count += 1
            if self.count == self.n:
                raise OSError(f"refused record {self.count}")
            self.kinds.append(record["kind"])


class FailAtKind:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.kinds: list[str] = []

    def open_run(self, header):
        return self

    def write(self, records):
        for record in records:
            if record["kind"] == self.kind:
                raise OSError(f"refused {self.kind}")
            self.kinds.append(record["kind"])


class Mutator:
    """A listener that mutates and then raises -- both must be isolated."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def on_record(self, record):
        self.kinds.append(record["kind"])
        try:
            record["kind"] = "TAMPERED"
            record["seq"] = -1
        except BaseException:
            pass
        raise RuntimeError("listener exploded")


def build(sink, listeners=()):
    return Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": lambda s: s, "b": lambda s: s, "c": lambda s: s},
        sink=sink,
        durability_profile="synchronous" if sink is not None else "in-memory",
        listeners=listeners, **PINNED,
    )


def observe(rt, err, sink, listener):
    return {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records] if rt.trace else None,
        "trace": doc(rt.trace) if rt.trace else None,
        "sink_kinds": list(getattr(sink, "kinds", [])),
        "listener_kinds": list(listener.kinds) if listener else None,
        "listener_failures": rt._hub.listener_failures if rt._hub else None,
        "running": rt._running,
    }


async def main() -> int:
    report = Report("x1_sinks -- durability, listeners and concurrent cancellation")

    # ---- sink failures, both drivers --------------------------------------
    scenarios = {
        "fail-at-record-3": lambda: FailAtNth(3),
        "fail-at-record-1": lambda: FailAtNth(1),
        "fail-on-run_completed": lambda: FailAtKind("run_completed"),
        "fail-on-open_run": lambda: FailAtNth(1, fail_open=True),
    }
    for label, factory in scenarios.items():
        s_sink = factory()
        rt = build(s_sink)
        err = None
        try:
            rt.run(State.empty("sink"), run_id="pinned")
        except BaseException as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}"
        sync_obs = observe(rt, err, s_sink, None)

        a_sink = factory()
        rta = build(a_sink)
        aerr = None
        try:
            await rta.arun(State.empty("sink"), run_id="pinned")
        except BaseException as exc:  # noqa: BLE001
            aerr = f"{type(exc).__name__}"
        async_obs = observe(rta, aerr, a_sink, None)

        d = diff(sync_obs, async_obs)
        report.record(f"sink {label}: run() == arun() exactly", not d, "; ".join(d[:4]))
        report.record(
            f"sink {label}: the surviving trace is contract-valid (in flight allowed)",
            validate.validate_trace(sync_obs["trace"], spec=LINEAR, expect_complete=False) == [],
            str(validate.validate_trace(sync_obs["trace"], spec=LINEAR, expect_complete=False)[:1]),
        )
        print(f"    . sink {label}: err={sync_obs['err']} kinds={sync_obs['kinds']} "
              f"sink_got={sync_obs['sink_kinds']}")

    # ---- a mutating, raising listener -------------------------------------
    m1, m2 = Mutator(), Mutator()
    rt = build(None, listeners=(m1,))
    rt.run(State.empty("listener"), run_id="pinned")
    sync_obs = observe(rt, None, None, m1)
    rta = build(None, listeners=(m2,))
    await rta.arun(State.empty("listener"), run_id="pinned")
    async_obs = observe(rta, None, None, m2)
    d = diff(sync_obs, async_obs)
    report.record("mutating+raising listener: run() == arun() exactly", not d, "; ".join(d[:4]))
    report.record(
        "mutating+raising listener cannot corrupt the trace",
        all(r["kind"] != "TAMPERED" and r["seq"] > 0 for r in rt.trace.records)
        and sync_obs["listener_failures"] == len(rt.trace.records),
        f"failures={sync_obs['listener_failures']}",
    )

    # ---- concurrent cancellation: a thread vs a task ----------------------
    ready = threading.Event()
    released = threading.Event()

    def blocking_node(state):
        ready.set()
        released.wait(5)
        return state.with_fact(Fact("k", 1, "s", FIXED_TS))

    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": blocking_node, "b": lambda s: s, "c": lambda s: s}, **PINNED,
    )

    def canceller():
        ready.wait(5)
        rt.cancel("external cancellation")
        released.set()

    thread = threading.Thread(target=canceller)
    thread.start()
    sync_result = rt.run(State.empty("cancel"), run_id="pinned")
    thread.join()
    sync_doc = doc(rt.trace)

    rta = None

    async def awaiting_node(state):
        await asyncio.sleep(0)
        rta.cancel("external cancellation")
        await asyncio.sleep(0)
        return state.with_fact(Fact("k", 1, "s", FIXED_TS))

    rta = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": awaiting_node, "b": lambda s: s, "c": lambda s: s}, **PINNED,
    )
    async_result = await rta.arun(State.empty("cancel"), run_id="pinned")
    async_doc = doc(rta.trace)

    d = diff(sync_doc["records"], async_doc["records"])
    report.record(
        "cancel from a thread (run) == cancel from a task (arun), record for record",
        not d, "; ".join(d[:4]),
    )
    report.record(
        "both land run_cancelled after the in-flight transition",
        sync_result.status == "cancelled" and async_result.status == "cancelled"
        and [r["kind"] for r in rt.trace.records][-1] == "run_cancelled",
        f"{[r['kind'] for r in rt.trace.records]}",
    )
    report.record(
        "both cancellation traces are contract-valid in both encodings",
        validate.validate_trace(sync_doc, spec=LINEAR) == []
        and validate.validate_jsonl(rt.trace.to_jsonl(), spec=LINEAR)[0] == []
        and validate.validate_trace(async_doc, spec=LINEAR) == [],
        "",
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
