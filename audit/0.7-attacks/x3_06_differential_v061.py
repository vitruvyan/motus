"""x3_06 — differential: is the inverted executor byte-identical to v0.6.1?

The branch claims the inversion is "proven behaviour-neutral" because the 439
pre-existing tests pass unedited. That proves no test noticed. This proves
something stronger for the shapes it covers: the *record stream itself* is
identical, byte for byte, between the v0.6.1 source tree and HEAD, for a set
of synchronous executions covering every terminal kind, every disposition,
every routing outcome, retries, redaction, effects, declaration violations,
seeded state, cancellation and the stream driver.

Nondeterminism is pinned through the public constructor (clock / identity /
random_source), so any difference is the executor's.

Usage:
    python .attack/x3_06_differential_v061.py --emit          # from one tree
    python .attack/x3_06_differential_v061.py --compare A B   # diff two dumps

Or just run it: it locates a v0.6.1 export next to itself (or makes one with
`git archive`), runs itself once per tree in a subprocess, and diffs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Pinned nondeterminism sources                                                #
# --------------------------------------------------------------------------- #

class Clock:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self):
        self.n += 1
        return NOW + timedelta(milliseconds=self.n)


class Ident:
    def __init__(self, prefix="id") -> None:
        self.n = 0
        self.prefix = prefix

    def __call__(self) -> str:
        self.n += 1
        return f"{self.prefix}-{self.n:04d}"


class Rand:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> float:
        self.n += 1
        return (self.n % 97) / 97.0


def pinned(prefix="id"):
    return dict(clock=Clock(), identity=Ident(prefix), random_source=Rand())


# --------------------------------------------------------------------------- #
# The scenarios (public API only, so they import against either tree)          #
# --------------------------------------------------------------------------- #

def build(m) -> dict[str, dict]:
    """`m` is the imported vitruvyan_motus module of whichever tree is active."""
    out: dict[str, dict] = {}

    def spec(name, nodes, transitions, **extra):
        doc = {
            "schema_version": "1.0.0", "name": name, "version": "1.0.0",
            "entry": nodes[0]["name"], "nodes": nodes, "transitions": transitions,
        }
        doc.update(extra)
        return m.GraphSpec.from_dict(doc)

    LINEAR = spec("d-linear",
                  [{"name": "a", "effect_class": "pure"},
                   {"name": "b", "effect_class": "pure"}],
                  {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}})
    ROUTED = spec("d-routed",
                  [{"name": "classify", "effect_class": "pure"},
                   {"name": "store", "effect_class": "pure"},
                   {"name": "escalate", "effect_class": "pure"}],
                  {"classify": {"kind": "route", "on": "verdict",
                                "map": {"approve": "store"}, "default": "escalate"},
                   "store": {"kind": "terminal"}, "escalate": {"kind": "terminal"}})
    STRICT = spec("d-strict",
                  [{"name": "classify", "effect_class": "pure"},
                   {"name": "store", "effect_class": "pure"}],
                  {"classify": {"kind": "route", "on": "verdict",
                                "map": {"approve": "store"}},
                   "store": {"kind": "terminal"}})
    CYCLIC = spec("d-cyclic",
                  [{"name": "fetch", "effect_class": "pure"},
                   {"name": "store", "effect_class": "pure"}],
                  {"fetch": {"kind": "route", "on": "status",
                             "map": {"ok": "store", "retry": "fetch"}},
                   "store": {"kind": "terminal"}},
                  max_transitions=6)
    DECL = spec("d-declared",
                [{"name": "work", "effect_class": "pure",
                  "reads_declared": ["seeded"], "writes_declared": ["allowed"]}],
                {"work": {"kind": "terminal"}})
    EFFECT = spec("d-effect",
                  [{"name": "call", "effect_class": "external_effect"},
                   {"name": "note", "effect_class": "recorded_effect"}],
                  {"call": {"kind": "next", "to": "note"},
                   "note": {"kind": "terminal"}})
    THREE = spec("d-three",
                 [{"name": "a", "effect_class": "pure"},
                  {"name": "b", "effect_class": "pure"},
                  {"name": "c", "effect_class": "pure"}],
                 {"a": {"kind": "next", "to": "b"}, "b": {"kind": "next", "to": "c"},
                  "c": {"kind": "terminal"}})
    CTX = spec("d-ctx", [{"name": "draw", "effect_class": "pure"}],
               {"draw": {"kind": "terminal"}})

    F, D, R = m.Fact, m.Decision, m.Rejection

    def fact(k, v="done"):
        return lambda s: s.with_fact(F(k, v, "d", NOW))

    def decide(k, v):
        return lambda s: s.with_decision(D(k, v, NOW, reason="d"))

    def boom(msg="planned"):
        def node(s):
            raise ValueError(msg)
        return node

    def capture(fn):
        try:
            return fn().trace.to_dict()
        except m.NodeFailed as exc:
            return exc.trace.to_dict()

    L = {"a": fact("a"), "b": fact("b")}
    T3 = {"a": fact("a"), "b": fact("b"), "c": fact("c")}

    out["linear"] = m.Runtime(LINEAR, L, **pinned()).run(
        m.State.empty("linear")).trace.to_dict()

    driver = m.Runtime(LINEAR, L, **pinned()).stream(m.State.empty("streamed"))
    for _ in driver:
        pass
    out["stream_drained"] = driver.trace.to_dict()

    out["routed_matched"] = m.Runtime(
        ROUTED, {"classify": decide("verdict", "approve"),
                 "store": fact("stored"), "escalate": fact("escalated")},
        **pinned()).run(m.State.empty("matched")).trace.to_dict()

    out["routed_default"] = m.Runtime(
        ROUTED, {"classify": decide("verdict", "other"),
                 "store": fact("stored"), "escalate": fact("escalated")},
        **pinned()).run(m.State.empty("default")).trace.to_dict()

    out["route_miss"] = capture(lambda: m.Runtime(
        STRICT, {"classify": decide("verdict", "nope"), "store": fact("stored")},
        **pinned()).run(m.State.empty("miss")))

    out["exploration_miss"] = m.Runtime(
        STRICT, {"classify": decide("verdict", "nope"), "store": fact("stored")},
        policy=m.Policy.EXPLORATION, **pinned()).run(
        m.State.empty("expl-miss")).trace.to_dict()

    out["cyclic_limit"] = capture(lambda: m.Runtime(
        CYCLIC, {"fetch": decide("status", "retry"), "store": fact("stored")},
        **pinned()).run(m.State.empty("cyclic")))

    attempts = {"n": 0}

    def flaky(s):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("nope")
        return s.with_fact(F("a", "eventually", "d", NOW))

    out["retry_chain"] = m.Runtime(
        LINEAR, {"a": flaky, "b": fact("b")}, max_attempts=3, **pinned()).run(
        m.State.empty("retry")).trace.to_dict()

    out["raised"] = capture(lambda: m.Runtime(
        LINEAR, {"a": boom(), "b": fact("b")}, **pinned()).run(
        m.State.empty("failure")))

    out["exploration_continue"] = m.Runtime(
        LINEAR, {"a": boom(), "b": fact("b")}, policy=m.Policy.EXPLORATION,
        **pinned()).run(m.State.empty("expl")).trace.to_dict()

    def undeclared(s):
        return s.with_fact(F("forbidden", 1, "d", NOW))

    out["decl_strict"] = capture(lambda: m.Runtime(
        DECL, {"work": undeclared}, **pinned()).run(m.State.empty("strict")))
    out["decl_exploration"] = m.Runtime(
        DECL, {"work": undeclared}, policy=m.Policy.EXPLORATION, **pinned()).run(
        m.State.empty("expl")).trace.to_dict()

    seeded = m.State.from_snapshot(
        {"facts": [F("seeded", "yes", "operator", NOW).to_dict()],
         "decisions": [], "rejections": []},
        intent="seeded", metadata={"ruleset_version": "d.1"})

    def read_seeded(s):
        _ = s.fact("seeded")
        return s.with_fact(F("allowed", "ok", "d", NOW))

    out["seeded"] = m.Runtime(DECL, {"work": read_seeded}, **pinned()).run(
        seeded).trace.to_dict()

    out["redacted"] = m.Runtime(
        LINEAR, {"a": lambda s: s.with_fact(
            F("a", m.redact("secret", "policy://d/pii"), "d", NOW)),
            "b": fact("b")}, **pinned()).run(
        m.State.empty("redaction")).trace.to_dict()

    out["rejection"] = m.Runtime(
        LINEAR, {"a": lambda s: s.with_rejection(
            R("route-b", "policy", NOW, evidence={"rule": "d"})).with_fact(
            F("a", "done", "d", NOW)), "b": fact("b")}, **pinned()).run(
        m.State.empty("rejection")).trace.to_dict()

    def calling(s, ctx):
        ctx.record_effect(m.EffectDescriptor(
            m.EffectClass.EXTERNAL_EFFECT, "POST /orders",
            idempotency_key="d-key", receipt=m.EffectReceipt("rcpt", "completed")))
        return s.with_fact(F("called", "yes", "d", NOW))

    def noting(s, ctx):
        ctx.record_effect(m.EffectDescriptor(
            m.EffectClass.RECORDED_EFFECT, "model", receipt=m.EffectReceipt("r2")))
        return s.with_fact(F("noted", "yes", "d", NOW))

    out["effects"] = m.Runtime(
        EFFECT, {"call": calling, "note": noting}, **pinned()).run(
        m.State.empty("effects")).trace.to_dict()

    def drawing(s, ctx):
        stamp = ctx.now()
        token = ctx.uuid()
        score = ctx.rand()
        return s.with_fact(F("draw", f"{token}:{score:.6f}", "d", stamp))

    out["context_draws"] = m.Runtime(CTX, {"draw": drawing}, **pinned()).run(
        m.State.empty("draws"),
        replay=m.ReplayStatus.declared("full")).trace.to_dict()

    rt = m.Runtime(THREE, T3, **pinned())
    driver = rt.stream(m.State.empty("cancel-mid"))
    next(driver)
    next(driver)
    driver.close("d consumer stopped")
    out["cancel_midstream"] = driver.trace.to_dict()

    rt = m.Runtime(THREE, T3, **pinned())
    rt.cancel("d pre-cancel")
    out["cancel_prerun"] = rt.run(m.State.empty("pre")).trace.to_dict()

    sink = m.InMemoryTraceSink()
    out["buffered"] = m.Runtime(
        LINEAR, L, sink=sink, durability_profile=m.DurabilityProfile.BUFFERED,
        chunk_records=2, flush_interval_ms=10, **pinned()).run(
        m.State.empty("buffered")).trace.to_dict()

    sink2 = m.InMemoryTraceSink()
    out["synchronous"] = m.Runtime(
        LINEAR, L, sink=sink2,
        durability_profile=m.DurabilityProfile.SYNCHRONOUS, **pinned()).run(
        m.State.empty("synchronous")).trace.to_dict()

    # in-flight (abandoned) + resume segment
    rt = m.Runtime(THREE, T3, **pinned())
    for record in rt.stream(m.State.empty("to-resume")):
        if record["kind"] == "transition":
            break
    source = rt._trace
    out["in_flight"] = source.to_dict()
    engine = m.ReplayEngine(m.TraceBundle(spec=THREE, trace=source))
    fresh = m.Runtime(THREE, T3, **pinned("seg"))
    out["resume_segment"] = engine.resume(
        fresh, run_id="d-resumed").trace.to_dict()

    # verify replay of a pure sync graph
    verify_rt = m.Runtime(LINEAR, L, **pinned())
    verified = verify_rt.run(m.State.empty("verify"),
                             replay=m.ReplayStatus.declared("full"))
    engine = m.ReplayEngine(m.TraceBundle(spec=LINEAR, trace=verified.trace))
    result = engine.verify(L)
    out["verify_result"] = {
        "mode": result.mode, "verified": [list(v) for v in result.verified],
        "state": result.state.snapshot(),
    }

    # BaseException from a node must tear the run down and leave the attempt
    # unclosed (node-protocol 7.3) — the inversion routes it through
    # machine.throw(), so this is the sharpest differential probe.
    class Torn(BaseException):
        pass

    def tearing(s):
        raise Torn("torn")

    rt = m.Runtime(LINEAR, {"a": tearing, "b": fact("b")}, **pinned())
    torn_kind = None
    try:
        rt.run(m.State.empty("torn"))
    except BaseException as exc:  # noqa: BLE001
        torn_kind = type(exc).__name__
    out["base_exception"] = {
        "propagated": torn_kind,
        "records": [r["kind"] for r in rt._trace.to_dict()["records"]],
        "running_after": rt._running,
        "active_attempt": rt._active_attempt,
    }

    # a node returning a non-State, and a node returning an awaitable-like
    class Awaitable:
        closed = False

        def __await__(self):
            yield
            return None

        def close(self):
            type(self).closed = True

    rt = m.Runtime(LINEAR, {"a": lambda s: Awaitable(), "b": fact("b")}, **pinned())
    try:
        rt.run(m.State.empty("awaitable"))
    except BaseException as exc:  # noqa: BLE001
        pass
    doc = rt._trace.to_dict()
    out["awaitable_return"] = {
        "records": [r["kind"] for r in doc["records"]],
        "error": next((r.get("error") for r in doc["records"]
                       if r["kind"] == "transition"), None),
        "close_called_by_runtime": Awaitable.closed,
    }

    rt = m.Runtime(LINEAR, {"a": lambda s: "not a state", "b": fact("b")}, **pinned())
    try:
        rt.run(m.State.empty("nonstate"))
    except BaseException:  # noqa: BLE001
        pass
    doc = rt._trace.to_dict()
    out["nonstate_return"] = {
        "records": [r["kind"] for r in doc["records"]],
        "error": next((r.get("error") for r in doc["records"]
                       if r["kind"] == "transition"), None),
    }

    return out


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #

def emit(src: Path) -> str:
    sys.path.insert(0, str(src))
    import vitruvyan_motus as m  # noqa: PLC0415

    assert Path(m.__file__).resolve().is_relative_to(src.resolve()), m.__file__
    return json.dumps(build(m), sort_keys=True, indent=1, default=repr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit", metavar="SRC")
    args = parser.parse_args()

    if args.emit:
        sys.stdout.write(emit(Path(args.emit)))
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="x3-v061-"))
    subprocess.run(
        f"git -C {REPO} archive v0.6.1 src | tar -x -C {tmp}",
        shell=True, check=True,
    )
    here = Path(__file__).resolve()
    dumps = {}
    for label, src in (("v0.6.1", tmp / "src"), ("HEAD", REPO / "src")):
        proc = subprocess.run(
            [sys.executable, str(here), "--emit", str(src)],
            capture_output=True, text=True, cwd=str(REPO),
        )
        if proc.returncode != 0:
            print(f"{label}: emit failed\n{proc.stderr[-3000:]}")
            return 2
        dumps[label] = json.loads(proc.stdout)

    a, b = dumps["v0.6.1"], dumps["HEAD"]
    keys = sorted(set(a) | set(b))
    differing = []
    for key in keys:
        if a.get(key) != b.get(key):
            differing.append(key)
    print(f"{len(keys)} probes compared between v0.6.1 and HEAD")
    for key in keys:
        mark = "DIFF" if key in differing else "same"
        print(f"  {mark}  {key}")
    if differing:
        print("\n--- differences ---")
        for key in differing:
            print(f"\n### {key}")
            print("v0.6.1:", json.dumps(a.get(key), sort_keys=True)[:2000])
            print("HEAD  :", json.dumps(b.get(key), sort_keys=True)[:2000])
    return 1 if differing else 0


if __name__ == "__main__":
    raise SystemExit(main())
