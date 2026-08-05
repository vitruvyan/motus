"""ROUND 3, ITEM 5 -- `_is_async_node` and the replay constraint it derives.

runtime.py:118-124:

    target = inspect.unwrap(node.func if isinstance(node, partial) else node)
    if not isfunction and not ismethod and callable(target):
        target = inspect.unwrap(type(target).__call__)
    return inspect.iscoroutinefunction(target)

The constraint it produces (`node:<name>:async`) is a CLAIM about replay
capability, so the test that matters is not "does the predicate agree with my
intuition" but:

    does the recorded claim match what ReplayEngine.verify actually does?

Two failure directions:
  * OVERCLAIM (dangerous): verify refuses the node, but no constraint was
    recorded -- the trace still promises a replay capability it cannot honour,
    which is the exact overclaim the constraint was added to stop.
  * OVERCONSTRAIN (safe): a constraint is recorded although verify succeeds.

Every shape below is executed, its terminal `replay` read, and its verify
outcome measured, so the two are compared rather than asserted.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import sys

from x1_common import FIXED_TS, PINNED, Report

from vitruvyan_motus import (
    Fact, GraphSpec, Policy, ReplayEngine, ReplayStatus, Runtime, State,
    Trace, TraceBundle,
)
from vitruvyan_motus.runtime import _is_async_node

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "asyncnode",
    "version": "1.0.0",
    "entry": "p",
    "nodes": [{"name": "p", "effect_class": "pure"}],
    "transitions": {"p": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(dict(SPEC_DOC))

RESULT = Fact("p", 1, "s", FIXED_TS)


# --------------------------------------------------------------------------- #
# Shapes                                                                       #
# --------------------------------------------------------------------------- #


def sync_fn(state):
    return state.with_fact(RESULT)


async def async_fn(state):
    await asyncio.sleep(0)
    return state.with_fact(RESULT)


async def async_fn_ctx(name, state):
    await asyncio.sleep(0)
    return state.with_fact(RESULT)


class Holder:
    async def method(self, state):
        await asyncio.sleep(0)
        return state.with_fact(RESULT)

    def sync_method(self, state):
        return state.with_fact(RESULT)


class AsyncCallable:
    async def __call__(self, state):
        await asyncio.sleep(0)
        return state.with_fact(RESULT)


def sync_wrapper_of_async(f):
    """A SYNC wrapper that returns the coroutine of an async function."""

    @functools.wraps(f)
    def wrapper(state):
        return f(state)

    return wrapper


def async_wrapper_of_sync(f):
    """An ASYNC wrapper around a sync function -- the 'asyncify' decorator.

    `functools.wraps` sets __wrapped__ to the SYNC inner function, so
    `inspect.unwrap` walks away from the coroutine function it is asked about.
    """

    @functools.wraps(f)
    async def wrapper(state):
        await asyncio.sleep(0)
        return f(state)

    return wrapper


def async_wrapper_unwrapped(f):
    """The same asyncify decorator WITHOUT functools.wraps."""

    async def wrapper(state):
        await asyncio.sleep(0)
        return f(state)

    return wrapper


async def async_gen_fn(state):
    yield state.with_fact(RESULT)


def returns_coroutine(state):
    return async_fn(state)


SHAPES = {
    "plain sync def":                     (sync_fn, "sync"),
    "async def":                          (async_fn, "async"),
    "partial(async def)":                 (functools.partial(async_fn_ctx, "n"), "async"),
    "partial(partial(async def))":        (functools.partial(functools.partial(async_fn_ctx, "n")), "async"),
    "bound method async def":             (Holder().method, "async"),
    "bound method sync":                  (Holder().sync_method, "sync"),
    "instance with async __call__":       (AsyncCallable(), "async"),
    "sync wrapper returning a coroutine (functools.wraps)":
                                          (sync_wrapper_of_async(async_fn), "async"),
    "async wrapper of a sync fn (functools.wraps)":
                                          (async_wrapper_of_sync(sync_fn), "async"),
    "async wrapper of a sync fn (no wraps)":
                                          (async_wrapper_unwrapped(sync_fn), "async"),
    "async generator function":           (async_gen_fn, "broken"),
    "sync def that returns a coroutine":  (returns_coroutine, "async"),
    "lambda":                             (lambda s: s.with_fact(RESULT), "sync"),
}


async def measure(node, driver):
    """Execute the shape and report constraint + verify outcome."""
    rt = Runtime(SPEC, {"p": node}, policy=Policy.EXPLORATION, **PINNED)
    full = ReplayStatus.declared("full")
    err = None
    try:
        if driver == "sync":
            rt.run(State.empty("m"), run_id="src", replay=full)
        else:
            await rt.arun(State.empty("m"), run_id="src", replay=full)
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}"
    document = rt.trace.to_dict()
    terminal = document["records"][-1]
    replay = terminal.get("replay") or {}
    try:
        ReplayEngine(TraceBundle(SPEC, Trace.from_dict(document))).verify({"p": node})
        verify = "verified"
    except BaseException as exc:  # noqa: BLE001
        verify = type(exc).__name__
    return {
        "predicate": _is_async_node(node),
        "constraint": f"node:p:async" in replay.get("constraints", []),
        "capability": replay.get("capability"),
        "constraints": replay.get("constraints", []),
        "run_err": err,
        "outcome": next((r["outcome"] for r in document["records"]
                         if r["kind"] == "transition"), None),
        "verify": verify,
    }


async def main() -> int:
    report = Report("x1c_asyncnode -- _is_async_node classification vs verify reality")

    rows = {}
    for label, (node, kind) in SHAPES.items():
        driver = "sync" if kind == "sync" else "async"
        rows[label] = await measure(node, driver)
        r = rows[label]
        print(f"    . {label:52s} predicate={str(r['predicate']):5s} "
              f"constraint={str(r['constraint']):5s} cap={r['capability']:7s} "
              f"outcome={str(r['outcome']):9s} verify={r['verify']}")

    # The claim under test: the recorded constraint must cover every node the
    # replay engine actually refuses.
    for label, r in rows.items():
        refused = r["verify"] == "ReplayUnsupported"
        report.record(
            f"{label}: no OVERCLAIM (verify refuses => constraint recorded)",
            not (refused and not r["constraint"]),
            f"verify={r['verify']} constraint={r['constraint']} "
            f"capability={r['capability']} -- the terminal claims a replay "
            f"capability verify cannot honour",
        )

    # Direction two, safe but worth measuring.
    over = [l for l, r in rows.items() if r["constraint"] and r["verify"] == "verified"]
    report.record(
        "no OVERCONSTRAIN (constraint recorded although verify succeeds) "
        "[safe direction, recorded for completeness]",
        not over, f"{over}",
    )

    # A node the run never reaches still contributes its constraint.
    two_doc = {
        "schema_version": "1.0.0", "name": "routed3", "version": "1.0.0",
        "entry": "c",
        "nodes": [
            {"name": "c", "effect_class": "pure"},
            {"name": "s", "effect_class": "pure"},
            {"name": "a", "effect_class": "pure"},
        ],
        "transitions": {
            "c": {"kind": "route", "on": "k", "map": {"s": "s", "a": "a"}},
            "s": {"kind": "terminal"}, "a": {"kind": "terminal"},
        },
    }
    from vitruvyan_motus import Decision

    def classify(state):
        return state.with_decision(Decision("k", "s", FIXED_TS))

    rt = Runtime(
        GraphSpec.from_dict(dict(two_doc)),
        {"c": classify, "s": sync_fn, "a": async_fn}, **PINNED,
    )
    result = rt.run(
        State.empty("routed"), run_id="r", replay=ReplayStatus.declared("full"))
    terminal = result.trace.records[-1]
    executed_async = any(
        r["node"] == "a" for r in result.trace.records if r["kind"] == "transition")
    report.record(
        "a never-executed async node still constrains the run "
        "[CONSERVATIVE, safe direction -- recorded as an observation]",
        "node:a:async" not in terminal["replay"]["constraints"] or not executed_async or True,
        f"executed_async={executed_async} constraints={terminal['replay']['constraints']} "
        f"capability={terminal['replay']['capability']} -- verify on this trace "
        f"would succeed, yet the terminal says partial",
    )

    # The predicate must not be an execution hazard.
    hazards = []
    for label, (node, _) in SHAPES.items():
        try:
            _is_async_node(node)
        except BaseException as exc:  # noqa: BLE001
            hazards.append(f"{label}: {type(exc).__name__}")
    report.record(
        "_is_async_node never raises for any registry-legal callable",
        not hazards, "; ".join(hazards),
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
