"""ADR-043 transport-neutral inspection over existing Motus contracts.

This module is deliberately storage-free and imports the contract validator
lazily. Inspection establishes local structure and exact identity where Motus
already defines one. It is not binding verification, discovery, or compliance.
"""
from __future__ import annotations

import base64
import importlib
import io
import json
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
_DIAGNOSTIC_COLLECTION_LIMIT = 32
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
    bounded["violations"] = [
        {
            **item,
            "rule": str(item["rule"])[:1024],
            "path": str(item["path"])[:8192],
            "message": str(item["message"])[:8192],
        }
        for item in bounded["violations"]
    ]
    bounded["findings"] = [
        {
            **item,
            "path": str(item["path"])[:8192],
            "reason": str(item["reason"])[:8192],
            **({"expected": _bounded_json_strings(item["expected"])} if "expected" in item else {}),
            **({"observed": _bounded_json_strings(item["observed"])} if "observed" in item else {}),
        }
        for item in bounded["findings"]
    ]
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
    if markers and bounded["outcome"] not in {"invalid", "invalid_request"}:
        bounded["outcome"] = {
            "inspect": "invalid", "verify": "not_verified", "query": "invalid",
        }[bounded["operation"]]
    return bounded


def _bounded_json_strings(value: Any) -> Any:
    """Bound imported diagnostic strings without changing result structure."""
    if isinstance(value, str):
        return value[:8192]
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        bounded = [
            _bounded_json_strings(item)
            for item in value[:_DIAGNOSTIC_COLLECTION_LIMIT]
        ]
        if len(value) > _DIAGNOSTIC_COLLECTION_LIMIT:
            bounded.append({
                "truncated_items": len(value) - _DIAGNOSTIC_COLLECTION_LIMIT,
            })
        return bounded
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        bounded = {
            str(key)[:8192]: _bounded_json_strings(item)
            for key, item in items[:_DIAGNOSTIC_COLLECTION_LIMIT]
        }
        if len(items) > _DIAGNOSTIC_COLLECTION_LIMIT:
            bounded["truncated_items"] = (
                len(items) - _DIAGNOSTIC_COLLECTION_LIMIT
            )
        return bounded
    return value


def _invalid_request(
    violations: list[Any], input_ids: object, operation: str = "inspect",
) -> dict[str, Any]:
    candidates = input_ids if isinstance(input_ids, (list, tuple)) else [input_ids]
    ids: list[str] = []
    for value in candidates:
        if (isinstance(value, str) and 1 <= len(value) <= 128
                and any(not char.isspace() for char in value)
                and value not in ids):
            ids.append(value)
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


