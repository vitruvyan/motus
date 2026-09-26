"""ADR-039 Incident / CAPA Ledger verification.

The canonical records are producer claims. This module verifies exact identity,
append-only lineage, incident/action linkage, and references to supplied Motus
evidence. It never decides blame, reportability, cause, effectiveness, closure,
or compliance.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal

from vitruvyan_motus._execution_ref import (
    receipt_execution_issue,
)

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = [
    "IncidentCAPAFinding",
    "IncidentCAPAVerdict",
    "order_incident_capa_entries",
    "verify_incident_capa_ledger",
]

_MATCHED = "matched"
_MISMATCHED = "mismatched"
_MISSING = "missing"
_NOT_VERIFIED = "not_verified"
_CONFLICT = "conflict"
_EXECUTION_JOIN_WORK_LIMIT = 1_000_000


@dataclass(frozen=True, slots=True)
class IncidentCAPAFinding:
    path: str
    status: Literal["matched", "mismatched", "missing", "not_verified", "conflict"]
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class IncidentCAPAVerdict:
    """Lineage and binding result, never an incident-management verdict."""

    ledger_fingerprint: str | None
    ledger_violations: tuple["Violation", ...]
    ordered_fingerprints: tuple[str, ...]
    findings: tuple[IncidentCAPAFinding, ...]

    @property
    def bindings_complete(self) -> bool:
        return (
            not self.ledger_violations
            and bool(self.findings)
            and all(item.status == _MATCHED for item in self.findings)
        )

    @property
    def has_mismatch(self) -> bool:
        return any(item.status == _MISMATCHED for item in self.findings)

    @property
    def has_unverified(self) -> bool:
        return any(item.status == _NOT_VERIFIED for item in self.findings)

    @property
    def has_missing(self) -> bool:
        return any(item.status == _MISSING for item in self.findings)

    @property
    def has_conflict(self) -> bool:
        return any(item.status == _CONFLICT for item in self.findings)


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _snapshot(validate, document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(validate.canonical_json(document).decode("utf-8"))


def _documents(
    validate,
    values: Iterable[dict[str, Any]],
    *,
    name: str,
    validator,
) -> tuple[dict[str, Any], ...]:
    if isinstance(values, (str, bytes, dict)):
        raise TypeError(f"{name} must be an iterable of dicts")
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise TypeError(f"{name}[{index}] must be a dict")
        violations = tuple(validator(value))
        if violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in violations[:3]
            )
            raise ValueError(
                f"{name}[{index}] does not satisfy the Motus contract: {detail}"
            )
        result.append(_snapshot(validate, value))
    return tuple(result)


def _entry_fingerprint(validate, entry: dict[str, Any]) -> str:
    if entry["kind"] == "incident_declaration":
        return validate.incident_declaration_fingerprint(entry["document"])
    return validate.capa_action_fingerprint(entry["document"])


def order_incident_capa_entries(
    ledger: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return a deterministic lineage order; transport array order is ignored.

    Parents precede children. Unrelated roots and amendment forks are ordered by
    exact fingerprint, never timestamp, ingestion order, role, or lifecycle.
    Invalid ledgers are refused rather than partially ordered.
    """
    if not isinstance(ledger, dict):
        raise TypeError("ledger must be a dict")
    validate = _contract_validate()
    violations = tuple(validate.validate_incident_capa_ledger(ledger))
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        raise ValueError(f"ledger does not satisfy the Motus contract: {detail}")
    snapshot = _snapshot(validate, ledger)
    rows = {
        _entry_fingerprint(validate, entry): entry
        for entry in snapshot["entries"]
    }
    children: dict[str, list[str]] = {fingerprint: [] for fingerprint in rows}
    indegree = dict.fromkeys(rows, 0)
    for fingerprint, entry in rows.items():
        predecessor = entry["document"].get("supersedes")
        if predecessor in rows:
            children[predecessor].append(fingerprint)
            indegree[fingerprint] += 1
    ready = sorted(key for key, degree in indegree.items() if degree == 0)
    ordered: list[dict[str, Any]] = []
    while ready:
        current = ready.pop(0)
        ordered.append(rows[current])
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    return tuple(ordered)


