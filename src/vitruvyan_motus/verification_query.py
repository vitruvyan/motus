"""ADR-043 transport-neutral inspection over existing Motus contracts.

This module is deliberately storage-free and imports the contract validator
lazily. Inspection establishes local structure and exact identity where Motus
already defines one. It is not binding verification, discovery, or compliance.
"""
from __future__ import annotations

import base64
import importlib
import io
import struct
import zipfile
import zlib
from typing import Any

__all__ = ["execute_verification_query", "inspect_artifact", "query_artifacts", "verify_artifact"]

_LIMITATIONS = [
    "inspection covers only the explicitly supplied artifact",
    "structural validity does not establish compliance or global completeness",
]

_RESULT_ITEM_LIMIT = 10_000
_PACKAGE_MEMBER_LIMIT = 1_000
_PACKAGE_MEMBER_MAX_BYTES = 28 * 1024 * 1024
_PACKAGE_TOTAL_MAX_BYTES = 128 * 1024 * 1024
_PACKAGE_DIRECTORY_TOTAL_MAX_BYTES = 16 * 1024 * 1024
_PACKAGE_NESTING_LIMIT = 16


def _bounded_member_name(name: str) -> str:
    return name if len(name) <= 512 else name[:509] + "..."


class _ZipWorkBudget:
    __slots__ = ("directory_bytes", "expanded_bytes", "members")

    def __init__(self) -> None:
        self.directory_bytes = 0
        self.expanded_bytes = 0
        self.members = 0

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


def _scope(input_ids: list[str], operation: str = "inspection") -> dict[str, Any]:
    return {
        "scope_kind": "supplied_inputs",
        "input_ids": input_ids,
        "global_complete": False,
        "limitations": [
            f"{operation} covers only the explicitly supplied artifacts",
            _LIMITATIONS[1],
        ],
    }


def _checked_result(result: dict[str, Any]) -> dict[str, Any]:
    result = _bounded_result(result)
    validate = _contract()
    violations = validate.validate_verification_query_message(result)
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        raise RuntimeError("internal verification/query result violated its contract: " + detail)
    return result


