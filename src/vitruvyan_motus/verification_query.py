"""ADR-043 transport-neutral inspection over existing Motus contracts.

This module is deliberately storage-free and imports the contract validator
lazily. Inspection establishes local structure and exact identity where Motus
already defines one. It is not binding verification, discovery, or compliance.
"""
from __future__ import annotations

import base64
import importlib
from typing import Any

__all__ = ["inspect_artifact"]

_LIMITATIONS = [
    "inspection covers only the explicitly supplied artifact",
    "structural validity does not establish compliance or global completeness",
]

_JSON_DISPATCH: dict[str, tuple[str, str | None]] = {
    "graphspec": ("validate_graphspec", "graph"),
    "trace": ("validate_trace", None),
    "commitment": ("validate_commitment", None),
    "checkpoint": ("validate_checkpoint", None),
    "execution_receipt": ("validate_receipt", "receipt_fingerprint"),
    "system_manifest": ("validate_system_manifest", "system_manifest_fingerprint"),
    "risk_control_registry": ("validate_risk_control_registry", "risk_control_registry_fingerprint"),
    "control_application": ("validate_control_application", "control_application_fingerprint"),
    "human_oversight_receipt": ("validate_human_oversight_receipt", "human_oversight_receipt_fingerprint"),
    "regulatory_evidence_profile": ("validate_regulatory_evidence_profile", "regulatory_evidence_profile_fingerprint"),
    "regulatory_evidence_dossier": ("validate_regulatory_evidence_dossier", "regulatory_evidence_dossier_fingerprint"),
    "incident_declaration": ("validate_incident_declaration", "incident_declaration_fingerprint"),
    "capa_action": ("validate_capa_action", "capa_action_fingerprint"),
    "incident_capa_ledger": ("validate_incident_capa_ledger", "incident_capa_ledger_fingerprint"),
    "retention_policy_declaration": ("validate_retention_policy_declaration", "retention_policy_declaration_fingerprint"),
    "legal_hold_declaration": ("validate_legal_hold_declaration", "legal_hold_declaration_fingerprint"),
    "retention_scope_snapshot": ("validate_retention_scope_snapshot", "retention_scope_snapshot_fingerprint"),
    "retention_trigger_occurrence": ("validate_retention_trigger_occurrence", "retention_trigger_occurrence_fingerprint"),
    "retention_application": ("validate_retention_application", "retention_application_fingerprint"),
    "custody_observation": ("validate_custody_observation", "custody_observation_fingerprint"),
    "ai_system_registration": ("validate_ai_system_registration", "ai_system_registration_fingerprint"),
    "ai_system_registry_event": ("validate_ai_system_registry_event", "ai_system_registry_event_fingerprint"),
    "ai_system_registry_snapshot": ("validate_ai_system_registry_snapshot", "ai_system_registry_snapshot_fingerprint"),
}


def _contract():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _violation(item: Any) -> dict[str, str]:
    return {"rule": item.rule, "path": item.path, "message": item.message}


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _scope(input_ids: list[str]) -> dict[str, Any]:
    return {
        "scope_kind": "supplied_inputs",
        "input_ids": input_ids,
        "global_complete": False,
        "limitations": list(_LIMITATIONS),
    }


def _checked_result(result: dict[str, Any]) -> dict[str, Any]:
    validate = _contract()
    violations = validate.validate_verification_query_message(result)
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        raise RuntimeError("internal verification/query result violated its contract: " + detail)
    return result


def _invalid_request(violations: list[Any], input_id: object) -> dict[str, Any]:
    ids = [input_id] if isinstance(input_id, str) and input_id else []
    return _checked_result({
        "interface_version": "1.0.0",
        "message_type": "result",
        "operation": "inspect",
        "outcome": "invalid_request",
        "scope": _scope(ids),
        "subject": None,
        "violations": [_violation(item) for item in violations],
        "findings": [],
        "matches": [],
        "records": [],
    })


def _json_fingerprint(validate: Any, kind: str, document: Any) -> str | None:
    fingerprint_name = _JSON_DISPATCH[kind][1]
    if fingerprint_name is None:
        return None
    if fingerprint_name == "graph":
        return validate.fingerprint("graph", document)
    return getattr(validate, fingerprint_name)(document)


