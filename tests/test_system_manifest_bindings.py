"""ADR-035 binding-verification tests.

Each comparison has a dedicated failure probe: if the implementation stops
checking that field, one of these tests goes green for the wrong reason and the
suite catches it.
"""
from __future__ import annotations

import copy

from vitruvyan_motus import (
    MATCHED,
    MISMATCHED,
    NOT_VERIFIED,
    TRACE_SCHEMA_VERSION,
    GraphSpec,
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


def test_exact_bindings_match_and_complete():
    spec = _spec("review")
    trace = _trace(spec, "run-exact")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert verdict.manifest_violations == ()
    assert verdict.manifest_fingerprint.startswith("sha256:")
    assert verdict.complete is True
    assert verdict.has_mismatch is False
    assert verdict.has_unverified is False
    assert {finding.status for finding in verdict.findings} == {MATCHED}


def test_runtime_version_mismatch_is_not_a_match():
    spec = _spec("review")
    trace = _trace(spec, "run-runtime")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["motus"]["runtime_version"] = "999.0.0"

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )
    finding = _by_path(verdict)["$.bindings.motus.runtime_version"]

    assert finding.status == MISMATCHED
    assert finding.observed == __version__
    assert verdict.complete is False
    assert verdict.has_mismatch is True


def test_trace_schema_version_mismatch_is_not_a_match():
    spec = _spec("review")
    trace = _trace(spec, "run-schema")
    manifest = _manifest_for([(spec, trace)])
    manifest["bindings"]["motus"]["trace_schema_version"] = "999.0.0"

    verdict = verify_system_manifest_bindings(
        manifest, graph_specs=[spec], traces=[trace]
    )

    assert _by_path(verdict)["$.bindings.motus.trace_schema_version"].status == MISMATCHED


def test_missing_graphspec_is_explicitly_not_verified_but_trace_code_can_match():
    spec = _spec("review")
    trace = _trace(spec, "run-no-spec")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(manifest, traces=[trace])
    findings = _by_path(verdict)

    assert findings["$.bindings.graphs[0].graph_fingerprint"].status == NOT_VERIFIED
    assert findings["$.bindings.graphs[0].code_fingerprint"].status == MATCHED
    assert verdict.has_unverified is True
    assert verdict.complete is False


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

    assert findings["$.bindings.graphs[0].graph_fingerprint"].status == MISMATCHED
    assert findings["$.bindings.graphs[0].code_fingerprint"].status == NOT_VERIFIED
    assert verdict.complete is False


def test_missing_trace_leaves_code_fingerprint_not_verified():
    spec = _spec("review")
    trace = _trace(spec, "run-no-trace")
    manifest = _manifest_for([(spec, trace)])

    verdict = verify_system_manifest_bindings(manifest, graph_specs=[spec])

    assert _by_path(verdict)["$.bindings.graphs[0].code_fingerprint"].status == NOT_VERIFIED
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
    assert finding.status == MISMATCHED
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
    assert verdict.complete is False


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

    assert verdict.complete is True
    assert len(verdict.findings) == 12
    assert all(item.status == MATCHED for item in verdict.findings)


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

    assert _by_path(verdict)["$.bindings.graphs[0].graph_fingerprint"].status == NOT_VERIFIED
    assert verdict.complete is False
