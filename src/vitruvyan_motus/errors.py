"""Stable public errors (ADR-001 Decision 2: errors.py owns this alone).

The errors are independent of every Axis type:

``GraphSpecValidationError`` — raised the instant an invalid GraphSpec is
constructed (contract/graphspec.v1.schema.json: "an invalid graph refuses to
exist — it does not start-and-warn"). Carries the violated rule(s) in a
stable, machine-readable form.

``NodeFailed`` is the predecessor-compatible failure-salvage pattern. It
carries the accumulated state and trace. ``SinkFailed`` states invariant II:
a required durable surface may prevent logical success.

Neither type implements retry, routing, cancellation or trace production —
those are runtime semantics, out of scope here (guarantees.md §6 assigns
them to the runner, not to an error class).
"""

from __future__ import annotations

from typing import Any, NamedTuple

__all__ = [
    "MotusError",
    "GraphSpecViolation",
    "GraphSpecValidationError",
    "NodeFailed",
    "NodeConfigurationError",
    "SinkFailed",
    "ReplayError",
    "ReplayMismatch",
    "ReplayUnsupported",
    "UnsafeResume",
    "DeclarationViolation",
]


class MotusError(Exception):
    """Base for every error ``vitruvyan_motus`` raises.

    Catching this catches anything the package itself raised, as distinct
    from an unrelated exception a node or a caller's own code produced.
    """


class GraphSpecViolation(NamedTuple):
    """One broken GraphSpec rule: which rule, where, and why.

    ``rule`` is the stable identifier the contract already uses for this
    class of defect: an R-code (``"R1"``..``"R12"``) for a semantic rule, or
    the literal string ``"SCHEMA"`` for a structural/shape defect that
    precedes any R-rule check (contract/validate.py's own layering: schema
    violations short-circuit before semantic rules run).
    """

    rule: str
    path: str
    message: str


class GraphSpecValidationError(MotusError):
    """Construction-time failure of an invalid GraphSpec.

    ``violations`` is the complete, ordered detail (possibly several entries
    sharing one rule — e.g. two independent trap-region nodes both reported
    under R11). ``rules`` is the deduplicated, stable, machine-readable
    summary: the set of distinct rule codes violated. A caller checking
    "did this fail for R11" tests membership in ``.rules``; a caller
    building a diagnostic reads ``.violations``.
    """

    def __init__(self, violations: tuple[GraphSpecViolation, ...]) -> None:
        if not violations:
            raise ValueError("GraphSpecValidationError requires at least one violation")
        self.violations = violations
        self.rules = frozenset(v.rule for v in violations)
        summary = "; ".join(f"{v.rule} {v.path}: {v.message}" for v in violations)
        count = len(violations)
        plural = "violation" if count == 1 else "violations"
        super().__init__(f"GraphSpec is invalid ({count} {plural}): {summary}")


class NodeFailed(MotusError):
    """A node raised. ``state`` is whatever the runner had accumulated.

    Mirrors the predecessor's failure-salvage pattern exactly at the
    attribute level (``except NodeFailed as exc: salvaged = exc.state``),
    so callers that already know that idiom need to learn nothing new.
    ``state`` is untyped (``Any``) — Milestone B raises no NodeFailed itself
    and defines no State; the type this attribute actually carries is fixed
    by Milestone C2's real construction path, not by this class.
    """

    def __init__(self, node: str, state: Any, trace: Any = None, cause: BaseException | None = None) -> None:
        self.node = node
        self.state = state
        self.trace = trace
        self.cause = cause
        super().__init__(f"node {node!r} failed")


class NodeConfigurationError(MotusError):
    """A node could not provide stable strict-JSON identity material."""

    def __init__(self, node: str, cause: BaseException) -> None:
        self.node = node
        self.cause = cause
        super().__init__(f"node {node!r} configuration identity failed: {cause}")


class SinkFailed(MotusError):
    """A required trace sink refused a record; logical success is impossible."""

    def __init__(self, trace: Any, cause: BaseException) -> None:
        self.trace = trace
        self.cause = cause
        super().__init__(f"required trace sink failed: {cause}")


class ReplayError(MotusError):
    """Base for playback, verification and resume failures."""


class ReplayMismatch(ReplayError):
    """Pure-node verification diverged from the recorded evidence."""

    def __init__(self, node: str, record_seq: int, field: str) -> None:
        self.node = node
        self.record_seq = record_seq
        self.field = field
        super().__init__(
            f"verify replay diverged at node {node!r}, record {record_seq}, field {field}"
        )


class ReplayUnsupported(ReplayError):
    """The replay engine cannot drive this node, and says so distinctly.

    Not a divergence. ``ReplayMismatch`` means the recorded evidence and the
    re-execution disagree — the contract's signal that the code changed. This
    means the engine never got to compare, so a caller can separate "your
    graph changed" from "this engine cannot re-execute an async node".
    """


class UnsafeResume(ReplayError):
    """A trace cannot be resumed without inventing an unsupported guarantee."""


class DeclarationViolation(MotusError):
    """Captured node behavior exceeded its declared read/write surface."""

    def __init__(self, violations: list[dict[str, str]]) -> None:
        self.violations = tuple(dict(item) for item in violations)
        # Name the offending keys in the message, not only in `violations`.
        # The structured field is complete, but a developer reading a traceback
        # in production sees the string — and "some read or write was
        # undeclared" without saying which one costs them the one lookup the
        # runtime already did. ReplayMismatch names its node, record and field
        # for the same reason; this follows it.
        detail = ", ".join(
            f"{v.get('kind', 'violation')} {v['key']!r}" if "key" in v
            else str(v.get("kind", "violation"))
            for v in self.violations
        )
        message = "captured reads or writes violate the node declaration"
        super().__init__(f"{message}: {detail}" if detail else message)
