"""Attack: generate every trace shape reachable from the public API and check
each artifact against `contract/validate.py` in the mode its own name claims.

Also: is the file the trace?  `Trace.to_jsonl()` is the writer's own encoding of
the same document.  Every difference between the two is a place a reader can
disagree about what the run did.
"""

from __future__ import annotations

import gc
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from x6_common import (  # noqa: E402
    BUFFERED, CHAIN, CHAIN_DOC, INMEM, JsonlTraceSink, NOW, Report, SYNC, State,
    Fact, Decision, GraphSpec, Rejection, Runtime, classify, listing, note,
    passthrough, registry, runtime, scratch, validate_expected,
)
from vitruvyan_motus import (  # noqa: E402
    NodeFailed, Policy, ReplayEngine, TraceBundle, redact,
)

LS, PS, NEL = " ", " ", "\x85"

ROUTED_DOC = {
    "schema_version": "1.0.0",
    "name": "x6-routed",
    "version": "1.0.0",
    "entry": "choose",
    "nodes": [
        {"name": "choose", "effect_class": "pure"},
        {"name": "left", "effect_class": "pure"},
        {"name": "right", "effect_class": "pure"},
    ],
    "transitions": {
        "choose": {"kind": "route", "on": "branch", "map": {"L": "left", "R": "right"}},
        "left": {"kind": "terminal"},
        "right": {"kind": "terminal"},
    },
}
ROUTED = GraphSpec.from_dict(dict(ROUTED_DOC))

RETRY_DOC = {
    "schema_version": "1.0.0",
    "name": "x6-retry",
    "version": "1.0.0",
    "entry": "flaky",
    "nodes": [
        {"name": "flaky", "effect_class": "pure"},
        {"name": "done", "effect_class": "pure"},
    ],
    "transitions": {
        "flaky": {"kind": "next", "to": "done"},
        "done": {"kind": "terminal"},
    },
}
RETRY = GraphSpec.from_dict(dict(RETRY_DOC))


