"""The one parser for ADR-027 execution references.

An execution reference is a location, not a proof:
``<tenant>/<writer_id>/<sequence>``. Keeping the parser here prevents the
consumer-facing Evidence API and the commitment log from drifting into two
slightly different ideas of the same key.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "parse_execution_ref",
    "receipt_execution_issue",
    "receipt_segment_for_execution_ref",
    "receipt_original_segment",
    "receipt_terminal_segment",
]


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
        or any(not part.strip() for part in parts)
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


def _receipt_segments(receipt: object) -> list[object] | None:
    """Return the receipt segment list without interpreting its contents."""
    if not isinstance(receipt, dict):
        return None
    segments = receipt.get("segments")
    return segments if isinstance(segments, list) else None


def receipt_original_segment(receipt: object) -> dict[str, Any] | None:
    """Return the first receipt segment when it has the expected object shape."""
    segments = _receipt_segments(receipt)
    if not segments or not isinstance(segments[0], dict):
        return None
    return segments[0]


def receipt_terminal_segment(receipt: object) -> dict[str, Any] | None:
    """Return the last receipt segment when it has the expected object shape."""
    segments = _receipt_segments(receipt)
    if not segments or not isinstance(segments[-1], dict):
        return None
    return segments[-1]


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
    segments = _receipt_segments(receipt)
    if segments is None:
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


def receipt_execution_issue(receipt: dict[str, Any]) -> str | None:
    """Return why the receipt's derived execution identity is inconsistent."""
    execution = receipt.get("execution")
    if not isinstance(execution, dict):
        return "receipt does not carry canonical execution identity"

    original = receipt_original_segment(receipt)
    terminal = receipt_terminal_segment(receipt)
    if original is None or terminal is None:
        return "receipt has no execution segments"

    first = original.get("begin")
    first_commitment = (
        first.get("commitment") if isinstance(first, dict) else None
    )
    if not isinstance(first_commitment, dict):
        return "receipt segments do not carry canonical commitments"

    tenant = first_commitment.get("tenant")
    writer_id = first_commitment.get("writer_id")
    sequence = first_commitment.get("sequence")
    if (
        not isinstance(tenant, str)
        or not isinstance(writer_id, str)
        or type(sequence) is not int
    ):
        return "receipt original BEGIN has malformed execution identity"
    expected_ref = f"{tenant}/{writer_id}/{sequence}"
    if execution.get("ref") != expected_ref:
        return "receipt execution.ref disagrees with its original BEGIN"
    if execution.get("run_id") != first_commitment.get("run_id"):
        return "receipt execution.run_id disagrees with its original BEGIN"

    terminal_end = terminal.get("end")
    if terminal_end is None:
        expected_fingerprint = None
    else:
        terminal_commitment = (
            terminal_end.get("commitment")
            if isinstance(terminal_end, dict) else None
        )
        if not isinstance(terminal_commitment, dict):
            return "receipt terminal END has no canonical commitment"
        expected_fingerprint = terminal_commitment.get("root")
    if execution.get("fingerprint") != expected_fingerprint:
        return "receipt execution.fingerprint disagrees with its terminal END"
    return None
