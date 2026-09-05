"""Two decisions, two domains, one evidence model — the demo's two pills.

    .venv/bin/python demo/two_scenarios.py

Writes `demo/out/scenarios/<name>/`. Both scenarios are invented and each trace
says so in its own first fact; **the traces are not invented**. They come out of
the shipped runtime, they validate against the shipped contract, and their
roots are anchored on OpenTimestamps by `anchor_scenarios.py`.

## Why two, and why these two

A visitor who sees one scenario learns a story. A visitor who sees the same
four acts over **a loan application** and **a transformer at a substation**
learns the product, because the only thing that changed was the domain.

    Motus does not know that "banking" or "energy" are domains. It orchestrates
    execution and preserves evidence, and the vocabulary is yours.

## The two scenarios prove their negative claims by DIFFERENT mechanisms

That is deliberate, and it is the strongest argument in the file.

**Credit — `reads_declared`.** The application carries the applicant's name,
date of birth, nationality and postcode. Postcode is the classic proxy: it is
how a model learns a neighbourhood without being told to. The assessing node
declares its reads, the declaration is all-or-nothing (node-protocol §3.2), and
a node that reached for anything else does not warn — it fails and the run
stops. So "the assessment did not look at where they live" is checkable.

**Infrastructure — `effect_class`.** Every node that decides is declared
`pure`, and a pure node that touched the outside world is a violation the
runtime raises rather than a note it files. The only effects in the whole run
are three declared READS. So "nothing here opened a breaker" is not a promise
about intent; it is the observation that this run recorded no external effect
at all, and the record enumerates every effect it did record.

**What neither of them proves, and the page says so.** Motus can show that a
node did not READ a value. It cannot show that nothing was inferred from the
values it did read. That boundary is real and stating it is what makes the rest
credible.

## Documents and chunks are facts with a source

There is no separate "chunk" record type and there should not be. A clause that
influenced a decision is a value the run used, so it is a `Fact` whose `source`
names the document it was quoted from, written by the node that retrieved it,
beside the `recorded_effect` describing the retrieval.

The demo page groups facts by `source` to render the documents panel. That
panel is therefore **derived from the trace** rather than authored beside it: it
cannot drift, and it cannot show a document the run did not actually consult.

## Integers only, and 35.38% is stored as 3538

The page re-derives each root in the visitor's browser, so the TypeScript has
to agree with the Python byte for byte, and #116 says it cannot when a float is
present: the canonical form is CPython's `repr(float)`, and JavaScript disagrees
about a great many of them.

So a ratio is carried in **basis points** — `debt_to_income_bp: 3538`, threshold
`3500` — and a temperature in whole degrees. Not a house style: the alternative
is publishing a root the visitor's own machine would refuse to reproduce, on a
page whose entire argument is that they should not have to take our word.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vitruvyan_motus import (
    Decision, EffectClass, EffectDescriptor, Fact, GraphSpec, InMemoryTraceSink,
    Rejection, Runtime, State, TraceBundle,
)

OUT = Path(__file__).resolve().parent / "out" / "scenarios"

#: One clock per scenario, so a regenerated artefact differs only where the
#: scenario differs. A demo whose timestamps move on every run makes its own
#: diff unreadable, and the diff is what a sceptic reads.
CREDIT_BASE = datetime(2026, 6, 9, 11, 20, tzinfo=timezone.utc)
GRID_BASE = datetime(2026, 6, 9, 3, 47, tzinfo=timezone.utc)


def _at(base: datetime):
    def at(minutes: int = 0) -> datetime:
        return base + timedelta(minutes=minutes)
    return at


# =========================================================================== #
# 1. Consumer credit — a person and access to financial services              #
# =========================================================================== #

CREDIT_PROMPT = ("Can John Smith's €35,000 loan application be approved "
                 "automatically?")
CREDIT_ANSWER = (
    "No. This application cannot be approved automatically. It must go to a "
    "person, because the verified debt-to-income ratio is above Acme Bank "
    "Europe's automatic-approval threshold.")

#: In the state for the whole run, read by nothing that assesses. Postcode is
#: the one that matters: it is how a model learns a neighbourhood without being
#: told to.
CREDIT_PROTECTED = ["applicant_name", "date_of_birth", "nationality",
                    "postcode"]


def credit_spec() -> GraphSpec:
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "credit_review",
        "version": "1.0.0",
        "entry": "loan_application",
        "nodes": [
            {"name": "loan_application", "effect_class": "pure"},
            {"name": "application_check", "effect_class": "pure"},
            # Reads of the outside world: the result IS the effect and
            # repeating it changes nothing out there, so `recorded_effect` and
            # no receipt is owed.
            {"name": "income_verification", "effect_class": "recorded_effect"},
            {"name": "existing_obligations", "effect_class": "recorded_effect"},
            {"name": "creditworthiness_assessment", "effect_class": "pure",
             "reads_declared": ["verified_monthly_income_eur",
                                "mortgage_payment_eur",
                                "vehicle_finance_eur",
                                "requested_amount_eur"],
             "writes_declared": ["debt_to_income_bp", "total_obligations_eur"]},
            {"name": "policy_evaluation", "effect_class": "recorded_effect",
             "writes_declared": ["credit_route", "policy_version",
                                 "dti_threshold_bp", "policy_clause_dti",
                                 "policy_clause_oversight",
                                 "threshold_exceeded"]},
            {"name": "human_review", "effect_class": "pure"},
            {"name": "auto_approve", "effect_class": "pure"},
            {"name": "auto_decline", "effect_class": "pure"},
            {"name": "recommendation", "effect_class": "pure"},
        ],
        "transitions": {
            "loan_application": {"kind": "next", "to": "application_check"},
            "application_check": {"kind": "next", "to": "income_verification"},
            "income_verification": {"kind": "next", "to": "existing_obligations"},
            "existing_obligations": {"kind": "next",
                                     "to": "creditworthiness_assessment"},
            "creditworthiness_assessment": {"kind": "next",
                                            "to": "policy_evaluation"},
            # `auto_approve` is a candidate in the SPEC, so its absence from
            # this run is evidence rather than a claim made afterwards.
            "policy_evaluation": {"kind": "route", "on": "credit_route",
                                  "map": {"auto_approve": "auto_approve",
                                          "human_review": "human_review",
                                          "auto_decline": "auto_decline"},
                                  "default": "human_review"},
            "human_review": {"kind": "next", "to": "recommendation"},
            "auto_approve": {"kind": "terminal"},
            "auto_decline": {"kind": "terminal"},
            "recommendation": {"kind": "terminal"},
        },
    })


def credit_nodes():
    at = _at(CREDIT_BASE)

    def loan_application(state: State) -> State:
        return (state
                .with_fact(Fact(
                    "record_is_synthetic",
                    "invented scenario — no real applicant, no real bank",
                    "loan_application", at()))
                .with_fact(Fact("application_ref", "LN-2026-77104",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("institution", "Acme Bank Europe",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("product", "Home Improvement Loan",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("requested_amount_eur", 35000,
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("declared_monthly_income_eur", 4800,
                                "Loan Application — John Smith", at()))
                # Present for the whole run, read by nothing that assesses.
                .with_fact(Fact("applicant_name", "John Smith",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("date_of_birth", "1984-11-03",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("nationality", "IE",
                                "Loan Application — John Smith", at()))
                .with_fact(Fact("postcode", "D08 R2X4",
                                "Loan Application — John Smith", at())))

    def application_check(state: State) -> State:
        return state.with_fact(Fact(
            "application_complete", "yes", "application_check", at(1)))

    def income_verification(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET income-verification/statements/LN-2026-77104"))
        return (state
                .with_fact(Fact("employer", "Acme Manufacturing Ltd",
                                "Employment & Income Statement — John Smith",
                                at(2)))
                .with_fact(Fact("verified_monthly_income_eur", 4720,
                                "Employment & Income Statement — John Smith",
                                at(2)))
                # The warning, and it is a first-class write rather than a log
                # line: the applicant said one number, the statement said
                # another, and the record carries BOTH plus which one was used.
                .with_rejection(Rejection(
                    "declared_monthly_income_eur",
                    "the applicant declared €4,800 and the verified "
                    "statement reports €4,720. The verified figure is used "
                    "in the assessment",
                    at(2),
                    evidence={"declared_eur": 4800, "verified_eur": 4720,
                              "used": "verified_monthly_income_eur"})))

    def existing_obligations(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET credit-bureau/obligations/LN-2026-77104"))
        return (state
                .with_fact(Fact("mortgage_payment_eur", 1250,
                                "Existing Credit Obligations — John Smith",
                                at(3)))
                .with_fact(Fact("vehicle_finance_eur", 420,
                                "Existing Credit Obligations — John Smith",
                                at(3))))

    def creditworthiness_assessment(state: State) -> State:
        income = state.fact("verified_monthly_income_eur")
        mortgage = state.fact("mortgage_payment_eur")
        vehicle = state.fact("vehicle_finance_eur")
        state.fact("requested_amount_eur")
        obligations = mortgage + vehicle
        # Basis points, because a browser and CPython do not agree about
        # floats and this root is re-derived in a browser. See motus#116.
        bp = round(obligations * 10000 / income)
        return (state
                .with_fact(Fact("total_obligations_eur", obligations,
                                "creditworthiness_assessment", at(4)))
                .with_fact(Fact("debt_to_income_bp", bp,
                                "creditworthiness_assessment", at(4))))

    def policy_evaluation(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET policy-store/acme-bank-europe/retail-credit-policy@v3.2"))
        bp = state.fact("debt_to_income_bp")
        threshold = 3500
        exceeded = bp > threshold
        return (state
                .with_fact(Fact("policy_version",
                                "Acme Bank Europe — Retail Credit Policy v3.2",
                                "Acme Bank Europe — Retail Credit Policy v3.2",
                                at(5)))
                .with_fact(Fact("dti_threshold_bp", threshold,
                                "Acme Bank Europe — Retail Credit Policy v3.2 §4.1",
                                at(5)))
                .with_fact(Fact(
                    "policy_clause_dti",
                    "Applications with debt-to-income above 35% cannot receive "
                    "automatic approval and must proceed to human review.",
                    "Acme Bank Europe — Retail Credit Policy v3.2 §4.1",
                    at(5)))
                .with_fact(Fact(
                    "policy_clause_oversight",
                    "Referral cases require review by an authorised credit "
                    "officer before a final lending decision is made.",
                    "Acme Bank Europe — Human Oversight Policy §1.3",
                    at(5)))
                .with_fact(Fact("threshold_exceeded", "yes" if exceeded else "no",
                                "policy_evaluation", at(5)))
                .with_decision(Decision(
                    "credit_route", "human_review" if exceeded else "auto_approve",
                    at(5),
                    reason=f"verified debt-to-income is {bp / 100:.2f}% against "
                           f"an automatic-approval threshold of "
                           f"{threshold / 100:.0f}%. Policy §4.1 does not "
                           f"permit automatic approval above it")))

    def human_review(state: State) -> State:
        return (state
                .with_fact(Fact("handoff", "authorised credit officer, queue CR-2",
                                "human_review", at(6)))
                .with_fact(Fact(
                    "handed_over",
                    "the verified income, the declared-versus-verified mismatch, "
                    "the obligations and the debt-to-income figure",
                    "human_review", at(6))))

    def auto_approve(state: State) -> State:          # pragma: no cover
        return state.with_fact(Fact("outcome", "approved without review",
                                    "auto_approve", at(7)))

    def auto_decline(state: State) -> State:          # pragma: no cover
        return state.with_fact(Fact("outcome", "declined without review",
                                    "auto_decline", at(7)))

    def recommendation(state: State) -> State:
        return (state
                .with_fact(Fact(
                    "recommendation",
                    "refer to an authorised credit officer; no automatic "
                    "approval and no automatic decline", "recommendation", at(7)))
                .with_fact(Fact("automated_decision_issued", "no",
                                "recommendation", at(7))))

    return {"loan_application": loan_application,
            "application_check": application_check,
            "income_verification": income_verification,
            "existing_obligations": existing_obligations,
            "creditworthiness_assessment": creditworthiness_assessment,
            "policy_evaluation": policy_evaluation,
            "human_review": human_review, "auto_approve": auto_approve,
            "auto_decline": auto_decline, "recommendation": recommendation}


# =========================================================================== #
# 2. Critical infrastructure — a transformer at a substation                  #
# =========================================================================== #

GRID_PROMPT = ("Can Acme Energy keep Substation 17 in automatic operation "
               "after the transformer anomaly?")
GRID_ANSWER = (
    "No. Substation 17 should not stay in unrestricted automatic operation. "
    "The recorded excursion on transformer T-17A is above the "
    "automatic-operation threshold and needs an authorised operator to review "
    "it.")

#: Nothing here is withheld from a node. This scenario makes its negative
#: claims from effect classes instead: every deciding node is `pure`, and the
#: only effects in the run are three declared reads.
GRID_PROTECTED: list[str] = []


def grid_spec() -> GraphSpec:
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "substation_safety",
        "version": "1.0.0",
        "entry": "anomaly_detected",
        "nodes": [
            {"name": "anomaly_detected", "effect_class": "pure"},
            {"name": "sensor_validation", "effect_class": "recorded_effect"},
            {"name": "operating_context", "effect_class": "recorded_effect"},
            {"name": "maintenance_history", "effect_class": "recorded_effect"},
            # PURE, and that is the load-bearing declaration in this scenario.
            # A pure node that touched a breaker is a violation the runtime
            # raises, not a note it files.
            {"name": "safety_assessment", "effect_class": "pure",
             "reads_declared": ["peak_temperature_c", "threshold_c",
                                "excursion_minutes", "cooling_advisory"],
             "writes_declared": ["excursion_exceeds_threshold",
                                 "margin_c"]},
            {"name": "operational_policy", "effect_class": "pure",
             "writes_declared": ["operation_mode", "policy_version",
                                 "policy_clause_threshold",
                                 "policy_clause_oversight"]},
            {"name": "human_operator_review", "effect_class": "pure"},
            {"name": "isolate_asset", "effect_class": "pure"},
            {"name": "continue_service", "effect_class": "pure"},
            {"name": "recommendation", "effect_class": "pure"},
        ],
        "transitions": {
            "anomaly_detected": {"kind": "next", "to": "sensor_validation"},
            "sensor_validation": {"kind": "next", "to": "operating_context"},
            "operating_context": {"kind": "next", "to": "maintenance_history"},
            "maintenance_history": {"kind": "next", "to": "safety_assessment"},
            "safety_assessment": {"kind": "next", "to": "operational_policy"},
            "operational_policy": {"kind": "route", "on": "operation_mode",
                                   "map": {"continue_service": "continue_service",
                                           "review_required": "human_operator_review",
                                           "isolate_asset": "isolate_asset"},
                                   "default": "human_operator_review"},
            "human_operator_review": {"kind": "next", "to": "recommendation"},
            "isolate_asset": {"kind": "terminal"},
            "continue_service": {"kind": "terminal"},
            "recommendation": {"kind": "terminal"},
        },
    })


def grid_nodes():
    at = _at(GRID_BASE)

    def anomaly_detected(state: State) -> State:
        return (state
                .with_fact(Fact(
                    "record_is_synthetic",
                    "invented scenario — no real operator, no real "
                    "substation", "anomaly_detected", at()))
                .with_fact(Fact("operator", "Acme Energy Grid Operations",
                                "Acme Energy — Sensor Event Report", at()))
                .with_fact(Fact("facility", "Substation 17",
                                "Acme Energy — Sensor Event Report", at()))
                .with_fact(Fact("asset", "Transformer T-17A",
                                "Acme Energy — Sensor Event Report", at()))
                .with_fact(Fact("event", "abnormal temperature excursion",
                                "Acme Energy — Sensor Event Report", at())))

    def sensor_validation(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET scada/substation-17/assets/T-17A/events?window=24h"))
        return (state
                .with_fact(Fact("peak_temperature_c", 96,
                                "Acme Energy — Sensor Event Report", at(1)))
                .with_fact(Fact("excursion_minutes", 7,
                                "Acme Energy — Sensor Event Report", at(1)))
                .with_fact(Fact("sensor_channel", "T-17A winding probe 2, "
                                                  "calibrated 2026-04-30",
                                "Acme Energy — Sensor Event Report", at(1)))
                # What was NOT available, inside the evidence rather than
                # absent from it.
                .with_rejection(Rejection(
                    "oil_analysis",
                    "not sampled at assessment time; the last sample predates "
                    "this excursion",
                    at(1),
                    evidence={"requested": True, "sampled_at": None})))

    def operating_context(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET document-store/assets/T-17A/operating-manual@rev6"))
        return (state
                .with_fact(Fact("threshold_c", 92,
                                "Transformer T-17A Operating Manual §6.2",
                                at(2)))
                .with_fact(Fact(
                    "manual_clause",
                    "Sustained operation above 92°C requires inspection "
                    "before unrestricted automatic operation resumes.",
                    "Transformer T-17A Operating Manual §6.2", at(2))))

    def maintenance_history(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET cmms/assets/T-17A/maintenance?limit=5"))
        return (state
                .with_fact(Fact("days_since_inspection", 42,
                                "Maintenance Record — T-17A", at(3)))
                .with_fact(Fact("cooling_advisory", "present",
                                "Maintenance Record — T-17A", at(3))))

    def safety_assessment(state: State) -> State:
        peak = state.fact("peak_temperature_c")
        threshold = state.fact("threshold_c")
        state.fact("excursion_minutes")
        state.fact("cooling_advisory")
        return (state
                .with_fact(Fact("margin_c", peak - threshold,
                                "safety_assessment", at(4)))
                .with_fact(Fact("excursion_exceeds_threshold",
                                "yes" if peak > threshold else "no",
                                "safety_assessment", at(4))))

    def operational_policy(state: State) -> State:
        exceeded = state.fact("excursion_exceeds_threshold") == "yes"
        return (state
                .with_fact(Fact("policy_version",
                                "Acme Energy — Grid Safety Policy v4.1",
                                "Acme Energy — Grid Safety Policy v4.1",
                                at(5)))
                .with_fact(Fact(
                    "policy_clause_threshold",
                    "Transformer excursions exceeding the declared "
                    "automatic-operation threshold require operator review.",
                    "Acme Energy — Grid Safety Policy v4.1 §3.4", at(5)))
                .with_fact(Fact(
                    "policy_clause_oversight",
                    "The automated system may recommend isolation, restricted "
                    "operation or continued service. The final operational "
                    "action after a threshold breach requires an authorised "
                    "operator.",
                    "Acme Energy — Human Oversight Procedure §2.2", at(5)))
                .with_decision(Decision(
                    "operation_mode",
                    "review_required" if exceeded else "continue_service",
                    at(5),
                    reason="the excursion peaked at 96°C against a 92°C "
                           "threshold, with a cooling advisory already on the "
                           "asset. Policy §3.4 requires operator review "
                           "before unrestricted automatic operation resumes")))

    def human_operator_review(state: State) -> State:
        return (state
                .with_fact(Fact("handoff",
                                "authorised operator, Substation 17 control desk",
                                "human_operator_review", at(6)))
                .with_fact(Fact(
                    "handed_over",
                    "the excursion peak and duration, the manual clause, the "
                    "cooling advisory and the missing oil analysis",
                    "human_operator_review", at(6))))

    def isolate_asset(state: State) -> State:         # pragma: no cover
        return state.with_fact(Fact("outcome", "isolated automatically",
                                    "isolate_asset", at(7)))

    def continue_service(state: State) -> State:      # pragma: no cover
        return state.with_fact(Fact("outcome", "kept in service automatically",
                                    "continue_service", at(7)))

    def recommendation(state: State) -> State:
        return (state
                .with_fact(Fact(
                    "recommendation",
                    "restrict to operator-supervised operation and inspect "
                    "before unrestricted automatic operation resumes",
                    "recommendation", at(7)))
                .with_fact(Fact("automated_action_issued", "no",
                                "recommendation", at(7))))

    return {"anomaly_detected": anomaly_detected,
            "sensor_validation": sensor_validation,
            "operating_context": operating_context,
            "maintenance_history": maintenance_history,
            "safety_assessment": safety_assessment,
            "operational_policy": operational_policy,
            "human_operator_review": human_operator_review,
            "isolate_asset": isolate_asset,
            "continue_service": continue_service,
            "recommendation": recommendation}


# =========================================================================== #

SCENARIOS = [
    {"name": "credit_review", "run_id": "demo-credit-review-77104",
     "prompt": CREDIT_PROMPT, "answer": CREDIT_ANSWER,
     "protected": CREDIT_PROTECTED, "spec": credit_spec, "nodes": credit_nodes,
     "tamper_node": "human_review"},
    {"name": "substation_safety", "run_id": "demo-substation-safety-17a",
     "prompt": GRID_PROMPT, "answer": GRID_ANSWER,
     "protected": GRID_PROTECTED, "spec": grid_spec, "nodes": grid_nodes,
     "tamper_node": "human_operator_review"},
]


def main() -> None:
    index = []
    for scenario in SCENARIOS:
        out = OUT / scenario["name"]
        out.mkdir(parents=True, exist_ok=True)
        declaration = scenario["spec"]()
        result = Runtime(declaration, scenario["nodes"](),
                         sink=InMemoryTraceSink()).run(
            State.empty(scenario["prompt"]), run_id=scenario["run_id"])
        bundle = TraceBundle(declaration, result.trace)

        (out / "trace.json").write_text(
            json.dumps(result.trace.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (out / "graph.json").write_text(
            json.dumps(declaration.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (out / "explain.json").write_text(
            json.dumps(bundle.explain(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        # The root string exactly as committed: no newline, no quotes.
        (out / "root.txt").write_bytes(result.trace.root.encode("utf-8"))

        entry = {
            "name": scenario["name"],
            "run_id": scenario["run_id"],
            "prompt": scenario["prompt"],
            "answer": scenario["answer"],
            "root": result.trace.root,
            "bundle_fingerprint": bundle.fingerprint,
            "graph_fingerprint": declaration.graph_fingerprint,
            "records": len(result.trace.records),
            "protected_attributes": scenario["protected"],
            "tamper_node": scenario["tamper_node"],
        }
        (out / "index.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        index.append(entry)
        print(f"{scenario['name']:20s} {result.trace.root}  "
              f"{len(result.trace.records)} records  "
              f"{bundle.explain()['terminal']}")

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
