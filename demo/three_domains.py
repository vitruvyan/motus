"""Three decisions, three industries, one graph — the demo's evidence.

    .venv/bin/python demo/three_domains.py

Writes `demo/out/domains/<name>.json` and prints each root. The scenarios are
invented; **the traces are not**. They are produced by the shipped runtime, they
validate against the shipped contract, and their roots are anchored for real —
which is the only way a mock can demonstrate auditability without contradicting
itself.

## Why the graph is the same three times

Five nodes, identical shape, identical declarations. Only the domain changes:

    receive_request  ->  fetch_readings  ->  assess  ->  apply_policy  ->  {3 terminals}

That repetition is the argument, not laziness. **Motus is the same everywhere;
what changes is your domain.** A reader who sees the same five boxes under a
cold-chain release, a pump prognosis and an emergency triage has learned the
product without being told.

## What each one is FOR

Each scenario is built to make one Motus record type do the work, because a
demo that shows the same thing three times has shown it once.

1. **cold_chain — the branch not taken.** The routing record carries the
   candidates and the selected one, so the trace shows what the run *nearly*
   did and which threshold excluded it. 40 minutes against a 45-minute policy:
   six minutes the other way and this lot goes to quarantine.

2. **pump_prognosis — the input that was missing.** A `Rejection` is a
   first-class write beside facts and decisions, so "the vibration channel was
   offline" is IN the evidence rather than absent from it. The prediction was
   made on two of three declared channels and the trace says so.

3. **emergency_triage — the information horizon.** Two rejections name what did
   not exist yet at decision time. Every malpractice case is fought with
   hindsight; this record freezes what was on the table at 22:14.

## Every number here is an integer, and that is a finding rather than a style

The demo recomputes each root **in the visitor's browser**, so the TypeScript
has to agree with the Python byte for byte. Writing that second implementation
is what surfaced #116: the canonical form is defined by CPython's `repr(float)`,
and `JSON.stringify(-14.0)` is `-14` while `json.dumps(-14.0)` is `-14.0`.

A genuine trace carrying one integral float does not survive `JSON.parse` +
`JSON.stringify` in Node: `derived_root` returns None and the validator reports
`T11 — payload_hash does not match the record`, which reads as tampering when
the truth is that a browser read the document.

So this file keeps to integers, which is #116's own option 1 applied to
ourselves. **The demo is not dodging the defect** — it is filed, it is
freeze-blocking, and the audit page says in one line that these traces are
integer-only and why.

## And the one thing all three share

Each domain carries a field the assessor **must not** use — a large customer, a
gold support tier, an insurance status. `assess` declares its reads, the runtime
holds it to the declaration, and a node that touched anything else **fails**.

So "we did not use that input" stops being a promise in a terms-of-service and
becomes a property of the execution. That is the sentence an enterprise needs
before it will let a model near a real process.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vitruvyan_motus import (
    Decision, EffectClass, EffectDescriptor, Fact, GraphSpec, InMemoryTraceSink,
    Rejection, Runtime, State,
)

OUT = Path(__file__).resolve().parent / "out" / "domains"

#: One clock for the whole demo, so a regenerated artefact differs only where
#: the scenario differs. A demo that changes its timestamps on every run makes
#: its own diff unreadable, and the diff is what a sceptic reads.
BASE = datetime(2026, 3, 14, 22, 14, tzinfo=timezone.utc)


def at(minutes: int = 0) -> datetime:
    return BASE + timedelta(minutes=minutes)


# --------------------------------------------------------------------------- #
# The graph, written once                                                      #
# --------------------------------------------------------------------------- #

def spec_for(name: str, reads: list[str], outcomes: list[str]) -> GraphSpec:
    """The same five nodes every time; only the names of things change.

    `reads_declared` on `assess` is the load-bearing line. It is all-or-nothing
    by design (node-protocol §3.2): declare a set and the runtime checks it
    against everything the node actually touched, so a partial declaration is
    worse than none. That is what makes the negative claim checkable.
    """
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": name,
        "version": "1.0.0",
        "entry": "receive_request",
        "nodes": [
            {"name": "receive_request", "effect_class": "pure"},
            # A READ of the outside world: the result is the effect, and
            # repeating it changes nothing out there. `recorded_effect`, and no
            # receipt is owed. Declaring it `external_effect` would be merely
            # over-conservative -- and would cost safe resumes, silently.
            {"name": "fetch_readings", "effect_class": "recorded_effect"},
            {"name": "assess", "effect_class": "pure", "reads_declared": reads,
             "writes_declared": ["band"]},
            {"name": "apply_policy", "effect_class": "pure",
             "writes_declared": ["disposition", "policy_version", "threshold"]},
            *({"name": outcome, "effect_class": "pure"} for outcome in outcomes),
        ],
        "transitions": {
            "receive_request": {"kind": "next", "to": "fetch_readings"},
            "fetch_readings": {"kind": "next", "to": "assess"},
            "assess": {"kind": "next", "to": "apply_policy"},
            # The record that carries the roads not taken. `map` is in the
            # SPEC, so the alternatives are evidence rather than a comment: a
            # reader sees every branch that was on offer and which one won.
            "apply_policy": {"kind": "route", "on": "disposition",
                             "map": {o: o for o in outcomes},
                             "default": outcomes[-1]},
            **{outcome: {"kind": "terminal"} for outcome in outcomes},
        },
    })


# --------------------------------------------------------------------------- #
# 1. Cold chain — the branch not taken                                         #
# --------------------------------------------------------------------------- #

COLD_CHAIN = {
    "name": "cold_chain_release",
    "prompt": "Lot FRZ-2291 logged a temperature excursion to -14 °C for 40 "
              "minutes. Can we release it?",
    "reads": ["excursion_minutes", "excursion_low_c"],
    "outcomes": ["release", "quarantine", "human_review"],
    "withheld": ("customer_priority", "strategic"),
}


def cold_chain_nodes():
    def receive_request(state: State) -> State:
        return (state
                .with_fact(Fact("lot_id", "FRZ-2291", "wms", at()))
                .with_fact(Fact("product", "frozen ready meals, 3 200 units",
                                "wms", at()))
                # In the state, and the assessor may not read it. A large
                # customer must not make a release easier, and this run can
                # prove it did not.
                .with_fact(Fact("customer_priority", "strategic", "crm", at())))

    def fetch_readings(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET coldchain-logger/lots/FRZ-2291/excursions"))
        return (state
                .with_fact(Fact("excursion_minutes", 40, "logger-4471", at(1)))
                .with_fact(Fact("excursion_low_c", -14, "logger-4471", at(1)))
                .with_fact(Fact("sensor_source", "logger-4471 fw 2.8.1",
                                "logger-4471", at(1))))

    def assess(state: State) -> State:
        minutes = state.fact("excursion_minutes")
        low = state.fact("excursion_low_c")
        band = "minor" if minutes < 45 and low > -18 else "major"
        return state.with_fact(Fact("band", band, "assessor", at(2)))

    def apply_policy(state: State) -> State:
        band = state.fact("band")
        disposition = {"minor": "release", "major": "quarantine"}[band]
        return (state
                .with_fact(Fact("policy_version", "cold-chain v2.1",
                                "policy", at(3)))
                .with_fact(Fact("threshold", "45 minutes above -18 °C",
                                "policy", at(3)))
                .with_decision(Decision(
                    "disposition", disposition, at(3),
                    reason="40 minutes is under the 45-minute threshold; "
                           "6 minutes longer and this lot goes to quarantine")))

    def release(state: State) -> State:
        return state.with_fact(Fact("outcome", "released to distribution",
                                    "policy", at(4)))

    def quarantine(state: State) -> State:
        return state.with_fact(Fact("outcome", "held", "policy", at(4)))

    def human_review(state: State) -> State:
        return state.with_fact(Fact("outcome", "queued for a person",
                                    "policy", at(4)))

    return {"receive_request": receive_request, "fetch_readings": fetch_readings,
            "assess": assess, "apply_policy": apply_policy,
            "release": release, "quarantine": quarantine,
            "human_review": human_review}


# --------------------------------------------------------------------------- #
# 2. Pump prognosis — the input that was missing                               #
# --------------------------------------------------------------------------- #

PUMP = {
    "name": "pump_prognosis",
    "prompt": "Can pump P-114 run to the scheduled June shutdown?",
    "reads": ["temperature_c", "flow_rate_m3h", "channels_read"],
    "outcomes": ["approve_run", "schedule_service", "human_review"],
    "withheld": ("contract_tier", "gold"),
}


def pump_nodes():
    def receive_request(state: State) -> State:
        return (state
                .with_fact(Fact("asset_id", "P-114", "cmms", at()))
                .with_fact(Fact("horizon_hours", 500, "cmms", at()))
                # A gold-tier contract must not buy a rosier prognosis.
                .with_fact(Fact("contract_tier", "gold", "crm", at())))

    def fetch_readings(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET historian/assets/P-114/channels?window=90d"))
        return (state
                .with_fact(Fact("temperature_c", 71, "historian", at(1)))
                .with_fact(Fact("flow_rate_m3h", 118, "historian", at(1)))
                .with_fact(Fact("channels_read", 2, "historian", at(1)))
                # The whole point of this scenario. A Rejection is a
                # first-class write beside facts and decisions, so what was
                # MISSING is inside the evidence instead of absent from it.
                .with_rejection(Rejection(
                    "vibration_channel",
                    "sensor offline since 2026-03-03; no samples in the window",
                    at(1),
                    evidence={"expected_channels": 3, "received_channels": 2,
                              "last_sample": "2026-03-03T04:12:00Z"})))

    def assess(state: State) -> State:
        # Reads the two channels it has AND the count, which is why the count
        # is a declared read: a confidence computed from a partial input has to
        # know it is partial.
        channels = state.fact("channels_read")
        band = "confident" if channels == 3 else "partial"
        return state.with_fact(Fact("band", band, "assessor", at(2)))

    def apply_policy(state: State) -> State:
        band = state.fact("band")
        disposition = {"confident": "approve_run",
                       "partial": "approve_run"}[band]
        return (state
                .with_fact(Fact("policy_version", "rotating-equipment v4.0",
                                "policy", at(3)))
                .with_fact(Fact("threshold",
                                "approve when no channel exceeds its alarm band",
                                "policy", at(3)))
                .with_decision(Decision(
                    "disposition", disposition, at(3),
                    reason="the two channels that reported are inside their "
                           "alarm bands. The vibration channel did not report "
                           "and was not evaluated")))

    def approve_run(state: State) -> State:
        return state.with_fact(Fact("outcome", "cleared to the June shutdown",
                                    "policy", at(4)))

    def schedule_service(state: State) -> State:
        return state.with_fact(Fact("outcome", "service booked", "policy", at(4)))

    def human_review(state: State) -> State:
        return state.with_fact(Fact("outcome", "queued for an engineer",
                                    "policy", at(4)))

    return {"receive_request": receive_request, "fetch_readings": fetch_readings,
            "assess": assess, "apply_policy": apply_policy,
            "approve_run": approve_run, "schedule_service": schedule_service,
            "human_review": human_review}


# --------------------------------------------------------------------------- #
# 3. Emergency triage — the information horizon                                #
# --------------------------------------------------------------------------- #

TRIAGE = {
    "name": "emergency_triage",
    "prompt": "61-year-old, chest pain, vitals on file. What triage code?",
    "reads": ["systolic_mmhg", "heart_rate_bpm", "spo2_pct", "readings_as_of"],
    "outcomes": ["code_green", "code_yellow", "code_red"],
    "withheld": ("insurance_status", "uninsured"),
}


def triage_nodes():
    def receive_request(state: State) -> State:
        return (state
                .with_fact(Fact("case_id", "ED-2026-0314-118", "eds", at()))
                .with_fact(Fact("presenting_complaint", "chest pain, 2 hours",
                                "eds", at()))
                .with_fact(Fact("age_years", 61, "eds", at()))
                # Must not touch triage. The record proves it did not.
                .with_fact(Fact("insurance_status", "uninsured", "eds", at())))

    def fetch_readings(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET monitor/cases/ED-2026-0314-118/vitals"))
        return (state
                .with_fact(Fact("systolic_mmhg", 138, "monitor", at()))
                .with_fact(Fact("heart_rate_bpm", 88, "monitor", at()))
                .with_fact(Fact("spo2_pct", 97, "monitor", at()))
                # The horizon, written down. Every malpractice case is fought
                # with hindsight; this is the line that says what was on the
                # table at 22:14 and what was not there yet.
                .with_fact(Fact("readings_as_of", "2026-03-14T22:14:00Z",
                                "monitor", at()))
                .with_rejection(Rejection(
                    "ecg", "not performed at decision time", at(),
                    evidence={"ordered": True, "performed_at": None}))
                .with_rejection(Rejection(
                    "second_blood_pressure",
                    "one reading available at decision time; the protocol asks "
                    "for two", at(),
                    evidence={"readings_available": 1, "protocol_minimum": 2})))

    def assess(state: State) -> State:
        systolic = state.fact("systolic_mmhg")
        rate = state.fact("heart_rate_bpm")
        spo2 = state.fact("spo2_pct")
        state.fact("readings_as_of")
        stable = 90 <= systolic <= 160 and 50 <= rate <= 110 and spo2 >= 94
        return state.with_fact(Fact("band", "stable" if stable else "unstable",
                                    "assessor", at()))

    def apply_policy(state: State) -> State:
        band = state.fact("band")
        disposition = {"stable": "code_green", "unstable": "code_red"}[band]
        return (state
                .with_fact(Fact("policy_version", "triage v3.4", "policy", at()))
                .with_fact(Fact("threshold",
                                "systolic 90-160, rate 50-110, SpO2 >= 94",
                                "policy", at()))
                .with_decision(Decision(
                    "disposition", disposition, at(),
                    reason="every vital available at 22:14 was inside its band. "
                           "No ECG and one blood-pressure reading were available "
                           "to this decision")))

    def code_green(state: State) -> State:
        return state.with_fact(Fact("outcome", "green — may wait", "policy", at()))

    def code_yellow(state: State) -> State:
        return state.with_fact(Fact("outcome", "yellow", "policy", at()))

    def code_red(state: State) -> State:
        return state.with_fact(Fact("outcome", "red — immediate", "policy", at()))

    return {"receive_request": receive_request, "fetch_readings": fetch_readings,
            "assess": assess, "apply_policy": apply_policy,
            "code_green": code_green, "code_yellow": code_yellow,
            "code_red": code_red}


# --------------------------------------------------------------------------- #

DOMAINS = [
    (COLD_CHAIN, cold_chain_nodes),
    (PUMP, pump_nodes),
    (TRIAGE, triage_nodes),
]


def merge_index_by_name(previous, current):
    """Merge one or more cached indexes, preferring complete anchors."""
    def entries(value):
        if isinstance(value, dict):
            return [value]
        if value and all(isinstance(item, dict) for item in value):
            return value
        return [item for copy in value for item in entries(copy)]

    prior_entries = entries(previous)
    old = {}
    for item in prior_entries:
        old.setdefault(item.get("name"), []).append(item)
    current_entries = current if isinstance(current, list) else [current]
    metadata = ("anchor", "proof_file", "verify_with")

    def anchor_rank(item):
        state = (item.get("anchor") or {}).get("state")
        return (2 if state == "anchored" else 1 if state else 0,
                sum(key in item for key in metadata))

    merged = []
    for item in current_entries:
        entry = dict(item)
        candidates = [item for item in old.get(entry.get("name"), [])
                      if item.get("root") == entry.get("root")]
        if any(key in entry for key in metadata):
            candidates.append(entry)
        if candidates:
            # Pick each field from the strongest same-root copy. This handles
            # a fresh per-scenario cache alongside a stale top-level cache.
            for key in metadata:
                available = [item for item in candidates if key in item]
                if available:
                    entry[key] = max(available, key=anchor_rank)[key]
        else:
            changed = [item for item in old.get(entry.get("name"), [])
                       if item.get("root") != entry.get("root")]
            if changed and any(any(key in item for key in metadata)
                               for item in changed):
                print(f"{entry.get('name')}: root changed; dropping old anchor")
        merged.append(entry)
    return merged if isinstance(current, list) else merged[0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    old_index = (
        json.loads((OUT / "index.json").read_text(encoding="utf-8"))
        if (OUT / "index.json").exists() else [])
    index = []
    for domain, build in DOMAINS:
        spec = spec_for(domain["name"], domain["reads"], domain["outcomes"])
        result = Runtime(spec, build(), sink=InMemoryTraceSink()).run(
            State.empty(domain["prompt"]), run_id=f"demo-{domain['name']}")
        document = result.trace.to_dict()
        path = OUT / f"{domain['name']}.json"
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        withheld_key, withheld_value = domain["withheld"]
        index.append({
            "name": domain["name"],
            "prompt": domain["prompt"],
            "root": result.trace.root,
            "records": len(result.trace.records),
            "declared_reads": domain["reads"],
            "withheld_from_assess": {"key": withheld_key, "value": withheld_value},
            "artefact": f"demo/out/domains/{domain['name']}.json",
        })
        print(f"{domain['name']:20s} {result.trace.root}  "
              f"{len(result.trace.records)} records")

    index = merge_index_by_name(old_index, index)
    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