def _json_inspection(
    validate: Any, input_id: str, kind: str, document: Any,
) -> dict[str, Any]:
    validator_name, _fingerprint_name = _JSON_DISPATCH[kind]
    violations = list(getattr(validate, validator_name)(document))
    fingerprint = None if violations else _json_fingerprint(validate, kind, document)
    return {
        "interface_version": "1.0.0",
        "message_type": "result",
        "operation": "inspect",
        "outcome": "invalid" if violations else "valid",
        "scope": _scope([input_id]),
        "subject": {"input_id": input_id, "kind": kind, "fingerprint": fingerprint},
        "violations": [_violation(item) for item in violations],
        "findings": [],
        "matches": [],
        "records": [],
    }


def _package_inspection(input_id: str, kind: str, data: bytes) -> dict[str, Any]:
    evidence = importlib.import_module("vitruvyan_motus.evidence")
    package = evidence.verify_package(data)
    findings: list[dict[str, Any]] = []
    for name in package.damaged:
        findings.append({
            "path": "$.members", "status": "damaged",
            "observed": name, "reason": "package member failed transport integrity",
        })
    for issue in package.trace_violations:
        findings.append({
            "path": "$.trace", "status": "mismatched",
            "observed": issue, "reason": "embedded trace failed its Motus contract",
        })
    violations = [] if package.verdict is None else [
        _violation(item) for item in package.verdict.violations
    ]
    valid = package.transport_ok and not package.damaged and not package.trace_violations and not violations
    return {
        "interface_version": "1.0.0", "message_type": "result",
        "operation": "inspect", "outcome": "valid" if valid else "invalid",
        "scope": _scope([input_id]),
        "subject": {"input_id": input_id, "kind": kind, "fingerprint": evidence.evidence_package_fingerprint(data)},
        "violations": violations, "findings": findings, "matches": [], "records": [],
    }


def _dossier_export_inspection(input_id: str, kind: str, data: bytes) -> dict[str, Any]:
    dossier = importlib.import_module("vitruvyan_motus.regulatory_dossier")
    verdict = dossier.verify_regulatory_dossier(data)
    findings = [
        {"path": item.path, "status": item.status, "expected": _json_value(item.expected), "observed": _json_value(item.observed), "reason": item.reason}
        for item in verdict.findings
    ]
    findings.extend(
        {"path": f"$.entries[{item.entry_id}]", "status": item.status, "observed": list(item.violations), "reason": "dossier member inspection did not match its declaration"}
        for item in verdict.entries if item.status != "matched"
    )
    return {
        "interface_version": "1.0.0", "message_type": "result",
        "operation": "inspect", "outcome": "valid" if verdict.transport_ok else "invalid",
        "scope": _scope([input_id]),
        "subject": {"input_id": input_id, "kind": kind, "fingerprint": verdict.export_fingerprint},
        "violations": [_violation(item) for item in verdict.manifest_violations],
        "findings": findings, "matches": [], "records": [],
    }


def inspect_artifact(artifact: Any) -> dict[str, Any]:
    """Inspect exactly one explicitly typed artifact and return contract JSON.

    Invalid interface input is reported as ``invalid_request``. A structurally
    invalid artifact is ``invalid``. ``valid`` means only that the existing
    Motus contract accepted the supplied artifact; it is not a binding,
    completeness, legal, safety, or compliance verdict.
    """
    request = {
        "interface_version": "1.0.0",
        "message_type": "request",
        "operation": "inspect",
        "artifact": artifact,
    }
    validate = _contract()
    request_violations = validate.validate_verification_query_message(request)
    if request_violations:
        input_id = artifact.get("input_id") if isinstance(artifact, dict) else None
        return _invalid_request(request_violations, input_id)

    input_id = artifact["input_id"]
    kind = artifact["kind"]
    if artifact["media_type"] == "application/json":
        result = _json_inspection(validate, input_id, kind, artifact["document"])
    else:
        data = base64.b64decode(artifact["content_base64"].encode("ascii"), validate=True)
        if kind == "execution_evidence_package":
            result = _package_inspection(input_id, kind, data)
        else:
            result = _dossier_export_inspection(input_id, kind, data)
    return _checked_result(result)
