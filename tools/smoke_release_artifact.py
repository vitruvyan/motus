"""Run a graph from the installed wheel and prove its validator catches tampering."""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from vitruvyan_motus import Decision, Fact, GraphSpec, Runtime, State
from vitruvyan_motus.contract.validate import validate_trace


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "release-smoke",
    "version": "1.0.0",
    "entry": "assess",
    "nodes": [
        {"name": "assess", "effect_class": "pure"},
        {"name": "approve", "effect_class": "pure"},
    ],
    "transitions": {
        "assess": {
            "kind": "route",
            "on": "risk",
            "map": {"low": "approve"},
            "default": "approve",
        },
        "approve": {"kind": "terminal"},
    },
})


result = Runtime(SPEC, {
    "assess": lambda state: state.with_decision(Decision("risk", "low", NOW)),
    "approve": lambda state: state.with_fact(Fact("outcome", "ok", "policy", NOW)),
}).run(State.empty(), run_id="release-smoke")
assert result.status == "completed", result.status
document = result.trace.to_dict()
assert not validate_trace(document), validate_trace(document)

tampered = copy.deepcopy(document)
for record in tampered["records"]:
    if record["kind"] == "transition" and record["writes"]["decisions"]:
        record["writes"]["decisions"][0]["value"] = "high"
        break
else:
    raise AssertionError("smoke trace contains no decision to tamper")
assert validate_trace(tampered), "the shipped validator accepted an edited trace"
print("installed release smoke: PASS")