def spec_file(rep_dir, spec, name):
    path = rep_dir / name
    path.write_text(json.dumps(spec.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path


def check(rep: Report, tag: str, sink: JsonlTraceSink, spec_path, trace=None) -> None:
    artifacts = sink.artifacts
    if not artifacts:
        rep.record(f"{tag}: produced an artifact", False, "no artifact at all")
        return
    for path in artifacts:
        ok, out = validate_expected(path, spec=spec_path)
        rep.record(f"{tag}: {classify(path)} validates", ok, out[:400])
    if trace is not None:
        expected = trace.to_jsonl()
        raw = artifacts[0].read_text(encoding="utf-8")
        same_objects = [json.loads(l) for l in raw.split("\n") if l.strip()] == [
            json.loads(l) for l in expected.split("\n") if l.strip()
        ]
        rep.record(f"{tag}: file documents == trace.to_jsonl documents", same_objects,
                   "the persisted account differs from the trace")
        rep.record(f"{tag}: file bytes == trace.to_jsonl bytes", raw == expected,
                   f"file={len(raw)}b to_jsonl={len(expected)}b "
                   f"(first difference at "
                   f"{next((i for i, (x, y) in enumerate(zip(raw, expected)) if x != y), -1)})")


def main() -> int:  # noqa: C901
    rep = Report("x6_04 trace-shape sweep")
    root = scratch("shapes")
    chain_spec = spec_file(root, CHAIN, "chain.spec.json")
    routed_spec = spec_file(root, ROUTED, "routed.spec.json")
    retry_spec = spec_file(root, RETRY, "retry.spec.json")

    # --- 1. plain completed run, all three profiles ------------------------ #
    for profile, label in ((SYNC, "sync"), (BUFFERED, "buffered"), (INMEM, "inmem")):
        d = scratch(f"shapes/plain-{label}")
        sink = JsonlTraceSink(d, fsync=False)
        result = runtime(sink=sink, profile=profile).run(
            State.empty("plain"), run_id=f"plain-{label}"
        )
        check(rep, f"plain/{label}", sink, chain_spec, result.trace)

    # --- 2. failure, retries ------------------------------------------------ #
    calls = {"n": 0}

    def flaky(state):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("not yet")
        return state.with_fact(Fact("tries", calls["n"], "x6", NOW))

    d = scratch("shapes/retry")
    sink = JsonlTraceSink(d, fsync=False)
    result = Runtime(
        RETRY, {"flaky": flaky, "done": passthrough}, sink=sink,
        durability_profile=SYNC, max_attempts=3,
    ).run(State.empty("retry"), run_id="retry-ok")
    check(rep, "retry/exhausted-then-ok", sink, retry_spec, result.trace)

    # retries that never succeed
    d = scratch("shapes/retry-fail")
    sink = JsonlTraceSink(d, fsync=False)

    def always_fails(state):
        raise RuntimeError("never")

    try:
        Runtime(
            RETRY, {"flaky": always_fails, "done": passthrough}, sink=sink,
            durability_profile=SYNC, max_attempts=3,
        ).run(State.empty("retry"), run_id="retry-fail")
    except NodeFailed:
        pass
    check(rep, "retry/exhausted", sink, retry_spec)

    # --- 3. route miss, strict and exploration ------------------------------ #
    def choose_bad(state):
        return state.with_decision(Decision("branch", "NOPE", "no such branch", NOW))

    for policy, label in ((Policy.STRICT, "strict"), (Policy.EXPLORATION, "exploration")):
        d = scratch(f"shapes/route-miss-{label}")
        sink = JsonlTraceSink(d, fsync=False)
        try:
            Runtime(
                ROUTED, {"choose": choose_bad, "left": passthrough, "right": passthrough},
                sink=sink, durability_profile=SYNC, policy=policy,
            ).run(State.empty("miss"), run_id=f"miss-{label}")
        except NodeFailed:
            pass
        check(rep, f"route-miss/{label}", sink, routed_spec)

    # exploration continuing past a node failure
    d = scratch("shapes/exploration-continue")
    sink = JsonlTraceSink(d, fsync=False)

    def boom(state):
        raise RuntimeError("explored")

    result = Runtime(
        CHAIN, {"a": boom, "b": passthrough, "c": passthrough}, sink=sink,
        durability_profile=SYNC, policy=Policy.EXPLORATION,
    ).run(State.empty("explore"), run_id="explore")
    check(rep, "exploration/continue", sink, chain_spec, result.trace)

    # --- 4. redaction, unicode, control characters, big states -------------- #
    def hostile_node(state):
        state = state.with_fact(Fact("u2028", f"a{LS}b", "x6", NOW))
        state = state.with_fact(Fact("u2029", f"a{PS}b", "x6", NOW))
        state = state.with_fact(Fact("u0085", f"a{NEL}b", "x6", NOW))
        state = state.with_fact(Fact("nul", "a\x00b", "x6", NOW))
        state = state.with_fact(Fact("nl", "a\nb\r\nc", "x6", NOW))
        state = state.with_fact(Fact("rtl", "‮evil", "x6", NOW))
        state = state.with_fact(Fact("emoji", "run \U0001f4a3 ok", "x6", NOW))
        return state.with_rejection(Rejection("no", f"why{LS}not", NOW))

    hostile = State.empty("hostile", metadata={
        "credential": redact("secret", "policy:credential"),
    })
    d = scratch("shapes/hostile-content")
    sink = JsonlTraceSink(d, fsync=False)
    result = Runtime(
        CHAIN, {"a": hostile_node, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=SYNC,
    ).run(hostile, run_id="hostile-content")
    check(rep, "content/hostile-unicode", sink, chain_spec, result.trace)
    raw = sink.artifacts[0].read_bytes()
    rep.record("hostile content: file line count matches split('\\n')",
               len([l for l in raw.split(b"\n") if l.strip()])
               == len(result.trace.to_dict()["records"]) + 1,
               f"lines={len([l for l in raw.split(b'\\n') if l.strip()])}")
    rep.record("hostile content: str.splitlines() sees the same number of lines",
               len([l for l in raw.decode('utf-8').splitlines() if l.strip()])
               == len(result.trace.to_dict()["records"]) + 1,
               "a recorded value forged a line break for splitlines() readers "
               f"({len([l for l in raw.decode('utf-8').splitlines() if l.strip()])} vs "
               f"{len(result.trace.to_dict()['records']) + 1})")

    # a large state
    def fat_node(state):
        for i in range(200):
            state = state.with_fact(Fact(f"k{i}", "y" * 2000, "x6", NOW))
        return state

    d = scratch("shapes/big")
    sink = JsonlTraceSink(d, fsync=False)
    result = Runtime(
        CHAIN, {"a": fat_node, "b": passthrough, "c": passthrough},
        sink=sink, durability_profile=SYNC,
    ).run(State.empty("big"), run_id="big-state")
    check(rep, "content/large-state", sink, chain_spec, result.trace)

    # --- 5. cut short: partial and part ------------------------------------- #
    d = scratch("shapes/partial")
    sink = JsonlTraceSink(d, fsync=False)
    driver = runtime(sink=sink).stream(State.empty("cut"), run_id="cut-short")
    next(driver)
    next(driver)
    del driver
    gc.collect()
    check(rep, "cut-short/partial", sink, chain_spec)

    d = scratch("shapes/inflight")
    sink = JsonlTraceSink(d, fsync=False)
    held = runtime(sink=sink).stream(State.empty("hold"), run_id="in-flight")
    next(held)
    next(held)
    part = [p for p in d.iterdir() if p.name.endswith(".jsonl.part")]
    rep.record("in-flight leaves exactly one .part", len(part) == 1, str(listing(d)))
    if part:
        ok, out = validate_expected(part[0], spec=chain_spec)
        rep.record("in-flight .part validates --allow-incomplete", ok, out[:400])
    held.close("done")

    # cancelled
    d = scratch("shapes/cancelled")
    sink = JsonlTraceSink(d, fsync=False)
    with runtime(sink=sink).stream(State.empty("cancel"), run_id="cancelled") as drv:
        next(drv)
    check(rep, "cancelled", sink, chain_spec)

    # --- 6. resume segment --------------------------------------------------- #
    d = scratch("shapes/resume-source")
    sink = JsonlTraceSink(d, fsync=False)
    src_driver = runtime(sink=sink).stream(State.empty("src"), run_id="resume-source")
    next(src_driver)
    next(src_driver)
    next(src_driver)
    del src_driver          # abandoned, not cancelled: no terminal record
    gc.collect()
    source_trace = None
    for path in sink.artifacts:
        ok, out = validate_expected(path, spec=chain_spec)
        rep.record(f"resume source ({classify(path)}) validates", ok, out[:300])
    from vitruvyan_motus import Trace
    source_trace = Trace.from_dict(json.loads(
        json.dumps({"schema_version": json.loads(
            sink.artifacts[0].read_text().split("\n")[0])["schema_version"],
            "run": json.loads(sink.artifacts[0].read_text().split("\n")[0])["run"],
            "records": [json.loads(l) for l in
                        sink.artifacts[0].read_text().split("\n")[1:] if l.strip()]})
    ))
    d2 = scratch("shapes/resume-segment")
    sink2 = JsonlTraceSink(d2, fsync=False)
    engine = ReplayEngine(TraceBundle(GraphSpec.from_dict(dict(CHAIN_DOC)), source_trace))
    rt2 = runtime(sink=sink2)
    try:
        result = engine.resume(rt2, run_id="resume-segment-1")
        check(rep, "resume/segment", sink2, chain_spec, result.trace)
    except BaseException as exc:  # noqa: BLE001
        rep.note(f"resume refused: {type(exc).__name__}: {exc}")

    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(main())