def _exact_reference_finding(
    *,
    path: str,
    expected: str,
    observed: tuple[str, ...],
    kind: str,
) -> IncidentCAPAFinding:
    if not observed:
        return IncidentCAPAFinding(
            path, _MISSING, expected, None,
            f"no contract-valid {kind} document was supplied for independent "
            "fingerprint derivation",
        )
    return IncidentCAPAFinding(
        path,
        _MATCHED if expected in observed else _MISMATCHED,
        expected,
        expected if expected in observed else observed,
        f"the reference was compared with fingerprints independently derived "
        f"from the supplied {kind} documents",
    )


def _package_values(values: Iterable[bytes]) -> tuple[bytes, ...]:
    if isinstance(values, (str, bytes, bytearray, dict)):
        raise TypeError("evidence_packages must be an iterable of bytes")
    result: list[bytes] = []
    for index, value in enumerate(values):
        if not isinstance(value, bytes):
            raise TypeError(f"evidence_packages[{index}] must be bytes")
        result.append(value)
    return tuple(result)


def _adapt_chain_finding(prefix: str, finding: Any) -> IncidentCAPAFinding:
    if finding.status == "not verified":
        status = _MISSING if finding.observed is None else _NOT_VERIFIED
    else:
        status = finding.status
    return IncidentCAPAFinding(
        f"{prefix}:{finding.path}",
        status,
        finding.expected,
        finding.observed,
        finding.reason,
    )


def _document_by_fingerprint(documents, fingerprint, derive):
    return next(
        (document for document in documents if derive(document) == fingerprint),
        None,
    )


def _receipt_index(
    receipts: tuple[dict[str, Any], ...],
) -> dict[str, dict[str, Any]]:
    """Index every contract-valid receipt BEGIN once, preserving first-match order."""
    index: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        for segment in receipt["segments"]:
            commitment = segment["begin"]["commitment"]
            execution_ref = (
                f"{commitment['tenant']}/{commitment['writer_id']}/"
                f"{commitment['sequence']}"
            )
            index.setdefault(execution_ref, receipt)
    return index


def _receipt_binding_reference_count(snapshot: dict[str, Any]) -> int:
    """Count ledger references that can cause a receipt binding lookup."""
    kinds = {"execution", "control_application", "human_oversight_receipt"}
    return sum(
        1
        for entry in snapshot["entries"]
        for reference in entry["document"].get("evidence", ())
        if reference["kind"] in kinds
    )


