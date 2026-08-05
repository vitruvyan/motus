"""ATTACK 7 -- `_run_from` / resume, and the node-calling sites the
inversion did NOT cover.

`Runtime._run_from` (runtime.py:856-875) is wired only to `_drive` +
`_invoke_sync`. `ReplayEngine.verify` (replay.py:372) does not use the
`_Invoke` protocol at all -- it calls the node directly. So the claim "there
is a single state machine that no longer calls nodes" holds for `_execute`,
but the package still contains node-calling code paths that the async driver
cannot reach.

This script measures the consequences for a consumer whose graph is async.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from x1_common import FIXED_TS, PINNED, Report, load_validator  # noqa: E402

from vitruvyan_motus import (  # noqa: E402
    Fact, GraphSpec, ReplayEngine, Runtime, State, TraceBundle, Trace,
)

validate = load_validator()

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "resume",
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
SPEC = GraphSpec.from_dict(dict(SPEC_DOC))


def sync_a(state):
    return state.with_fact(Fact("a", 1, "s", FIXED_TS))


def sync_b(state):
    return state.with_fact(Fact("b", 2, "s", FIXED_TS))


def sync_c(state):
    return state.with_fact(Fact("c", 3, "s", FIXED_TS))


async def async_a(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", 1, "s", FIXED_TS))


async def async_b(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("b", 2, "s", FIXED_TS))


async def async_c(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("c", 3, "s", FIXED_TS))


SYNC = {"a": sync_a, "b": sync_b, "c": sync_c}
ASYNC = {"a": async_a, "b": async_b, "c": async_c}


def truncated_bundle(document: dict, keep_kinds: int) -> TraceBundle:
    """A crash-truncated trace: evidence, not malformation (guarantees II)."""
    doc2 = json.loads(json.dumps(document))
    doc2["records"] = doc2["records"][:keep_kinds]
    return TraceBundle(SPEC, Trace.from_dict(doc2))


async def main() -> int:
    report = Report("x1_resume -- resume/verify under both drivers")

    # A crashed synchronous run: run_started, attempt_started, transition.
    rt = Runtime(SPEC, SYNC, **PINNED)
    full = rt.run(State.empty("source"), run_id="source-run").trace.to_dict()
    bundle = truncated_bundle(full, 3)

    # An identically-shaped crashed asynchronous run.
    rta = Runtime(SPEC, ASYNC, **PINNED)
    afull = (await rta.arun(State.empty("source"), run_id="source-run-a")).trace.to_dict()
    abundle = truncated_bundle(afull, 3)

    report.record(
        "the truncated source traces validate with expect_complete=False",
        validate.validate_trace(bundle.trace.to_dict(), spec=SPEC_DOC, expect_complete=False) == []
        and validate.validate_trace(abundle.trace.to_dict(), spec=SPEC_DOC, expect_complete=False) == [],
        "",
    )

    # ---- resume with sync nodes: the supported path -----------------------
    target = Runtime(SPEC, SYNC, **PINNED)
    result = ReplayEngine(bundle).resume(target, run_id="resumed-sync")
    report.record(
        "resume of a synchronous graph completes",
        result.status == "completed",
        f"status={result.status}",
    )
    report.record(
        "…and the resumed segment is contract-valid in both encodings",
        validate.validate_trace(result.trace.to_dict(), spec=SPEC_DOC) == []
        and validate.validate_jsonl(result.trace.to_jsonl(), spec=SPEC_DOC)[0] == [],
        "",
    )

    # ---- resume with async nodes: no async entry point exists -------------
    report.record(
        "Runtime exposes no asynchronous resume entry point",
        not any(hasattr(Runtime, n) for n in ("_arun_from", "arun_from", "aresume")),
        [n for n in dir(Runtime) if "from" in n or "resume" in n],
    )

    atarget = Runtime(SPEC, ASYNC, **PINNED)
    err = None
    try:
        ReplayEngine(abundle).resume(atarget, run_id="resumed-async")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    kinds = [r["kind"] for r in atarget.trace.records]
    transition = next(
        (r for r in atarget.trace.records if r["kind"] == "transition"), None
    )
    report.record(
        "resuming an ASYNC graph fails on the first node, driven synchronously",
        err is not None and err.startswith("NodeFailed")
        and transition is not None and transition["error"]["type"] == "TypeError"
        and kinds[-1] == "run_failed",
        f"err={err} kinds={kinds} errtype={transition and transition['error']}",
    )
    report.record(
        "…the failed resume segment is still a contract-valid trace",
        validate.validate_trace(atarget.trace.to_dict(), spec=SPEC_DOC) == []
        and validate.validate_jsonl(atarget.trace.to_jsonl(), spec=SPEC_DOC)[0] == [],
        str(validate.validate_trace(atarget.trace.to_dict(), spec=SPEC_DOC)[:1]),
    )

    # ---- verify-replay: a second node-calling site, never inverted --------
    ok = ReplayEngine(TraceBundle(SPEC, Trace.from_dict(full))).verify(SYNC)
    report.record(
        "verify-replay works for a synchronous graph",
        ok.mode == "verify" and len(ok.verified) == 3,
        f"verified={ok.verified}",
    )

    verr = None
    try:
        ReplayEngine(TraceBundle(SPEC, Trace.from_dict(afull))).verify(ASYNC)
    except BaseException as exc:  # noqa: BLE001
        verr = f"{type(exc).__name__}: {exc}"
    report.record(
        "verify-replay of an ASYNC graph is impossible (ReplayMismatch)",
        verr is not None and verr.startswith("ReplayMismatch"),
        f"{verr}",
    )

    # ---- and it leaks the coroutine it refused ---------------------------
    env = dict(os.environ, PYTHONPATH=str(HERE))
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1_resume.py"), "verify-probe"],
        capture_output=True, text=True, env=env, cwd=str(HERE),
    )
    leaked = "never awaited" in proc.stderr
    report.record(
        "verify-replay does not leak an un-awaited coroutine",
        not leaked,
        " ".join(l for l in proc.stderr.splitlines() if "never awaited" in l)[:200],
    )

    return report.emit()


async def verify_probe() -> None:
    rta = Runtime(SPEC, ASYNC, **PINNED)
    afull = (await rta.arun(State.empty("source"), run_id="probe")).trace.to_dict()
    try:
        ReplayEngine(TraceBundle(SPEC, Trace.from_dict(afull))).verify(ASYNC)
    except BaseException:  # noqa: BLE001
        pass
    import gc

    gc.collect()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify-probe":
        asyncio.run(verify_probe())
    else:
        sys.exit(1 if asyncio.run(main()) else 0)
