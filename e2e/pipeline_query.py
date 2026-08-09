"""One real query, through the real pipeline, recorded by Motus.

    .venv/bin/python e2e/pipeline_query.py "Dovrei procedere con ALPHA?"

Requires the live `api_graph` service on http://localhost:9004 (frontier_graph).
Not part of the test suite and not run by CI: it depends on a service being up.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "src")
sys.path.insert(0, "contract")

from vitruvyan_motus import Decision, Fact, GraphSpec, Runtime, State
import validate

ENDPOINT = "http://localhost:9004/run"
QUERY = sys.argv[1] if len(sys.argv) > 1 else "Dovrei procedere con la revisione del contratto ALPHA?"
USER = "motus_e2e_test"
NOW = lambda: datetime.now(timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "vitruvyan-pipeline-probe",
    "version": "1.0.0",
    "entry": "accept_query",
    "nodes": [
        {"name": "accept_query", "effect_class": "pure"},
        {"name": "call_pipeline", "effect_class": "external_effect"},
        {"name": "assess_health", "effect_class": "pure"},
        {"name": "deliver", "effect_class": "pure"},
        {"name": "flag_degradation", "effect_class": "pure"},
    ],
    "transitions": {
        "accept_query": {"kind": "next", "to": "call_pipeline"},
        "call_pipeline": {"kind": "next", "to": "assess_health"},
        "assess_health": {
            "kind": "route",
            "on": "pipeline_health",
            "map": {"ok": "deliver", "degraded": "flag_degradation"},
            "default": "flag_degradation",
        },
        "deliver": {"kind": "terminal"},
        "flag_degradation": {"kind": "terminal"},
    },
})


def accept_query(state: State) -> State:
    return (
        state
        .with_fact(Fact("query_chars", len(QUERY), "caller", NOW()))
        .with_fact(Fact("user_id", USER, "caller", NOW()))
        .with_fact(Fact("language_requested", "it", "caller", NOW()))
    )


def call_pipeline(state: State) -> State:
    payload = json.dumps({"input_text": QUERY, "user_id": USER, "language": "it"}).encode()
    request = urllib.request.Request(
        ENDPOINT, data=payload, headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        status = response.status
        body = json.loads(response.read())
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    inner = json.loads(body.get("json", "{}"))
    answer = body.get("human", "") or ""
    return (
        state
        .with_fact(Fact("http_status", status, ENDPOINT, NOW()))
        .with_fact(Fact("latency_ms", elapsed_ms, "clock", NOW()))
        .with_fact(Fact("route_taken", body.get("route_taken"), "api_graph", NOW()))
        .with_fact(Fact("orthodoxy_status", body.get("orthodoxy_status"), "api_graph", NOW()))
        .with_fact(Fact("babel_status", inner.get("babel_status"), "api_graph", NOW()))
        .with_fact(Fact("language_detected", inner.get("language_detected"), "api_graph", NOW()))
        .with_fact(Fact("answer_chars", len(answer), "api_graph", NOW()))
        .with_fact(Fact("correlation_id", body.get("correlation_id"), "api_graph", NOW()))
    )


def assess_health(state: State) -> State:
    """Three checks, each one a thing the caller was promised.

    `babel_status` is the pipeline's own word for whether language handling
    worked; it answers "success", not "ok". Getting that wrong once already
    produced a false "degraded", which is why the accepted values are written
    out rather than guessed.
    """
    babel_ok = state.fact("babel_status") in ("ok", "success")
    language_ok = state.fact("language_detected") == state.fact("language_requested")
    answered = (state.fact("answer_chars") or 0) > 60

    if babel_ok and language_ok and answered:
        verdict = "ok"
    elif state.fact("http_status") == 200:
        verdict = "degraded"
    else:
        verdict = "failed"
    return (
        state
        .with_fact(Fact("check_babel", babel_ok, "probe", NOW()))
        .with_fact(Fact("check_language", language_ok, "probe", NOW()))
        .with_fact(Fact("check_answered", answered, "probe", NOW()))
        .with_decision(Decision("pipeline_health", verdict, NOW()))
    )


def deliver(state: State) -> State:
    return state.with_fact(Fact("delivered", True, "probe", NOW()))


def flag_degradation(state: State) -> State:
    return state.with_fact(
        Fact("degradation_reason",
             f"babel={state.fact('babel_status')}"
             f" language chiesta={state.fact('language_requested')}"
             f" rilevata={state.fact('language_detected')}"
             f" answer_chars={state.fact('answer_chars')}",
             "probe", NOW())
    )


runtime = Runtime(SPEC, {
    "accept_query": accept_query,
    "call_pipeline": call_pipeline,
    "assess_health": assess_health,
    "deliver": deliver,
    "flag_degradation": flag_degradation,
})

wall_started = time.perf_counter()
result = runtime.run(State.empty(), run_id=f"e2e-{int(time.time())}")
wall_ms = (time.perf_counter() - wall_started) * 1000

print("=" * 72)
print("QUERY :", QUERY)
print("ESITO :", result.status)
print("=" * 72)
print("\nIL GIRO CHE HA FATTO (dalla traccia, non dal codice):\n")
for record in result.trace.records:
    kind = record["kind"]
    if kind == "attempt_started":
        print(f"   -> {record['node']}")
    elif kind == "transition":
        writes = record["writes"]
        for fact in writes.get("facts", []):
            print(f"        registra  {fact['key']} = {fact['value']}")
        for decision in writes.get("decisions", []):
            print(f"        DECIDE    {decision['key']} = {decision['value']}")
    elif kind == "routing":
        print(f"        instrada  -> {record['selected']}  ({record['outcome']})")

print("\nCOSA DICE IL VALIDATORE DEL CONTRATTO SULLA TRACCIA:")
violations = validate.validate_trace(result.trace.to_dict(), spec=SPEC.to_dict())
print("   ", "nessuna violazione" if not violations else violations)

print("\nEVIDENZA:")
print("    record scritti      :", len(result.trace.records))
print("    schema traccia      :", result.trace.header["schema_version"])
print("    catena di integrita':", "presente" if result.trace.records[0]["integrity"]["payload_hash"] else "assente")
print("    radice della catena :", (result.trace.root or "n/a")[:32], "...")
print("    replay dichiarato   :", result.trace.records[-1]["replay"]["capability"])

pipeline_ms = result.state.fact("latency_ms")
motus_ms = wall_ms - pipeline_ms
print("\nQUANTO PESA MOTUS IN UNA RICHIESTA VERA (ipotesi H1, issue #38):")
print(f"    richiesta intera    : {wall_ms:9.1f} ms")
print(f"    di cui la pipeline  : {pipeline_ms:9.1f} ms")
print(f"    di cui Motus        : {motus_ms:9.1f} ms   = {motus_ms / wall_ms * 100:.3f}% del totale")
