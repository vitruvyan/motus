"""ADR-043 bounded supplied-set query tests."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

from vitruvyan_motus import query_artifacts
from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent
verification_query = importlib.import_module("vitruvyan_motus.verification_query")


def fixture(name: str, directory: str = "fixtures") -> dict:
    value = json.loads((ROOT / "contract" / directory / name).read_text("utf-8"))
    return value.get("instance", value)


def ai_fixture(number: int, kind: str) -> dict:
    return fixture(
        f"{number}-{kind.replace('_', '-')}-valid.json",
        "ai-system-registry-fixtures",
    )


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


def test_execution_ref_query_never_promotes_a_receipt_end_coordinate():
    receipt = fixture("205-receipt-a-run-resumed-once.json")
    artifacts = [typed("execution_receipt", receipt, "receipt")]
    begin = query_artifacts(
        {"kind": "execution_ref", "execution_ref": "acme/w1/1"}, artifacts,
    )
    end = query_artifacts(
        {"kind": "execution_ref", "execution_ref": "acme/w1/2"}, artifacts,
    )
    assert [item["input_id"] for item in begin["matches"]] == ["receipt"]
    assert end["matches"] == []


def test_execution_ref_query_reads_incident_and_capa_execution_evidence():
    for kind, fixture_name in (
        ("incident_declaration", "360-incident-declaration-valid.json"),
        ("capa_action", "361-capa-action-valid.json"),
    ):
        document = fixture(fixture_name)
        execution_ref = next(
            item["execution_ref"] for item in document["evidence"]
            if item["kind"] == "execution"
        )
        result = query_artifacts(
            {"kind": "execution_ref", "execution_ref": execution_ref},
            [typed(kind, document, kind)],
        )
        assert [item["input_id"] for item in result["matches"]] == [kind]


def test_controls_for_risk_composes_the_existing_registry_helper():
    registry = fixture("320-risk-control-registry-valid.json")
    risk_id = registry["risks"][0]["risk_id"]
    result = query_artifacts({"kind": "controls_for_risk", "registry_input_id": "registry", "risk_id": risk_id}, [typed("risk_control_registry", registry, "registry")])
    expected = [item for item in registry["controls"] if risk_id in item["risk_refs"]]
    assert [item["record"] for item in result["records"]] == expected


def test_projected_record_order_ignores_object_key_insertion_order():
    registry = fixture("320-risk-control-registry-valid.json")
    reordered = copy.deepcopy(registry)
    first = reordered["controls"][0]
    reordered["controls"][0] = {
        "title": first["title"],
        **{key: value for key, value in first.items() if key != "title"},
    }
    assert verification_query._canonical_json_sort_key(first) == (
        verification_query._canonical_json_sort_key(reordered["controls"][0])
    )
    projection = {
        "kind": "controls_for_risk",
        "registry_input_id": "registry",
        "risk_id": "R-001",
    }
    original = query_artifacts(
        projection, [typed("risk_control_registry", registry, "registry")],
    )
    equivalent = query_artifacts(
        projection, [typed("risk_control_registry", reordered, "registry")],
    )
    assert [item["record"]["control_id"] for item in original["records"]] == [
        "C-001", "C-002",
    ]
    assert [item["record"]["control_id"] for item in equivalent["records"]] == [
        "C-001", "C-002",
    ]


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


def test_correction_lineage_exposes_missing_predecessor_duplicate_and_cycle(monkeypatch):
    missing = fixture("360-incident-declaration-valid.json")
    missing["supersedes"] = "sha256:" + "9" * 64
    incomplete = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [typed("incident_declaration", missing, "missing")],
    )
    assert any(item["status"] == "incomplete" for item in incomplete["findings"])

    first = fixture("360-incident-declaration-valid.json")
    second = copy.deepcopy(first)
    second["declared_at"] = "2026-09-24T10:00:01Z"
    first["supersedes"] = "sha256:" + "2" * 64
    second["supersedes"] = "sha256:" + "1" * 64

    def fingerprint(document):
        return "sha256:" + ("1" if document["declared_at"].endswith("00Z") else "2") * 64

    monkeypatch.setattr(validate, "incident_declaration_fingerprint", fingerprint)
    cycle = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [typed("incident_declaration", first, "first"), typed("incident_declaration", second, "second")],
    )
    assert cycle["outcome"] == "conflict"
    assert any(item["path"] == "lineage:cycle" for item in cycle["findings"])

    monkeypatch.setattr(
        validate, "incident_declaration_fingerprint", lambda _document: "sha256:" + "3" * 64,
    )
    duplicate = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [typed("incident_declaration", fixture("360-incident-declaration-valid.json"), "one"),
         typed("incident_declaration", fixture("360-incident-declaration-valid.json"), "two")],
    )
    assert any("same correction identity" in item["reason"] for item in duplicate["findings"])


def test_maximum_lineage_traversal_visits_each_node_once():
    class CountingPredecessors(dict):
        checks = 0

        def __contains__(self, key):
            self.checks += 1
            return super().__contains__(key)

    size = 10_000
    predecessors = CountingPredecessors({
        f"node-{index:05d}": f"node-{index - 1:05d}"
        for index in range(1, size)
    })
    members = verification_query._lineage_cycle_members(
        predecessors,
        {f"node-{index:05d}" for index in range(size)},
    )
    assert members == set()
    assert predecessors.checks < size * 6


def test_correction_lineage_uses_authoritative_stable_identity_checks():
    parent = fixture(
        "370-retention-policy-declaration-valid.json", "retention-fixtures",
    )
    child = copy.deepcopy(parent)
    child["policy_id"] = "different-policy"
    child["supersedes"] = validate.retention_policy_declaration_fingerprint(parent)
    result = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "retention_policy_declaration"},
        [
            typed("retention_policy_declaration", parent, "parent"),
            typed("retention_policy_declaration", child, "child"),
        ],
    )
    assert any(item["status"] == "mismatched" for item in result["findings"])
    assert any("stable identifier" in item["reason"] for item in result["findings"])


def test_incident_and_capa_lineage_reject_cross_identity_corrections():
    cases = (
        ("incident_declaration", "360-incident-declaration-valid.json", "incident_id", validate.incident_declaration_fingerprint),
        ("capa_action", "361-capa-action-valid.json", "action_id", validate.capa_action_fingerprint),
    )
    for kind, fixture_name, stable_field, fingerprint in cases:
        parent = fixture(fixture_name)
        child = copy.deepcopy(parent)
        child[stable_field] = "different-stable-id"
        child["supersedes"] = fingerprint(parent)
        result = query_artifacts(
            {"kind": "correction_lineage", "artifact_kind": kind},
            [typed(kind, parent, "parent"), typed(kind, child, "child")],
        )
        assert any(item["status"] == "mismatched" for item in result["findings"])
        assert any("LEDGER2" in item["reason"] for item in result["findings"])


def test_incident_lineage_sees_a_supplied_cross_kind_predecessor():
    parent = fixture("361-capa-action-valid.json")
    child = fixture("360-incident-declaration-valid.json")
    child["supersedes"] = validate.capa_action_fingerprint(parent)
    result = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [
            typed("capa_action", parent, "capa-parent"),
            typed("incident_declaration", child, "incident-child"),
        ],
    )
    assert any(item["status"] == "mismatched" for item in result["findings"])
    assert any("LEDGER2" in item["reason"] for item in result["findings"])
    assert not any(
        item["status"] == "incomplete"
        and "predecessor is absent" in item["reason"]
        for item in result["findings"]
    )
    assert [item["input_id"] for item in result["matches"]] == ["incident-child"]


def test_incident_lineage_excludes_unrequested_capa_identity_failures():
    incident = fixture("360-incident-declaration-valid.json")
    capa_parent = fixture("361-capa-action-valid.json")
    capa_child = copy.deepcopy(capa_parent)
    capa_child["action_id"] = "different-capa-action"
    capa_child["supersedes"] = validate.capa_action_fingerprint(capa_parent)
    result = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [
            typed("incident_declaration", incident, "incident"),
            typed("capa_action", capa_parent, "capa-parent"),
            typed("capa_action", capa_child, "capa-child"),
        ],
    )
    assert not any("LEDGER2" in item["reason"] for item in result["findings"])
    assert [item["input_id"] for item in result["matches"]] == ["incident"]


def test_cross_kind_duplicate_predecessors_remain_ambiguous():
    parent = fixture("361-capa-action-valid.json")
    child = fixture("360-incident-declaration-valid.json")
    child["supersedes"] = validate.capa_action_fingerprint(parent)
    result = query_artifacts(
        {"kind": "correction_lineage", "artifact_kind": "incident_declaration"},
        [
            typed("capa_action", parent, "capa-parent-a"),
            typed("capa_action", copy.deepcopy(parent), "capa-parent-b"),
            typed("incident_declaration", child, "incident-child"),
        ],
    )
    assert result["outcome"] == "conflict"
    assert any(
        item["status"] == "conflict"
        and "cross-kind predecessor identity is ambiguous" in item["reason"]
        for item in result["findings"]
    )


def test_invalid_supplied_artifact_is_visible_and_fails_query_closed():
    invalid = fixture("310-system-manifest-valid.json")
    invalid["compliant"] = True
    del invalid["schema_version"]
    result = query_artifacts(
        {"kind": "artifact_identity", "artifact_kind": "system_manifest", "fingerprint": "sha256:" + "a" * 64},
        [typed("system_manifest", invalid, "invalid-manifest")],
    )
    assert result["outcome"] == "invalid"
    assert result["matches"] == []
    assert result["findings"][0]["path"] == "$.artifacts[invalid-manifest]"
    assert result["findings"][0]["status"] == "not_verified"
    assert result["findings"][0]["observed"] == "invalid"
    assert result["findings"][0]["reason"].startswith(
        "the supplied artifact did not satisfy its own Motus contract:"
    )
    assert len(result["violations"]) == 2
    assert all(
        item["path"].startswith("$.artifacts[invalid-manifest]")
        for item in result["violations"]
    )


def test_query_preserves_every_prefixed_inspection_diagnostic(monkeypatch):
    artifact = typed(
        "system_manifest", fixture("310-system-manifest-valid.json"), "manifest",
    )
    monkeypatch.setattr(verification_query, "_inspect_artifact", lambda *_args: {
        "outcome": "invalid",
        "violations": [
            {"rule": "ONE", "path": "$.first", "message": "first"},
            {"rule": "TWO", "path": "$.second", "message": "second"},
        ],
        "findings": [
            {"path": "$.third", "status": "damaged", "reason": "third"},
            {"path": "$.fourth", "status": "incomplete", "reason": "fourth"},
        ],
    })
    result = query_artifacts({
        "kind": "artifact_identity",
        "artifact_kind": "system_manifest",
        "fingerprint": "sha256:" + "a" * 64,
    }, [artifact])
    assert [item["rule"] for item in result["violations"]] == ["ONE", "TWO"]
    assert {item["path"] for item in result["findings"][1:]} == {
        "$.artifacts[manifest].third", "$.artifacts[manifest].fourth",
    }


def test_ai_lifecycle_matches_and_provenance_exclude_unrelated_registration():
    target = ai_fixture(400, "ai_system_registration")
    target_event = ai_fixture(401, "ai_system_registry_event")
    target_event["registration_fingerprint"] = validate.ai_system_registration_fingerprint(target)

    unrelated = copy.deepcopy(target)
    unrelated["registration_id"] = "unrelated-registration"
    unrelated["system_id"] = "acme/unrelated-system"
    unrelated_event = copy.deepcopy(target_event)
    unrelated_event["registration_id"] = unrelated["registration_id"]
    unrelated_event["event_id"] = "unrelated-event"
    unrelated_event["registration_fingerprint"] = validate.ai_system_registration_fingerprint(unrelated)

    artifacts = [
        typed("ai_system_registration", unrelated, "unrelated-registration"),
        typed("ai_system_registry_event", unrelated_event, "unrelated-event"),
        typed("ai_system_registration", target, "target-registration"),
        typed("ai_system_registry_event", target_event, "target-event"),
    ]
    result = query_artifacts({
        "kind": "ai_system_lifecycle",
        "producer_namespace": target["producer_namespace"],
        "registration_id": target["registration_id"],
    }, artifacts)

    assert {item["input_id"] for item in result["matches"]} == {
        "target-registration", "target-event",
    }
    assert result["records"][0]["source_input_id"] == "target-registration"


def test_ai_lifecycle_authority_sees_cross_namespace_cited_registration():
    target = ai_fixture(400, "ai_system_registration")
    event = ai_fixture(401, "ai_system_registry_event")
    foreign = copy.deepcopy(target)
    foreign["producer_namespace"] = "foreign-producer"
    foreign["registry_id"] = "foreign-registry"
    foreign["registration_id"] = "foreign-registration"
    event["registration_fingerprint"] = (
        validate.ai_system_registration_fingerprint(foreign)
    )
    result = query_artifacts({
        "kind": "ai_system_lifecycle",
        "producer_namespace": target["producer_namespace"],
        "registration_id": target["registration_id"],
    }, [
        typed("ai_system_registration", foreign, "foreign-registration"),
        typed("ai_system_registry_event", event, "target-event"),
    ])
    assert any(
        item["status"] == "mismatched"
        and "another stable registry subject" in item["reason"]
        for item in result["findings"]
    )
    assert [item["input_id"] for item in result["matches"]] == ["target-event"]


def test_ai_lifecycle_does_not_match_unselected_same_registration_events():
    target = ai_fixture(400, "ai_system_registration")
    first = ai_fixture(401, "ai_system_registry_event")
    first["registration_fingerprint"] = validate.ai_system_registration_fingerprint(target)
    competing_root = copy.deepcopy(first)
    competing_root["event_id"] = "competing-root"

    result = query_artifacts({
        "kind": "ai_system_lifecycle",
        "producer_namespace": target["producer_namespace"],
        "registration_id": target["registration_id"],
    }, [
        typed("ai_system_registration", target, "target-registration"),
        typed("ai_system_registry_event", first, "first-event"),
        typed("ai_system_registry_event", competing_root, "competing-event"),
    ])

    assert {item["input_id"] for item in result["matches"]} == {"target-registration"}
    assert result["records"][0]["record"]["ordered_event_fingerprints"] == []


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