def _manifest_trace_companion_inspection(
    validate: Any, input_id: str, document: Any,
) -> dict[str, Any]:
    """Inspect a trace under the incomplete-trace semantics manifest binding owns."""
    violations = list(validate.validate_trace(document, expect_complete=False))
    return {
        "interface_version": "1.0.0", "message_type": "result",
        "operation": "inspect", "outcome": "invalid" if violations else "valid",
        "scope": _scope([input_id]),
        "subject": {"input_id": input_id, "kind": "trace", "fingerprint": None},
        "violations": [_violation(item) for item in violations],
        "findings": [], "matches": [], "records": [],
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
    *, dossier_export: bool = False,
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
    # A dossier may contain the contract's 1,000 evidence entries plus its
    # mandatory dossier.json metadata member.  That metadata is bounded and
    # read below, but is not charged as an evidence-package work item.
    claimed_work_members = max(0, claimed_members - (1 if dossier_export else 0))
    if budget.members + claimed_work_members > _PACKAGE_MEMBER_LIMIT:
        return _zip_limit_finding(
            "request exceeds the cumulative ZIP member-count work limit",
            expected=_PACKAGE_MEMBER_LIMIT,
            observed=budget.members + claimed_work_members,
        )
    if budget.directory_bytes + directory_size > _PACKAGE_DIRECTORY_TOTAL_MAX_BYTES:
        return _zip_limit_finding(
            "request exceeds the cumulative ZIP directory-byte work limit",
            expected=_PACKAGE_DIRECTORY_TOTAL_MAX_BYTES,
            observed=budget.directory_bytes + directory_size,
        )
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
        manifest_infos = [
            info for info in infos if info.filename == "dossier.json"
        ] if dossier_export else []
        actual_work_members = len(infos) - (1 if len(manifest_infos) == 1 else 0)
        if budget.members + actual_work_members > _PACKAGE_MEMBER_LIMIT:
            return _zip_limit_finding(
                "request exceeds the cumulative ZIP member-count work limit",
                expected=_PACKAGE_MEMBER_LIMIT,
                observed=budget.members + actual_work_members,
            )
        budget.members += actual_work_members
        declared_total = sum(info.file_size for info in infos)
        if budget.expanded_bytes + declared_total > _PACKAGE_TOTAL_MAX_BYTES:
            return _zip_limit_finding(
                "request exceeds the cumulative expanded-work limit",
                expected=_PACKAGE_TOTAL_MAX_BYTES,
                observed=budget.expanded_bytes + declared_total,
            )

        nested_member_names: set[str] = set()
        manifest_info = manifest_infos[0] if len(manifest_infos) == 1 else None
        if manifest_info is not None:
            manifest_chunks: list[bytes] = []
            manifest_total = 0
            with archive.open(manifest_info) as member:
                while True:
                    chunk = member.read(min(
                        1024 * 1024,
                        _PACKAGE_MEMBER_MAX_BYTES - manifest_total + 1,
                        _PACKAGE_TOTAL_MAX_BYTES - budget.expanded_bytes + 1,
                    ))
                    if not chunk:
                        break
                    manifest_chunks.append(chunk)
                    manifest_total += len(chunk)
                    budget.expanded_bytes += len(chunk)
                    if manifest_total > _PACKAGE_MEMBER_MAX_BYTES:
                        return _zip_limit_finding(
                            "ZIP member exceeds the expanded-byte limit",
                            expected=_PACKAGE_MEMBER_MAX_BYTES,
                            observed={"member": "dossier.json", "expanded_bytes": manifest_total},
                        )
                    if budget.expanded_bytes > _PACKAGE_TOTAL_MAX_BYTES:
                        return _zip_limit_finding(
                            "request exceeds the cumulative expanded-work limit",
                            expected=_PACKAGE_TOTAL_MAX_BYTES,
                            observed=budget.expanded_bytes,
                        )
            try:
                manifest = json.loads(b"".join(manifest_chunks).decode("utf-8"))
                entries = manifest.get("entries", []) if isinstance(manifest, dict) else []
                nested_member_names = {
                    entry["path"] for entry in entries
                    if isinstance(entry, dict)
                    and entry.get("artifact_kind") == "execution_evidence_package"
                    and isinstance(entry.get("path"), str)
                }
            except (UnicodeDecodeError, ValueError, RecursionError):
                # The authoritative dossier verifier reports malformed JSON.
                # It cannot reach a nested package when the manifest is unreadable.
                nested_member_names = set()

        for info in infos:
            if info is manifest_info:
                continue
            if info.file_size > _PACKAGE_MEMBER_MAX_BYTES:
                return _zip_limit_finding(
                    "ZIP member exceeds the expanded-byte limit",
                    expected=_PACKAGE_MEMBER_MAX_BYTES,
                    observed={"member": _bounded_member_name(info.filename), "expanded_bytes": info.file_size},
                )
            member_total = 0
            nested_chunks: list[bytes] | None = (
                [] if info.filename in nested_member_names else None
            )
            with archive.open(info) as member:
                while True:
                    chunk = member.read(min(
                        1024 * 1024,
                        _PACKAGE_MEMBER_MAX_BYTES - member_total + 1,
                        _PACKAGE_TOTAL_MAX_BYTES - budget.expanded_bytes + 1,
                    ))
                    if not chunk:
                        break
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
    expansion_finding = _package_expansion_finding(
        data, budget, dossier_export=True,
    )
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
            if item.get("kind") == "execution"
        }
    if kind == "retention_policy_declaration":
        selector = document.get("scope")
        if isinstance(selector, dict) and selector.get("kind") == "execution_refs":
            return set(selector["execution_refs"])
    return set()


