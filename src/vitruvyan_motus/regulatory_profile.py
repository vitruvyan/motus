"""ADR-038 deterministic Regulatory Evidence Profile assessment.

A profile maps opaque external requirement references to evidence kinds Motus
already owns. This module reports whether the requested Motus evidence is
missing, contract-invalid or contradictory, not independently verifiable with
the supplied material, or matched. It never decides legal compliance.

The contract validator is imported lazily so importing vitruvyan_motus does
not import the third-party jsonschema package.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal

from vitruvyan_motus._execution_ref import receipt_execution_issue

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation
    from vitruvyan_motus.graph import GraphSpec
    from vitruvyan_motus.trace import Trace

__all__ = [
    "RegulatoryEvidenceFinding",
    "RegulatoryEvidenceAssessment",
    "assess_evidence_profile",
]

_MISSING = "missing"
_NOT_VERIFIED = "not_verified"
_MISMATCHED = "mismatched"
_MATCHED = "matched"


@dataclass(frozen=True, slots=True)
class RegulatoryEvidenceFinding:
    requirement_ref: str
    kind: str
    status: Literal["missing", "not_verified", "mismatched", "matched"]
    reason: str


@dataclass(frozen=True, slots=True)
class RegulatoryEvidenceAssessment:
    """Evidence mapping result; never a compliance or conformity verdict."""

    profile_fingerprint: str | None
    profile_violations: tuple["Violation", ...]
    findings: tuple[RegulatoryEvidenceFinding, ...]


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _binding_status(verdict: Any) -> tuple[str, str]:
    """Collapse an existing Motus binding verdict without weakening it."""
    violation_fields = (
        "manifest_violations",
        "application_violations",
        "registry_violations",
        "oversight_violations",
    )
    if any(getattr(verdict, name, ()) for name in violation_fields):
        return _MISMATCHED, "the supplied artifact violates its Motus contract"
    if getattr(verdict, "has_mismatch", False):
        return _MISMATCHED, "an existing Motus binding verifier reported a mismatch"
    if getattr(verdict, "has_unverified", False):
        return _NOT_VERIFIED, "required independent binding material was not supplied"
    if getattr(verdict, "bindings_complete", False):
        return _MATCHED, "the existing Motus binding verifier matched every evaluated binding"
    return _NOT_VERIFIED, "the existing Motus verifier could not establish a complete binding"


def _validate_document(document: dict[str, Any], validator: Any) -> tuple[str, str]:
    violations = tuple(validator(document))
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        return _MISMATCHED, "the supplied artifact violates its Motus contract: " + detail
    return _MATCHED, "a contract-valid Motus artifact of the requested kind was supplied"


def _evaluate_kind(
    kind: str,
    *,
    execution_receipt: dict[str, Any] | None,
    system_manifest: dict[str, Any] | None,
    risk_control_registry: dict[str, Any] | None,
    control_application: dict[str, Any] | None,
    human_oversight_receipt: dict[str, Any] | None,
    graph_specs: tuple["GraphSpec", ...],
    traces: tuple["Trace", ...],
) -> tuple[str, str]:
    validate = _contract_validate()

    if kind == "execution_receipt":
        if execution_receipt is None:
            return _MISSING, "no execution receipt was supplied"
        verdict = validate.verify(execution_receipt)
        if verdict.refused:
            return (
                _NOT_VERIFIED,
                "the existing Motus receipt verifier refused to evaluate the supplied receipt",
            )
        if verdict.violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in verdict.violations[:3]
            )
            return (
                _MISMATCHED,
                "the supplied receipt violates the Motus contract: " + detail,
            )
        issue = receipt_execution_issue(execution_receipt)
        if issue is not None:
            return (
                _MISMATCHED,
                "execution receipt has inconsistent derived identity: " + issue,
            )
        return (
            _MATCHED,
            "the existing Motus receipt verifier accepted the supplied receipt",
        )

    if kind == "system_manifest":
        if system_manifest is None:
            return _MISSING, "no System Manifest was supplied"
        from vitruvyan_motus.system_manifest import verify_system_manifest_bindings
        try:
            verdict = verify_system_manifest_bindings(
                system_manifest,
                graph_specs=graph_specs,
                traces=traces,
            )
        except ValueError as exc:
            return (
                _MISMATCHED,
                "System Manifest binding verification refused the supplied evidence: "
                + str(exc),
            )
        return _binding_status(verdict)

    if kind == "risk_control_registry":
        if risk_control_registry is None:
            return _MISSING, "no Risk & Control Registry was supplied"
        return _validate_document(
            risk_control_registry, validate.validate_risk_control_registry
        )

    if kind == "control_application":
        if control_application is None:
            return _MISSING, "no ControlApplication was supplied"
        if risk_control_registry is None:
            return (
                _NOT_VERIFIED,
                "a ControlApplication was supplied but no Risk & Control Registry "
                "was supplied for its mandatory binding",
            )
        from vitruvyan_motus.risk_control import verify_control_application_bindings
        try:
            verdict = verify_control_application_bindings(
                control_application,
                registry=risk_control_registry,
                manifest=system_manifest,
                receipt=execution_receipt,
            )
        except ValueError as exc:
            return _MISMATCHED, (
                "ControlApplication binding verification refused the supplied "
                "evidence: " + str(exc)
            )
        return _binding_status(verdict)

    if kind == "human_oversight_receipt":
        if human_oversight_receipt is None:
            return _MISSING, "no HumanOversightReceipt was supplied"
        from vitruvyan_motus.human_oversight import verify_human_oversight_bindings
        try:
            verdict = verify_human_oversight_bindings(
                human_oversight_receipt,
                execution_receipt=execution_receipt,
                manifest=system_manifest,
                registry=risk_control_registry,
                control_application=control_application,
            )
        except ValueError as exc:
            return _MISMATCHED, (
                "HumanOversightReceipt binding verification refused the supplied "
                "evidence: " + str(exc)
            )
        return _binding_status(verdict)

    raise ValueError(f"unsupported regulatory evidence kind {kind!r}")


def assess_evidence_profile(
    profile: dict[str, Any],
    *,
    execution_receipt: dict[str, Any] | None = None,
    system_manifest: dict[str, Any] | None = None,
    risk_control_registry: dict[str, Any] | None = None,
    control_application: dict[str, Any] | None = None,
    human_oversight_receipt: dict[str, Any] | None = None,
    graph_specs: Iterable["GraphSpec"] = (),
    traces: Iterable["Trace"] = (),
) -> RegulatoryEvidenceAssessment:
    """Assess supplied Motus evidence against one external mapping profile.

    matched means the evidence expectation declared by this profile was matched
    under existing Motus contract semantics. It does not mean the external
    requirement is legally satisfied.
    """
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dict")
    for name, document in (
        ("execution_receipt", execution_receipt),
        ("system_manifest", system_manifest),
        ("risk_control_registry", risk_control_registry),
        ("control_application", control_application),
        ("human_oversight_receipt", human_oversight_receipt),
    ):
        if document is not None and not isinstance(document, dict):
            raise TypeError(f"{name} must be a dict or None")

    validate = _contract_validate()
    violations = tuple(validate.validate_regulatory_evidence_profile(profile))
    if violations:
        return RegulatoryEvidenceAssessment(None, violations, ())

    profile_document = json.loads(validate.canonical_json(profile).decode("utf-8"))
    profile_fingerprint = validate.regulatory_evidence_profile_fingerprint(
        profile_document
    )
    spec_values = tuple(graph_specs)
    trace_values = tuple(traces)
    findings: list[RegulatoryEvidenceFinding] = []
    requested_kinds = {
        expectation["kind"]
        for requirement in profile_document["requirements"]
        for expectation in requirement["evidence"]
    }
    evaluation_cache: dict[str, tuple[str, str]] = {}
    for kind in requested_kinds:
        evaluation_cache[kind] = _evaluate_kind(
            kind,
            execution_receipt=execution_receipt,
            system_manifest=system_manifest,
            risk_control_registry=risk_control_registry,
            control_application=control_application,
            human_oversight_receipt=human_oversight_receipt,
            graph_specs=spec_values,
            traces=trace_values,
        )
    for requirement in profile_document["requirements"]:
        requirement_ref = requirement["requirement_ref"]
        for expectation in requirement["evidence"]:
            kind = expectation["kind"]
            status, reason = evaluation_cache[kind]
            findings.append(RegulatoryEvidenceFinding(
                requirement_ref=requirement_ref,
                kind=kind,
                status=status,
                reason=reason,
            ))

    return RegulatoryEvidenceAssessment(
        profile_fingerprint=profile_fingerprint,
        profile_violations=(),
        findings=tuple(findings),
    )
