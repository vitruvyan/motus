"""ADR-036 Risk & Control Registry queries and ControlApplication binding.

Document validity, cross-document binding, and control effectiveness are three
different questions. This module answers the middle one without manufacturing
the third: it compares one contract-valid ControlApplication with the exact
registry, optional System Manifest, and receipt evidence supplied by a caller.

The contract validator is imported lazily so importing ``vitruvyan_motus``
still does not import the third-party ``jsonschema`` package.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from vitruvyan_motus._execution_ref import receipt_segment_for_execution_ref

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = [
    "ControlApplicationBindingFinding",
    "ControlApplicationBindingVerdict",
    "controls_for_risk",
    "risks_for_control",
    "verify_control_application_bindings",
]

_MATCHED = "matched"
_MISMATCHED = "mismatched"
_NOT_VERIFIED = "not verified"


@dataclass(frozen=True, slots=True)
class ControlApplicationBindingFinding:
    path: str
    status: Literal["matched", "mismatched", "not verified"]
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class ControlApplicationBindingVerdict:
    """Cross-document result, never an effectiveness/compliance verdict."""

    application_fingerprint: str | None
    registry_fingerprint: str | None
    application_violations: tuple["Violation", ...]
    registry_violations: tuple["Violation", ...]
    findings: tuple[ControlApplicationBindingFinding, ...]

    @property
    def bindings_complete(self) -> bool:
        return (
            not self.application_violations
            and not self.registry_violations
            and bool(self.findings)
            and all(item.status == _MATCHED for item in self.findings)
        )

    @property
    def has_mismatch(self) -> bool:
        return any(item.status == _MISMATCHED for item in self.findings)

    @property
    def has_unverified(self) -> bool:
        return any(item.status == _NOT_VERIFIED for item in self.findings)


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _plain_snapshot(validate, document: dict[str, Any]) -> dict[str, Any]:
    canonical = validate.canonical_json(document)
    return json.loads(canonical.decode("utf-8"))


def _matched_or_mismatched(
    path: str,
    expected: str,
    observed: str | None,
    reason: str,
) -> ControlApplicationBindingFinding:
    status = _MATCHED if expected == observed else _MISMATCHED
    return ControlApplicationBindingFinding(
        path, status, expected, observed, reason,
    )


def _validated_registry_snapshot(registry: dict[str, Any]):
    if not isinstance(registry, dict):
        raise TypeError("registry must be a dict")
    validate = _contract_validate()
    violations = tuple(validate.validate_risk_control_registry(registry))
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        raise ValueError("registry does not satisfy the Motus contract: " + detail)
    return validate, _plain_snapshot(validate, registry)


def controls_for_risk(
    registry: dict[str, Any], risk_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return detached control declarations linked to one declared risk."""
    if not isinstance(risk_id, str):
        raise TypeError("risk_id must be a string")
    _validate, document = _validated_registry_snapshot(registry)
    if not any(item["risk_id"] == risk_id for item in document["risks"]):
        raise KeyError(risk_id)
    return tuple(
        control for control in document["controls"]
        if risk_id in control["risk_refs"]
    )


