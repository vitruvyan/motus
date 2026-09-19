"""Transport-neutral access to Motus evidence for bridges and other consumers.

ADR-034 keeps ownership of evidence inside Motus.  This module is therefore a
thin read-only boundary over artifacts Motus already knows how to produce and
verify; it is not an HTTP API and it contains no product-specific vocabulary.
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Callable, Iterable, Protocol

from vitruvyan_motus._execution_ref import (parse_execution_ref,
                                              receipt_segment_for_execution_ref)
from vitruvyan_motus.evidence import (_identity_documents_from_package,
                                      PackageVerdict, pack, verify_package)

if TYPE_CHECKING:
    from vitruvyan_motus.commitlog import CommitmentLog
    from vitruvyan_motus.commitments import AnchorReceipt, Attestation
    from vitruvyan_motus.replay import TraceBundle

__all__ = ["EvidenceAPI", "EvidenceSource", "LiveEvidenceSource"]


class EvidenceSource(Protocol):
    """Storage-neutral source of already-produced Motus evidence.

    A source may be backed by a live process, a database, object storage, or
    another persistence layer.  The consumer-facing API does not get to know.
    """

    def receipt_for(self, execution_ref: str) -> dict[str, Any]:
        """Return the Motus receipt for one ADR-027 execution reference."""
        ...

    def package_for(self, execution_ref: str) -> bytes:
        """Return an evidence package for one ADR-027 execution reference."""
        ...


def _canonical_ref(value: object) -> str:
    tenant, writer_id, sequence = parse_execution_ref(value)
    return f"{tenant}/{writer_id}/{sequence}"

def _receipt_is_contract_valid(receipt: dict[str, Any]) -> bool:
    """Use the shipped receipt validator before trusting identity fields."""
    # Deliberately lazy: importing the kernel must not pull jsonschema.
    from vitruvyan_motus.contract.validate import validate_receipt

    try:
        return not validate_receipt(receipt)
    except Exception:
        # The Evidence API is a hostile-storage boundary. A validator failure
        # makes the receipt unusable for identity binding, never trustworthy.
        return False


def _receipt_binds(receipt: dict[str, Any], ref: str) -> bool:
    """One predicate for the ADR-027 coordinate carried by receipt segments."""
    return receipt_segment_for_execution_ref(receipt, ref) is not None


def _identity_binding_issue(
    manifest: dict[str, Any], receipt: dict[str, Any], ref: str,
) -> str | None:
    """Return why readable, contract-valid evidence cannot bind ``ref``."""
    if not _receipt_binds(receipt, ref):
        return f"receipt does not contain requested execution_ref {ref!r}"

    manifest_execution = manifest.get("execution")
    receipt_execution = receipt.get("execution")
    if not isinstance(manifest_execution, dict) or not isinstance(receipt_execution, dict):
        return "manifest/receipt do not carry canonical execution identity"
    if manifest_execution != receipt_execution:
        return "manifest execution identity disagrees with receipt execution identity"
    return None


class EvidenceAPI:
    """Canonical read-only boundary consumed by Motus bridges.

    Receipt retrieval is not verification.  ``verify`` always executes the
    shipped package verifier over the package bytes returned by the source.
    """

    def __init__(self, source: EvidenceSource) -> None:
        self._source = source

    def receipt_for(self, execution_ref: str) -> dict[str, Any]:
        ref = _canonical_ref(execution_ref)
        receipt = self._source.receipt_for(ref)
        if not isinstance(receipt, dict):
            raise TypeError("evidence source receipt_for must return dict")
        if not _receipt_is_contract_valid(receipt):
            raise ValueError("evidence source returned a schema-invalid receipt")
        if not _receipt_binds(receipt, ref):
            raise ValueError(
                "evidence source returned a receipt that does not bind the "
                f"requested execution_ref {ref!r}")
        # A bridge may reshape its own copy for presentation. It must never be
        # able to mutate a source-owned cached receipt by accident.
        return copy.deepcopy(receipt)

    def _bound_package(self, ref: str, package: bytes) -> bytes:
        """Strict retrieval: return only a usable, identity-bound artifact."""
        if not isinstance(package, bytes):
            raise TypeError("evidence source package_for must return bytes")
        manifest, receipt = _identity_documents_from_package(package)
        if not _receipt_is_contract_valid(receipt):
            raise ValueError("evidence source package has a schema-invalid receipt")
        issue = _identity_binding_issue(manifest, receipt, ref)
        if issue is not None:
            raise ValueError(f"evidence source returned an unbound package: {issue}")
        return package

    def package_for(self, execution_ref: str) -> bytes:
        ref = _canonical_ref(execution_ref)
        return self._bound_package(ref, self._source.package_for(ref))

    def verify(
        self, execution_ref: str, *, package: bytes | None = None,
    ) -> PackageVerdict:
        """Verify exactly the supplied bytes, preserving fail-closed results.

        Malformed or schema-invalid stored evidence is verifier input, so the
        shipped ``verify_package`` result is returned rather than converted
        into an application exception. A readable, contract-valid package for
        a *different* execution is source substitution and is refused before
        a bridge can associate that verdict with the requested execution.
        """
        ref = _canonical_ref(execution_ref)
        raw = package if package is not None else self._source.package_for(ref)
        if not isinstance(raw, bytes):
            raise TypeError("evidence source package_for must return bytes")

        try:
            manifest, receipt = _identity_documents_from_package(raw)
        except ValueError:
            return verify_package(raw)

        if not _receipt_is_contract_valid(receipt):
            return verify_package(raw)

        issue = _identity_binding_issue(manifest, receipt, ref)
        if issue is not None:
            raise ValueError(f"evidence source returned an unbound package: {issue}")

        # Run the shipped verifier exactly once, over the bytes the bridge has.
        return verify_package(raw)

class LiveEvidenceSource:
    """Reference source for an embedder that owns a live CommitmentLog.

    This adapter is intentionally small: ``receipt_for`` delegates to the
    commitment log and ``package_for`` delegates to ``pack``. Optional provider
    callbacks carry already-produced anchors and attestations into that package
    without teaching the Evidence API how they were obtained. A deployment
    that persists receipts/packages elsewhere implements ``EvidenceSource``
    instead; the Evidence API does not change.
    """

    def __init__(
        self,
        log: "CommitmentLog",
        bundle_for: Callable[[str], "TraceBundle"],
        *,
        anchors_for: Callable[[str], Iterable["AnchorReceipt"]] | None = None,
        attestations_for: Callable[[str], Iterable["Attestation"]] | None = None,
    ) -> None:
        self._log = log
        self._bundle_for = bundle_for
        self._anchors_for = anchors_for
        self._attestations_for = attestations_for

    def receipt_for(self, execution_ref: str) -> dict[str, Any]:
        ref = _canonical_ref(execution_ref)
        return self._log.receipt_for(ref)

    def package_for(self, execution_ref: str) -> bytes:
        ref = _canonical_ref(execution_ref)
        bundle = self._bundle_for(ref)
        anchors = (() if self._anchors_for is None
                   else tuple(self._anchors_for(ref)))
        attestations = (() if self._attestations_for is None
                        else tuple(self._attestations_for(ref)))
        return pack(
            bundle, log=self._log, execution_ref=ref,
            anchors=anchors, attestations=attestations,
        )
