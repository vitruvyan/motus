"""ROUND 2, ITEM 2 -- attack the `ReplayEngine.verify` guard.

replay.py:374-390 now reads:

    returned = node(attempt, ctx) if len(positional) == 2 else node(attempt)
    if inspect.isawaitable(returned):
        closer = getattr(returned, "close", None)
        if callable(closer): closer()
        raise ReplayError("... is asynchronous; verify replay drives nodes
                           synchronously and cannot re-execute it")
except (ReplayMismatch, ReplayError):
    raise

Two things to attack:
  * the WIDENED except clause. `ReplayMismatch` and `UnsafeResume` are both
    subclasses of `ReplayError` (errors.py:119/123/135), so
    `except (ReplayMismatch, ReplayError)` is simply `except ReplayError` --
    strictly wider than the `except ReplayMismatch` it replaced. Any node that
    raises a `ReplayError` (public API: `from vitruvyan_motus import
    ReplayError`) now escapes verify instead of being matched against the
    recorded `error.type`.
  * the DETECTION predicate. `inspect.isawaitable` is False for an async
    generator object, so an `async def ... yield` node still slips past the
    guard into the false-ReplayMismatch path the fix exists to prevent.

Everything is measured against the pre-fix tree (51e2439) in a subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")
PREFIX_SRC = os.environ.get("MOTUS_PREFIX_SRC", f"{SCRATCH}/prefix/src")

from x1_common import FIXED_TS, PINNED, Report, diff  # noqa: E402

from vitruvyan_motus import (  # noqa: E402
    Fact, GraphSpec, Policy, ReplayEngine, ReplayError, ReplayMismatch,
    Runtime, State, Trace, TraceBundle, UnsafeResume,
)

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "verify",
    "version": "1.0.0",
    "entry": "p",
    "nodes": [
        {"name": "p", "effect_class": "pure"},
        {"name": "e", "effect_class": "recorded_effect"},
    ],
    "transitions": {"p": {"kind": "next", "to": "e"}, "e": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(dict(SPEC_DOC))


def ok_pure(state):
    return state.with_fact(Fact("p", 1, "s", FIXED_TS))


def ok_effect(state):
    return state.with_fact(Fact("e", 2, "s", FIXED_TS))


def raises_replay_error(state):
    raise ReplayError("a node's own domain error that happens to be a ReplayError")


def raises_unsafe_resume(state):
    raise UnsafeResume("a node's own UnsafeResume")


def raises_replay_mismatch(state):
    raise ReplayMismatch("p", 3, "field")


def raises_value_error(state):
    raise ValueError("an ordinary domain failure")


async def async_pure(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("p", 1, "s", FIXED_TS))


async def async_gen_pure(state):
    yield state.with_fact(Fact("p", 1, "s", FIXED_TS))


def future_pure(state):
    fut = asyncio.new_event_loop().create_future()
    fut.set_exception(ValueError("future blew up"))
    return fut


async def async_effect(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("e", 2, "s", FIXED_TS))


def diverged_pure(state):
    return state.with_fact(Fact("p", 999, "s", FIXED_TS))


def make_trace(nodes, policy=Policy.STRICT):
    rt = Runtime(SPEC, nodes, policy=policy, **PINNED)
    try:
        rt.run(State.empty("v"), run_id="src")
    except BaseException:  # noqa: BLE001
        pass
    return rt.trace.to_dict()


async def make_trace_async(nodes, policy=Policy.STRICT):
    rt = Runtime(SPEC, nodes, policy=policy, **PINNED)
    try:
        await rt.arun(State.empty("v"), run_id="src")
    except BaseException:  # noqa: BLE001
        pass
    return rt.trace.to_dict()


def verify_outcome(document, nodes):
    try:
        result = ReplayEngine(TraceBundle(SPEC, Trace.from_dict(document))).verify(nodes)
        return {
            "ok": True,
            "mode": result.mode,
            "verified": [list(v) for v in result.verified],
            "state": result.state.snapshot(),
        }
    except BaseException as exc:  # noqa: BLE001
        return {"ok": False, "exc": type(exc).__name__, "msg": str(exc)[:160]}


NODE_SETS = {
    "sync-clean": ({"p": ok_pure, "e": ok_effect}, Policy.STRICT),
    "pure-raises-ReplayError": ({"p": raises_replay_error, "e": ok_effect}, Policy.EXPLORATION),
    "pure-raises-UnsafeResume": ({"p": raises_unsafe_resume, "e": ok_effect}, Policy.EXPLORATION),
    "pure-raises-ReplayMismatch": ({"p": raises_replay_mismatch, "e": ok_effect}, Policy.EXPLORATION),
    "pure-raises-ValueError": ({"p": raises_value_error, "e": ok_effect}, Policy.EXPLORATION),
}


def dump() -> None:
    """One side of the pre/post differential: every verify outcome as JSON."""
    out = {}
    for label, (nodes, policy) in NODE_SETS.items():
        document = make_trace(nodes, policy)
        out[label] = {
            "trace_error_types": [
                (r["node"], r["error"] and r["error"]["type"])
                for r in document["records"] if r["kind"] == "transition"
            ],
            "verify": verify_outcome(document, nodes),
        }
    # async pure node, verified against the async registry
    adoc = asyncio.run(make_trace_async({"p": async_pure, "e": async_effect}))
    out["async-pure"] = {"verify": verify_outcome(adoc, {"p": async_pure, "e": async_effect})}
    # async-generator pure node
    gdoc = make_trace({"p": async_gen_pure, "e": ok_effect}, Policy.EXPLORATION)
    out["async-generator-pure"] = {
        "trace_error_types": [
            (r["node"], r["error"] and r["error"]["type"])
            for r in gdoc["records"] if r["kind"] == "transition"
        ],
        "verify": verify_outcome(gdoc, {"p": async_gen_pure, "e": ok_effect}),
    }
    # true divergence must still be detected
    clean = make_trace({"p": ok_pure, "e": ok_effect})
    out["true-divergence"] = {
        "verify": verify_outcome(clean, {"p": diverged_pure, "e": ok_effect})
    }
    sys.stdout.write(json.dumps(out, default=str))


def run_side(src: str | None):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    if src:
        env["MOTUS_SRC"] = src
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1b_verify.py"), "dump"],
        capture_output=True, text=True, env=env, cwd=str(HERE), timeout=180,
    )
    if not proc.stdout.strip():
        return None, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def main() -> int:
    report = Report("x1b_verify -- the verify guard and its widened except clause")

    report.record(
        "ReplayMismatch and UnsafeResume are subclasses of ReplayError, so "
        "`except (ReplayMismatch, ReplayError)` is strictly wider than "
        "`except ReplayMismatch`",
        issubclass(ReplayMismatch, ReplayError) and issubclass(UnsafeResume, ReplayError),
        f"mro widened = {issubclass(ReplayMismatch, ReplayError)}",
    )
    report.record(
        "ReplayError is public API, so a node can raise it",
        "ReplayError" in __import__("vitruvyan_motus").__all__, "",
    )

    post, post_err = run_side(None)
    pre, pre_err = run_side(PREFIX_SRC) if Path(PREFIX_SRC).is_dir() else (None, "missing")
    if post is None:
        print("post-fix dump failed:\n", post_err[-3000:])
        return 1

    for label in post:
        print(f"    . {label}: {json.dumps(post[label].get('verify'))[:230]}")

    # --- 1. the widened clause: behaviour change on pre-existing paths ------
    if pre is not None:
        for label in ("sync-clean", "pure-raises-ReplayError",
                      "pure-raises-UnsafeResume", "pure-raises-ReplayMismatch",
                      "pure-raises-ValueError", "true-divergence"):
            d = diff(pre[label], post[label])
            report.record(
                f"{label}: verify behaviour unchanged by the fix",
                not d, "; ".join(d[:4]),
            )
    else:
        report.record("pre-fix tree available for differential", False, pre_err[:200])

    # --- 2. the guard itself ------------------------------------------------
    v = post["async-pure"]["verify"]
    report.record(
        "async pure node: refused with ReplayError, not a false ReplayMismatch",
        v["ok"] is False and v["exc"] == "ReplayError"
        and "is asynchronous" in v["msg"],
        json.dumps(v)[:200],
    )
    report.record(
        "async pure node: no un-awaited coroutine leaked",
        "never awaited" not in post_err,
        " ".join(l for l in post_err.splitlines() if "never awaited" in l)[:200],
    )

    g = post["async-generator-pure"]["verify"]
    report.record(
        "async-GENERATOR pure node: also refused with ReplayError "
        "(not a false ReplayMismatch)",
        g["ok"] is False and g.get("exc") == "ReplayError",
        json.dumps(g)[:220],
    )

    t = post["true-divergence"]["verify"]
    report.record(
        "true divergence is still detected as ReplayMismatch",
        t["ok"] is False and t["exc"] == "ReplayMismatch",
        json.dumps(t)[:200],
    )

    c = post["sync-clean"]["verify"]
    report.record(
        "a clean synchronous graph still verifies",
        c["ok"] is True and len(c["verified"]) == 1,
        json.dumps(c)[:200],
    )

    # --- 3. effect classes the guard never sees ----------------------------
    report.record(
        "verify only re-executes `pure` nodes, so an async recorded_effect / "
        "external_effect node is silently never checked "
        "(documented scope, recorded here as coverage)",
        len(c["verified"]) == 1,
        f"verified={c['verified']} of 2 transitions",
    )

    if post_err.strip():
        print("\n--- post-fix child stderr ---")
        print(post_err[-2500:])
    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        sys.exit(1 if main() else 0)
