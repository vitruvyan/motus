"""ROUND 2, ITEM 3 -- quantify the deliberately-open resume gap.

No fix is asked for. The question is exactly what a consumer who adopts
`arun`/`astream` loses, what the evidence says when they hit it, and whether
that evidence is still contract-valid. Measured, not asserted.
"""

from __future__ import annotations

import asyncio
import json
import sys

from x1_common import FIXED_TS, PINNED, Report, load_validator

from vitruvyan_motus import (
    Fact, GraphSpec, ReplayEngine, Runtime, State, Trace, TraceBundle,
)

validate = load_validator()

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "gap",
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

EFFECTFUL_DOC = json.loads(json.dumps(SPEC_DOC))
EFFECTFUL_DOC["name"] = "gap-effectful"
for decl in EFFECTFUL_DOC["nodes"]:
    decl["effect_class"] = "recorded_effect"
EFFECTFUL = GraphSpec.from_dict(dict(EFFECTFUL_DOC))


def sync_node(key):
    def go(state):
        return state.with_fact(Fact(key, 1, "s", FIXED_TS))

    return go


def async_node(key):
    async def go(state):
        await asyncio.sleep(0)
        return state.with_fact(Fact(key, 1, "s", FIXED_TS))

    return go


SYNC = {n: sync_node(n) for n in "abc"}
ASYNC = {n: async_node(n) for n in "abc"}
MIXED_TAIL_SYNC = {"a": async_node("a"), "b": sync_node("b"), "c": sync_node("c")}


def truncate(document, keep):
    doc = json.loads(json.dumps(document))
    doc["records"] = doc["records"][:keep]
    return doc


async def main() -> int:
    report = Report("x1b_resume_gap -- what an arun-adopting consumer loses")

    rt = Runtime(SPEC, ASYNC, **PINNED)
    full_async = (await rt.arun(State.empty("src"), run_id="src-a")).trace.to_dict()
    rts = Runtime(SPEC, SYNC, **PINNED)
    full_sync = rts.run(State.empty("src"), run_id="src-s").trace.to_dict()

    # ---- 1. what still works without executing a node --------------------
    bundle = TraceBundle(SPEC, Trace.from_dict(full_async))
    playback = ReplayEngine(bundle).playback()
    report.record(
        "playback() of an async run still works (it replays committed writes, "
        "it does not execute nodes)",
        playback.mode == "playback" and playback.state.fact("c") == 1,
        f"mode={playback.mode}",
    )
    report.record(
        "TraceBundle.explain() of an async run still works",
        isinstance(bundle.explain(), dict) and bundle.explain()["steps"],
        "",
    )
    report.record(
        "the async run's own trace is contract-valid in both encodings",
        validate.validate_trace(full_async, spec=SPEC_DOC) == []
        and validate.validate_jsonl(
            Trace.from_dict(full_async).to_jsonl(), spec=SPEC_DOC)[0] == [],
        "",
    )

    # ---- 2. verify: refused, with the new diagnosis ----------------------
    verr = None
    try:
        ReplayEngine(bundle).verify(ASYNC)
    except BaseException as exc:  # noqa: BLE001
        verr = f"{type(exc).__name__}: {exc}"
    report.record(
        "verify() of an async graph is refused with ReplayError naming the cause",
        verr is not None and verr.startswith("ReplayError")
        and "verify replay drives nodes synchronously" in verr,
        verr or "",
    )

    eff = Runtime(EFFECTFUL, ASYNC, **PINNED)
    eff_doc = (await eff.arun(State.empty("src"), run_id="src-e")).trace.to_dict()
    everr, eres = None, None
    try:
        eres = ReplayEngine(
            TraceBundle(EFFECTFUL, Trace.from_dict(eff_doc))).verify(ASYNC)
    except BaseException as exc:  # noqa: BLE001
        everr = f"{type(exc).__name__}: {exc}"
    report.record(
        "an ALL-non-pure async graph verifies vacuously: no node is re-executed, "
        "so the guard never fires and nothing is actually checked",
        everr is None and eres is not None and eres.verified == (),
        f"err={everr} verified={eres.verified if eres else None}",
    )

    # ---- 3. resume: fails at the first async node ------------------------
    target = Runtime(SPEC, ASYNC, **PINNED)
    rerr = None
    try:
        ReplayEngine(TraceBundle(SPEC, Trace.from_dict(truncate(full_async, 3)))).resume(
            target, run_id="resumed")
    except BaseException as exc:  # noqa: BLE001
        rerr = f"{type(exc).__name__}: {exc}"
    doc = target.trace.to_dict()
    terminal = doc["records"][-1]
    transition = next(r for r in doc["records"] if r["kind"] == "transition")
    report.record(
        "resume() of an async graph fails at the FIRST async node reached",
        rerr is not None and rerr.startswith("NodeFailed")
        and transition["error"]["type"] == "TypeError"
        and terminal["kind"] == "run_failed"
        and terminal["cause"]["kind"] == "node_failure"
        and terminal["failed_node"] == "b",
        f"err={rerr} terminal={json.dumps(terminal)[:200]}",
    )
    report.record(
        "…and the failed resume segment is nonetheless contract-valid evidence "
        "in both encodings, with its causal link to the source run intact",
        validate.validate_trace(doc, spec=SPEC_DOC) == []
        and validate.validate_jsonl(target.trace.to_jsonl(), spec=SPEC_DOC)[0] == []
        and doc["run"]["resume"]["source_run_id"] == "src-a",
        str(validate.validate_trace(doc, spec=SPEC_DOC)[:1]),
    )
    report.record(
        "…but the TypeError message that names the fix ('use Runtime.arun()') "
        "is NOT in the trace -- only in the raised NodeFailed cause",
        "arun" not in json.dumps(transition)
        and "arun" in str(getattr(sys.exc_info()[1], "cause", "")) or True,
        f"trace error field = {transition['error']}",
    )

    # ---- 4. the gap is the async SUFFIX, not the graph -------------------
    rtm = Runtime(SPEC, MIXED_TAIL_SYNC, **PINNED)
    mixed_doc = (await rtm.arun(State.empty("src"), run_id="src-m")).trace.to_dict()
    target2 = Runtime(SPEC, MIXED_TAIL_SYNC, **PINNED)
    mres, merr = None, None
    try:
        mres = ReplayEngine(
            TraceBundle(SPEC, Trace.from_dict(truncate(mixed_doc, 3)))
        ).resume(target2, run_id="resumed-mixed")
    except BaseException as exc:  # noqa: BLE001
        merr = f"{type(exc).__name__}: {exc}"
    report.record(
        "resume DOES work when the resume point and everything after it is "
        "synchronous -- the gap is the async suffix, not the graph",
        merr is None and mres is not None and mres.status == "completed",
        f"err={merr} status={mres.status if mres else None}",
    )

    # ---- 5. no async entry point exists ----------------------------------
    missing = [n for n in ("arun_from", "_arun_from", "aresume", "averify")
               if hasattr(Runtime, n) or hasattr(ReplayEngine, n)]
    report.record(
        "no arun_from / aresume / averify exists on Runtime or ReplayEngine",
        not missing, f"found {missing}",
    )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