def _bounded_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep every public result inside the v1 envelope's cardinality bounds.

    Domain verifiers are allowed to report one or more findings per supplied
    record, so a bounded request can still expand beyond the result schema.
    Truncation is explicit and fail-closed rather than an internal exception.
    """
    bounded = dict(result)
    markers: list[dict[str, Any]] = []
    for field in ("violations", "matches", "records"):
        values = list(bounded[field])
        if len(values) > _RESULT_ITEM_LIMIT:
            bounded[field] = values[:_RESULT_ITEM_LIMIT]
            markers.append({
                "path": f"$.{field}",
                "status": "incomplete",
                "expected": f"at most {_RESULT_ITEM_LIMIT} result items",
                "observed": len(values),
                "reason": f"{field} were deterministically truncated to the v1 result bound",
            })

    findings = list(bounded["findings"])
    if len(findings) + len(markers) > _RESULT_ITEM_LIMIT:
        keep = max(0, _RESULT_ITEM_LIMIT - len(markers) - 1)
        observed = len(findings)
        findings = findings[:keep]
        markers.append({
            "path": "$.findings",
            "status": "incomplete",
            "expected": f"at most {_RESULT_ITEM_LIMIT} result items",
            "observed": observed,
            "reason": "findings were deterministically truncated to the v1 result bound",
        })
    bounded["findings"] = findings + markers
    if markers:
        bounded["outcome"] = {
            "inspect": "invalid", "verify": "not_verified", "query": "invalid",
        }[bounded["operation"]]
    return bounded


def _invalid_request(
    violations: list[Any], input_id: object, operation: str = "inspect",
) -> dict[str, Any]:
    ids = [input_id] if isinstance(input_id, str) and input_id else []
    return _checked_result({
        "interface_version": "1.0.0",
        "message_type": "result",
        "operation": operation,
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


def _zip_limit_finding(
    reason: str, *, expected: Any = None, observed: Any = None,
) -> dict[str, Any]:
    finding = {"path": "$.members", "status": "not_verified", "reason": reason}
    if expected is not None:
        finding["expected"] = expected
    if observed is not None:
        finding["observed"] = observed
    return finding


def _zip_directory_claim(data: bytes) -> tuple[int, int] | None:
    """Read the classic ZIP directory count/size without constructing ZipFile."""
    offset = data.rfind(b"PK\x05\x06", max(0, len(data) - 65_557))
    if offset < 0:
        return None
    if offset + 22 > len(data):
        raise ValueError("truncated ZIP end-of-central-directory record")
    (_signature, disk, directory_disk, entries_on_disk, entries, directory_size,
     _directory_offset, comment_size) = struct.unpack_from("<4s4H2LH", data, offset)
    if offset + 22 + comment_size != len(data):
        raise ValueError("ZIP end-of-central-directory record is not terminal")
    if disk != 0 or directory_disk != 0 or entries_on_disk != entries:
        raise ValueError("multi-disk ZIP cannot be bounded by this interface")
    if entries == 0xFFFF or directory_size == 0xFFFFFFFF:
        raise ValueError("ZIP64 directory cannot be bounded by this interface")
    return entries, directory_size


def _package_expansion_finding(
    data: bytes, budget: _ZipWorkBudget, depth: int = 0,
) -> dict[str, Any] | None:
    """Bound cumulative and nested ZIP work before a domain verifier runs."""
    if depth > _PACKAGE_NESTING_LIMIT:
        return _zip_limit_finding(
            "nested ZIP depth exceeds the interface work limit",
            expected=_PACKAGE_NESTING_LIMIT, observed=depth,
        )
    try:
        claim = _zip_directory_claim(data)
    except ValueError as exc:
        return _zip_limit_finding(
            "ZIP directory could not be bounded before parsing",
            observed=str(exc),
        )
    if claim is None:
        return None  # The authoritative verifier reports malformed transport.
    claimed_members, directory_size = claim
    if budget.members + claimed_members > _PACKAGE_MEMBER_LIMIT:
        return _zip_limit_finding(
            "request exceeds the cumulative ZIP member-count work limit",
            expected=_PACKAGE_MEMBER_LIMIT,
            observed=budget.members + claimed_members,
        )
    if budget.directory_bytes + directory_size > _PACKAGE_DIRECTORY_TOTAL_MAX_BYTES:
        return _zip_limit_finding(
            "request exceeds the cumulative ZIP directory-byte work limit",
            expected=_PACKAGE_DIRECTORY_TOTAL_MAX_BYTES,
            observed=budget.directory_bytes + directory_size,
        )
    budget.members += claimed_members
    budget.directory_bytes += directory_size
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return None  # The authoritative verifier reports malformed transport.
    try:
        infos = archive.infolist()
        if len(infos) != claimed_members:
            return _zip_limit_finding(
                "ZIP directory count changed during bounded parsing",
                expected=claimed_members, observed=len(infos),
            )
        declared_total = sum(info.file_size for info in infos)
        if budget.expanded_bytes + declared_total > _PACKAGE_TOTAL_MAX_BYTES:
            return _zip_limit_finding(
                "request exceeds the cumulative expanded-work limit",
                expected=_PACKAGE_TOTAL_MAX_BYTES,
                observed=budget.expanded_bytes + declared_total,
            )
        for info in infos:
            if info.file_size > _PACKAGE_MEMBER_MAX_BYTES:
                return _zip_limit_finding(
                    "ZIP member exceeds the expanded-byte limit",
                    expected=_PACKAGE_MEMBER_MAX_BYTES,
                    observed={"member": _bounded_member_name(info.filename), "expanded_bytes": info.file_size},
                )
            member_total = 0
            nested_chunks: list[bytes] | None = None
            with archive.open(info) as member:
                while True:
                    chunk = member.read(min(
                        1024 * 1024,
                        _PACKAGE_MEMBER_MAX_BYTES - member_total + 1,
                        _PACKAGE_TOTAL_MAX_BYTES - budget.expanded_bytes + 1,
                    ))
                    if not chunk:
                        break
                    if member_total == 0 and chunk.startswith(b"PK"):
                        nested_chunks = []
                    if nested_chunks is not None:
                        nested_chunks.append(chunk)
                    member_total += len(chunk)
                    budget.expanded_bytes += len(chunk)
                    if member_total > _PACKAGE_MEMBER_MAX_BYTES:
                        return _zip_limit_finding(
                            "ZIP member exceeds the expanded-byte limit",
                            expected=_PACKAGE_MEMBER_MAX_BYTES,
                            observed={"member": _bounded_member_name(info.filename), "expanded_bytes": member_total},
                        )
                    if budget.expanded_bytes > _PACKAGE_TOTAL_MAX_BYTES:
                        return _zip_limit_finding(
                            "request exceeds the cumulative expanded-work limit",
                            expected=_PACKAGE_TOTAL_MAX_BYTES,
                            observed=budget.expanded_bytes,
                        )
            if nested_chunks is not None:
                nested_finding = _package_expansion_finding(
                    b"".join(nested_chunks), budget, depth + 1,
                )
                if nested_finding is not None:
                    return nested_finding
    except (NotImplementedError, RuntimeError, zipfile.LargeZipFile,
            zipfile.BadZipFile, EOFError, OSError, MemoryError, zlib.error) as exc:
        return {
            "path": "$.members", "status": "not_verified",
            "observed": type(exc).__name__,
            "reason": "evidence package expansion could not be bounded safely",
        }
    finally:
        archive.close()
    return None


def _package_inspection(
    input_id: str, kind: str, data: bytes, budget: _ZipWorkBudget,
) -> dict[str, Any]:
    evidence = importlib.import_module("vitruvyan_motus.evidence")
    expansion_finding = _package_expansion_finding(data, budget)
    if expansion_finding is not None:
        return {
            "interface_version": "1.0.0", "message_type": "result",
            "operation": "inspect", "outcome": "invalid",
            "scope": _scope([input_id]),
            "subject": {"input_id": input_id, "kind": kind, "fingerprint": evidence.evidence_package_fingerprint(data)},
            "violations": [], "findings": [expansion_finding], "matches": [], "records": [],
        }
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


def _dossier_export_inspection(
    input_id: str, kind: str, data: bytes, budget: _ZipWorkBudget,
) -> dict[str, Any]:
    dossier = importlib.import_module("vitruvyan_motus.regulatory_dossier")
    expansion_finding = _package_expansion_finding(data, budget)
    if expansion_finding is not None:
        return {
            "interface_version": "1.0.0", "message_type": "result",
            "operation": "inspect", "outcome": "invalid",
            "scope": _scope([input_id]),
            "subject": {"input_id": input_id, "kind": kind, "fingerprint": dossier.regulatory_dossier_export_fingerprint(data)},
            "violations": [], "findings": [expansion_finding], "matches": [], "records": [],
        }
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
    return _inspect_artifact(artifact, _ZipWorkBudget())


def _inspect_artifact(artifact: Any, work_budget: _ZipWorkBudget) -> dict[str, Any]:
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
            result = _package_inspection(input_id, kind, data, work_budget)
        else:
            result = _dossier_export_inspection(input_id, kind, data, work_budget)
    return _checked_result(result)


def _validated_inputs(
    artifacts: list[dict[str, Any]], work_budget: _ZipWorkBudget,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    valid = []
    invalid = []
    for artifact in artifacts:
        inspected = _inspect_artifact(artifact, work_budget)
        if inspected["outcome"] == "valid":
            valid.append((artifact, inspected))
        else:
            if inspected["findings"]:
                detail = inspected["findings"][0]["reason"]
            elif inspected["violations"]:
                detail = inspected["violations"][0]["message"]
            else:
                detail = inspected["outcome"]
            reason = (
                "the supplied artifact did not satisfy its own Motus contract: "
                + str(detail)
            )[:8192]
            invalid.append({
                "path": f"$.artifacts[{artifact['input_id']}]",
                "status": "not_verified",
                "observed": inspected["outcome"],
                "reason": reason,
            })
    return valid, invalid


def _execution_refs(kind: str, document: dict[str, Any]) -> set[str]:
    if kind in ("control_application", "human_oversight_receipt"):
        return {document["execution_ref"]}
    if kind == "execution_receipt":
        refs = set()
        for segment in document["segments"]:
            entry = segment.get("begin")
            if isinstance(entry, dict):
                commitment = entry["commitment"]
                refs.add(f"{commitment['tenant']}/{commitment['writer_id']}/{commitment['sequence']}")
        return refs
    if kind in ("incident_declaration", "capa_action"):
        return {
            item["execution_ref"] for item in document.get("evidence", ())
            if item.get("kind") == "execution_ref"
        }
    if kind == "retention_policy_declaration":
        selector = document.get("scope")
        if isinstance(selector, dict) and selector.get("kind") == "execution_refs":
            return set(selector["execution_refs"])
    return set()


def _query_result(
    artifacts: list[dict[str, Any]], projection: dict[str, Any],
    work_budget: _ZipWorkBudget,
) -> dict[str, Any]:
    supplied_ids = [item["input_id"] for item in artifacts]
    valid, invalid_findings = _validated_inputs(artifacts, work_budget)
    by_id = {item["input_id"]: (item, inspected) for item, inspected in valid}
    matches: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = list(invalid_findings)
    kind = projection["kind"]

    if kind == "artifact_identity":
        for artifact, inspected in valid:
            subject = inspected["subject"]
            if (artifact["kind"] == projection["artifact_kind"]
                    and subject["fingerprint"] == projection["fingerprint"]):
                matches.append(subject)
    elif kind == "execution_ref":
        for artifact, inspected in valid:
            if artifact["media_type"] == "application/json" and projection["execution_ref"] in _execution_refs(artifact["kind"], artifact["document"]):
                matches.append(inspected["subject"])
    elif kind in ("controls_for_risk", "risks_for_control"):
        if projection["registry_input_id"] not in by_id:
            return {
                "interface_version": "1.0.0", "message_type": "result", "operation": "query", "outcome": "invalid",
                "scope": _scope(supplied_ids, "query"), "subject": None, "violations": [],
                "findings": [*findings, {"path": "$.projection.registry_input_id", "status": "not_verified", "reason": "the selected supplied registry did not satisfy its structural contract"}],
                "matches": [], "records": [],
            }
        source, inspected = by_id[projection["registry_input_id"]]
        risk_control = importlib.import_module("vitruvyan_motus.risk_control")
        try:
            if kind == "controls_for_risk":
                values = risk_control.controls_for_risk(source["document"], projection["risk_id"])
                record_kind = "control"
            else:
                values = risk_control.risks_for_control(source["document"], projection["control_id"])
                record_kind = "risk"
        except KeyError as exc:
            values = ()
            record_kind = "control" if kind == "controls_for_risk" else "risk"
            findings.append({"path": "$.projection", "status": "missing", "expected": str(exc.args[0]), "reason": "the selected identifier is absent from the supplied registry"})
        matches.append(inspected["subject"])
        records.extend({"source_input_id": source["input_id"], "record_kind": record_kind, "record": value} for value in values)
    elif kind == "correction_lineage":
        wanted = projection["artifact_kind"]
        rows = []
        for artifact, inspected in valid:
            if artifact["kind"] == wanted:
                document = artifact["document"]
                rows.append((inspected["subject"]["fingerprint"], artifact, document))
                matches.append(inspected["subject"])
        for fingerprint, artifact, document in sorted(rows, key=lambda row: row[0]):
            records.append({
                "source_input_id": artifact["input_id"],
                "record_kind": "lineage_edge",
                "record": {"fingerprint": fingerprint, "supersedes": document.get("supersedes")},
            })
        by_fingerprint: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        parents: dict[str, int] = {}
        predecessor_of: dict[str, str] = {}
        for fingerprint, artifact, document in rows:
            by_fingerprint.setdefault(fingerprint, []).append((artifact, document))
            parent = document.get("supersedes")
            if parent is not None:
                parents[parent] = parents.get(parent, 0) + 1
                predecessor_of[fingerprint] = parent
        for fingerprint, duplicates in sorted(by_fingerprint.items()):
            if len(duplicates) > 1:
                findings.append({"path": f"lineage:{fingerprint}", "status": "conflict", "expected": "one supplied artifact per fingerprint", "observed": len(duplicates), "reason": "multiple supplied artifacts have the same correction identity"})
        for fingerprint, parent in sorted(predecessor_of.items()):
            candidates = by_fingerprint.get(parent, ())
            if not candidates:
                findings.append({"path": f"lineage:{fingerprint}.supersedes", "status": "incomplete", "expected": parent, "reason": "the immediate predecessor is absent from this supplied view"})
            elif len(candidates) > 1:
                findings.append({"path": f"lineage:{fingerprint}.supersedes", "status": "conflict", "expected": parent, "observed": len(candidates), "reason": "the predecessor identity is ambiguous in the supplied view"})
        for parent, count in sorted(parents.items()):
            if count > 1:
                findings.append({"path": f"lineage:{parent}", "status": "conflict", "expected": parent, "observed": count, "reason": "multiple supplied revisions name the same predecessor; no winner was selected"})
        cycle_members: set[str] = set()
        for start in sorted(predecessor_of):
            cursor = start
            path: list[str] = []
            positions: dict[str, int] = {}
            while cursor in predecessor_of and len(by_fingerprint.get(cursor, ())) == 1:
                if cursor in positions:
                    cycle_members.update(path[positions[cursor]:])
                    break
                positions[cursor] = len(path)
                path.append(cursor)
                parent = predecessor_of[cursor]
                if len(by_fingerprint.get(parent, ())) != 1:
                    break
                cursor = parent
        if cycle_members:
            findings.append({"path": "lineage:cycle", "status": "conflict", "observed": sorted(cycle_members), "reason": "the supplied correction lineage contains a cycle; no winner was selected"})
    elif kind == "dossier_membership":
        if projection["dossier_input_id"] not in by_id:
            return {
                "interface_version": "1.0.0", "message_type": "result", "operation": "query", "outcome": "invalid",
                "scope": _scope(supplied_ids, "query"), "subject": None, "violations": [],
                "findings": [*findings, {"path": "$.projection.dossier_input_id", "status": "not_verified", "reason": "the selected supplied dossier did not satisfy its structural contract"}],
                "matches": [], "records": [],
            }
        source, inspected = by_id[projection["dossier_input_id"]]
        matches.append(inspected["subject"])
        records.extend({"source_input_id": source["input_id"], "record_kind": "dossier_entry", "record": entry} for entry in source["document"]["entries"])
    else:
        namespace = projection["producer_namespace"]
        registration_id = projection["registration_id"]
        registrations = [item["document"] for item, _ in valid if item["kind"] == "ai_system_registration" and item["document"]["producer_namespace"] == namespace]
        events = [item["document"] for item, _ in valid if item["kind"] == "ai_system_registry_event" and item["document"]["producer_namespace"] == namespace]
        ai = importlib.import_module("vitruvyan_motus.ai_system_registry")
        projected = ai.project_supplied_ai_system_lifecycle(registration_id, registrations=registrations, events=events)
        selected_event_fingerprints = set(projected.ordered_event_fingerprints)
        selected_registrations = [
            (item, inspected) for item, inspected in valid
            if item["kind"] == "ai_system_registration"
            and item["document"]["producer_namespace"] == namespace
            and item["document"]["registration_id"] == registration_id
        ]
        selected_events = [
            (item, inspected) for item, inspected in valid
            if item["kind"] == "ai_system_registry_event"
            and item["document"]["producer_namespace"] == namespace
            and item["document"]["registration_id"] == registration_id
            and inspected["subject"]["fingerprint"] in selected_event_fingerprints
        ]
        selected = selected_registrations + selected_events
        source_ids = [item["input_id"] for item, _ in selected]
        for artifact, inspected in valid:
            if artifact["input_id"] in source_ids:
                matches.append(inspected["subject"])
        if source_ids:
            registration_source_ids = sorted(item["input_id"] for item, _ in selected_registrations)
            source_id = registration_source_ids[0] if registration_source_ids else sorted(source_ids)[0]
            records.append({"source_input_id": source_id, "record_kind": "ai_lifecycle", "record": {"producer_namespace": namespace, "registration_id": registration_id, "ordered_event_fingerprints": list(projected.ordered_event_fingerprints), "terminal_action": projected.terminal_action}})
        findings.extend({"path": item.path, "status": item.status.replace(" ", "_"), "expected": _json_value(item.expected), "observed": _json_value(item.observed), "reason": item.reason} for item in projected.findings)

    matches.sort(key=lambda item: (item["kind"], item["input_id"], item["fingerprint"] or ""))
    records.sort(key=lambda item: (item["record_kind"], item["source_input_id"], str(item["record"])))
    outcome = "invalid" if invalid_findings else ("conflict" if any(item["status"] == "conflict" for item in findings) else "completed")
    return {
        "interface_version": "1.0.0", "message_type": "result",
        "operation": "query", "outcome": outcome,
        "scope": _scope(supplied_ids, "query"), "subject": None,
        "violations": [], "findings": findings, "matches": matches,
        "records": records,
    }


def query_artifacts(
    projection: Any, artifacts: Any,
) -> dict[str, Any]:
    """Run one closed projection over exactly the supplied artifact inputs."""
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": projection, "artifacts": artifacts}
    validate = _contract()
    violations = validate.validate_verification_query_message(request)
    if violations:
        return _invalid_request(violations, None, "query")
    result = _query_result(list(artifacts), projection, _ZipWorkBudget())
    return _checked_result(result)


def _status(value: str) -> str:
    normalized = value.lower().strip()
    return {
        "verified": "matched", "established": "matched", "matched": "matched",
        "failed": "mismatched", "mismatched": "mismatched",
        "not established": "not_verified", "not yet": "not_verified",
        "claimed, unchecked": "not_verified", "not verified": "not_verified",
        "refused": "not_verified", "missing": "missing", "conflict": "conflict",
        "damaged": "damaged", "incomplete": "incomplete",
    }.get(normalized, "not_verified")


def _finding_value(item: Any, index: int) -> dict[str, Any]:
    path = getattr(item, "path", None) or getattr(item, "level", None) or f"$.findings[{index}]"
    result = {"path": str(path), "status": _status(str(item.status)), "reason": str(item.reason)}
    if hasattr(item, "expected"):
        result["expected"] = _json_value(item.expected)
    if hasattr(item, "observed"):
        result["observed"] = _json_value(item.observed)
    return result


def _flatten_violations(value: Any) -> list[dict[str, str]]:
    if hasattr(value, "rule") and hasattr(value, "path") and hasattr(value, "message"):
        return [_violation(value)]
    if isinstance(value, (tuple, list)):
        out = []
        for item in value:
            out.extend(_flatten_violations(item))
        return out
    return []


def _verdict_parts(verdict: Any) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    violations = []
    for name in dir(verdict):
        if name.endswith("violations"):
            violations.extend(_flatten_violations(getattr(verdict, name)))
    findings = [_finding_value(item, index) for index, item in enumerate(getattr(verdict, "findings", ()))]
    return violations, findings


def _verification_outcome(violations: list[Any], findings: list[dict[str, Any]]) -> str:
    if violations:
        return "invalid"
    statuses = {item["status"] for item in findings}
    if "conflict" in statuses:
        return "conflict"
    if statuses & {"mismatched", "damaged"}:
        return "mismatched"
    if statuses & {"missing", "not_verified", "incomplete"} or not findings:
        return "not_verified"
    return "matched"


def verify_artifact(artifact: Any, companions: Any = ()) -> dict[str, Any]:
    """Compose the authoritative verifier for one artifact with explicit companions."""
    companion_values = list(companions) if isinstance(companions, (tuple, list)) else companions
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "verify", "artifact": artifact, "companions": companion_values}
    validate = _contract()
    request_violations = validate.validate_verification_query_message(request)
    if request_violations:
        input_id = artifact.get("input_id") if isinstance(artifact, dict) else None
        return _invalid_request(request_violations, input_id, "verify")
    work_budget = _ZipWorkBudget()
    inspected = _inspect_artifact(artifact, work_budget)
    companions = companion_values
    scope_ids = [artifact["input_id"], *[item["input_id"] for item in companions]]
    subject = inspected["subject"]
    if inspected["outcome"] != "valid":
        return _checked_result({"interface_version": "1.0.0", "message_type": "result", "operation": "verify", "outcome": "invalid", "scope": _scope(scope_ids, "verification"), "subject": subject, "violations": inspected["violations"], "findings": inspected["findings"], "matches": [], "records": []})

    pools: dict[str, list[Any]] = {}
    binary_pools: dict[str, list[bytes]] = {}
    companion_findings: list[dict[str, Any]] = []
    for item in companions:
        companion_result = _inspect_artifact(item, work_budget)
        if companion_result["outcome"] != "valid":
            companion_findings.append({"path": f"$.companions.{item['input_id']}", "status": "not_verified", "reason": "the supplied companion did not satisfy its own Motus contract"})
            continue
        if item["media_type"] == "application/json":
            pools.setdefault(item["kind"], []).append(item["document"])
        else:
            binary_pools.setdefault(item["kind"], []).append(base64.b64decode(item["content_base64"], validate=True))
    kind = artifact["kind"]
    document = artifact.get("document")
    violations: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = companion_findings

    def one(name: str) -> Any:
        values = pools.get(name, [])
        if len(values) > 1:
            findings.append({"path": f"$.companions.{name}", "status": "conflict", "observed": len(values), "reason": "multiple supplied companions are ambiguous"})
            return None
        return values[0] if values else None

    verdict = None
    if kind == "trace":
        spec = one("graphspec")
        if spec is None:
            findings.append({"path": "$.companions.graphspec", "status": "not_verified", "reason": "trace binding requires one explicit GraphSpec companion"})
        else:
            violations = [_violation(item) for item in validate.validate_trace(document, spec=spec)]
            if not violations:
                findings.append({"path": "$.artifact", "status": "matched", "reason": "trace matched the supplied GraphSpec under existing SB rules"})
    elif kind == "execution_receipt":
        verdict = validate.verify(document, one("trace"))
    elif kind == "system_manifest":
        graph = importlib.import_module("vitruvyan_motus.graph").GraphSpec
        trace = importlib.import_module("vitruvyan_motus.trace").Trace
        module = importlib.import_module("vitruvyan_motus.system_manifest")
        verdict = module.verify_system_manifest_bindings(document, graph_specs=[graph.from_dict(value) for value in pools.get("graphspec", ())], traces=[trace.from_dict(value) for value in pools.get("trace", ())])
    elif kind == "control_application":
        registry = one("risk_control_registry")
        if registry is None:
            findings.append({"path": "$.companions.risk_control_registry", "status": "not_verified", "reason": "ControlApplication binding requires one explicit Registry companion"})
        else:
            verdict = importlib.import_module("vitruvyan_motus.risk_control").verify_control_application_bindings(document, registry=registry, manifest=one("system_manifest"), receipt=one("execution_receipt"))
    elif kind == "human_oversight_receipt":
        verdict = importlib.import_module("vitruvyan_motus.human_oversight").verify_human_oversight_bindings(document, execution_receipt=one("execution_receipt"), manifest=one("system_manifest"), registry=one("risk_control_registry"), control_application=one("control_application"))
    elif kind == "regulatory_evidence_profile":
        graph = importlib.import_module("vitruvyan_motus.graph").GraphSpec
        trace = importlib.import_module("vitruvyan_motus.trace").Trace
        verdict = importlib.import_module("vitruvyan_motus.regulatory_profile").assess_evidence_profile(document, execution_receipt=one("execution_receipt"), system_manifest=one("system_manifest"), risk_control_registry=one("risk_control_registry"), control_application=one("control_application"), human_oversight_receipt=one("human_oversight_receipt"), graph_specs=[graph.from_dict(value) for value in pools.get("graphspec", ())], traces=[trace.from_dict(value) for value in pools.get("trace", ())])
    elif kind == "incident_capa_ledger":
        verdict = importlib.import_module("vitruvyan_motus.incident_capa").verify_incident_capa_ledger(document, execution_receipts=pools.get("execution_receipt", ()), manifests=pools.get("system_manifest", ()), registries=pools.get("risk_control_registry", ()), control_applications=pools.get("control_application", ()), human_oversight_receipts=pools.get("human_oversight_receipt", ()), evidence_packages=binary_pools.get("execution_evidence_package", ()))
    elif kind == "retention_application":
        verdict = importlib.import_module("vitruvyan_motus.retention").verify_retention_application_bindings(document, policy=one("retention_policy_declaration"), holds=pools.get("legal_hold_declaration", ()), snapshots=pools.get("retention_scope_snapshot", ()))
    elif kind == "ai_system_registration":
        verdict = importlib.import_module("vitruvyan_motus.ai_system_registry").verify_ai_system_registration_binding(document, manifests=pools.get("system_manifest", ()))
    elif kind == "ai_system_registry_snapshot":
        verdict = importlib.import_module("vitruvyan_motus.ai_system_registry").verify_ai_system_registry_snapshot(document, registrations=pools.get("ai_system_registration", ()), events=pools.get("ai_system_registry_event", ()))
    elif kind == "execution_evidence_package":
        package = importlib.import_module("vitruvyan_motus.evidence").verify_package(base64.b64decode(artifact["content_base64"], validate=True))
        if package.verdict is None:
            findings.append({"path": "$.artifact", "status": "not_verified", "reason": "package carries no receipt verdict"})
        else:
            verdict = package.verdict
        findings.extend({"path": "$.members", "status": "damaged", "observed": item, "reason": "package member failed transport integrity"} for item in package.damaged)
        findings.extend({"path": "$.trace", "status": "mismatched", "observed": item, "reason": "embedded trace failed its Motus contract"} for item in package.trace_violations)
        if not package.transport_ok and not package.damaged:
            findings.append({"path": "$.transport", "status": "mismatched", "reason": "package transport verification failed"})
    elif kind == "regulatory_evidence_dossier_export":
        verdict = importlib.import_module("vitruvyan_motus.regulatory_dossier").verify_regulatory_dossier(base64.b64decode(artifact["content_base64"], validate=True))
    else:
        findings.append({"path": "$.artifact", "status": "not_verified", "reason": "this artifact kind has structural inspection but no standalone binding verifier"})

    if verdict is not None:
        found_violations, found_findings = _verdict_parts(verdict)
        violations.extend(found_violations)
        findings.extend(found_findings)
        if kind == "regulatory_evidence_dossier_export":
            findings.extend(_finding_value(item, index) for index, item in enumerate(verdict.binding_findings))
            findings.extend({"path": f"$.entries[{item.entry_id}]", "status": item.status, "observed": list(item.violations), "reason": "dossier member did not verify as declared"} for item in verdict.entries if item.status != "matched")
            if verdict.profile_assessment is not None:
                findings.extend({"path": f"requirement:{item.requirement_ref}:{item.kind}", "status": item.status, "reason": item.reason} for item in verdict.profile_assessment.findings)
    outcome = _verification_outcome(violations, findings)
    return _checked_result({"interface_version": "1.0.0", "message_type": "result", "operation": "verify", "outcome": outcome, "scope": _scope(scope_ids, "verification"), "subject": subject, "violations": violations, "findings": findings, "matches": [], "records": []})


def execute_verification_query(request: Any) -> dict[str, Any]:
    """Execute a validated v1 request; verify dispatch is added separately."""
    validate = _contract()
    violations = validate.validate_verification_query_message(request)
    if violations:
        operation = request.get("operation") if isinstance(request, dict) else None
        if operation not in ("inspect", "verify", "query"):
            raise ValueError("request has no executable v1 operation")
        return _invalid_request(violations, None, operation)
    if request["operation"] == "inspect":
        return inspect_artifact(request["artifact"])
    if request["operation"] == "query":
        return query_artifacts(request["projection"], request["artifacts"])
    return verify_artifact(request["artifact"], request["companions"])
