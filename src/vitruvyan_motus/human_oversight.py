"""ADR-037 HumanOversightReceipt cross-document binding verification.

Document validity, artifact binding, and the truth or adequacy of a claimed
human act are separate questions. This module answers only the middle one.
The contract validator is imported lazily so importing ``vitruvyan_motus``
does not import the third-party ``jsonschema`` package.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from vitruvyan_motus._execution_ref import (
    receipt_execution_issue,
    receipt_segment_for_execution_ref,
)

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = [
    "HumanOversightBindingFinding",
    "HumanOversightBindingVerdict",
    "verify_human_oversight_bindings",
]

_MATCHED = "matched"
_MISMATCHED = "mismatched"
_NOT_VERIFIED = "not verified"


@dataclass(frozen=True, slots=True)
class HumanOversightBindingFinding:
    path: str
    status: Literal["matched", "mismatched", "not verified"]
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class HumanOversightBindingVerdict:
    """Cross-document result, never an identity, authority, or compliance verdict."""

    oversight_fingerprint: str | None
    oversight_violations: tuple["Violation", ...]
    findings: tuple[HumanOversightBindingFinding, ...]

    @property
    def bindings_complete(self) -> bool:
        return (
            not self.oversight_violations
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
) -> HumanOversightBindingFinding:
    return HumanOversightBindingFinding(
        path,
        _MATCHED if expected == observed else _MISMATCHED,
        expected,
        observed,
        reason,
    )


def _validated_optional_document(
    validate,
    document: dict[str, Any] | None,
    *,
    name: str,
    validator,
) -> dict[str, Any] | None:
    if document is None:
        return None
    violations = tuple(validator(document))
    if violations:
        detail = "; ".join(
            f"{item.rule} {item.path}: {item.message}" for item in violations[:3]
        )
        raise ValueError(
            f"{name} does not satisfy the Motus contract: {detail}"
        )
    return _plain_snapshot(validate, document)


def _execution_refs(receipt: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for segment in receipt["segments"]:
        commitment = segment["begin"]["commitment"]
        refs.append(
            f"{commitment['tenant']}/{commitment['writer_id']}/"
            f"{commitment['sequence']}"
        )
    return tuple(refs)


def verify_human_oversight_bindings(
    oversight_receipt: dict[str, Any],
    *,
    execution_receipt: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    control_application: dict[str, Any] | None = None,
) -> HumanOversightBindingVerdict:
    """Compare one HumanOversightReceipt with supplied Motus artifacts.

    A matched result establishes only equality with contract-valid documents
    supplied to this call. It does not establish that the actor is human or
    authorised, that the event occurred, or that oversight was sufficient.
    """
    if not isinstance(oversight_receipt, dict):
        raise TypeError("oversight_receipt must be a dict")
    for name, document in (
        ("execution_receipt", execution_receipt),
        ("manifest", manifest),
        ("registry", registry),
        ("control_application", control_application),
    ):
        if document is not None and not isinstance(document, dict):
            raise TypeError(f"{name} must be a dict or None")

    validate = _contract_validate()
    oversight_violations = tuple(
        validate.validate_human_oversight_receipt(oversight_receipt)
    )
    if oversight_violations:
        return HumanOversightBindingVerdict(None, oversight_violations, ())

    oversight = _plain_snapshot(validate, oversight_receipt)
    oversight_fingerprint = validate.human_oversight_receipt_fingerprint(
        oversight
    )

    execution = _validated_optional_document(
        validate,
        execution_receipt,
        name="execution_receipt",
        validator=validate.validate_receipt,
    )
    if execution is not None:
        issue = receipt_execution_issue(execution)
        if issue is not None:
            raise ValueError(
                "execution_receipt has inconsistent derived execution identity: "
                + issue
            )
    manifest_document = _validated_optional_document(
        validate,
        manifest,
        name="manifest",
        validator=validate.validate_system_manifest,
    )
    registry_document = _validated_optional_document(
        validate,
        registry,
        name="registry",
        validator=validate.validate_risk_control_registry,
    )
    application_document = _validated_optional_document(
        validate,
        control_application,
        name="control_application",
        validator=validate.validate_control_application,
    )

    findings: list[HumanOversightBindingFinding] = []
    execution_ref = oversight["execution_ref"]
    if execution is None:
        findings.append(HumanOversightBindingFinding(
            "$.execution_ref", _NOT_VERIFIED, execution_ref, None,
            "no execution receipt was supplied that can bind this locator",
        ))
    else:
        binds = receipt_segment_for_execution_ref(execution, execution_ref) is not None
        findings.append(HumanOversightBindingFinding(
            "$.execution_ref",
            _MATCHED if binds else _MISMATCHED,
            execution_ref,
            execution_ref if binds else _execution_refs(execution),
            "a contract-valid receipt contains a BEGIN at this execution_ref; "
            "this identity binding establishes no ADR-020 assurance level"
            if binds else
            "the supplied contract-valid receipt contains no BEGIN at this "
            "execution_ref",
        ))

    bindings = oversight.get("bindings", {})
    manifest_expected = bindings.get("manifest_fingerprint")
    if manifest_expected is not None:
        observed = (
            validate.system_manifest_fingerprint(manifest_document)
            if manifest_document is not None else None
        )
        if observed is None:
            findings.append(HumanOversightBindingFinding(
                "$.bindings.manifest_fingerprint", _NOT_VERIFIED,
                manifest_expected, None,
                "no System Manifest was supplied for independent fingerprint derivation",
            ))
        else:
            findings.append(_matched_or_mismatched(
                "$.bindings.manifest_fingerprint", manifest_expected, observed,
                "receipt value compared with the fingerprint independently "
                "derived from the supplied System Manifest",
            ))

    registry_expected = bindings.get("registry_fingerprint")
    if registry_expected is not None:
        observed = (
            validate.risk_control_registry_fingerprint(registry_document)
            if registry_document is not None else None
        )
        if observed is None:
            findings.append(HumanOversightBindingFinding(
                "$.bindings.registry_fingerprint", _NOT_VERIFIED,
                registry_expected, None,
                "no Risk & Control Registry was supplied for independent "
                "fingerprint derivation",
            ))
        else:
            findings.append(_matched_or_mismatched(
                "$.bindings.registry_fingerprint", registry_expected, observed,
                "receipt value compared with the fingerprint independently "
                "derived from the supplied Registry",
            ))

    application_expected_paths: list[tuple[str, str]] = []
    subject = oversight["subject"]
    if subject["kind"] == "control_application":
        application_expected_paths.append((
            "$.subject.control_application_fingerprint",
            subject["control_application_fingerprint"],
        ))
    if "control_application_fingerprint" in bindings:
        application_expected_paths.append((
            "$.bindings.control_application_fingerprint",
            bindings["control_application_fingerprint"],
        ))

    application_fingerprint = (
        validate.control_application_fingerprint(application_document)
        if application_document is not None else None
    )
    for path, expected in application_expected_paths:
        if application_fingerprint is None:
            findings.append(HumanOversightBindingFinding(
                path, _NOT_VERIFIED, expected, None,
                "no ControlApplication was supplied for independent fingerprint derivation",
            ))
        else:
            findings.append(_matched_or_mismatched(
                path, expected, application_fingerprint,
                "receipt value compared with the fingerprint independently "
                "derived from the supplied ControlApplication",
            ))

    if application_document is not None and application_expected_paths:
        findings.append(_matched_or_mismatched(
            "control_application:$.execution_ref",
            execution_ref,
            application_document["execution_ref"],
            "ControlApplication and HumanOversightReceipt must bind the same "
            "canonical Motus execution",
        ))

    return HumanOversightBindingVerdict(
        oversight_fingerprint,
        (),
        tuple(findings),
    )
