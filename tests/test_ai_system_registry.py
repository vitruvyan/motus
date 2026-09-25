"""ADR-041 structural, binding, lineage and supplied-projection tests."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vitruvyan_motus import (
    project_supplied_ai_system_lifecycle,
    verify_ai_system_registration_binding,
    verify_ai_system_registry_lineage,
    verify_ai_system_registry_snapshot,
)
from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent
KINDS = (
    "ai-system-registration", "ai-system-registry-event",
    "ai-system-registry-snapshot",
)
FIXTURES = dict(zip(KINDS, range(400, 403)))


def fixture(kind: str) -> dict:
    path = ROOT / "contract" / "ai-system-registry-fixtures" / f"{FIXTURES[kind]}-{kind}-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def rules(violations) -> set[str]:
    return {item.rule for item in violations}


def manifest(system_id="acme/customer-support-assistant"):
    return {
        "schema_version": "1.0.0",
        "system": {"id": system_id, "manifest_version": "2026.09.25-1"},
        "bindings": {
            "motus": {"runtime_version": "0.19.0", "trace_schema_version": "3.2.0"},
            "graphs": [{
                "name": "support", "version": "1.0.0", "spec_schema_version": "1.0.0",
                "graph_fingerprint": "graph:sha256:" + "a" * 64,
                "code_fingerprint": "code:sha256:" + "b" * 64,
            }],
        },
        "declarations": {"operator": {"id": "acme/support"}},
        "created_at": "2026-09-25T09:00:00Z",
        "supersedes": None,
    }


def registration(manifest_doc=None):
    value = fixture("ai-system-registration")
    if manifest_doc is not None:
        value["system_manifest_fingerprint"] = validate.system_manifest_fingerprint(manifest_doc)
    return value


def event(registration_doc, event_id="event-001", action="registered", predecessor=None):
    value = fixture("ai-system-registry-event")
    value["event_id"] = event_id
    value["registration_fingerprint"] = validate.ai_system_registration_fingerprint(registration_doc)
    value["action"] = action
    if predecessor is None:
        value.pop("predecessor_event_fingerprint", None)
    else:
        value["predecessor_event_fingerprint"] = predecessor
    return value


@pytest.mark.parametrize("kind", KINDS)
def test_schema_fixture_fingerprint_and_cli(kind, tmp_path):
    schema = json.loads((ROOT / "contract" / f"{kind}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    document = fixture(kind)
    validator = getattr(validate, "validate_" + kind.replace("-", "_"))
    fingerprint = getattr(validate, kind.replace("-", "_") + "_fingerprint")
    assert validator(document) == []
    assert fingerprint(document) == "sha256:" + hashlib.sha256(validate.canonical_json(document)).hexdigest()
    path = tmp_path / "document.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "contract" / "validate.py"), kind, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", KINDS)
def test_closed_documents_refuse_legal_or_completeness_verdicts(kind):
    document = fixture(kind)
    document["compliant"] = True
    validator = getattr(validate, "validate_" + kind.replace("-", "_"))
    assert "SCHEMA" in rules(validator(document))


@pytest.mark.parametrize("kind,field", [
    ("ai-system-registration", "declared_at"),
    ("ai-system-registry-event", "occurred_at"),
    ("ai-system-registry-snapshot", "observed_at"),
])
def test_real_utc_instants_are_required(kind, field):
    document = fixture(kind)
    document[field] = "2026-02-30T00:00:00Z"
    validator = getattr(validate, "validate_" + kind.replace("-", "_"))
    assert "AIR1" in rules(validator(document))


def test_event_lifecycle_predecessor_is_separate_from_correction():
    document = fixture("ai-system-registry-event")
    document["action"] = "suspended"
    assert "SCHEMA" in rules(validate.validate_ai_system_registry_event(document))
    document["predecessor_event_fingerprint"] = "sha256:" + "d" * 64
    assert validate.validate_ai_system_registry_event(document) == []
    document["action"] = "registered"
    assert "SCHEMA" in rules(validate.validate_ai_system_registry_event(document))


def test_duplicate_party_and_evidence_identities_are_refused():
    document = fixture("ai-system-registration")
    document["parties"] *= 2
    assert "AIR2" in rules(validate.validate_ai_system_registration(document))
    ev = fixture("ai-system-registry-event")
    identity = {"kind": "receipt", "fingerprint": "sha256:" + "e" * 64}
    ev["evidence"] = [identity, identity]
    assert "AIR2" in rules(validate.validate_ai_system_registry_event(ev))


def test_exact_manifest_binding_matches_and_system_id_mismatch_is_visible():
    supplied = manifest()
    document = registration(supplied)
    verdict = verify_ai_system_registration_binding(document, manifests=[supplied])
    assert verdict.binding_complete is True
    changed = copy.deepcopy(document)
    changed["system_id"] = "acme/another-system"
    verdict = verify_ai_system_registration_binding(changed, manifests=[supplied])
    assert {item.status for item in verdict.findings} == {"matched", "mismatched"}
    assert verdict.binding_complete is False


def test_missing_manifest_never_becomes_a_match():
    verdict = verify_ai_system_registration_binding(fixture("ai-system-registration"))
    assert verdict.findings[0].status == "missing"
    assert verdict.binding_complete is False


def test_registration_lineage_preserves_fork_conflict_and_order():
    first = fixture("ai-system-registration")
    first_fp = validate.ai_system_registration_fingerprint(first)
    second = copy.deepcopy(first)
    second["supersedes"] = first_fp
    second["contexts"] = ["Revision two."]
    third = copy.deepcopy(second)
    third["contexts"] = ["Competing revision."]
    verdict = verify_ai_system_registry_lineage("ai-system-registration", [third, first, second])
    assert verdict.ordered_fingerprints[0] == first_fp
    assert any(item.status == "conflict" for item in verdict.findings)


def test_conflict_free_supplied_lifecycle_projection_is_explicitly_scoped():
    reg = registration(manifest())
    first = event(reg)
    first_fp = validate.ai_system_registry_event_fingerprint(first)
    second = event(reg, "event-002", "activated", first_fp)
    verdict = project_supplied_ai_system_lifecycle(
        reg["registration_id"], registrations=[reg], events=[second, first]
    )
    assert verdict.ordered_event_fingerprints == (
        first_fp, validate.ai_system_registry_event_fingerprint(second)
    )
    assert verdict.terminal_action == "activated"
    assert "supplied" in verdict.scope


def test_lifecycle_fork_refuses_terminal_action():
    reg = registration(manifest())
    first = event(reg)
    first_fp = validate.ai_system_registry_event_fingerprint(first)
    second = event(reg, "event-002", "activated", first_fp)
    third = event(reg, "event-003", "suspended", first_fp)
    verdict = project_supplied_ai_system_lifecycle(
        reg["registration_id"], registrations=[reg], events=[first, second, third]
    )
    assert verdict.terminal_action is None
    assert any(item.status == "conflict" for item in verdict.findings)


def test_event_correction_requires_an_exact_conflict_free_chain():
    reg = registration(manifest())
    first = event(reg)
    correction = copy.deepcopy(first)
    correction["supersedes"] = validate.ai_system_registry_event_fingerprint(first)
    correction["reason_ref"] = "Corrected producer claim."
    verdict = project_supplied_ai_system_lifecycle(
        reg["registration_id"], registrations=[reg], events=[first, correction]
    )
    assert verdict.ordered_event_fingerprints == (
        validate.ai_system_registry_event_fingerprint(correction),
    )
    assert verdict.terminal_action == "registered"


def test_same_registration_id_across_registries_is_not_collapsed():
    first = registration(manifest())
    second = copy.deepcopy(first)
    second["producer_namespace"] = "other/governance"
    second["registry_id"] = "other/inventory"
    first_event = event(first)
    second_event = event(second)
    second_event["producer_namespace"] = second["producer_namespace"]
    second_event["registry_id"] = second["registry_id"]
    second_event["registration_fingerprint"] = validate.ai_system_registration_fingerprint(second)
    verdict = project_supplied_ai_system_lifecycle(
        first["registration_id"], registrations=[first, second],
        events=[first_event, second_event],
    )
    assert verdict.terminal_action is None
    assert any(item.path == "lifecycle:scope" and item.status == "conflict"
               for item in verdict.findings)


def test_snapshot_checks_exact_members_without_claiming_completeness():
    reg = registration(manifest())
    ev = event(reg)
    snapshot = fixture("ai-system-registry-snapshot")
    snapshot["registrations"] = [validate.ai_system_registration_fingerprint(reg)]
    snapshot["events"] = [validate.ai_system_registry_event_fingerprint(ev)]
    verdict = verify_ai_system_registry_snapshot(snapshot, registrations=[reg], events=[ev])
    assert {item.status for item in verdict.findings} == {"matched"}
    assert "not proof of completeness" in verdict.scope
    missing = verify_ai_system_registry_snapshot(snapshot)
    assert {item.status for item in missing.findings} == {"missing"}


def test_public_surface_exports_read_only_helpers():
    import vitruvyan_motus as motus
    for name in (
        "AISystemRegistryFinding", "AISystemRegistryLineageVerdict",
        "AISystemRegistrationBindingVerdict", "AISystemLifecycleProjection",
        "AISystemRegistrySnapshotVerdict", "verify_ai_system_registry_lineage",
        "verify_ai_system_registration_binding", "project_supplied_ai_system_lifecycle",
        "verify_ai_system_registry_snapshot",
    ):
        assert name in motus.__all__
        assert getattr(motus, name) is not None
