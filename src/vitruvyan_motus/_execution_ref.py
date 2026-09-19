"""The one parser for ADR-027 execution references.

An execution reference is a location, not a proof:
``<tenant>/<writer_id>/<sequence>``. Keeping the parser here prevents the
consumer-facing Evidence API and the commitment log from drifting into two
slightly different ideas of the same key.
"""
from __future__ import annotations

__all__ = ["parse_execution_ref"]


def parse_execution_ref(execution_ref: object) -> tuple[str, str, int]:
    """Parse one canonical ADR-027 execution reference.

    The grammar intentionally matches the public ``CommitmentLog.receipt_for``
    boundary that predates this helper. In particular, the sequence is a
    canonical decimal integer: ``01`` is refused rather than silently
    normalised to ``1``.
    """
    parts = execution_ref.split("/") if isinstance(execution_ref, str) else []
    malformed = (
        len(parts) != 3
        or any(not part for part in parts)
        or not parts[2].isdigit()
    )
    if malformed:
        raise ValueError(f"invalid execution reference {execution_ref!r}")

    tenant, writer_id, raw_sequence = parts
    try:
        sequence = int(raw_sequence)
    except ValueError:
        raise ValueError(
            f"invalid execution reference {execution_ref!r}"
        ) from None
    if str(sequence) != raw_sequence:
        raise ValueError(f"invalid execution reference {execution_ref!r}")
    return tenant, writer_id, sequence
