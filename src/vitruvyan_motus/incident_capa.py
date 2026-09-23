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
    receipt_segment_for_execution_ref,
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
_NOT_VERIFIED = "not verified"
_CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class IncidentCAPAFinding:
    path: str
    status: Literal["matched", "mismatched", "not verified", "conflict"]
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
            path, _NOT_VERIFIED, expected, None,
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


def verify_incident_capa_ledger(
    ledger: dict[str, Any],
    *,
    execution_receipts: Iterable[dict[str, Any]] = (),
    manifests: Iterable[dict[str, Any]] = (),
    registries: Iterable[dict[str, Any]] = (),
    control_applications: Iterable[dict[str, Any]] = (),
    human_oversight_receipts: Iterable[dict[str, Any]] = (),
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
                        path + ".execution_ref", _NOT_VERIFIED, expected, None,
                        "no execution receipt was supplied that can bind this locator",
                    ))
                else:
                    matched = any(
                        receipt_segment_for_execution_ref(receipt, expected) is not None
                        for receipt in receipts
                    )
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
                findings.append(_exact_reference_finding(
                    path=path + ".fingerprint",
                    expected=reference["fingerprint"],
                    observed=pools[reference["kind"]],
                    kind=reference["kind"],
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