def verify_incident_capa_ledger(
    ledger: dict[str, Any],
    *,
    execution_receipts: Iterable[dict[str, Any]] = (),
    manifests: Iterable[dict[str, Any]] = (),
    registries: Iterable[dict[str, Any]] = (),
    control_applications: Iterable[dict[str, Any]] = (),
    human_oversight_receipts: Iterable[dict[str, Any]] = (),
    evidence_packages: Iterable[bytes] = (),
) -> IncidentCAPAVerdict:
    """Verify one ledger view against the Motus evidence supplied by the caller.

    A matched finding establishes equality or an execution locator binding only.
    It does not establish that an incident occurred, an action was effective, or
    an incident was correctly closed.
    """
    if not isinstance(ledger, dict):
        raise TypeError("ledger must be a dict")
    validate = _contract_validate()
    ledger_violations = tuple(validate.validate_incident_capa_ledger(ledger))
    if ledger_violations:
        return IncidentCAPAVerdict(None, ledger_violations, (), ())

    snapshot = _snapshot(validate, ledger)
    receipts = _documents(
        validate, execution_receipts, name="execution_receipts",
        validator=validate.validate_receipt,
    )
    for receipt in receipts:
        issue = receipt_execution_issue(receipt)
        if issue is not None:
            raise ValueError(
                "execution_receipts contains a receipt with inconsistent "
                "derived execution identity: " + issue
            )
    execution_join_work = (
        sum(len(receipt["segments"]) for receipt in receipts)
        + (_receipt_binding_reference_count(snapshot) if receipts else 0)
    )
    execution_join_over_budget = (
        execution_join_work > _EXECUTION_JOIN_WORK_LIMIT
    )
    receipt_index = (
        {} if not receipts or execution_join_over_budget
        else _receipt_index(receipts)
    )
    manifest_docs = _documents(
        validate, manifests, name="manifests",
        validator=validate.validate_system_manifest,
    )
    registry_docs = _documents(
        validate, registries, name="registries",
        validator=validate.validate_risk_control_registry,
    )
    application_docs = _documents(
        validate, control_applications, name="control_applications",
        validator=validate.validate_control_application,
    )
    oversight_docs = _documents(
        validate, human_oversight_receipts, name="human_oversight_receipts",
        validator=validate.validate_human_oversight_receipt,
    )
    package_docs = _package_values(evidence_packages)
    if package_docs:
        from vitruvyan_motus.evidence import evidence_package_fingerprint

        package_fingerprints = tuple(
            evidence_package_fingerprint(value) for value in package_docs
        )
    else:
        package_fingerprints = ()
    package_by_fingerprint: dict[str, bytes] = {}
    for fingerprint, value in zip(package_fingerprints, package_docs):
        package_by_fingerprint.setdefault(fingerprint, value)
    package_verdicts: dict[str, Any] = {}
    application_binding_cache: dict[str, tuple[str, Any]] = {}
    oversight_binding_cache: dict[str, tuple[Any, ...]] = {}

    pools = {
        "system_manifest": tuple(
            validate.system_manifest_fingerprint(value) for value in manifest_docs
        ),
        "risk_control_registry": tuple(
            validate.risk_control_registry_fingerprint(value) for value in registry_docs
        ),
        "control_application": tuple(
            validate.control_application_fingerprint(value) for value in application_docs
        ),
        "human_oversight_receipt": tuple(
            validate.human_oversight_receipt_fingerprint(value) for value in oversight_docs
        ),
        "receipt": tuple(validate.receipt_fingerprint(value) for value in receipts),
        "evidence_package": package_fingerprints,
    }

    entries: dict[str, tuple[int, dict[str, Any]]] = {}
    incident_entries: dict[str, tuple[int, dict[str, Any]]] = {}
    children: dict[str, list[str]] = {}
    for index, entry in enumerate(snapshot["entries"]):
        fingerprint = _entry_fingerprint(validate, entry)
        entries[fingerprint] = (index, entry)
        if entry["kind"] == "incident_declaration":
            incident_entries[fingerprint] = (index, entry)
        predecessor = entry["document"].get("supersedes")
        if predecessor is not None:
            children.setdefault(predecessor, []).append(fingerprint)

    findings: list[IncidentCAPAFinding] = []
    if execution_join_over_budget:
        findings.append(IncidentCAPAFinding(
            "$.execution_receipts",
            _NOT_VERIFIED,
            str(_EXECUTION_JOIN_WORK_LIMIT),
            str(execution_join_work),
            "receipt BEGIN indexing and ledger reference matching exceed the "
            "cumulative semantic-work limit",
        ))
    for fingerprint, (index, entry) in entries.items():
        document = entry["document"]
        predecessor = document.get("supersedes")
        if predecessor is not None:
            target = entries.get(predecessor)
            findings.append(IncidentCAPAFinding(
                f"$.entries[{index}].document.supersedes",
                _MATCHED if target is not None else _NOT_VERIFIED,
                predecessor,
                predecessor if target is not None else None,
                "the immediate predecessor is present in this ledger view"
                if target is not None else
                "the claimed immediate predecessor is absent from this ledger "
                "view; the lineage claim is unresolved, not false",
            ))

        if entry["kind"] == "capa_action":
            expected = document["incident_fingerprint"]
            target = incident_entries.get(expected)
            if target is None:
                findings.append(IncidentCAPAFinding(
                    f"$.entries[{index}].document.incident_fingerprint",
                    _NOT_VERIFIED, expected, None,
                    "the exact IncidentDeclaration revision is absent from this "
                    "ledger view",
                ))
            else:
                incident = target[1]["document"]
                agrees = (
                    incident["incident_id"] == document["incident_id"]
                    and incident["producer_namespace"]
                    == document["producer_namespace"]
                )
                findings.append(IncidentCAPAFinding(
                    f"$.entries[{index}].document.incident_fingerprint",
                    _MATCHED if agrees else _MISMATCHED,
                    expected,
                    expected,
                    "the supplied incident revision has the same producer "
                    "namespace and stable incident_id"
                    if agrees else
                    "the fingerprint identifies an incident revision whose "
                    "producer namespace or stable incident_id disagrees",
                ))

        for ref_index, reference in enumerate(document.get("evidence", ())):
            path = f"$.entries[{index}].document.evidence[{ref_index}]"
            if reference["kind"] == "execution":
                expected = reference["execution_ref"]
                if not receipts:
                    findings.append(IncidentCAPAFinding(
                        path + ".execution_ref", _MISSING, expected, None,
                        "no execution receipt was supplied that can bind this locator",
                    ))
                elif execution_join_over_budget:
                    findings.append(IncidentCAPAFinding(
                        path + ".execution_ref",
                        _NOT_VERIFIED,
                        expected,
                        None,
                        "receipt binding was not attempted because the cumulative "
                        "semantic-work limit was exceeded",
                    ))
                else:
                    matched = expected in receipt_index
                    findings.append(IncidentCAPAFinding(
                        path + ".execution_ref",
                        _MATCHED if matched else _MISMATCHED,
                        expected,
                        expected if matched else None,
                        "a contract-valid receipt contains a BEGIN at this "
                        "execution_ref" if matched else
                        "none of the supplied contract-valid receipts contains "
                        "a BEGIN at this execution_ref",
                    ))
            else:
                kind = reference["kind"]
                identity_finding = _exact_reference_finding(
                    path=path + ".fingerprint",
                    expected=reference["fingerprint"],
                    observed=pools[kind],
                    kind=kind,
                )
                findings.append(identity_finding)
                if identity_finding.status != _MATCHED:
                    continue

                if kind == "control_application":
                    from vitruvyan_motus.risk_control import (
                        verify_control_application_bindings,
                    )

                    binding_fingerprint = reference["fingerprint"]
                    if binding_fingerprint not in application_binding_cache:
                        application = _document_by_fingerprint(
                            application_docs,
                            binding_fingerprint,
                            validate.control_application_fingerprint,
                        )
                        registry = _document_by_fingerprint(
                            registry_docs,
                            application["registry_fingerprint"],
                            validate.risk_control_registry_fingerprint,
                        )
                        if registry is None:
                            application_binding_cache[binding_fingerprint] = (
                                "missing_registry",
                                application["registry_fingerprint"],
                            )
                        else:
                            manifest_fingerprint = application.get(
                                "manifest_fingerprint"
                            )
                            manifest = (
                                _document_by_fingerprint(
                                    manifest_docs,
                                    manifest_fingerprint,
                                    validate.system_manifest_fingerprint,
                                )
                                if manifest_fingerprint is not None else None
                            )
                            receipt = receipt_index.get(application["execution_ref"])
                            chain = verify_control_application_bindings(
                                application,
                                registry=registry,
                                manifest=manifest,
                                receipt=receipt,
                            )
                            application_binding_cache[binding_fingerprint] = (
                                "findings", tuple(chain.findings),
                            )
                    cached_kind, cached_value = application_binding_cache[
                        binding_fingerprint
                    ]
                    if cached_kind == "missing_registry":
                        findings.append(IncidentCAPAFinding(
                            path + ".verification:$.registry_fingerprint",
                            _MISSING,
                            cached_value,
                            None,
                            "the exact ControlApplication is present, but its "
                            "mandatory Registry revision was not supplied",
                        ))
                    else:
                        findings.extend(
                            _adapt_chain_finding(path + ".verification", item)
                            for item in cached_value
                        )

                elif kind == "human_oversight_receipt":
                    from vitruvyan_motus.human_oversight import (
                        verify_human_oversight_bindings,
                    )

                    binding_fingerprint = reference["fingerprint"]
                    if binding_fingerprint not in oversight_binding_cache:
                        oversight = _document_by_fingerprint(
                            oversight_docs,
                            binding_fingerprint,
                            validate.human_oversight_receipt_fingerprint,
                        )
                        bindings = oversight.get("bindings", {})
                        manifest = _document_by_fingerprint(
                            manifest_docs,
                            bindings.get("manifest_fingerprint"),
                            validate.system_manifest_fingerprint,
                        )
                        registry = _document_by_fingerprint(
                            registry_docs,
                            bindings.get("registry_fingerprint"),
                            validate.risk_control_registry_fingerprint,
                        )
                        application_fingerprint = bindings.get(
                            "control_application_fingerprint"
                        )
                        if oversight["subject"]["kind"] == "control_application":
                            application_fingerprint = oversight["subject"][
                                "control_application_fingerprint"
                            ]
                        application = _document_by_fingerprint(
                            application_docs,
                            application_fingerprint,
                            validate.control_application_fingerprint,
                        )
                        receipt = receipt_index.get(oversight["execution_ref"])
                        chain = verify_human_oversight_bindings(
                            oversight,
                            execution_receipt=receipt,
                            manifest=manifest,
                            registry=registry,
                            control_application=application,
                        )
                        oversight_binding_cache[binding_fingerprint] = tuple(
                            chain.findings
                        )
                    findings.extend(
                        _adapt_chain_finding(path + ".verification", item)
                        for item in oversight_binding_cache[binding_fingerprint]
                    )

                elif kind == "evidence_package":
                    from vitruvyan_motus.evidence import verify_package

                    fingerprint = reference["fingerprint"]
                    if fingerprint not in package_verdicts:
                        package_verdicts[fingerprint] = verify_package(
                            package_by_fingerprint[fingerprint]
                        )
                    package_verdict = package_verdicts[fingerprint]
                    if package_verdict.verdict is None:
                        status = _NOT_VERIFIED
                    elif (
                        package_verdict.transport_ok
                        and not package_verdict.damaged
                        and not package_verdict.trace_violations
                        and not package_verdict.verdict.violations
                        and not package_verdict.verdict.refused
                    ):
                        status = _MATCHED
                    else:
                        status = _MISMATCHED
                    findings.append(IncidentCAPAFinding(
                        path + ".verification",
                        status,
                        reference["fingerprint"],
                        reference["fingerprint"],
                        "the exact package bytes were checked with the native "
                        "Motus evidence-package verifier",
                    ))

    for predecessor, successors in sorted(children.items()):
        if len(successors) > 1:
            findings.append(IncidentCAPAFinding(
                f"lineage:{predecessor}",
                _CONFLICT,
                predecessor,
                tuple(sorted(successors)),
                "more than one record claims the same immediate predecessor; "
                "Motus preserves the amendment fork and selects no winner",
            ))

    ordered_entries = order_incident_capa_entries(snapshot)
    ordered_fingerprints = tuple(
        _entry_fingerprint(validate, entry) for entry in ordered_entries
    )
    return IncidentCAPAVerdict(
        validate.incident_capa_ledger_fingerprint(snapshot),
        (),
        ordered_fingerprints,
        tuple(findings),
    )
