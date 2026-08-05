"""ATTACK 1 + 6 -- sync/async equivalence across a graph-shape matrix,
with contract conformance in both encodings for every trace produced.

The comparison is deliberately harder than the repo's own async tests:
  * the SAME node registry is driven by run() and by arun(), so the traces
    must match byte for byte INCLUDING run.created_ts, record ts and
    graph.code_fingerprint (all three nondeterminism sources are pinned);
  * a second async-bodied registry is compared with only code_fingerprint
    pinned, which is the weakest form the claim allows;
  * stream() vs astream() record streams are compared as well;
  * every trace produced is validated with validate_trace AND validate_jsonl
    and the two verdicts plus the reassembled document must agree.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from x1_common import (
    FIXED_TS,
    PINNED,
    BEHAVIOURS,
    Report,
    async_registry,
    diff,
    doc,
    load_validator,
    pin_fingerprint,
    sync_registry,
)

from vitruvyan_motus import (
    Decision,
    EffectClass,
    EffectDescriptor,
    EffectReceipt,
    Fact,
    GraphSpec,
    InMemoryTraceSink,
    Policy,
    Rejection,
    Runtime,
    State,
    redact,
)

validate = load_validator()


# --------------------------------------------------------------------------- #
# Graph shapes                                                                 #
# --------------------------------------------------------------------------- #


def linear(n: int = 2, **extra):
    names = [f"n{i}" for i in range(n)]
    transitions = {names[i]: {"kind": "next", "to": names[i + 1]} for i in range(n - 1)}
    transitions[names[-1]] = {"kind": "terminal"}
    spec = {
        "schema_version": "1.0.0",
        "name": f"linear-{n}",
        "version": "1.0.0",
        "entry": names[0],
        "nodes": [{"name": x, "effect_class": "pure"} for x in names],
        "transitions": transitions,
    }
    spec.update(extra)
    return spec, names


def routed(default: bool = True):
    spec = {
        "schema_version": "1.0.0",
        "name": "routed" + ("-default" if default else "-strictroute"),
        "version": "1.0.0",
        "entry": "classify",
        "nodes": [
            {"name": "classify", "effect_class": "pure"},
            {"name": "review", "effect_class": "pure"},
            {"name": "reject", "effect_class": "pure"},
        ],
        "transitions": {
            "classify": {"kind": "route", "on": "risk", "map": {"review": "review"}},
            "review": {"kind": "terminal"},
            "reject": {"kind": "terminal"},
        },
    }
    if default:
        spec["transitions"]["classify"]["default"] = "reject"
    else:
        spec["transitions"]["classify"]["map"]["low"] = "reject"
    return spec, ["classify", "review", "reject"]


def cyclic(limit: int = 4):
    spec = {
        "schema_version": "1.0.0",
        "name": "cyclic",
        "version": "1.0.0",
        "entry": "a",
        "max_transitions": limit,
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
        ],
        "transitions": {
            "a": {"kind": "route", "on": "go", "map": {"a": "a", "b": "b"}},
            "b": {"kind": "terminal"},
        },
    }
    return spec, ["a", "b"]


def wide(n: int = 520):
    leaves = [f"L{i:04d}" for i in range(n)]
    spec = {
        "schema_version": "1.0.0",
        "name": "wide",
        "version": "1.0.0",
        "entry": "hub",
        "nodes": [{"name": "hub", "effect_class": "pure"}]
        + [{"name": x, "effect_class": "pure"} for x in leaves],
        "transitions": {
            "hub": {"kind": "route", "on": "pick", "map": {x: x for x in leaves}},
            **{x: {"kind": "terminal"} for x in leaves},
        },
    }
    return spec, ["hub"] + leaves


def declared(reads, writes):
    spec = {
        "schema_version": "1.0.0",
        "name": "declared",
        "version": "1.0.0",
        "entry": "d",
        "nodes": [
            {
                "name": "d",
                "effect_class": "pure",
                "reads_declared": reads,
                "writes_declared": writes,
            }
        ],
        "transitions": {"d": {"kind": "terminal"}},
    }
    return spec, ["d"]


def effectful():
    spec = {
        "schema_version": "1.0.0",
        "name": "effectful",
        "version": "1.0.0",
        "entry": "e",
        "nodes": [{"name": "e", "effect_class": "external_effect"}],
        "transitions": {"e": {"kind": "terminal"}},
    }
    return spec, ["e"]


# --------------------------------------------------------------------------- #
# Behaviours                                                                   #
# --------------------------------------------------------------------------- #


def b_identity(state, ctx):
    return state


def b_fact(key, value):
    def go(state, ctx):
        return state.with_fact(Fact(key, value, "src", FIXED_TS))

    return go


def b_decide(key, value):
    def go(state, ctx):
        return state.with_decision(Decision(key, value, FIXED_TS, reason="because"))

    return go


def b_raise(exc):
    def go(state, ctx):
        raise exc

    return go


def b_flaky(fail_times: int, box: list):
    def go(state, ctx):
        box.append(1)
        if len(box) <= fail_times:
            raise RuntimeError("transient")
        return state.with_fact(Fact("k", len(box), "src", FIXED_TS))

    return go


def b_draws(state, ctx):
    now = ctx.now()
    r = ctx.rand()
    u = ctx.uuid()
    return state.with_fact(Fact("drawn", [now.isoformat(), r, u], "ctx", FIXED_TS))


def b_effect(state, ctx):
    ctx.record_effect(
        EffectDescriptor(
            EffectClass.EXTERNAL_EFFECT,
            "charged the card",
            idempotency_key="idem-1",
            receipt=EffectReceipt("rcpt-1", "completed", "effect:sha256:" + "ab" * 32),
        )
    )
    ctx.record_effect(
        EffectDescriptor(EffectClass.RECORDED_EFFECT, "asked the model", receipt=None)
    )
    return state.with_fact(Fact("charged", True, "psp", FIXED_TS))


def b_reject_no_evidence(state, ctx):
    return state.with_rejection(Rejection("plan-A", "budget", FIXED_TS))


def b_reject_evidence(state, ctx):
    return state.with_rejection(
        Rejection("plan-B", "policy", FIXED_TS, evidence={"rule": "R7", "n": 3})
    )


def b_redacted(state, ctx):
    return state.with_fact(Fact("secret", redact("hunter2", "policy://pii"), "kms", FIXED_TS))


def b_read_then_write(read_key, write_key):
    def go(state, ctx):
        state.fact(read_key)
        return state.with_fact(Fact(write_key, 1, "src", FIXED_TS))

    return go


def b_return_none(state, ctx):
    return None


def b_return_junk(state, ctx):
    return {"not": "a state"}


# --------------------------------------------------------------------------- #
# Cases                                                                        #
# --------------------------------------------------------------------------- #


def seeded():
    return State.new(
        intent="seeded",
        facts=[Fact("seed", 7, "src", FIXED_TS)],
        decisions=[Decision("risk", "review", FIXED_TS)],
    )


CASES: list[dict[str, Any]] = []


def case(name, spec, names, behaviours, state=None, **kw):
    CASES.append(
        dict(name=name, spec=spec, names=names, behaviours=behaviours, state=state, kw=kw)
    )


_lin2, _lin2n = linear(2)
case("linear", _lin2, _lin2n, lambda: {"n0": b_fact("a", 1), "n1": b_fact("b", 2)})

_rd, _rdn = routed(default=True)
case(
    "routed-matched",
    _rd,
    _rdn,
    lambda: {
        "classify": b_decide("risk", "review"),
        "review": b_fact("out", "escalated"),
        "reject": b_identity,
    },
)
case(
    "routed-default",
    _rd,
    _rdn,
    lambda: {
        "classify": b_decide("risk", "unknown-label"),
        "review": b_identity,
        "reject": b_fact("out", "rejected"),
    },
)

_rs, _rsn = routed(default=False)
case(
    "routed-miss-strict",
    _rs,
    _rsn,
    lambda: {"classify": b_decide("risk", "nope"), "review": b_identity, "reject": b_identity},
)
case(
    "routed-miss-exploration",
    _rs,
    _rsn,
    lambda: {"classify": b_decide("risk", "nope"), "review": b_identity, "reject": b_identity},
    policy=Policy.EXPLORATION,
)
case(
    "routed-miss-nonstring",
    _rs,
    _rsn,
    lambda: {"classify": b_decide("risk", 42), "review": b_identity, "reject": b_identity},
    policy=Policy.EXPLORATION,
)

_cy, _cyn = cyclic(4)
case("cycle-limit", _cy, _cyn, lambda: {"a": b_decide("go", "a"), "b": b_identity})
case(
    "cycle-escape",
    _cy,
    _cyn,
    lambda: {"a": b_decide("go", "b"), "b": b_fact("done", True)},
)

case(
    "retry-then-commit",
    _lin2,
    _lin2n,
    lambda: {"n0": b_flaky(2, []), "n1": b_identity},
    max_attempts={"n0": 3, "n1": 1},
)
case(
    "retry-exhausted-strict",
    _lin2,
    _lin2n,
    lambda: {"n0": b_flaky(9, []), "n1": b_identity},
    max_attempts={"n0": 3, "n1": 1},
)
case(
    "retry-exhausted-exploration",
    _lin2,
    _lin2n,
    lambda: {"n0": b_flaky(9, []), "n1": b_identity},
    max_attempts={"n0": 2, "n1": 1},
    policy=Policy.EXPLORATION,
)
case(
    "raise-abort-strict",
    _lin2,
    _lin2n,
    lambda: {"n0": b_raise(ValueError("down")), "n1": b_identity},
)
case(
    "raise-continue-exploration",
    _lin2,
    _lin2n,
    lambda: {"n0": b_raise(ValueError("down")), "n1": b_identity},
    policy=Policy.EXPLORATION,
)
case(
    "returns-none",
    _lin2,
    _lin2n,
    lambda: {"n0": b_return_none, "n1": b_identity},
    policy=Policy.EXPLORATION,
)
case(
    "returns-junk",
    _lin2,
    _lin2n,
    lambda: {"n0": b_return_junk, "n1": b_identity},
    policy=Policy.EXPLORATION,
)

_dv, _dvn = declared(reads=["other"], writes=["allowed"])
case(
    "declaration-violation-strict",
    _dv,
    _dvn,
    lambda: {"d": b_read_then_write("seed", "w")},
    state=seeded,
)
case(
    "declaration-violation-exploration",
    _dv,
    _dvn,
    lambda: {"d": b_read_then_write("seed", "w")},
    state=seeded,
    policy=Policy.EXPLORATION,
)

_ok, _okn = declared(reads=["seed"], writes=["w"])
case("declaration-clean", _ok, _okn, lambda: {"d": b_read_then_write("seed", "w")}, state=seeded)

case("seeded-state", _lin2, _lin2n, lambda: {"n0": b_identity, "n1": b_identity}, state=seeded)
case("redaction", _lin2, _lin2n, lambda: {"n0": b_redacted, "n1": b_identity})
case("context-draws", _lin2, _lin2n, lambda: {"n0": b_draws, "n1": b_draws})
case(
    "rejections",
    _lin2,
    _lin2n,
    lambda: {"n0": b_reject_no_evidence, "n1": b_reject_evidence},
)

_ef, _efn = effectful()
case("effects-with-receipts", _ef, _efn, lambda: {"e": b_effect})

_deep, _deepn = linear(600)
case("deep-600", _deep, _deepn, lambda: {n: b_identity for n in _deepn})

_wide, _widen = wide(520)
case(
    "wide-520",
    _wide,
    _widen,
    lambda: {
        **{n: b_identity for n in _widen},
        "hub": b_decide("pick", "L0300"),
    },
)

case(
    "sink-and-listeners",
    _lin2,
    _lin2n,
    lambda: {"n0": b_fact("a", 1), "n1": b_fact("b", 2)},
    durability_profile="synchronous",
    _needs_sink=True,
)
case(
    "buffered-sink",
    _deep,
    _deepn,
    lambda: {n: b_identity for n in _deepn},
    durability_profile="buffered",
    chunk_records=7,
    flush_interval_ms=0,
    _needs_sink=True,
)


# --------------------------------------------------------------------------- #
# Execution                                                                    #
# --------------------------------------------------------------------------- #


def build(case_, registry):
    kw = dict(case_["kw"])
    kw.pop("_needs_sink", None)
    if case_["kw"].get("_needs_sink"):
        kw["sink"] = InMemoryTraceSink()
    return Runtime(GraphSpec.from_dict(dict(case_["spec"])), registry, **kw, **PINNED)


def capture_sync(fn):
    try:
        return fn(), None
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


async def capture_async(coro):
    try:
        return await coro, None
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    report = Report("x1_matrix -- sync/async equivalence + contract conformance")
    for case_ in CASES:
        name = case_["name"]
        state_factory = case_["state"] or (lambda: State.empty(name))
        names = case_["names"]

        # ---- run() vs arun() with the identical (synchronous) registry ----
        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt_s = build(case_, sync_registry(names))
        _, err_s = capture_sync(lambda: rt_s.run(state_factory(), run_id="pinned"))
        doc_s = doc(rt_s.trace)

        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt_a = build(case_, sync_registry(names))
        _, err_a = await capture_async(rt_a.arun(state_factory(), run_id="pinned"))
        doc_a = doc(rt_a.trace)

        d = diff(doc_s, doc_a)
        report.record(
            f"{name}: run() == arun() byte-for-byte (same registry)",
            not d,
            "; ".join(d[:4]),
        )
        report.record(
            f"{name}: same terminal exception under both drivers",
            err_s == err_a,
            f"sync={err_s!r} async={err_a!r}",
        )

        # ---- async-bodied registry, code_fingerprint pinned ----
        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt_ab = build(case_, async_registry(names))
        _, err_ab = await capture_async(rt_ab.arun(state_factory(), run_id="pinned"))
        doc_ab = doc(rt_ab.trace)
        d2 = diff(pin_fingerprint(doc_s), pin_fingerprint(doc_ab))
        report.record(
            f"{name}: async-def registry == sync registry (fingerprint pinned)",
            not d2,
            "; ".join(d2[:4]),
        )
        report.record(
            f"{name}: async-def registry raises the same terminal",
            err_s == err_ab,
            f"sync={err_s!r} asyncdef={err_ab!r}",
        )

        # ---- stream() vs astream() ----
        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt_st = build(case_, sync_registry(names))
        stream_records: list[dict] = []
        try:
            with rt_st.stream(state_factory(), run_id="pinned") as driver:
                for rec in driver:
                    stream_records.append(rec)
            serr = None
        except BaseException as exc:  # noqa: BLE001
            serr = f"{type(exc).__name__}: {exc}"

        BEHAVIOURS.clear()
        BEHAVIOURS.update(case_["behaviours"]())
        rt_ast = build(case_, sync_registry(names))
        astream_records: list[dict] = []
        try:
            adriver = rt_ast.astream(state_factory(), run_id="pinned")
            async with adriver as d3:
                async for rec in d3:
                    astream_records.append(rec)
            aerr = None
        except BaseException as exc:  # noqa: BLE001
            aerr = f"{type(exc).__name__}: {exc}"

        sd = diff(stream_records, astream_records)
        report.record(
            f"{name}: stream() == astream() record stream", not sd, "; ".join(sd[:4])
        )
        report.record(
            f"{name}: stream/astream raise the same terminal",
            serr == aerr,
            f"sync={serr!r} async={aerr!r}",
        )
        report.record(
            f"{name}: streamed records == run() records",
            not diff(stream_records, doc_s["records"]),
            "; ".join(diff(stream_records, doc_s["records"])[:3]),
        )

        # ---- contract conformance, both encodings, both drivers ----
        spec_doc = dict(case_["spec"])
        for label, trace in (("sync", rt_s.trace), ("async", rt_a.trace), ("asyncdef", rt_ab.trace)):
            document = trace.to_dict()
            v_json = validate.validate_trace(document, spec=spec_doc)
            v_lines, reassembled = validate.validate_jsonl(trace.to_jsonl(), spec=spec_doc)
            report.record(
                f"{name}/{label}: validate_trace == [] ",
                v_json == [],
                str(v_json[:2]),
            )
            report.record(
                f"{name}/{label}: validate_jsonl == [] and identical verdict",
                v_lines == [] and [str(x) for x in v_lines] == [str(x) for x in v_json],
                f"jsonl={v_lines[:2]} json={v_json[:2]}",
            )
            report.record(
                f"{name}/{label}: JSONL reassembles to the JSON document",
                reassembled == document,
                "reassembly differs" if reassembled != document else "",
            )

    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
