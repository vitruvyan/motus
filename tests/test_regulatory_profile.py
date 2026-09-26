"""ADR-038 Regulatory Evidence Profile v1 contract and assessment tests."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from vitruvyan_motus import (
    TRACE_SCHEMA_VERSION,
    GraphSpec,
    Runtime,
    State,
    __version__,
    assess_evidence_profile,
)
import vitruvyan_motus.regulatory_profile as regulatory_profile
from vitruvyan_motus.contract import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "contract" / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))["instance"]


def _profile(*kinds: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "profile": {"id": "example/framework", "version": "2026-01"},
        "requirements": [
            {
                "requirement_ref": "REQ-001",
                "evidence": [{"kind": kind} for kind in kinds],
            }
        ],
    }


def _by_kind(assessment):
    return {finding.kind: finding for finding in assessment.findings}


def _identity(state):
    return state


def _spec(name: str = "profile_review") -> GraphSpec:
    node = f"{name}_node"
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": name,
        "version": "1.0.0",
        "entry": node,
        "nodes": [{"name": node, "effect_class": "pure"}],
        "transitions": {node: {"kind": "terminal"}},
    })


def _trace(spec: GraphSpec):
    node = spec.nodes[0].name
    return Runtime(spec, {node: _identity}).run(
        State.empty("regulatory-profile"), run_id="profile-run"
    ).trace


def _bound_manifest(spec: GraphSpec, trace) -> dict:
    graph = trace.run["graph"]
    return {
        "schema_version": "1.0.0",
        "system": {
            "id": "example/system",
            "manifest_version": "2026-09-22-1",
        },
        "bindings": {
            "motus": {
                "runtime_version": __version__,
                "trace_schema_version": TRACE_SCHEMA_VERSION,
            },
            "graphs": [{
                "name": spec.name,
                "version": spec.version,
                "spec_schema_version": spec.schema_version,
                "graph_fingerprint": spec.graph_fingerprint,
                "code_fingerprint": graph["code_fingerprint"],
            }],
        },
        "declarations": {"operator": {"id": "example-operator"}},
        "created_at": "2026-09-22T18:00:00Z",
        "supersedes": None,
    }


def test_profile_fingerprint_binds_the_complete_mapping():
    document = _profile("execution_receipt")
    changed = copy.deepcopy(document)
    changed["profile"]["version"] = "2026-02"

    assert validate.validate_regulatory_evidence_profile(document) == []
    assert (
        validate.regulatory_evidence_profile_fingerprint(document)
        != validate.regulatory_evidence_profile_fingerprint(changed)
    )


def test_schema_forbids_compliance_verdicts_and_unknown_evidence_kinds():
    verdict = _profile("execution_receipt")
    verdict["requirements"][0]["compliant"] = True
    assert {
        item.rule for item in validate.validate_regulatory_evidence_profile(verdict)
    } == {"SCHEMA"}

    unknown = _profile("legal_opinion")
    assert {
        item.rule for item in validate.validate_regulatory_evidence_profile(unknown)
    } == {"SCHEMA"}


def test_duplicate_requirement_refs_are_rep1():
    document = _profile("execution_receipt")
    document["requirements"].append(copy.deepcopy(document["requirements"][0]))

    violations = validate.validate_regulatory_evidence_profile(document)
    assert {item.rule for item in violations} == {"REP1"}


def test_missing_requested_evidence_is_not_promoted_to_success():
    assessment = assess_evidence_profile(
        _profile(
            "execution_receipt",
            "system_manifest",
            "risk_control_registry",
            "control_application",
            "human_oversight_receipt",
        )
    )

    assert assessment.profile_violations == ()
    assert {item.status for item in assessment.findings} == {"missing"}


def test_registry_matches_as_a_valid_declaration():
    assessment = assess_evidence_profile(
        _profile("risk_control_registry"),
        risk_control_registry=_fixture("320-risk-control-registry-valid.json"),
    )

    assert _by_kind(assessment)["risk_control_registry"].status == "matched"


def test_manifest_without_binding_material_is_not_verified():
    spec = _spec()
    trace = _trace(spec)
    manifest = _bound_manifest(spec, trace)

    assessment = assess_evidence_profile(
        _profile("system_manifest"),
        system_manifest=manifest,
    )

    assert _by_kind(assessment)["system_manifest"].status == "not_verified"


def test_manifest_uses_existing_binding_verifier_to_reach_matched():
    spec = _spec()
    trace = _trace(spec)
    manifest = _bound_manifest(spec, trace)

    assessment = assess_evidence_profile(
        _profile("system_manifest"),
        system_manifest=manifest,
        graph_specs=[spec],
        traces=[trace],
    )

    assert _by_kind(assessment)["system_manifest"].status == "matched"


def test_valid_execution_receipt_matches_and_identity_corruption_mismatches():
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    matched = assess_evidence_profile(
        _profile("execution_receipt"), execution_receipt=receipt
    )
    assert _by_kind(matched)["execution_receipt"].status == "matched"

    corrupted = copy.deepcopy(receipt)
    corrupted["execution"]["run_id"] = "tampered"
    mismatched = assess_evidence_profile(
        _profile("execution_receipt"), execution_receipt=corrupted
    )
    assert _by_kind(mismatched)["execution_receipt"].status == "mismatched"


def test_receipt_verifier_refusal_is_not_verified_not_mismatched():
    refused = _fixture("313-receipt-p10-unknown-type.json")

    assessment = assess_evidence_profile(
        _profile("execution_receipt"),
        execution_receipt=refused,
    )

    assert _by_kind(assessment)["execution_receipt"].status == "not_verified"


def test_control_application_without_registry_is_not_verified():
    application = _fixture("330-control-application-valid.json")
    assessment = assess_evidence_profile(
        _profile("control_application"), control_application=application
    )

    assert _by_kind(assessment)["control_application"].status == "not_verified"


def test_real_control_application_binding_mismatch_stays_mismatched():
    application = _fixture("330-control-application-valid.json")
    registry = _fixture("320-risk-control-registry-valid.json")

    assessment = assess_evidence_profile(
        _profile("control_application"),
        risk_control_registry=registry,
        control_application=application,
    )

    assert _by_kind(assessment)["control_application"].status == "mismatched"


def test_human_oversight_without_binding_material_is_not_verified():
    oversight = _fixture("340-human-oversight-receipt-valid.json")
    assessment = assess_evidence_profile(
        _profile("human_oversight_receipt"),
        human_oversight_receipt=oversight,
    )

    finding = _by_kind(assessment)["human_oversight_receipt"]
    assert finding.status == "not_verified"


def test_assessment_is_bound_to_one_profile_snapshot(monkeypatch):
    profile = {
        "schema_version": "1.0.0",
        "profile": {"id": "example/framework", "version": "2026-01"},
        "requirements": [
            {
                "requirement_ref": "REQ-001",
                "evidence": [{"kind": "execution_receipt"}],
            },
            {
                "requirement_ref": "REQ-002",
                "evidence": [{"kind": "system_manifest"}],
            },
        ],
    }

    original = regulatory_profile._evaluate_kind
    calls = 0

    def mutating_evaluate(kind, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            profile["requirements"][1]["requirement_ref"] = "MUTATED"
        return original(kind, **kwargs)

    monkeypatch.setattr(regulatory_profile, "_evaluate_kind", mutating_evaluate)
    assessment = assess_evidence_profile(profile)

    assert [item.requirement_ref for item in assessment.findings] == [
        "REQ-001",
        "REQ-002",
    ]


def test_repeated_manifest_expectations_reuse_one_binding_verdict(monkeypatch):
    profile = _profile("system_manifest")
    profile["requirements"].append({
        "requirement_ref": "REQ-002",
        "evidence": [{"kind": "system_manifest"}],
    })
    calls = []

    def evaluate(kind, **_kwargs):
        calls.append(kind)
        return "not_verified", "bounded manifest result"

    monkeypatch.setattr(regulatory_profile, "_evaluate_kind", evaluate)
    assessment = assess_evidence_profile(profile)
    assert calls == ["system_manifest"]
    assert len(assessment.findings) == 2


def test_repeated_non_manifest_expectations_reuse_one_evaluation(monkeypatch):
    profile = _profile("risk_control_registry")
    profile["requirements"].append({
        "requirement_ref": "REQ-002",
        "evidence": [{"kind": "risk_control_registry"}],
    })
    calls = []

    def evaluate(kind, **_kwargs):
        calls.append(kind)
        return "not_verified", "cached evidence result"

    monkeypatch.setattr(regulatory_profile, "_evaluate_kind", evaluate)
    assessment = assess_evidence_profile(profile)
    assert calls == ["risk_control_registry"]
    assert len(assessment.findings) == 2


def test_regulatory_profile_is_reachable_from_motus_validate(tmp_path):
    profile = _profile("execution_receipt")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "contract" / "validate.py"),
            "regulatory-evidence-profile",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_invalid_profile_produces_no_fingerprint_or_findings():
    profile = _profile("execution_receipt")
    profile["requirements"][0]["unexpected"] = True

    assessment = assess_evidence_profile(profile)

    assert assessment.profile_fingerprint is None
    assert assessment.profile_violations
    assert assessment.findings == ()
