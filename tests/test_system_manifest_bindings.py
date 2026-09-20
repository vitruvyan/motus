"""ADR-035 binding-verification tests.

Each comparison has a dedicated failure probe: if the implementation stops
checking that field, one of these tests goes green for the wrong reason and the
suite catches it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vitruvyan_motus import (
    TRACE_SCHEMA_VERSION,
    GraphSpec,
    Trace,
    Runtime,
    State,
    __version__,
    verify_system_manifest_bindings,
)


def _identity(state):
    return state


def _spec(name: str, version: str = "1.0.0") -> GraphSpec:
    node = f"{name}_node"
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": name,
        "version": version,
        "entry": node,
        "nodes": [{"name": node, "effect_class": "pure"}],
        "transitions": {node: {"kind": "terminal"}},
    })


def _trace(spec: GraphSpec, run_id: str):
    node = spec.nodes[0].name
    return Runtime(spec, {node: _identity}).run(
        State.empty("manifest-binding"), run_id=run_id
    ).trace


def _manifest_for(pairs):
    graphs = []
    for spec, trace in pairs:
        header = trace.run["graph"]
        graphs.append({
            "name": spec.name,
            "version": spec.version,
            "spec_schema_version": spec.schema_version,
            "graph_fingerprint": spec.graph_fingerprint,
            "code_fingerprint": header["code_fingerprint"],
        })
    return {
        "schema_version": "1.0.0",
        "system": {
            "id": "acme/ai-system",
            "manifest_version": "2026.09.20-1",
        },
        "bindings": {
            "motus": {
                "runtime_version": __version__,
                "trace_schema_version": TRACE_SCHEMA_VERSION,
            },
            "graphs": graphs,
        },
        "declarations": {"operator": {"id": "acme"}},
        "created_at": "2026-09-20T18:00:00Z",
        "supersedes": None,
    }


def _by_path(verdict):
    return {finding.path: finding for finding in verdict.findings}


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


def test_exact_bindings_match_and_complete():
    spec = _spec("review")
    trace = _trace(spec, "run-exact")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert verdict.manifest_violations == ()
    assert verdict.manifest_fingerprint.startswith("sha256:")
    assert verdict.bindings_complete is True
    assert verdict.has_mismatch is False
    assert verdict.has_unverified is False
    assert {finding.status for finding in verdict.findings} == {"matched"}


def test_runtime_version_mismatch_is_not_a_match():
    spec = _spec("review")
    trace = _trace(spec, "run-runtime")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["motus"]["runtime_version"] = "999.0.0"

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )
    finding = _by_path(verdict)["$.bindings.motus.runtime_version"]

    assert finding.status == "mismatched"
    assert finding.observed == __version__
    assert verdict.bindings_complete is False
    assert verdict.has_mismatch is True


def test_trace_schema_version_mismatch_is_not_a_match():
    spec = _spec("review")
    trace = _trace(spec, "run-schema")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["motus"]["trace_schema_version"] = "999.0.0"

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert _by_path(verdict)["$.bindings.motus.trace_schema_version"].status == "mismatched"


def test_missing_graphspec_is_explicitly_not_verified_but_trace_code_can_match():
    spec = _spec("review")
    trace = _trace(spec, "run-no-spec")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(manifest, traces=[trace])
    findings = _by_path(verdict)

    assert findings["$.bindings.graphs[0].graph_fingerprint"].status == "not verified"
    assert findings["$.bindings.graphs[0].code_fingerprint"].status == "matched"
    assert verdict.has_unverified is True
    assert verdict.bindings_complete is False


def test_graph_fingerprint_mismatch_is_detected():
    spec = _spec("review")
    trace = _trace(spec, "run-graph-mismatch")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["graphs"][0]["graph_fingerprint"] = (
        "graph:sha256:" + "f" * 64
    )

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )
    findings = _by_path(verdict)

    assert findings["$.bindings.graphs[0].graph_fingerprint"].status == "mismatched"
    assert findings["$.bindings.graphs[0].code_fingerprint"].status == "not verified"
    assert verdict.bindings_complete is False


def test_missing_trace_leaves_code_fingerprint_not_verified():
    spec = _spec("review")
    trace = _trace(spec, "run-no-trace")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(manifest, graph_specs=[spec])

    assert _by_path(verdict)["$.bindings.graphs[0].code_fingerprint"].status == "not verified"
    assert verdict.has_unverified is True


def test_code_fingerprint_mismatch_is_detected_against_matching_trace():
    spec = _spec("review")
    trace = _trace(spec, "run-code-mismatch")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["graphs"][0]["code_fingerprint"] = (
        "code:sha256:" + "e" * 64
    )

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    finding = _by_path(verdict)["$.bindings.graphs[0].code_fingerprint"]
    assert finding.status == "mismatched"
    assert "trace evidence" in finding.reason
    assert verdict.has_mismatch is True


def test_invalid_manifest_gets_no_successful_binding_findings():
    spec = _spec("review")
    trace = _trace(spec, "run-invalid")
    manifest = _manifest_for([(spec, trace)])
    manifest["compliant"] = True

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert verdict.manifest_violations
    assert verdict.manifest_fingerprint is None
    assert verdict.findings == ()
    assert verdict.bindings_complete is False


def test_multiple_graphs_bind_independently():
    first = _spec("review")
    second = _spec("search")
    first_trace = _trace(first, "run-review")
    second_trace = _trace(second, "run-search")
    manifest = _manifest_for([(first, first_trace), (second, second_trace)])

    verdict = verify_system_manifest_bindings(
        manifest,
        graph_specs=[second, first],
        traces=[first_trace, second_trace],
    )

    assert verdict.bindings_complete is True
    assert len(verdict.findings) == 12
    assert all(item.status == "matched" for item in verdict.findings)


def test_verification_does_not_mutate_inputs():
    spec = _spec("review")
    trace = _trace(spec, "run-immutable")
    manifest = _manifest_for([(spec, trace)])
    before_manifest = copy.deepcopy(manifest)
    before_spec = spec.to_dict()
    before_trace = trace.to_dict()

    verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert manifest == before_manifest
    assert spec.to_dict() == before_spec
    assert trace.to_dict() == before_trace


def test_ambiguous_duplicate_graphspec_evidence_is_not_silently_chosen():
    spec = _spec("review")
    trace = _trace(spec, "run-ambiguous")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec, spec], traces=[trace]
    )

    assert _by_path(verdict)["$.bindings.graphs[0].graph_fingerprint"].status == "not verified"
    assert verdict.bindings_complete is False


def test_forged_trace_object_is_refused_before_it_can_match_code_binding():
    spec = _spec("review")
    trace = _trace(spec, "run-forged")
    manifest = _manifest_for([(spec, trace)])

    forged_document = trace.to_dict()
    forged_document["run"]["graph"]["code_fingerprint"] = (
        "code:sha256:" + "a" * 64
    )
    forged = Trace.from_dict(forged_document)

    with pytest.raises(ValueError, match="does not satisfy the Motus contract"):
        verify_system_manifest_bindings(
            manifest, graph_specs=[spec], traces=[forged]
        )


def test_code_evidence_from_another_trace_schema_does_not_match():
    wrapper = json.loads(
        (FIXTURES_DIR / "309-trace-3-1-float-loads.json").read_text("utf-8")
    )
    old_trace = Trace.from_dict(wrapper["instance"])
    graph = old_trace.run["graph"]
    manifest = {
        "schema_version": "1.0.0",
        "system": {"id": "acme/ai-system", "manifest_version": "current"},
        "bindings": {
            "motus": {
                "runtime_version": __version__,
                "trace_schema_version": TRACE_SCHEMA_VERSION,
            },
            "graphs": [{
                "name": graph["name"],
                "version": graph["version"],
                "spec_schema_version": graph["spec_schema_version"],
                "graph_fingerprint": graph["graph_fingerprint"],
                "code_fingerprint": graph["code_fingerprint"],
            }],
        },
        "declarations": {"operator": {"id": "acme"}},
        "created_at": "2026-09-20T18:00:00Z",
    }

    verdict = verify_system_manifest_bindings(manifest, traces=[old_trace])

    assert _by_path(verdict)["$.bindings.graphs[0].code_fingerprint"].status == "not verified"


def test_wrong_graphspec_does_not_invalidate_independent_trace_evidence():
    spec = _spec("review")
    trace = _trace(spec, "run-independent-sources")
    manifest = _manifest_for([(spec, trace)])

    wrong_document = spec.to_dict()
    wrong_document["requires_motus"] = ">=0"
    wrong_spec = GraphSpec.from_dict(wrong_document)
    assert wrong_spec.graph_fingerprint != spec.graph_fingerprint

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[wrong_spec], traces=[trace]
    )
    findings = _by_path(verdict)

    assert findings["$.bindings.graphs[0].graph_fingerprint"].status == "mismatched"
    assert findings["$.bindings.graphs[0].code_fingerprint"].status == "matched"
