"""ADR-042 bounded, deterministic Regulatory Evidence Dossier exports.

The dossier manifest identifies semantic Motus artifacts.  The ZIP export is a
separate transport object and preserves every supplied member byte-for-byte.
Verification is local and caller-supplied; no result is a compliance verdict.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import stat
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation
    from vitruvyan_motus.regulatory_profile import RegulatoryEvidenceAssessment

__all__ = [
    "RegulatoryDossierFinding", "RegulatoryDossierEntryVerdict",
    "RegulatoryDossierVerdict", "RegulatoryDossierLineageVerdict",
    "pack_regulatory_dossier", "verify_regulatory_dossier",
    "verify_regulatory_dossier_lineage",
    "regulatory_dossier_export_fingerprint",
]

_MANIFEST_MEMBER = "dossier.json"
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_SHA256_PREFIX = "sha256:"
_MEMBER_MAX_BYTES = 28 * 1024 * 1024
_TOTAL_MAX_BYTES = 128 * 1024 * 1024
_ARCHIVE_MAX_BYTES = 160 * 1024 * 1024
_MAX_ENTRIES = 1000
_JSON_KINDS = {
    "system_manifest": "system_manifest",
    "risk_control_registry": "risk_control_registry",
    "control_application": "control_application",
    "human_oversight_receipt": "human_oversight_receipt",
    "regulatory_evidence_profile": "regulatory_evidence_profile",
    "incident_declaration": "incident_declaration",
    "capa_action": "capa_action",
    "incident_capa_ledger": "incident_capa_ledger",
    "retention_policy_declaration": "retention_policy_declaration",
    "legal_hold_declaration": "legal_hold_declaration",
    "retention_scope_snapshot": "retention_scope_snapshot",
    "retention_trigger_occurrence": "retention_trigger_occurrence",
    "retention_application": "retention_application",
    "custody_observation": "custody_observation",
    "ai_system_registration": "ai_system_registration",
    "ai_system_registry_event": "ai_system_registry_event",
    "ai_system_registry_snapshot": "ai_system_registry_snapshot",
}
_PROFILE_KINDS = (
    "execution_receipt", "system_manifest", "risk_control_registry",
    "control_application", "human_oversight_receipt",
)
Status = Literal["matched", "mismatched", "missing", "not_verified", "conflict"]


@dataclass(frozen=True, slots=True)
class RegulatoryDossierFinding:
    path: str
    status: Status
    expected: Any
    observed: Any
    reason: str


@dataclass(frozen=True, slots=True)
class RegulatoryDossierEntryVerdict:
    entry_id: str
    artifact_kind: str
    path: str
    status: Status
    violations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RegulatoryDossierVerdict:
    dossier_fingerprint: str | None
    export_fingerprint: str
    manifest_violations: tuple["Violation", ...]
    findings: tuple[RegulatoryDossierFinding, ...]
    entries: tuple[RegulatoryDossierEntryVerdict, ...]
    binding_findings: tuple[RegulatoryDossierFinding, ...] = ()
    profile_assessment: "RegulatoryEvidenceAssessment | None" = None

    @property
    def transport_ok(self) -> bool:
        return (
            not self.manifest_violations
            and all(item.status == "matched" for item in self.entries)
            and not any(item.status != "matched" for item in self.findings)
        )


@dataclass(frozen=True, slots=True)
class RegulatoryDossierLineageVerdict:
    ordered_fingerprints: tuple[str, ...]
    violations: tuple["Violation", ...]
    findings: tuple[RegulatoryDossierFinding, ...]


def _validate_module():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _sha256(data: bytes) -> str:
    return _SHA256_PREFIX + hashlib.sha256(data).hexdigest()


def regulatory_dossier_export_fingerprint(data: bytes) -> str:
    """Fingerprint the exact ZIP transport bytes, independently of the dossier."""
    if not isinstance(data, bytes):
        raise TypeError("dossier export data must be bytes")
    return _sha256(data)


def _safe_path(name: object) -> bool:
    if not isinstance(name, str) or not name or name.startswith(("/", "\\")):
        return False
    if len(name) >= 2 and name[0].isalpha() and name[1] == ":":
        return False
    parts = name.split("/")
    return (
        "\\" not in name and len(parts) <= 16
        and all(part not in ("", ".", "..") for part in parts)
        and name != _MANIFEST_MEMBER
    )


def _canonical_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    validate = _validate_module()
    return json.loads(validate.canonical_json(document).decode("utf-8"))


def pack_regulatory_dossier(
    manifest: dict[str, Any], members: Mapping[str, bytes],
) -> bytes:
    """Create a deterministic ZIP after validating every declared exact byte set."""
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict")
    if not isinstance(members, Mapping):
        raise TypeError("members must be a mapping")
    if any(not isinstance(path, str) for path in members):
        raise TypeError("dossier member paths must be strings")
    validate = _validate_module()
    violations = validate.validate_regulatory_evidence_dossier(manifest)
    if violations:
        detail = "; ".join(f"{v.rule} {v.path}: {v.message}" for v in violations[:3])
        raise ValueError("invalid Regulatory Evidence Dossier manifest: " + detail)
    snapshot = _canonical_snapshot(manifest)
    declared = {entry["path"]: entry for entry in snapshot["entries"]}
    if set(members) != set(declared):
        missing = sorted(set(declared) - set(members))
        extra = sorted(set(members) - set(declared))
        raise ValueError(f"dossier members do not match manifest; missing={missing!r}, extra={extra!r}")
    total = 0
    payloads: list[tuple[str, bytes]] = []
    for path in sorted(declared):
        if not _safe_path(path):
            raise ValueError(f"unsafe dossier member path: {path!r}")
        payload = members[path]
        if not isinstance(payload, bytes):
            raise TypeError("dossier member payloads must be bytes")
        entry = declared[path]
        if len(payload) != entry["size_bytes"]:
            raise ValueError(f"dossier member {path!r} size does not match manifest")
        if _sha256(payload) != entry["content_sha256"]:
            raise ValueError(f"dossier member {path!r} digest does not match manifest")
        total += len(payload)
        if total > _TOTAL_MAX_BYTES:
            raise ValueError("dossier members exceed the 128 MiB uncompressed total limit")
        payloads.append((path, payload))
    manifest_bytes = validate.canonical_json(snapshot)
    if len(manifest_bytes) > _MEMBER_MAX_BYTES:
        raise ValueError("dossier manifest exceeds the member size limit")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, payload in [(_MANIFEST_MEMBER, manifest_bytes), *payloads]:
            info = zipfile.ZipInfo(path, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 0
            archive.writestr(info, payload)
    return buffer.getvalue()


def _read_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info) as member:
            while True:
                chunk = member.read(min(1024 * 1024, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("decompressed member exceeds limit")
                chunks.append(chunk)
    except (RuntimeError, NotImplementedError, zipfile.BadZipFile, EOFError,
            OSError, MemoryError, zlib.error) as exc:
        raise ValueError(f"unreadable ZIP member: {type(exc).__name__}") from None
    return b"".join(chunks)


def _finding(path: str, status: Status, expected: Any, observed: Any, reason: str):
    return RegulatoryDossierFinding(path, status, expected, observed, reason)


def _empty_verdict(data: bytes, finding: RegulatoryDossierFinding) -> RegulatoryDossierVerdict:
    return RegulatoryDossierVerdict(
        None, regulatory_dossier_export_fingerprint(data), (), (finding,), (), (), None,
    )


def _verify_json_artifact(
    kind: str, document: dict[str, Any], validate: Any,
) -> tuple[str, tuple[str, ...]]:
    if kind == "execution_receipt":
        verdict = validate.verify(document)
        messages = tuple(
            f"{v.rule} {v.path}: {v.message}" for v in verdict.violations
        )
        if verdict.refused:
            return "not_verified", messages or ("receipt verification refused",)
        if messages:
            return "mismatched", messages
        from vitruvyan_motus._execution_ref import receipt_execution_issue
        issue = receipt_execution_issue(document)
        return ("mismatched", (issue,)) if issue is not None else ("matched", ())
    stem = _JSON_KINDS[kind]
    violations = getattr(validate, "validate_" + stem)(document)
    messages = tuple(f"{v.rule} {v.path}: {v.message}" for v in violations)
    return ("mismatched", messages) if messages else ("matched", ())


def _artifact_fingerprint(kind: str, payload: bytes, document: dict[str, Any] | None, validate: Any) -> str:
    if kind == "execution_evidence_package":
        from vitruvyan_motus.evidence import evidence_package_fingerprint
        return evidence_package_fingerprint(payload)
    if kind == "execution_receipt":
        return validate.receipt_fingerprint(document)
    return getattr(validate, _JSON_KINDS[kind] + "_fingerprint")(document)


def _converted_findings(prefix: str, values: Iterable[Any]) -> list[RegulatoryDossierFinding]:
    converted = []
    for item in values:
        status = item.status if item.status in (
            "matched", "mismatched", "missing", "not_verified", "conflict"
        ) else "not_verified"
        converted.append(_finding(
            prefix + item.path, status, item.expected, item.observed, item.reason,
        ))
    return converted


def _matching_document(
    documents: dict[str, list[dict[str, Any]]], kind: str,
    expected: str | None, validate: Any,
) -> dict[str, Any] | None:
    if expected is None:
        return None
    stem = "receipt" if kind == "execution_receipt" else kind
    fingerprint_fn = getattr(validate, stem + "_fingerprint")
    matches = [value for value in documents.get(kind, ()) if fingerprint_fn(value) == expected]
    return matches[0] if len(matches) == 1 else None


def _compose_existing_verifiers(
    documents: dict[str, list[dict[str, Any]]],
    package_bytes: list[bytes], validate: Any,
) -> tuple[RegulatoryDossierFinding, ...]:
    """Use existing Motus authorities; never reimplement their semantics."""
    findings: list[RegulatoryDossierFinding] = []

    from vitruvyan_motus.risk_control import verify_control_application_bindings
    for application in documents.get("control_application", ()):
        app_fp = validate.control_application_fingerprint(application)
        registry = _matching_document(
            documents, "risk_control_registry", application["registry_fingerprint"], validate,
        )
        if registry is None:
            findings.append(_finding(
                f"binding:control_application:{app_fp}.registry_fingerprint",
                "missing", application["registry_fingerprint"], None,
                "the exact Registry required by the existing binding verifier is absent or ambiguous",
            ))
            continue
        manifest = _matching_document(
            documents, "system_manifest", application.get("manifest_fingerprint"), validate,
        )
        receipt = None
        receipts = documents.get("execution_receipt", ())
        if receipts:
            from vitruvyan_motus._execution_ref import receipt_segment_for_execution_ref
            matching = [value for value in receipts if receipt_segment_for_execution_ref(
                value, application["execution_ref"]
            ) is not None]
            if len(matching) == 1:
                receipt = matching[0]
        verdict = verify_control_application_bindings(
            application, registry=registry, manifest=manifest, receipt=receipt,
        )
        findings.extend(_converted_findings(
            f"binding:control_application:{app_fp}", verdict.findings,
        ))

    from vitruvyan_motus.human_oversight import verify_human_oversight_bindings
    for oversight in documents.get("human_oversight_receipt", ()):
        fp = validate.human_oversight_receipt_fingerprint(oversight)
        bindings = oversight.get("bindings", {})
        receipt = None
        from vitruvyan_motus._execution_ref import receipt_segment_for_execution_ref
        matches = [value for value in documents.get("execution_receipt", ())
                   if receipt_segment_for_execution_ref(value, oversight["execution_ref"]) is not None]
        if len(matches) == 1:
            receipt = matches[0]
        verdict = verify_human_oversight_bindings(
            oversight,
            execution_receipt=receipt,
            manifest=_matching_document(documents, "system_manifest", bindings.get("manifest_fingerprint"), validate),
            registry=_matching_document(documents, "risk_control_registry", bindings.get("registry_fingerprint"), validate),
            control_application=_matching_document(documents, "control_application", bindings.get("control_application_fingerprint"), validate),
        )
        findings.extend(_converted_findings(
            f"binding:human_oversight_receipt:{fp}", verdict.findings,
        ))

    from vitruvyan_motus.incident_capa import verify_incident_capa_ledger
    for ledger in documents.get("incident_capa_ledger", ()):
        fp = validate.incident_capa_ledger_fingerprint(ledger)
        verdict = verify_incident_capa_ledger(
            ledger,
            execution_receipts=documents.get("execution_receipt", ()),
            manifests=documents.get("system_manifest", ()),
            registries=documents.get("risk_control_registry", ()),
            control_applications=documents.get("control_application", ()),
            human_oversight_receipts=documents.get("human_oversight_receipt", ()),
            evidence_packages=package_bytes,
        )
        findings.extend(_converted_findings(
            f"binding:incident_capa_ledger:{fp}", verdict.findings,
        ))

    from vitruvyan_motus.retention import (
        verify_retention_application_bindings, verify_retention_lineage,
    )
    retention_kinds = (
        "retention-policy-declaration", "legal-hold-declaration",
        "retention-scope-snapshot", "retention-trigger-occurrence",
        "retention-application", "custody-observation",
    )
    for hyphen_kind in retention_kinds:
        underscore_kind = hyphen_kind.replace("-", "_")
        values = documents.get(underscore_kind, ())
        if values:
            verdict = verify_retention_lineage(hyphen_kind, values)
            findings.extend(_converted_findings(
                f"lineage:{underscore_kind}", verdict.findings,
            ))
    for application in documents.get("retention_application", ()):
        fp = validate.retention_application_fingerprint(application)
        verdict = verify_retention_application_bindings(
            application,
            policy=_matching_document(
                documents, "retention_policy_declaration",
                application["policy_fingerprint"], validate,
            ),
            holds=documents.get("legal_hold_declaration", ()),
            snapshots=documents.get("retention_scope_snapshot", ()),
        )
        findings.extend(_converted_findings(
            f"binding:retention_application:{fp}", verdict.findings,
        ))

    from vitruvyan_motus.ai_system_registry import (
        project_supplied_ai_system_lifecycle,
        verify_ai_system_registration_binding, verify_ai_system_registry_lineage,
        verify_ai_system_registry_snapshot,
    )
    for underscore_kind in (
        "ai_system_registration", "ai_system_registry_event", "ai_system_registry_snapshot",
    ):
        values = documents.get(underscore_kind, ())
        if values:
            verdict = verify_ai_system_registry_lineage(underscore_kind.replace("_", "-"), values)
            findings.extend(_converted_findings(
                f"lineage:{underscore_kind}", verdict.findings,
            ))
    for registration in documents.get("ai_system_registration", ()):
        fp = validate.ai_system_registration_fingerprint(registration)
        verdict = verify_ai_system_registration_binding(
            registration, manifests=documents.get("system_manifest", ()),
        )
        findings.extend(_converted_findings(
            f"binding:ai_system_registration:{fp}", verdict.findings,
        ))
    registration_ids = sorted({
        value["registration_id"]
        for value in documents.get("ai_system_registration", ())
    })
    for registration_id in registration_ids:
        projection = project_supplied_ai_system_lifecycle(
            registration_id,
            registrations=documents.get("ai_system_registration", ()),
            events=documents.get("ai_system_registry_event", ()),
        )
        findings.extend(_converted_findings(
            f"projection:ai_system_registry:{registration_id}", projection.findings,
        ))
    for snapshot in documents.get("ai_system_registry_snapshot", ()):
        fp = validate.ai_system_registry_snapshot_fingerprint(snapshot)
        verdict = verify_ai_system_registry_snapshot(
            snapshot,
            registrations=documents.get("ai_system_registration", ()),
            events=documents.get("ai_system_registry_event", ()),
        )
        findings.extend(_converted_findings(
            f"binding:ai_system_registry_snapshot:{fp}", verdict.findings,
        ))
    return tuple(sorted(findings, key=lambda item: (
        item.path, item.status, str(item.expected), str(item.observed), item.reason,
    )))


def verify_regulatory_dossier(data: bytes) -> RegulatoryDossierVerdict:
    """Verify transport, exact bytes, Motus contracts, and profile assessment."""
    if not isinstance(data, bytes):
        raise TypeError("dossier export data must be bytes")
    export_fp = regulatory_dossier_export_fingerprint(data)
    if len(data) > _ARCHIVE_MAX_BYTES:
        return _empty_verdict(data, _finding(
            "$", "mismatched", _ARCHIVE_MAX_BYTES, len(data),
            "archive exceeds the compressed transport size limit",
        ))
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return _empty_verdict(data, _finding("$", "mismatched", "ZIP", None, "export is not a ZIP archive"))
    findings: list[RegulatoryDossierFinding] = []
    entry_verdicts: list[RegulatoryDossierEntryVerdict] = []
    try:
        infos = archive.infolist()
        if len(infos) > _MAX_ENTRIES + 1:
            return _empty_verdict(data, _finding("$", "mismatched", _MAX_ENTRIES + 1, len(infos), "too many ZIP members"))
        names = [info.filename for info in infos]
        duplicate = sorted(name for name, count in Counter(names).items() if count > 1)
        if duplicate:
            return _empty_verdict(data, _finding("$", "conflict", "unique member names", duplicate, "duplicate physical ZIP member names"))
        unsafe = sorted(name for name in names if name != _MANIFEST_MEMBER and not _safe_path(name))
        if unsafe:
            return _empty_verdict(data, _finding("$", "mismatched", "safe relative POSIX paths", unsafe, "unsafe ZIP member name"))
        if _MANIFEST_MEMBER not in names:
            return _empty_verdict(data, _finding("$", "missing", _MANIFEST_MEMBER, None, "dossier manifest is missing"))
        if any(info.is_dir() for info in infos):
            return _empty_verdict(data, _finding("$", "mismatched", "file members", "directory", "directory entries are not permitted"))
        if any(info.flag_bits & 0x1 for info in infos):
            return _empty_verdict(data, _finding("$", "not_verified", "unencrypted ZIP", "encrypted member", "encrypted members cannot be verified"))
        if any(info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for info in infos):
            return _empty_verdict(data, _finding("$", "not_verified", "stored or deflated", "unsupported compression", "unsupported compression method"))
        if any(info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16) for info in infos):
            return _empty_verdict(data, _finding("$", "mismatched", "regular files", "symbolic link", "symbolic links are not permitted"))
        if any(info.file_size > _MEMBER_MAX_BYTES for info in infos):
            return _empty_verdict(data, _finding("$", "mismatched", _MEMBER_MAX_BYTES, "oversize member", "member exceeds decompressed size limit"))
        artifact_total = sum(
            info.file_size for info in infos if info.filename != _MANIFEST_MEMBER
        )
        if artifact_total > _TOTAL_MAX_BYTES:
            return _empty_verdict(data, _finding("$", "mismatched", _TOTAL_MAX_BYTES, artifact_total, "artifact members exceed uncompressed total limit"))
        info_by_name = {info.filename: info for info in infos}
        try:
            manifest_bytes = _read_bounded(archive, info_by_name[_MANIFEST_MEMBER], _MEMBER_MAX_BYTES)
            validate = _validate_module()
            manifest = validate._loads_strict(manifest_bytes.decode("utf-8"), governed_as="regulatory-evidence-dossier")
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            return _empty_verdict(data, _finding("$.dossier", "not_verified", "strict JSON object", type(exc).__name__, "dossier manifest is not readable strict JSON"))
        if not isinstance(manifest, dict):
            return _empty_verdict(data, _finding("$.dossier", "mismatched", "object", type(manifest).__name__, "dossier manifest is not an object"))
        manifest_violations = tuple(validate.validate_regulatory_evidence_dossier(manifest))
        if manifest_violations:
            return RegulatoryDossierVerdict(
                None, export_fp, manifest_violations, (), (), (), None,
            )
        snapshot = _canonical_snapshot(manifest)
        dossier_fp = validate.regulatory_evidence_dossier_fingerprint(snapshot)
        declared = {entry["path"]: entry for entry in snapshot["entries"]}
        actual = set(names) - {_MANIFEST_MEMBER}
        for missing in sorted(set(declared) - actual):
            findings.append(_finding(f"$.entries[{missing}]", "missing", missing, None, "declared member is absent"))
        for extra in sorted(actual - set(declared)):
            findings.append(_finding(f"$.members[{extra}]", "mismatched", None, extra, "undeclared member is present"))

        documents: dict[str, list[dict[str, Any]]] = {}
        package_bytes: list[bytes] = []
        for entry in snapshot["entries"]:
            path = entry["path"]
            if path not in actual:
                continue
            kind = entry["artifact_kind"]
            try:
                payload = _read_bounded(archive, info_by_name[path], _MEMBER_MAX_BYTES)
            except ValueError as exc:
                entry_verdicts.append(RegulatoryDossierEntryVerdict(entry["entry_id"], kind, path, "not_verified", (str(exc),)))
                continue
            problems: list[str] = []
            if len(payload) != entry["size_bytes"]:
                problems.append("size_bytes does not match exact member bytes")
            raw_digest = _sha256(payload)
            if raw_digest != entry["content_sha256"]:
                problems.append("content_sha256 does not match exact member bytes")
            document: dict[str, Any] | None = None
            artifact_status: Status = "matched"
            if kind == "execution_evidence_package":
                from vitruvyan_motus.evidence import verify_package
                package = verify_package(payload)
                if not package.transport_ok or package.trace_violations or package.verdict is None:
                    artifact_status = "not_verified"
                    problems.append("nested evidence package did not verify completely")
                elif package.verdict.refused or package.verdict.violations:
                    artifact_status = "mismatched"
                    problems.append("nested evidence package receipt did not verify")
            else:
                try:
                    parsed = validate._loads_strict(payload.decode("utf-8"), governed_as=kind.replace("_", "-"))
                    if not isinstance(parsed, dict):
                        raise ValueError("JSON artifact is not an object")
                    document = parsed
                    artifact_status, violations = _verify_json_artifact(kind, document, validate)
                    problems.extend(violations)
                except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                    artifact_status = "not_verified"
                    problems.append("artifact is not readable strict JSON: " + type(exc).__name__)
            if document is not None or kind == "execution_evidence_package":
                try:
                    observed_fp = _artifact_fingerprint(kind, payload, document, validate)
                    if observed_fp != entry["artifact_fingerprint"]:
                        artifact_status = "mismatched"
                        problems.append("artifact_fingerprint does not match recomputed identity")
                except (TypeError, ValueError, KeyError, RecursionError) as exc:
                    artifact_status = "not_verified"
                    problems.append("artifact fingerprint could not be recomputed: " + type(exc).__name__)
            if problems and artifact_status == "matched":
                artifact_status = "mismatched"
            if artifact_status == "matched" and not problems and document is not None:
                documents.setdefault(kind, []).append(document)
            if artifact_status == "matched" and not problems and kind == "execution_evidence_package":
                package_bytes.append(payload)
            entry_verdicts.append(RegulatoryDossierEntryVerdict(entry["entry_id"], kind, path, artifact_status, tuple(problems)))

        assessment = None
        profile_docs = documents.get("regulatory_evidence_profile", [])
        requested_kinds = {
            expectation["kind"]
            for requirement in profile_docs[0]["requirements"]
            for expectation in requirement["evidence"]
        } if len(profile_docs) == 1 else set()
        ambiguous = [
            kind for kind in _PROFILE_KINDS
            if kind in requested_kinds and len(documents.get(kind, [])) > 1
        ]
        if ambiguous:
            findings.append(_finding("$.profile_assessment", "conflict", "at most one candidate per profiled kind", ambiguous, "profile expectations cannot select among multiple candidates"))
        elif len(profile_docs) == 1:
            from vitruvyan_motus.regulatory_profile import assess_evidence_profile
            kwargs = {kind: (documents.get(kind) or [None])[0] for kind in _PROFILE_KINDS}
            assessment = assess_evidence_profile(profile_docs[0], **kwargs)
        binding_findings = _compose_existing_verifiers(documents, package_bytes, validate)
        return RegulatoryDossierVerdict(
            dossier_fp, export_fp, (), tuple(findings), tuple(entry_verdicts),
            binding_findings, assessment,
        )
    finally:
        archive.close()


def verify_regulatory_dossier_lineage(
    dossiers: Iterable[dict[str, Any]],
) -> RegulatoryDossierLineageVerdict:
    """Verify caller-supplied correction lineage without resolving hidden state."""
    if isinstance(dossiers, (str, bytes, dict)):
        raise TypeError("dossiers must be an iterable of dicts")
    validate = _validate_module()
    supplied = tuple(dossiers)
    valid: dict[str, dict[str, Any]] = {}
    invalid_fps: set[str] = set()
    violations: list["Violation"] = []
    findings: list[RegulatoryDossierFinding] = []
    counts: dict[str, int] = {}
    for index, document in enumerate(supplied):
        if not isinstance(document, dict):
            findings.append(_finding(f"$[{index}]", "not_verified", "object", type(document).__name__, "lineage item is not an object"))
            continue
        try:
            item_violations = tuple(
                validate.validate_regulatory_evidence_dossier(document)
            )
        except (TypeError, ValueError, RecursionError) as exc:
            findings.append(_finding(
                f"$[{index}]", "not_verified", "canonical dossier object",
                type(exc).__name__,
                "the supplied dossier could not be structurally evaluated",
            ))
            continue
        try:
            fp = validate.regulatory_evidence_dossier_fingerprint(document)
        except (TypeError, ValueError, RecursionError) as exc:
            violations.extend(item_violations)
            findings.append(_finding(
                f"$[{index}]", "not_verified", "canonical dossier object",
                type(exc).__name__,
                "the supplied dossier has no canonical lineage identity",
            ))
            continue
        if item_violations:
            violations.extend(item_violations)
            invalid_fps.add(fp)
            continue
        counts[fp] = counts.get(fp, 0) + 1
        valid[fp] = _canonical_snapshot(document)
    for fp, count in sorted(counts.items()):
        if count > 1:
            findings.append(_finding(f"lineage:{fp}", "conflict", "one exact dossier", count, "duplicate exact dossier supplied"))
    predecessors: dict[str, str] = {}
    roots: dict[tuple[str, str], list[str]] = {}
    for fp, document in valid.items():
        stable = (document["producer_namespace"], document["dossier_id"])
        predecessor = document.get("supersedes")
        if predecessor is None:
            roots.setdefault(stable, []).append(fp)
            continue
        predecessors[fp] = predecessor
        if predecessor == fp:
            findings.append(_finding(f"lineage:{fp}.supersedes", "conflict", "different predecessor", fp, "dossier supersedes itself"))
        elif predecessor in invalid_fps:
            findings.append(_finding(f"lineage:{fp}.supersedes", "not_verified", predecessor, predecessor, "exact predecessor is supplied but contract-invalid"))
        elif predecessor not in valid:
            findings.append(_finding(f"lineage:{fp}.supersedes", "missing", predecessor, None, "predecessor was not supplied"))
        else:
            parent = valid[predecessor]
            parent_stable = (parent["producer_namespace"], parent["dossier_id"])
            if parent_stable != stable:
                findings.append(_finding(f"lineage:{fp}.supersedes", "mismatched", stable, parent_stable, "correction crosses stable dossier identity"))
    for stable, values in sorted(roots.items()):
        if len(values) > 1:
            findings.append(_finding(f"lineage:{stable[0]}/{stable[1]}", "conflict", "one root", tuple(sorted(values)), "multiple roots claim one stable identity"))
    successors: dict[str, list[str]] = {}
    for child, parent in predecessors.items():
        if parent in valid:
            successors.setdefault(parent, []).append(child)
    for parent, children in sorted(successors.items()):
        if len(children) > 1:
            findings.append(_finding(f"lineage:{parent}", "conflict", "one successor", tuple(sorted(children)), "correction lineage forks"))
    for start in sorted(valid):
        seen: set[str] = set()
        cursor = start
        while cursor in predecessors and predecessors[cursor] in valid:
            if cursor in seen:
                findings.append(_finding(f"lineage:{start}", "conflict", "acyclic lineage", cursor, "correction lineage contains a cycle"))
                break
            seen.add(cursor)
            cursor = predecessors[cursor]
    ordered: list[str] = []
    remaining = set(valid)
    while remaining:
        ready = sorted(fp for fp in remaining if predecessors.get(fp) not in remaining)
        if not ready:
            ready = [min(remaining)]
        for fp in ready:
            ordered.append(fp)
            remaining.remove(fp)
    return RegulatoryDossierLineageVerdict(tuple(ordered), tuple(violations), tuple(findings))