def risks_for_control(
    registry: dict[str, Any], control_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return detached risk declarations linked from one declared control."""
    if not isinstance(control_id, str):
        raise TypeError("control_id must be a string")
    _validate, document = _validated_registry_snapshot(registry)
    control = next(
        (item for item in document["controls"] if item["control_id"] == control_id),
        None,
    )
    if control is None:
        raise KeyError(control_id)
    linked = set(control["risk_refs"])
    return tuple(risk for risk in document["risks"] if risk["risk_id"] in linked)


def _receipt_refs(receipt: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for segment in receipt["segments"]:
        commitment = segment["begin"]["commitment"]
        refs.append(
            f"{commitment['tenant']}/{commitment['writer_id']}/"
            f"{commitment['sequence']}"
        )
    return tuple(refs)


def verify_control_application_bindings(
    application: dict[str, Any],
    *,
    registry: dict[str, Any],
    manifest: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
) -> ControlApplicationBindingVerdict:
    """Bind one ControlApplication to supplied Motus declarations/evidence.

    A matched receipt finding says only that a contract-valid receipt contains
    the application ``execution_ref``. It does not verify anchors,
    attestations, retention, legal time, or any other ADR-020 assurance level.
    """
    if not isinstance(application, dict):
        raise TypeError("application must be a dict")
    if not isinstance(registry, dict):
        raise TypeError("registry must be a dict")
    if manifest is not None and not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict or None")
    if receipt is not None and not isinstance(receipt, dict):
        raise TypeError("receipt must be a dict or None")

    validate = _contract_validate()
    application_violations = tuple(
        validate.validate_control_application(application)
    )
    registry_violations = tuple(
        validate.validate_risk_control_registry(registry)
    )
    if application_violations or registry_violations:
        return ControlApplicationBindingVerdict(
            None, None, application_violations, registry_violations, (),
        )

    application_document = _plain_snapshot(validate, application)
    registry_document = _plain_snapshot(validate, registry)
    application_fingerprint = validate.control_application_fingerprint(
        application_document
    )
    registry_fingerprint = validate.risk_control_registry_fingerprint(
        registry_document
    )

    manifest_document = None
    manifest_fingerprint = None
    if manifest is not None:
        manifest_violations = tuple(validate.validate_system_manifest(manifest))
        if manifest_violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in manifest_violations[:3]
            )
            raise ValueError(
                "manifest does not satisfy the Motus contract: " + detail
            )
        manifest_document = _plain_snapshot(validate, manifest)
        manifest_fingerprint = validate.system_manifest_fingerprint(
            manifest_document
        )

    receipt_document = None
    if receipt is not None:
        receipt_violations = tuple(validate.validate_receipt(receipt))
        if receipt_violations:
            detail = "; ".join(
                f"{item.rule} {item.path}: {item.message}"
                for item in receipt_violations[:3]
            )
            raise ValueError(
                "receipt does not satisfy the Motus contract: " + detail
            )
        receipt_document = _plain_snapshot(validate, receipt)

    findings: list[ControlApplicationBindingFinding] = []
    findings.append(_matched_or_mismatched(
        "$.registry_fingerprint",
        application_document["registry_fingerprint"],
        registry_fingerprint,
        "application value compared with the fingerprint independently "
        "derived from the supplied registry",
    ))

    control = next(
        (
            item for item in registry_document["controls"]
            if item["control_id"] == application_document["control_id"]
        ),
        None,
    )
    findings.append(ControlApplicationBindingFinding(
        "$.control_id",
        _MATCHED if control is not None else _MISMATCHED,
        application_document["control_id"],
        application_document["control_id"] if control is not None else None,
        "control_id names a declaration in the supplied registry"
        if control is not None
        else "control_id names no declaration in the supplied registry",
    ))

    if control is not None and "enforcement_point" in control:
        findings.append(_matched_or_mismatched(
            "$.enforcement_point",
            application_document["enforcement_point"],
            control["enforcement_point"],
            "application event point compared with the registry declaration",
        ))

    registry_system = registry_document.get("system")
    registry_manifest = (
        registry_system["manifest_fingerprint"]
        if isinstance(registry_system, dict) else None
    )
    application_manifest = application_document.get("manifest_fingerprint")
    if registry_manifest is not None:
        if application_manifest is None:
            findings.append(ControlApplicationBindingFinding(
                "$.manifest_fingerprint", _NOT_VERIFIED,
                registry_manifest, None,
                "the registry binds a System Manifest but the application "
                "does not carry its fingerprint",
            ))
        elif manifest_fingerprint is None:
            findings.append(ControlApplicationBindingFinding(
                "$.manifest_fingerprint", _NOT_VERIFIED,
                application_manifest, registry_manifest,
                "application and registry values can be compared, but no "
                "System Manifest document was supplied for independent derivation",
            ))
        else:
            observed = tuple((registry_manifest, manifest_fingerprint))
            status = (
                _MATCHED
                if application_manifest == registry_manifest == manifest_fingerprint
                else _MISMATCHED
            )
            findings.append(ControlApplicationBindingFinding(
                "$.manifest_fingerprint", status, application_manifest,
                observed,
                "application value compared with the registry binding and the "
                "fingerprint independently derived from the supplied manifest",
            ))
            findings.append(_matched_or_mismatched(
                "registry:$.system.system_id",
                registry_system["system_id"],
                manifest_document["system"]["id"],
                "registry system_id compared with the supplied System Manifest",
            ))
    elif application_manifest is not None:
        findings.append(ControlApplicationBindingFinding(
            "$.manifest_fingerprint", _NOT_VERIFIED,
            application_manifest, manifest_fingerprint,
            "the application carries a manifest fingerprint but the supplied "
            "registry declares no system binding",
        ))

    execution_ref = application_document["execution_ref"]
    if receipt_document is None:
        findings.append(ControlApplicationBindingFinding(
            "$.execution_ref", _NOT_VERIFIED, execution_ref, None,
            "no receipt was supplied that can bind this execution locator",
        ))
    else:
        binds = receipt_segment_for_execution_ref(
            receipt_document, execution_ref
        ) is not None
        refs = _receipt_refs(receipt_document)
        findings.append(ControlApplicationBindingFinding(
            "$.execution_ref",
            _MATCHED if binds else _MISMATCHED,
            execution_ref,
            execution_ref if binds else refs,
            "a contract-valid receipt contains a BEGIN at this execution_ref; "
            "this identity binding does not establish an ADR-020 assurance level"
            if binds else
            "the supplied contract-valid receipt contains no BEGIN at this "
            "execution_ref",
        ))

    return ControlApplicationBindingVerdict(
        application_fingerprint,
        registry_fingerprint,
        (),
        (),
        tuple(findings),
    )
