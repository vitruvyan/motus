"""ATTACK 2 -- the send/throw protocol.

Falsification targets:
  * a node that returns something that is not a State, or None;
  * `invoke` itself raising a BaseException (KeyboardInterrupt, SystemExit,
    asyncio.CancelledError, GeneratorExit, BaseExceptionGroup, a custom one);
  * whether `machine.throw(exc)` runs `_managed_execute`'s finally exactly as
    a direct call did -- checked against the pre-inversion engine, in a
    subprocess, not against my memory of it;
  * exception identity, __cause__/__context__ and whether the node frame
    survives in the traceback after the throw;
  * lifecycle flags: _running, _cancel_reason, _pending_cancel_reason, the
    return of a later cancel(), and whether a later run still works.

Usage
  python x1_protocol.py            # post-inversion, sync vs async, + pre diff
  python x1_protocol.py dump       # one side, MOTUS_SRC selects it
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

from x1_common import PINNED, Report, diff, load_validator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRE_SRC = os.environ.get(
    "MOTUS_PRE_SRC",
    "/tmp/claude-1000/-home-vitruvyan-motus/2eec55a9-be04-4965-b2c4-951aee158e49"
    "/scratchpad/pre/src",
)

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State  # noqa: E402

LINEAR = {
    "schema_version": "1.0.0",
    "name": "protocol",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}


class CustomBase(BaseException):
    pass


def _base_exceptions():
    out = {
        "KeyboardInterrupt": KeyboardInterrupt,
        "SystemExit": SystemExit,
        "GeneratorExit": GeneratorExit,
        "CustomBaseException": CustomBase,
        "CancelledError": asyncio.CancelledError,
    }
    if sys.version_info >= (3, 11):
        out["BaseExceptionGroup"] = lambda: BaseExceptionGroup("g", [KeyboardInterrupt()])
    return out


def make_node(raiser, attempt_box, fail_on_attempt=1):
    """Raise an ordinary Exception until `fail_on_attempt`, then the BaseException.

    With fail_on_attempt=2 the first attempt is a normal retryable failure, so
    the BaseException lands on attempt 2 and the trace must show a closed
    attempt 1 followed by an unclosed attempt 2.
    """

    def node(state):
        attempt_box.append(1)
        if len(attempt_box) == fail_on_attempt:
            raise raiser()
        raise ValueError("transient")

    return node


def observe(runtime, raised, records_seen, sink):
    return {
        "raised_type": type(raised).__name__ if raised is not None else None,
        "trace_kinds": [r["kind"] for r in runtime.trace.records] if runtime.trace else None,
        "running": runtime._running,
        "cancel_reason": runtime._cancel_reason,
        "pending_cancel_reason": runtime._pending_cancel_reason,
        "active_attempt": runtime._active_attempt,
        "hub_closed": runtime._hub._closed if runtime._hub else None,
        "listener_records": records_seen,
        "sink_kinds": [r["kind"] for r in sink.records] if sink else None,
        "cancel_after": runtime.cancel("late"),
        "node_frame_in_traceback": (
            any(f.name == "node" for f in traceback.extract_tb(raised.__traceback__))
            if raised is not None else None
        ),
        "cause": type(raised.__cause__).__name__ if raised is not None and raised.__cause__ else None,
        "context": (
            type(raised.__context__).__name__
            if raised is not None and raised.__context__ else None
        ),
    }


def probe_sync(label, raiser, max_attempts=1, fail_on_attempt=1, use_stream=False):
    box: list[int] = []
    seen: list[str] = []
    sink = InMemoryTraceSink()
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": make_node(raiser, box, fail_on_attempt), "b": lambda s: s},
        max_attempts={"a": max_attempts, "b": 1},
        sink=sink,
        durability_profile="synchronous",
        listeners=(lambda r: seen.append(r["kind"]),),
        **PINNED,
    )
    raised = None
    try:
        if use_stream:
            with rt.stream(State.empty(label), run_id="pinned") as driver:
                for _ in driver:
                    pass
        else:
            rt.run(State.empty(label), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    out = observe(rt, raised, seen, sink)
    out["identity_preserved"] = raised is not None
    out["attempts_executed"] = len(box)
    return out


async def probe_async(label, raiser, max_attempts=1, fail_on_attempt=1, use_stream=False):
    box: list[int] = []
    seen: list[str] = []
    sink = InMemoryTraceSink()
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": make_node(raiser, box, fail_on_attempt), "b": lambda s: s},
        max_attempts={"a": max_attempts, "b": 1},
        sink=sink,
        durability_profile="synchronous",
        listeners=(lambda r: seen.append(r["kind"]),),
        **PINNED,
    )
    raised = None
    try:
        if use_stream:
            driver = rt.astream(State.empty(label), run_id="pinned")
            async with driver as d:
                async for _ in d:
                    pass
        else:
            await rt.arun(State.empty(label), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    out = observe(rt, raised, seen, sink)
    out["identity_preserved"] = raised is not None
    out["attempts_executed"] = len(box)
    return out


SCENARIOS = [
    ("run/attempt1", dict(max_attempts=1, fail_on_attempt=1, use_stream=False)),
    ("run/attempt2-of-3", dict(max_attempts=3, fail_on_attempt=2, use_stream=False)),
    ("stream/attempt1", dict(max_attempts=1, fail_on_attempt=1, use_stream=True)),
]


def dump() -> None:
    out = {}
    for exc_name, raiser in _base_exceptions().items():
        for scen, kw in SCENARIOS:
            out[f"{exc_name}|{scen}"] = probe_sync(exc_name, raiser, **kw)
    sys.stdout.write(json.dumps(out, default=str))


# --------------------------------------------------------------------------- #
# Non-State / None returns, both drivers                                       #
# --------------------------------------------------------------------------- #

BAD_RETURNS = {
    "None": lambda s: None,
    "dict": lambda s: {"facts": []},
    "str": lambda s: "state",
    "int": lambda s: 0,
    "the-same-state-object": lambda s: s,
    "a-foreign-State": lambda s: State.empty("foreign"),
    "list-of-records": lambda s: [],
    "generator": lambda s: (x for x in range(3)),
}


def probe_return_sync(fn):
    validate = VALIDATE
    sink = InMemoryTraceSink()
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)), {"a": fn, "b": lambda s: s},
        sink=sink, durability_profile="synchronous", **PINNED,
    )
    err = None
    try:
        rt.run(State.empty("ret"), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}"
    document = rt.trace.to_dict()
    return {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records],
        "transition": [
            {k: r[k] for k in ("outcome", "disposition", "error", "writes")}
            for r in rt.trace.records if r["kind"] == "transition"
        ],
        "violations": [str(v) for v in validate.validate_trace(document, spec=LINEAR)],
        "jsonl": [str(v) for v in validate.validate_jsonl(rt.trace.to_jsonl(), spec=LINEAR)[0]],
        "sink_kinds": [r["kind"] for r in sink.records],
    }


async def probe_return_async(fn):
    validate = VALIDATE
    sink = InMemoryTraceSink()
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)), {"a": fn, "b": lambda s: s},
        sink=sink, durability_profile="synchronous", **PINNED,
    )
    err = None
    try:
        await rt.arun(State.empty("ret"), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}"
    document = rt.trace.to_dict()
    return {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records],
        "transition": [
            {k: r[k] for k in ("outcome", "disposition", "error", "writes")}
            for r in rt.trace.records if r["kind"] == "transition"
        ],
        "violations": [str(v) for v in validate.validate_trace(document, spec=LINEAR)],
        "jsonl": [str(v) for v in validate.validate_jsonl(rt.trace.to_jsonl(), spec=LINEAR)[0]],
        "sink_kinds": [r["kind"] for r in sink.records],
    }


VALIDATE = None


async def main() -> int:
    global VALIDATE
    VALIDATE = load_validator()
    report = Report("x1_protocol -- send/throw protocol")

    # ---- BaseException: sync vs async vs pre-inversion --------------------
    post = {}
    for exc_name, raiser in _base_exceptions().items():
        for scen, kw in SCENARIOS:
            key = f"{exc_name}|{scen}"
            s = probe_sync(exc_name, raiser, **kw)
            a = await probe_async(exc_name, raiser, **kw)
            post[key] = s
            d = diff(s, a)
            report.record(f"{key}: sync driver == async driver", not d, "; ".join(d[:5]))
            expected_kinds = (
                ["run_started", "attempt_started"] if kw["fail_on_attempt"] == 1
                else ["run_started", "attempt_started", "transition", "attempt_started"]
            )
            report.record(
                f"{key}: attempt_started left unclosed (node-protocol 7.3)",
                s["trace_kinds"] == expected_kinds,
                f"got {s['trace_kinds']}",
            )
            report.record(
                f"{key}: BaseException propagates to the caller",
                s["raised_type"] == exc_name.replace("CustomBaseException", "CustomBase"),
                f"got {s['raised_type']}",
            )
            report.record(
                f"{key}: _managed_execute finally ran (_running False, hub closed)",
                s["running"] is False and s["hub_closed"] is True
                and s["cancel_reason"] is None and s["pending_cancel_reason"] is None
                and s["active_attempt"] is None,
                json.dumps({k: s[k] for k in (
                    "running", "hub_closed", "cancel_reason",
                    "pending_cancel_reason", "active_attempt")}),
            )
            report.record(
                f"{key}: node frame survives machine.throw() in the traceback",
                s["node_frame_in_traceback"] is True,
                "node frame lost",
            )
            report.record(
                f"{key}: a later cancel() is refused (run already started)",
                s["cancel_after"] is False,
                f"cancel()={s['cancel_after']}",
            )

    # ---- the pre-inversion oracle ----------------------------------------
    if Path(PRE_SRC).is_dir():
        env = dict(os.environ, MOTUS_SRC=PRE_SRC, PYTHONPATH=str(HERE))
        proc = subprocess.run(
            [sys.executable, str(HERE / "x1_protocol.py"), "dump"],
            capture_output=True, text=True, env=env, cwd=str(HERE),
        )
        if proc.returncode != 0:
            report.record("pre-inversion oracle available", False, proc.stderr[-800:])
        else:
            pre = json.loads(proc.stdout)
            for key in post:
                d = diff(pre[key], post[key])
                report.record(
                    f"{key}: pre-inversion == post-inversion", not d, "; ".join(d[:5])
                )
    else:
        report.record("pre-inversion oracle available", False, f"missing {PRE_SRC}")

    # ---- non-State returns -----------------------------------------------
    for label, fn in BAD_RETURNS.items():
        s = probe_return_sync(fn)
        a = await probe_return_async(fn)
        d = diff(s, a)
        report.record(f"return {label}: sync == async", not d, "; ".join(d[:5]))
        report.record(
            f"return {label}: trace still contract-valid in both encodings",
            s["violations"] == [] and s["jsonl"] == [],
            f"json={s['violations'][:1]} jsonl={s['jsonl'][:1]}",
        )
        report.record(
            f"return {label}: no writes attributed to a failed attempt",
            all(t["writes"] == {"facts": [], "decisions": [], "rejections": []}
                for t in s["transition"] if t["outcome"] == "raised"),
            json.dumps(s["transition"])[:200],
        )

    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        sys.exit(1 if asyncio.run(main()) else 0)
