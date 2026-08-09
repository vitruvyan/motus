"""LENS concurrency 07 — the same two outcomes reached through ADR-008 §4's own
declared failure mode, so neither can be dismissed as caller error.

ADR-008 §4: "Motus evaluates [motus_config()] at Runtime construction and each
run start ... A failure is wrapped as NodeConfigurationError, a MotusError,
before run_started; no run trace exists."

That is a start which, by decision, claims the lifecycle and then raises.  Two
Runtimes, identical code, differing only in whether they had run before:

  fresh   -> the concurrent cancellation leaks forward onto a later run
             started by someone else, carrying that caller's reason
  used    -> the concurrent cancellation is discarded, cancel() having said
             True, and can never be lodged again
"""
import sys, threading, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.runtime import Runtime
from vitruvyan_motus.errors import NodeConfigurationError

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "conc07", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
})


class Node:
    """`a` blocks in motus_config (config is fetched); `b`'s config then fails."""

    def __init__(self, name, executed):
        self.name = name
        self.executed = executed
        self.mode = "ok"
        self.gate = None
        self.landed = None

    def motus_config(self):
        if self.mode == "slow":
            self.gate.set()
            self.landed.wait(5)
        if self.mode == "boom":
            raise RuntimeError("configuration service unavailable")
        return {"v": 1}

    def __call__(self, state):
        self.executed.append(self.name)
        return state


def trial(pre_run):
    executed = []
    a = Node("a", executed)
    b = Node("b", executed)
    gate, landed = threading.Event(), threading.Event()
    a.gate, a.landed = gate, landed
    rt = Runtime(SPEC, {"a": a, "b": b})

    if pre_run:
        assert rt.run(run_id="a-real-run").status == "completed"
        executed.clear()

    a.mode, b.mode = "slow", "boom"
    raised = []

    def doomed():
        try:
            rt.run(run_id="the-doomed-start")
        except BaseException as exc:
            raised.append(exc)

    t = threading.Thread(target=doomed)
    t.start()
    assert gate.wait(5), "never entered the window"
    bound = rt.cancel("supervisor: stop THIS run")
    landed.set()
    t.join(5)

    a.mode, b.mode = "ok", "ok"
    relodge = rt.cancel("supervisor: try again")
    later = rt.run(run_id="a-later-unrelated-run")
    reasons = [r["reason"] for r in later.trace.records if r["kind"] == "run_cancelled"]
    return {
        "raised": type(raised[0]).__name__,
        "cancel_returned": bound,
        "can_relodge": relodge,
        "later_status": later.status,
        "later_reason": reasons,
        "executed": list(executed),
    }


for pre in (False, True):
    label = "USED   " if pre else "FRESH  "
    print(label, trial(pre))
