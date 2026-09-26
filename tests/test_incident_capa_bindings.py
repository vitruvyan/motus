"""ADR-039 deterministic lineage and evidence binding tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import vitruvyan_motus.incident_capa as incident_capa
from vitruvyan_motus import (
    evidence_package_fingerprint,
    order_incident_capa_entries,
    verify_incident_capa_ledger,
)
from vitruvyan_motus.contract import validate

FIXTURES = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


def _incident(**changes) -> dict:
    document = {
        "schema_version": "1.0.0",
        "producer_namespace": "acme/incidents",
        "producer_ref": "orbis-operator",
        "incident_id": "INC-001",
        "lifecycle": "open",
        "event_class": "reliability",
        "title": "Incomplete execution",
        "description": "A producer-declared incident.",
        "declared_at": "2026-09-23T10:00:00Z",
    }
    document.update(changes)
    return document


def _action(incident: dict, **changes) -> dict:
    document = {
        "schema_version": "1.0.0",
        "producer_namespace": incident["producer_namespace"],
        "producer_ref": "orbis-operator",
        "action_id": "CAPA-001",
        "incident_id": incident["incident_id"],
        "incident_fingerprint": validate.incident_declaration_fingerprint(incident),
        "action_kind": "corrective",
        "description": "Add a retry guard.",
        "lifecycle": "proposed",
        "declared_at": "2026-09-23T10:05:00Z",
    }
    document.update(changes)
    return document


def _ledger(*entries: tuple[str, dict]) -> dict:
    return {
        "schema_version": "1.0.0",
        "entries": [
            {"kind": kind, "document": document} for kind, document in entries
        ],
    }


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))["instance"]


def test_capa_links_stable_incident_reference_to_exact_revision():
    incident = _incident()
    action = _action(incident)
    verdict = verify_incident_capa_ledger(
        _ledger(("capa_action", action), ("incident_declaration", incident))
    )

    finding = next(
        item for item in verdict.findings if item.path.endswith("incident_fingerprint")
    )
    assert finding.status == "matched"
    assert verdict.bindings_complete is True


def test_absent_predecessor_is_not_verified_and_never_assumed_false():
    incident = _incident(supersedes="sha256:" + "a" * 64)
    verdict = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident))
    )

    assert verdict.ledger_violations == ()
    assert verdict.has_unverified is True
    assert verdict.findings[0].status == "not_verified"


def test_amendment_fork_is_preserved_as_conflict_and_has_no_winner():
    root = _incident()
    predecessor = validate.incident_declaration_fingerprint(root)
    first = _incident(
        lifecycle="contained",
        description="First producer amendment.",
        declared_at="2026-09-23T10:10:00Z",
        supersedes=predecessor,
    )
    second = _incident(
        lifecycle="contained",
        description="Second contradictory producer amendment.",
        declared_at="2026-09-23T10:11:00Z",
        supersedes=predecessor,
    )
    ledger = _ledger(
        ("incident_declaration", second),
        ("incident_declaration", root),
        ("incident_declaration", first),
    )

    verdict = verify_incident_capa_ledger(ledger)
    conflict = next(item for item in verdict.findings if item.status == "conflict")
    assert verdict.has_conflict is True
    assert conflict.observed == tuple(sorted(conflict.observed))
    assert set(verdict.ordered_fingerprints[1:]) == {
        validate.incident_declaration_fingerprint(first),
        validate.incident_declaration_fingerprint(second),
    }


def test_ordering_and_ledger_identity_are_independent_of_input_array_order():
    incident = _incident()
    action = _action(incident)
    left = _ledger(("capa_action", action), ("incident_declaration", incident))
    right = copy.deepcopy(left)
    right["entries"].reverse()

    left_verdict = verify_incident_capa_ledger(left)
    right_verdict = verify_incident_capa_ledger(right)
    assert left_verdict.ordered_fingerprints == right_verdict.ordered_fingerprints
    assert left_verdict.ledger_fingerprint == right_verdict.ledger_fingerprint


def test_execution_and_exact_artifact_references_are_derived_from_supplied_docs():
    manifest = _fixture("310-system-manifest-valid.json")
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    incident = _incident(evidence=[
        {"kind": "execution", "execution_ref": receipt["execution"]["ref"]},
        {
            "kind": "system_manifest",
            "fingerprint": validate.system_manifest_fingerprint(manifest),
        },
    ])

    verdict = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        execution_receipts=[receipt],
        manifests=[manifest],
    )

    assert {item.status for item in verdict.findings} == {"matched"}
    assert verdict.bindings_complete is True


def test_execution_receipt_join_has_a_cumulative_semantic_work_budget(monkeypatch):
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    incident = _incident(evidence=[{
        "kind": "execution",
        "execution_ref": receipt["execution"]["ref"],
    }])
    monkeypatch.setattr(incident_capa, "_EXECUTION_JOIN_WORK_LIMIT", -1)

    verdict = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        execution_receipts=[receipt],
    )

    assert verdict.bindings_complete is False
    assert any(
        item.path == "$.execution_receipts"
        and item.status == "not_verified"
        and "semantic-work limit" in item.reason
        for item in verdict.findings
    )
    execution = next(
        item for item in verdict.findings if item.path.endswith("execution_ref")
    )
    assert execution.status == "not_verified"


def test_wrong_exact_artifact_is_mismatched_and_absence_is_not_verified():
    incident = _incident(evidence=[{
        "kind": "system_manifest",
        "fingerprint": "sha256:" + "f" * 64,
    }])
    ledger = _ledger(("incident_declaration", incident))

    absent = verify_incident_capa_ledger(ledger)
    wrong = verify_incident_capa_ledger(
        ledger, manifests=[_fixture("310-system-manifest-valid.json")]
    )
    assert absent.findings[0].status == "missing"
    assert absent.has_missing is True
    assert wrong.findings[0].status == "mismatched"


def test_exact_control_application_identity_does_not_bypass_its_verifier():
    registry = _fixture("320-risk-control-registry-valid.json")
    registry.pop("system")
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    application = {
        "schema_version": "1.0.0",
        "registry_fingerprint": validate.risk_control_registry_fingerprint(registry),
        "control_id": "C-001",
        "execution_ref": receipt["execution"]["ref"],
        "enforcement_point": "tool_dispatch",
        "outcome": "blocked",
        "observed_at": "2026-09-23T10:01:00Z",
        "evidence": {"kind": "motus_execution"},
    }
    incident = _incident(evidence=[{
        "kind": "control_application",
        "fingerprint": validate.control_application_fingerprint(application),
    }])

    incomplete = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        control_applications=[application],
    )
    complete = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        registries=[registry],
        control_applications=[application],
        execution_receipts=[receipt],
    )

    assert incomplete.findings[0].status == "matched"
    assert any(item.status == "missing" for item in incomplete.findings)
    assert incomplete.bindings_complete is False
    assert {item.status for item in complete.findings} == {"matched"}
    assert complete.bindings_complete is True


def test_receipt_and_evidence_package_reference_kinds_are_admitted():
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    package = b"not a valid evidence package"
    receipt_ref = {
        "kind": "receipt",
        "fingerprint": validate.receipt_fingerprint(receipt),
    }
    package_ref = {
        "kind": "evidence_package",
        "fingerprint": evidence_package_fingerprint(package),
    }
    incident = _incident(evidence=[receipt_ref, package_ref])

    assert validate.validate_incident_declaration(incident) == []
    absent = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        execution_receipts=[receipt],
    )
    supplied = verify_incident_capa_ledger(
        _ledger(("incident_declaration", incident)),
        execution_receipts=[receipt],
        evidence_packages=[package],
    )

    by_path = {item.path: item for item in absent.findings}
    assert by_path["$.entries[0].document.evidence[0].fingerprint"].status == "matched"
    assert by_path["$.entries[0].document.evidence[1].fingerprint"].status == "missing"
    supplied_by_path = {item.path: item for item in supplied.findings}
    assert supplied_by_path[
        "$.entries[0].document.evidence[1].fingerprint"
    ].status == "matched"
    assert supplied_by_path[
        "$.entries[0].document.evidence[1].verification"
    ].status == "not_verified"


def test_receipt_with_inconsistent_derived_identity_is_refused():
    receipt = _fixture("311-receipt-attestation-rfc3161-claimed.json")
    receipt["execution"]["run_id"] = "tampered"
    incident = _incident(evidence=[{
        "kind": "execution",
        "execution_ref": "acme/w1/0",
    }])

    with pytest.raises(ValueError, match="inconsistent derived execution identity"):
        verify_incident_capa_ledger(
            _ledger(("incident_declaration", incident)),
            execution_receipts=[receipt],
        )


def test_invalid_ledger_produces_no_fingerprint_order_or_findings():
    ledger = _ledger(("incident_declaration", _incident(compliant=True)))
    verdict = verify_incident_capa_ledger(ledger)
    assert verdict.ledger_fingerprint is None
    assert verdict.ledger_violations
    assert verdict.ordered_fingerprints == ()
    assert verdict.findings == ()


def test_verification_and_ordering_do_not_mutate_inputs_and_return_snapshots():
    incident = _incident()
    action = _action(incident)
    ledger = _ledger(("incident_declaration", incident), ("capa_action", action))
    before = copy.deepcopy(ledger)

    ordered = order_incident_capa_entries(ledger)
    verify_incident_capa_ledger(ledger)
    ordered[0]["document"]["title"] = "detached"

    assert ledger == before
