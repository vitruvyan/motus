"""Transport-neutral access to Motus evidence for bridges and other consumers.

ADR-034 keeps ownership of evidence inside Motus.  This module is therefore a
thin read-only boundary over artifacts Motus already knows how to produce and
verify; it is not an HTTP API and it contains no product-specific vocabulary.
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, Callable, Protocol

from vitruvyan_motus._execution_ref import (parse_execution_ref,
                                              receipt_segment_for_execution_ref)
from vitruvyan_motus.evidence import (_receipt_from_package, PackageVerdict,
                                      pack, verify_package)

if TYPE_CHECKING:
    from vitruvyan_motus.commitlog import CommitmentLog
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
        if receipt_segment_for_execution_ref(receipt, ref) is None:
            raise ValueError(
                "evidence source returned a receipt that does not bind the "
                f"requested execution_ref {ref!r}")
        # A bridge may reshape its own copy for presentation. It must never be
        # able to mutate a source-owned cached receipt by accident.
        return copy.deepcopy(receipt)

    def _bound_package(self, ref: str, package: bytes) -> bytes:
        if not isinstance(package, bytes):
            raise TypeError("evidence source package_for must return bytes")
        receipt = _receipt_from_package(package)
        if receipt_segment_for_execution_ref(receipt, ref) is None:
            raise ValueError(
                "evidence source returned a package that does not bind the "
                f"requested execution_ref {ref!r}")
        return package

    def package_for(self, execution_ref: str) -> bytes:
        ref = _canonical_ref(execution_ref)
        return self._bound_package(ref, self._source.package_for(ref))

    def verify(
        self, execution_ref: str, *, package: bytes | None = None,
    ) -> PackageVerdict:
        """Run the real Motus verifier over the exact package bytes supplied.

        If ``package`` is omitted the source is fetched once. A bridge that has
        already retrieved bytes should pass them here so the displayed artifact
        and the verified artifact are necessarily the same object.
        """
        ref = _canonical_ref(execution_ref)
        bound = (self._bound_package(ref, package) if package is not None
                 else self.package_for(ref))
        return verify_package(bound)


class LiveEvidenceSource:
    """Reference source for an embedder that owns a live CommitmentLog.

    This adapter is intentionally small: ``receipt_for`` delegates to the
    commitment log and ``package_for`` delegates to ``pack``.  A deployment
    that persists receipts/packages elsewhere implements ``EvidenceSource``
    instead; the Evidence API does not change.
    """

    def __init__(
        self,
        log: "CommitmentLog",
        bundle_for: Callable[[str], "TraceBundle"],
    ) -> None:
        self._log = log
        self._bundle_for = bundle_for

    def receipt_for(self, execution_ref: str) -> dict[str, Any]:
        ref = _canonical_ref(execution_ref)
        return self._log.receipt_for(ref)

    def package_for(self, execution_ref: str) -> bytes:
        ref = _canonical_ref(execution_ref)
        bundle = self._bundle_for(ref)
        return pack(bundle, log=self._log, execution_ref=ref)
