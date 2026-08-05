"""x2c-05 -- does ``finally: machine.close()`` change any observable trace?

Emits the record stream and lifecycle for every scenario the new ``finally``
can touch, so the same script can be run against v0.6.1, 51e2439, a72cdf1 and
fb30b8d and the outputs diffed directly.  A lifecycle fix must not move a
single record.

Usage:
    PYTHONPATH=<tree>/src:.attack python .attack/x2c_05_trace_differential.py <label>
"""

from __future__ import annotations

import gc
import sys

from vitruvyan_motus import GraphSpec, NodeFailed, Policy, Runtime, State

LABEL = sys.argv[1] if len(sys.argv) > 1 else "working-tree"

LINEAR_DOC = {
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


def snode(state: State) -> State:
    return state


def kinds(trace):
    return [] if trace is None else [r["kind"] for r in trace.records]


def emit(name: str, **facts) -> None:
    body = " ".join(f"{k}={v!r}" for k, v in facts.items())
    print(f"{LABEL:>12} | {name:<34} | {body}", flush=True)


def rt() -> Runtime:
    return Runtime(LINEAR, {"a": snode, "b": snode, "c": snode})


def probe_run() -> None:
    r = rt()
    result = r.run(State.empty("run"))
    emit("run() to exhaustion", trace=kinds(result.trace), running=r._running)


def probe_stream_exhausted() -> None:
    r = rt()
    with r.stream(State.empty("stream")) as d:
        for _ in d:
            pass
    emit("stream() exhausted", trace=kinds(d.trace), running=r._running,
         closed=d._closed)


def probe_stream_close_midrun() -> None:
    r = rt()
    d = r.stream(State.empty("close"))
    next(d)
    next(d)
    d.close("consumer stopped")
    terminal = d.trace.records[-1]
    emit("stream().close() mid-run", trace=kinds(d.trace), running=r._running,
         reason=terminal.get("reason"), active=terminal.get("active_attempt"))


def probe_stream_abandoned() -> None:
    r = rt()
    d = r.stream(State.empty("abandon"))
    next(d)
    next(d)
    trace = kinds(r.trace)
    del d
    gc.collect()
    try:
        second = r.run(State.empty("second")).status
    except BaseException as exc:  # noqa: BLE001
        second = type(exc).__name__
    emit("stream() abandoned mid-run", trace=trace, running_after_gc=r._running,
         second=second)


def probe_node_failure() -> None:
    def boom(state: State) -> State:
        raise RuntimeError("down")

    r = Runtime(LINEAR, {"a": boom, "b": snode, "c": snode})
    raised = "none"
    try:
        r.run(State.empty("fail"))
    except NodeFailed:
        raised = "NodeFailed"
    emit("run() node failure", trace=kinds(r.trace), raised=raised,
         running=r._running)


def probe_base_exception() -> None:
    class Abort(BaseException):
        pass

    def hard(state: State) -> State:
        raise Abort("hard")

    r = Runtime(LINEAR, {"a": hard, "b": snode, "c": snode})
    raised = "none"
    try:
        r.run(State.empty("torn"))
    except Abort:
        raised = "Abort"
    emit("run() BaseException teardown", trace=kinds(r.trace), raised=raised,
         running=r._running)


def probe_exploration_route_miss() -> None:
    doc = dict(LINEAR_DOC)
    doc["name"] = "x2c-routed"
    doc["transitions"] = {
        "a": {"kind": "route", "on": "k", "map": {"go": "b"}},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    }
    spec = GraphSpec.from_dict(dict(doc))
    r = Runtime(spec, {"a": snode, "b": snode, "c": snode})
    result = r.run(State.empty("miss"))
    emit("route miss (strict)", trace=kinds(result.trace), status=result.status)


def probe_resume() -> None:
    r = rt()
    result = r._run_from(
        State.empty("resumed"), start_node="b",
        resume_info={"source_run_id": "src", "reason": "test"}, run_id="res-1",
    )
    emit("_run_from()", trace=kinds(result.trace), status=result.status,
         running=r._running)


if __name__ == "__main__":
    probe_run()
    probe_stream_exhausted()
    probe_stream_close_midrun()
    probe_stream_abandoned()
    probe_node_failure()
    probe_base_exception()
    probe_exploration_route_miss()
    probe_resume()