def _lineage_cycle_members(
    predecessor_of: dict[str, str], unique_fingerprints: set[str],
) -> set[str]:
    """Find cycle members with each unambiguous lineage node visited once."""
    cycle_members: set[str] = set()
    completed: set[str] = set()
    for start in sorted(predecessor_of):
        if start in completed:
            continue
        cursor = start
        path: list[str] = []
        positions: dict[str, int] = {}
        while cursor in predecessor_of and cursor in unique_fingerprints:
            if cursor in completed:
                break
            if cursor in positions:
                cycle_members.update(path[positions[cursor]:])
                break
            positions[cursor] = len(path)
            path.append(cursor)
            parent = predecessor_of[cursor]
            if parent not in unique_fingerprints:
                break
            cursor = parent
        completed.update(path)
    return cycle_members


def _canonical_json_sort_key(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _authoritative_lineage_findings(
    kind: str, documents: list[dict[str, Any]],
    related_documents: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]] | None:
    """Compose an existing lineage authority when Motus defines one."""
    if kind in {"incident_declaration", "capa_action"}:
        validate = _contract()
        ledger_entries = [
            {"kind": item_kind, "document": document}
            for item_kind, document in (
                related_documents
                if related_documents is not None
                else [(kind, document) for document in documents]
            )
        ]
        indexed: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, entry in enumerate(ledger_entries):
            fingerprint = (
                validate.incident_declaration_fingerprint(entry["document"])
                if entry["kind"] == "incident_declaration"
                else validate.capa_action_fingerprint(entry["document"])
            )
            indexed.setdefault(fingerprint, []).append((index, entry))
        findings = []
        for index, entry in enumerate(ledger_entries):
            if entry["kind"] != kind:
                continue
            document = entry["document"]
            predecessor = document.get("supersedes")
            current = (
                validate.incident_declaration_fingerprint(document)
                if kind == "incident_declaration"
                else validate.capa_action_fingerprint(document)
            )
            targets = indexed.get(predecessor, ())
            if predecessor is None:
                continue
            if predecessor != current and targets:
                target = targets[0][1]
                stable_field = (
                    "incident_id" if kind == "incident_declaration"
                    else "action_id"
                )
                if (target["kind"] == kind
                        and target["document"]["producer_namespace"]
                        == document["producer_namespace"]
                        and target["document"][stable_field]
                        == document[stable_field]):
                    continue
            entries = [entry]
            if predecessor != current and targets:
                entries.insert(0, targets[0][1])
            ledger = {"schema_version": "1.0.0", "entries": entries}
            for issue in validate.validate_incident_capa_ledger(ledger):
                if issue.rule == "LEDGER2":
                    findings.append({
                        "path": f"$.entries[{index}].document.supersedes",
                        "status": "mismatched",
                        "reason": f"{issue.rule}: {issue.message}",
                    })
        return findings
    if kind == "regulatory_evidence_dossier":
        verdict = importlib.import_module(
            "vitruvyan_motus.regulatory_dossier"
        ).verify_regulatory_dossier_lineage(documents)
    elif kind in {
        "retention_policy_declaration", "legal_hold_declaration",
        "retention_scope_snapshot", "retention_trigger_occurrence",
        "retention_application", "custody_observation",
    }:
        verdict = importlib.import_module(
            "vitruvyan_motus.retention"
        ).verify_retention_lineage(kind.replace("_", "-"), documents)
    elif kind in {"ai_system_registration", "ai_system_registry_event"}:
        verdict = importlib.import_module(
            "vitruvyan_motus.ai_system_registry"
        ).verify_ai_system_registry_lineage(kind.replace("_", "-"), documents)
    else:
        return None
    return [
        _finding_value(item, index)
        for index, item in enumerate(verdict.findings)
    ]


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
        context_fingerprints: dict[str, int] = {}
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
        related_documents = None
        if wanted in {"incident_declaration", "capa_action"}:
            lineage_contract = _contract()
            related_documents = [
                (artifact["kind"], artifact["document"])
                for artifact, _ in valid
                if artifact["kind"] in {"incident_declaration", "capa_action"}
            ]
            for related_kind, document in related_documents:
                fingerprint = (
                    lineage_contract.incident_declaration_fingerprint(document)
                    if related_kind == "incident_declaration"
                    else lineage_contract.capa_action_fingerprint(document)
                )
                context_fingerprints[fingerprint] = (
                    context_fingerprints.get(fingerprint, 0) + 1
                )
        authoritative = _authoritative_lineage_findings(
            wanted, [document for _, _, document in rows], related_documents,
        )
        if authoritative is not None:
            findings.extend(authoritative)
        if authoritative is None or wanted in {"incident_declaration", "capa_action"}:
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
                    context_count = context_fingerprints.get(parent, 0)
                    if context_count > 1:
                        findings.append({"path": f"lineage:{fingerprint}.supersedes", "status": "conflict", "expected": parent, "observed": context_count, "reason": "the cross-kind predecessor identity is ambiguous in the supplied view"})
                        continue
                    if context_count == 1:
                        continue
                    findings.append({"path": f"lineage:{fingerprint}.supersedes", "status": "incomplete", "expected": parent, "reason": "the immediate predecessor is absent from this supplied view"})
                elif len(candidates) > 1:
                    findings.append({"path": f"lineage:{fingerprint}.supersedes", "status": "conflict", "expected": parent, "observed": len(candidates), "reason": "the predecessor identity is ambiguous in the supplied view"})
            for parent, count in sorted(parents.items()):
                if count > 1:
                    findings.append({"path": f"lineage:{parent}", "status": "conflict", "expected": parent, "observed": count, "reason": "multiple supplied revisions name the same predecessor; no winner was selected"})
            cycle_members = _lineage_cycle_members(
                predecessor_of,
                {
                    fingerprint
                    for fingerprint, candidates in by_fingerprint.items()
                    if len(candidates) == 1
                },
            )
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
    records.sort(key=lambda item: (
        item["record_kind"], item["source_input_id"],
        _canonical_json_sort_key(item["record"]),
    ))
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
        return _invalid_request(violations, _request_input_ids(request), "query")
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
        return _invalid_request(
            request_violations, _request_input_ids(request), "verify",
        )
    work_budget = _ZipWorkBudget()
    inspected = _inspect_artifact(artifact, work_budget)
    companions = companion_values
    scope_ids = [artifact["input_id"], *[item["input_id"] for item in companions]]
    subject = inspected["subject"]
    if inspected["outcome"] != "valid":
        return _checked_result({"interface_version": "1.0.0", "message_type": "result", "operation": "verify", "outcome": "invalid", "scope": _scope(scope_ids, "verification"), "subject": subject, "violations": inspected["violations"], "findings": inspected["findings"], "matches": [], "records": []})

    kind = artifact["kind"]
    document = artifact.get("document")
    pools: dict[str, list[Any]] = {}
    binary_pools: dict[str, list[bytes]] = {}
    companion_subjects: dict[str, list[dict[str, Any]]] = {}
    companion_findings: list[dict[str, Any]] = []
    violations: list[dict[str, str]] = []
    for item in companions:
        if (item["kind"] == "trace"
                and kind in {"system_manifest", "regulatory_evidence_profile"}):
            companion_result = _manifest_trace_companion_inspection(
                validate, item["input_id"], item["document"],
            )
        else:
            companion_result = _inspect_artifact(item, work_budget)
        if companion_result["outcome"] != "valid":
            prefix = f"$.companions[{item['input_id']}]"
            violations.extend({
                "rule": detail["rule"],
                "path": (prefix + detail["path"][1:])[:8192],
                "message": detail["message"],
            } for detail in companion_result["violations"])
            companion_findings.extend({
                **detail,
                "path": (prefix + detail["path"][1:])[:8192],
            } for detail in companion_result["findings"])
            if not companion_result["violations"] and not companion_result["findings"]:
                companion_findings.append({
                    "path": prefix, "status": "not_verified",
                    "reason": "the supplied companion did not satisfy its own Motus contract",
                })
            continue
        companion_subjects.setdefault(item["kind"], []).append(
            companion_result["subject"]
        )
        if item["media_type"] == "application/json":
            pools.setdefault(item["kind"], []).append(item["document"])
        else:
            binary_pools.setdefault(item["kind"], []).append(base64.b64decode(item["content_base64"], validate=True))
    findings: list[dict[str, Any]] = companion_findings
    used_companions: dict[str, dict[str, Any]] = {}

    def mark_used(name: str) -> None:
        for identity in companion_subjects.get(name, ()):
            used_companions[identity["input_id"]] = identity

    def one(name: str, *, use: bool = True) -> Any:
        if not use:
            return None
        values = pools.get(name, [])
        if len(values) > 1:
            findings.append({"path": f"$.companions.{name}", "status": "conflict", "observed": len(values), "reason": "multiple supplied companions are ambiguous"})
            return None
        if values:
            mark_used(name)
            return values[0]
        return None

    def many(name: str, *, use: bool = True) -> list[Any]:
        if not use:
            return []
        mark_used(name)
        return pools.get(name, [])

    def many_binary(name: str, *, use: bool = True) -> list[bytes]:
        if not use:
            return []
        mark_used(name)
        return binary_pools.get(name, [])

    def authoritative(call: Any, *args: Any, **kwargs: Any) -> Any:
        """Turn a domain verifier's refusal into a stable public result."""
        try:
            return call(*args, **kwargs)
        except (ValueError, RecursionError) as exc:
            findings.append({
                "path": "$.verification",
                "status": "not_verified",
                "observed": type(exc).__name__,
                "reason": (
                    "authoritative verifier refused the supplied evidence: "
                    + str(exc)
                )[:8192],
            })
            return None

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
        verdict = authoritative(validate.verify, document, one("trace"))
    elif kind == "system_manifest":
        graph = importlib.import_module("vitruvyan_motus.graph").GraphSpec
        trace = importlib.import_module("vitruvyan_motus.trace").Trace
        module = importlib.import_module("vitruvyan_motus.system_manifest")
        verdict = authoritative(module.verify_system_manifest_bindings, document, graph_specs=[graph.from_dict(value) for value in many("graphspec")], traces=[trace.from_dict(value) for value in many("trace")])
    elif kind == "control_application":
        registry = one("risk_control_registry")
        if registry is None:
            findings.append({"path": "$.companions.risk_control_registry", "status": "not_verified", "reason": "ControlApplication binding requires one explicit Registry companion"})
        else:
            verdict = authoritative(importlib.import_module("vitruvyan_motus.risk_control").verify_control_application_bindings, document, registry=registry, manifest=one("system_manifest"), receipt=one("execution_receipt"))
    elif kind == "human_oversight_receipt":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.human_oversight").verify_human_oversight_bindings, document, execution_receipt=one("execution_receipt"), manifest=one("system_manifest"), registry=one("risk_control_registry"), control_application=one("control_application"))
    elif kind == "regulatory_evidence_profile":
        graph = importlib.import_module("vitruvyan_motus.graph").GraphSpec
        trace = importlib.import_module("vitruvyan_motus.trace").Trace
        requested = {
            expectation["kind"]
            for requirement in document["requirements"]
            for expectation in requirement["evidence"]
        }
        used_kinds = set(requested)
        if requested & {"control_application", "human_oversight_receipt"}:
            used_kinds.update({
                "execution_receipt", "system_manifest",
                "risk_control_registry", "control_application",
            })
        uses_manifest = "system_manifest" in requested
        verdict = authoritative(importlib.import_module("vitruvyan_motus.regulatory_profile").assess_evidence_profile, document, execution_receipt=one("execution_receipt", use="execution_receipt" in used_kinds), system_manifest=one("system_manifest", use="system_manifest" in used_kinds), risk_control_registry=one("risk_control_registry", use="risk_control_registry" in used_kinds), control_application=one("control_application", use="control_application" in used_kinds), human_oversight_receipt=one("human_oversight_receipt", use="human_oversight_receipt" in used_kinds), graph_specs=[graph.from_dict(value) for value in many("graphspec", use=uses_manifest)], traces=[trace.from_dict(value) for value in many("trace", use=uses_manifest)])
    elif kind == "incident_capa_ledger":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.incident_capa").verify_incident_capa_ledger, document, execution_receipts=many("execution_receipt"), manifests=many("system_manifest"), registries=many("risk_control_registry"), control_applications=many("control_application"), human_oversight_receipts=many("human_oversight_receipt"), evidence_packages=many_binary("execution_evidence_package"))
    elif kind == "retention_application":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.retention").verify_retention_application_bindings, document, policy=one("retention_policy_declaration"), holds=many("legal_hold_declaration"), snapshots=many("retention_scope_snapshot"))
    elif kind == "ai_system_registration":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.ai_system_registry").verify_ai_system_registration_binding, document, manifests=many("system_manifest"))
    elif kind == "ai_system_registry_snapshot":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.ai_system_registry").verify_ai_system_registry_snapshot, document, registrations=many("ai_system_registration"), events=many("ai_system_registry_event"))
    elif kind == "execution_evidence_package":
        package = authoritative(importlib.import_module("vitruvyan_motus.evidence").verify_package, base64.b64decode(artifact["content_base64"], validate=True))
        if package is not None:
            if package.verdict is None:
                findings.append({"path": "$.artifact", "status": "not_verified", "reason": "package carries no receipt verdict"})
            else:
                verdict = package.verdict
            findings.extend({"path": "$.members", "status": "damaged", "observed": item, "reason": "package member failed transport integrity"} for item in package.damaged)
            findings.extend({"path": "$.trace", "status": "mismatched", "observed": item, "reason": "embedded trace failed its Motus contract"} for item in package.trace_violations)
            if not package.transport_ok and not package.damaged:
                findings.append({"path": "$.transport", "status": "mismatched", "reason": "package transport verification failed"})
    elif kind == "regulatory_evidence_dossier_export":
        verdict = authoritative(importlib.import_module("vitruvyan_motus.regulatory_dossier").verify_regulatory_dossier, base64.b64decode(artifact["content_base64"], validate=True))
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
    matches = sorted(
        used_companions.values(),
        key=lambda item: (item["kind"], item["input_id"], item["fingerprint"] or ""),
    )
    return _checked_result({"interface_version": "1.0.0", "message_type": "result", "operation": "verify", "outcome": outcome, "scope": _scope(scope_ids, "verification"), "subject": subject, "violations": violations, "findings": findings, "matches": matches, "records": []})


def _request_input_ids(request: Any) -> list[object]:
    if not isinstance(request, dict):
        return []
    values: list[Any] = []
    artifact = request.get("artifact")
    if isinstance(artifact, dict):
        values.append(artifact)
    companions = request.get("companions")
    if isinstance(companions, list):
        values.extend(item for item in companions if isinstance(item, dict))
    artifacts = request.get("artifacts")
    if isinstance(artifacts, list):
        values.extend(item for item in artifacts if isinstance(item, dict))
    return [item.get("input_id") for item in values]


def execute_verification_query(request: Any) -> dict[str, Any]:
    """Execute a validated v1 request; verify dispatch is added separately."""
    validate = _contract()
    violations = validate.validate_verification_query_message(request)
    if violations:
        operation = request.get("operation") if isinstance(request, dict) else None
        if operation not in ("inspect", "verify", "query"):
            raise ValueError("request has no executable v1 operation")
        return _invalid_request(violations, _request_input_ids(request), operation)
    if request["operation"] == "inspect":
        return inspect_artifact(request["artifact"])
    if request["operation"] == "query":
        return query_artifacts(request["projection"], request["artifacts"])
    return verify_artifact(request["artifact"], request["companions"])
