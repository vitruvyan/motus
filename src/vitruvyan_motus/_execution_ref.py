"""The one parser for ADR-027 execution references.

An execution reference is a location, not a proof:
``<tenant>/<writer_id>/<sequence>``. Keeping the parser here prevents the
consumer-facing Evidence API and the commitment log from drifting into two
slightly different ideas of the same key.
"""
from __future__ import annotations

from typing import Any

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

def receipt_segment_for_execution_ref(
    receipt: object, execution_ref: object,
) -> dict[str, Any] | None:
    """Return the receipt segment whose BEGIN is ``execution_ref``.

    A resumed receipt may be requested by any included BEGIN while its
    top-level ``execution.ref`` names the original BEGIN.  Binding consumers
    against the segment, rather than only the top-level field, preserves that
    semantics and still refuses substitution with another execution.
    """
    tenant, writer_id, sequence = parse_execution_ref(execution_ref)
    if not isinstance(receipt, dict):
        return None
    segments = receipt.get("segments")
    if not isinstance(segments, list):
        return None
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        begin = segment.get("begin")
        if not isinstance(begin, dict):
            continue
        commitment = begin.get("commitment")
        if not isinstance(commitment, dict):
            continue
        if (
            commitment.get("kind") == "begin"
            and commitment.get("tenant") == tenant
            and commitment.get("writer_id") == writer_id
            and commitment.get("sequence") == sequence
        ):
            return segment
    return None
