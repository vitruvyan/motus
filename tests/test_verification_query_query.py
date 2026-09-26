"""ADR-043 bounded supplied-set query tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from vitruvyan_motus import query_artifacts
from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent


def fixture(name: str, directory: str = "fixtures") -> dict:
    value = json.loads((ROOT / "contract" / directory / name).read_text("utf-8"))
    return value.get("instance", value)


def typed(kind: str, document: dict, input_id: str) -> dict:
    return {"input_id": input_id, "kind": kind, "media_type": "application/json", "document": document}


def test_exact_identity_query_matches_only_exact_kind_and_fingerprint():
    manifest = fixture("310-system-manifest-valid.json")
    registry = fixture("320-risk-control-registry-valid.json")
    artifacts = [typed("system_manifest", manifest, "manifest"), typed("risk_control_registry", registry, "registry")]
    result = query_artifacts({"kind": "artifact_identity", "artifact_kind": "system_manifest", "fingerprint": validate.system_manifest_fingerprint(manifest)}, artifacts)
    assert result["outcome"] == "completed"
    assert [item["input_id"] for item in result["matches"]] == ["manifest"]
    assert result["scope"]["global_complete"] is False


def test_execution_ref_query_uses_only_contract_defined_paths():
    application = fixture("330-control-application-valid.json")
    decoy = fixture("310-system-manifest-valid.json")
    decoy["declarations"]["operator"]["execution_ref"] = application["execution_ref"]
    artifacts = [typed("control_application", application, "application"), typed("system_manifest", decoy, "decoy")]
    result = query_artifacts({"kind": "execution_ref", "execution_ref": application["execution_ref"]}, artifacts)
    assert [item["input_id"] for item in result["matches"]] == ["application"]


def test_controls_for_risk_composes_the_existing_registry_helper():
    registry = fixture("320-risk-control-registry-valid.json")
    risk_id = registry["risks"][0]["risk_id"]
    result = query_artifacts({"kind": "controls_for_risk", "registry_input_id": "registry", "risk_id": risk_id}, [typed("risk_control_registry", registry, "registry")])
    expected = [item for item in registry["controls"] if risk_id in item["risk_refs"]]
    assert [item["record"] for item in result["records"]] == expected


def test_missing_risk_remains_missing_not_an_exception():
    registry = fixture("320-risk-control-registry-valid.json")
    result = query_artifacts({"kind": "controls_for_risk", "registry_input_id": "registry", "risk_id": "absent"}, [typed("risk_control_registry", registry, "registry")])
    assert result["outcome"] == "completed"
    assert result["records"] == []
    assert result["findings"][0]["status"] == "missing"


def test_correction_lineage_preserves_a_fork_and_selects_no_winner():
    root = fixture("360-incident-declaration-valid.json")
    root_fp = validate.incident_declaration_fingerprint(root)
    left = copy.deepcopy(root)
    left["supersedes"] = root_fp
    left["declared_at"] = "2026-09-24T10:00:01Z"
    right = copy.deepcopy(left)
    right["declared_at"] = "2026-09-24T10:00:02Z"
    result = query_artifacts({"kind": "correction_lineage", "artifact_kind": "incident_declaration"}, [typed("incident_declaration", root, "root"), typed("incident_declaration", left, "left"), typed("incident_declaration", right, "right")])
    assert result["outcome"] == "conflict"
    assert len(result["records"]) == 3


def test_dossier_membership_returns_exact_declared_entries():
    dossier = fixture("410-regulatory-evidence-dossier-valid.json", "regulatory-evidence-dossier-fixtures")
    result = query_artifacts({"kind": "dossier_membership", "dossier_input_id": "dossier"}, [typed("regulatory_evidence_dossier", dossier, "dossier")])
    assert [item["record"] for item in result["records"]] == dossier["entries"]


def test_query_does_not_mutate_inputs_and_result_satisfies_contract():
    manifest = fixture("310-system-manifest-valid.json")
    artifacts = [typed("system_manifest", manifest, "manifest")]
    original = copy.deepcopy(artifacts)
    result = query_artifacts({"kind": "artifact_identity", "artifact_kind": "system_manifest", "fingerprint": validate.system_manifest_fingerprint(manifest)}, artifacts)
    assert artifacts == original
    assert validate.validate_verification_query_message(result) == []
