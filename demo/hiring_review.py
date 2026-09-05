"""The hiring screen that is not allowed to reject anybody — the demo's spine.

    .venv/bin/python demo/hiring_review.py

Writes `demo/out/hiring/` : the trace, the graph declaration, the bundle, and
an index the page reads. The scenario is invented and the artefact says so in
its own first fact; **the trace is not invented**. It comes out of the shipped
runtime, it validates against the shipped contract, and its root is anchored on
OpenTimestamps by `anchor_domains.py`.

## Why recruitment, and why this particular question

"Should this candidate be automatically rejected?" is a question a stranger
understands with no preparation, and it is the one place where everybody
already agrees that a machine acting alone is not acceptable. So the scenario
needs no explanation of the stakes, which buys the page ten seconds it would
otherwise spend on setup.

The candidate **fails** the automated screen — 54 months of relevant experience
against a 60-month minimum. That is deliberate. A candidate who passes makes
"we did not reject them" trivially true; a candidate who fails and is *still*
not rejected is the whole point, and the record has to show which clause
stopped it.

## What this graph is built to make checkable, one line each

1. **`auto_reject` was on the map and was not taken.** It is in the spec, so
   the routing record carries it as a candidate with `taken: false`. The run
   did not merely fail to reject — it *declined* to, and the alternative it
   declined is in the evidence.

2. **No assessing node read a protected attribute.** `receive_application`
   writes the name, the date of birth, the nationality and the photo into the
   state, where they sit for the rest of the run. `check_qualification` and
   `assess_experience` declare their reads, and `reads_declared` is
   all-or-nothing (node-protocol §3.2): the runtime checks the declaration
   against everything the node actually touched, and a node that reached for
   anything else **fails** rather than warning.

   So "the screen did not look at who they are" stops being a policy sentence
   and becomes a property of the execution, checkable by somebody who was not
   there and does not trust us.

3. **What was missing at decision time is IN the record.** The reference check
   had not come back. A `Rejection` is a first-class write beside facts and
   decisions, so the gap is inside the evidence instead of absent from it —
   which is the difference between a record that survives a hearing and one
   that invites the question "what else is not in here?".

## Integers only, and that is a finding rather than a house style

The page re-derives this root **in the visitor's browser**, so the TypeScript
has to agree with the Python byte for byte, and #116 says it cannot when a
float is present: the canonical form is CPython's `repr(float)`, and
`JSON.stringify(4.5)` is `4.5` while a great many other floats disagree.

Hence experience is carried in **months** and not in years. 54, not 4.5. The
constraint is real, it is filed, it blocks the 1.0.0 freeze, and the honest
response in the meantime is to stay inside what a second implementation can
reproduce — not to publish a root the visitor's own machine would refuse.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vitruvyan_motus import (
    Decision, EffectClass, EffectDescriptor, Fact, GraphSpec, InMemoryTraceSink,
    Rejection, Runtime, State, TraceBundle,
)

OUT = Path(__file__).resolve().parent / "out" / "hiring"

#: One clock, so a regenerated artefact differs only where the scenario does.
BASE = datetime(2026, 5, 12, 9, 40, tzinfo=timezone.utc)


def at(minutes: int = 0) -> datetime:
    return BASE + timedelta(minutes=minutes)


PROMPT = "Should this candidate be automatically rejected for the job?"

#: Present in the state for the whole run, and read by nobody after the node
#: that wrote them. The page computes that claim from the trace rather than
#: taking it from this list — this is only what the scenario puts on the table.
PROTECTED = [
    "applicant_name",
    "date_of_birth",
    "nationality",
    "photo_reference",
]


def spec() -> GraphSpec:
    """Seven nodes, and the two that assess are the two that declare.

    `human_review` is **not** a terminal. The run continues through it to
    `final_recommendation`, because a graph that ended at "a person will look
    at this" would leave the record silent about what was handed over.
    """
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "hiring_review",
        "version": "1.0.0",
        "entry": "receive_application",
        "nodes": [
            {"name": "receive_application", "effect_class": "pure"},
            # A READ of the document store: the result IS the effect and
            # repeating it changes nothing out there, so `recorded_effect` and
            # no receipt is owed. Declaring `external_effect` here would be
            # over-conservative and would silently cost a safe resume.
            {"name": "parse_cv", "effect_class": "recorded_effect"},
            {"name": "check_qualification", "effect_class": "pure",
             "reads_declared": ["qualification_level", "required_qualification"],
             "writes_declared": ["qualification_met"]},
            {"name": "assess_experience", "effect_class": "pure",
             "reads_declared": ["relevant_experience_months",
                                "required_experience_months"],
             "writes_declared": ["experience_met", "shortfall_months"]},
            {"name": "apply_policy", "effect_class": "pure",
             # `screen_result` belongs here, and the first draft of this file
             # left it out: the runtime refused the node with
             # `undeclared_write 'screen_result'` and the run did not finish.
             # That is the guarantee this page sells, applied to the page.
             "writes_declared": ["disposition", "policy_version",
                                 "policy_clause", "screen_result"]},
            {"name": "human_review", "effect_class": "pure"},
            {"name": "auto_reject", "effect_class": "pure"},
            {"name": "auto_advance", "effect_class": "pure"},
            {"name": "final_recommendation", "effect_class": "pure"},
        ],
        "transitions": {
            "receive_application": {"kind": "next", "to": "parse_cv"},
            "parse_cv": {"kind": "next", "to": "check_qualification"},
            "check_qualification": {"kind": "next", "to": "assess_experience"},
            "assess_experience": {"kind": "next", "to": "apply_policy"},
            # The record that carries the road not taken. `auto_reject` is a
            # candidate here, in the SPEC, so its absence from the run is
            # evidence rather than a claim somebody made afterwards.
            "apply_policy": {"kind": "route", "on": "disposition",
                             "map": {"auto_reject": "auto_reject",
                                     "human_review": "human_review",
                                     "auto_advance": "auto_advance"},
                             "default": "human_review"},
            "human_review": {"kind": "next", "to": "final_recommendation"},
            "auto_reject": {"kind": "terminal"},
            "auto_advance": {"kind": "terminal"},
            "final_recommendation": {"kind": "terminal"},
        },
    })


def nodes() -> dict[str, object]:
    def receive_application(state: State) -> State:
        return (state
                # The artefact declares its own fictionality, in the evidence
                # rather than in a caption somebody can crop out.
                .with_fact(Fact(
                    "record_is_synthetic",
                    "invented scenario — no real applicant, no real employer",
                    "demo", at()))
                .with_fact(Fact("applicant_ref", "APP-2026-00421", "ats", at()))
                .with_fact(Fact("role", "Maintenance planner, night shift",
                                "ats", at()))
                # Protected attributes. They are IN the state for the whole
                # run, which is the only way "nothing read them" can be
                # checked. A field that was never there proves nothing.
                .with_fact(Fact("applicant_name", "Nowak, A.", "ats", at()))
                .with_fact(Fact("date_of_birth", "1979-04-02", "ats", at()))
                .with_fact(Fact("nationality", "PL", "ats", at()))
                .with_fact(Fact("photo_reference",
                                "cv-store://APP-2026-00421/photo.jpg",
                                "ats", at()))
                # The bar, written down before anything is measured against it.
                .with_fact(Fact("required_qualification",
                                "engineering degree or equivalent",
                                "policy", at()))
                .with_fact(Fact("required_experience_months", 60,
                                "policy", at())))

    def parse_cv(state: State, ctx) -> State:
        ctx.record_effect(EffectDescriptor(
            EffectClass.RECORDED_EFFECT,
            "GET cv-store/applications/APP-2026-00421/document"))
        return (state
                .with_fact(Fact("qualification_level",
                                "BSc Mechanical Engineering", "cv-parser",
                                at(1)))
                # Months, not years. 54 rather than 4.5 — see the module
                # docstring and motus#116.
                .with_fact(Fact("relevant_experience_months", 54, "cv-parser",
                                at(1)))
                .with_fact(Fact("certifications_count", 2, "cv-parser", at(1)))
                .with_fact(Fact("cv_source",
                                "cv-store document r7, parsed by cv-parser 1.9.2",
                                "cv-parser", at(1)))
                # What was NOT available. Inside the evidence, not absent
                # from it.
                .with_rejection(Rejection(
                    "reference_check",
                    "not returned at decision time",
                    at(1),
                    evidence={"requested": True, "returned_at": None})))

    def check_qualification(state: State) -> State:
        level = state.fact("qualification_level")
        state.fact("required_qualification")
        met = "yes" if "BSc" in level or "MSc" in level else "no"
        return state.with_fact(Fact("qualification_met", met, "screen", at(2)))

    def assess_experience(state: State) -> State:
        months = state.fact("relevant_experience_months")
        minimum = state.fact("required_experience_months")
        shortfall = max(0, minimum - months)
        return (state
                .with_fact(Fact("experience_met",
                                "no" if shortfall else "yes", "screen", at(3)))
                .with_fact(Fact("shortfall_months", shortfall, "screen", at(3))))

    def apply_policy(state: State) -> State:
        shortfall = state.fact("shortfall_months")
        # The screen's own answer, and then the clause that overrides it. Both
        # are written down: a record that showed only the outcome would hide
        # the fact that a rejection was the arithmetic result.
        screen_says = "reject" if shortfall else "advance"
        return (state
                .with_fact(Fact("policy_version", "hiring-screen v4.2",
                                "policy", at(4)))
                .with_fact(Fact(
                    "policy_clause",
                    "§3 — no automated rejection for this role class",
                    "policy", at(4)))
                .with_fact(Fact("screen_result", screen_says, "screen", at(4)))
                .with_decision(Decision(
                    "disposition", "human_review", at(4),
                    reason=f"the screen found a {shortfall}-month shortfall "
                           f"against the 60-month minimum. Clause §3 does not "
                           f"permit an automated rejection for this role "
                           f"class, so the shortfall goes to a person")))

    def human_review(state: State) -> State:
        return (state
                .with_fact(Fact("handoff", "hiring panel, queue HR-4",
                                "policy", at(5)))
                .with_fact(Fact("handed_over",
                                "the shortfall, the CV source, and the missing "
                                "reference check", "policy", at(5))))

    def auto_reject(state: State) -> State:            # pragma: no cover
        return state.with_fact(Fact("outcome", "rejected without review",
                                    "policy", at(6)))

    def auto_advance(state: State) -> State:           # pragma: no cover
        return state.with_fact(Fact("outcome", "advanced without review",
                                    "policy", at(6)))

    def final_recommendation(state: State) -> State:
        return (state
                .with_fact(Fact("recommendation",
                                "proceed to human review; do not reject "
                                "automatically", "policy", at(6)))
                .with_fact(Fact("automated_rejection_issued", "no",
                                "policy", at(6))))

    return {
        "receive_application": receive_application,
        "parse_cv": parse_cv,
        "check_qualification": check_qualification,
        "assess_experience": assess_experience,
        "apply_policy": apply_policy,
        "human_review": human_review,
        "auto_reject": auto_reject,
        "auto_advance": auto_advance,
        "final_recommendation": final_recommendation,
    }


ANSWER = ("No. This application cannot be rejected automatically. It goes to a "
          "person, with the shortfall named.")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    old_index = (
        json.loads((OUT / "index.json").read_text(encoding="utf-8"))
        if (OUT / "index.json").exists() else {})
    declaration = spec()
    result = Runtime(declaration, nodes(), sink=InMemoryTraceSink()).run(
        State.empty(PROMPT), run_id="demo-hiring-review-0421")

    document = result.trace.to_dict()
    bundle = TraceBundle(declaration, result.trace)

    (OUT / "trace.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (OUT / "graph.json").write_text(
        json.dumps(declaration.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (OUT / "explain.json").write_text(
        json.dumps(bundle.explain(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # The root string as it is committed: no newline, no quotes, exactly the
    # bytes the anchor timestamps. A verifier that has to guess the framing
    # cannot verify anything.
    (OUT / "root.txt").write_bytes(result.trace.root.encode("utf-8"))

    index = {
        "name": "hiring_review",
        "prompt": PROMPT,
        "answer": ANSWER,
        "root": result.trace.root,
        "bundle_fingerprint": bundle.fingerprint,
        "graph_fingerprint": declaration.graph_fingerprint,
        "records": len(result.trace.records),
        "protected_attributes": PROTECTED,
        "artefact": "demo/out/hiring/trace.json",
    }
    from three_domains import merge_index_by_name
    index = merge_index_by_name(old_index, index)
    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"root            {result.trace.root}")
    print(f"bundle          {bundle.fingerprint}")
    print(f"records         {len(result.trace.records)}")
    print(f"terminal        {bundle.explain()['terminal']}")
    print(f"written to      {OUT}")


if __name__ == "__main__":
    main()
