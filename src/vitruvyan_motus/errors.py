"""Stable public errors (ADR-001 §Decision 2: errors.py owns this alone).

Two error types, both usable the moment they're imported, both independent
of any Axis type:

``GraphSpecValidationError`` — raised the instant an invalid GraphSpec is
constructed (contract/graphspec.v1.schema.json: "an invalid graph refuses to
exist — it does not start-and-warn"). Carries the violated rule(s) in a
stable, machine-readable form.

``NodeFailed`` — the Motus equivalent of the predecessor's failure-salvage
error. Its contract (constructor shape, the ``.state`` attribute) is fixed
now so that later milestones can start passing real ``State`` instances
through it without any change here; ``state`` is deliberately untyped
because no ``State`` type exists yet (Milestone C2), and this module does
not scaffold one to get out of that.

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

    def __init__(self, node: str, state: Any) -> None:
        self.node = node
        self.state = state
        super().__init__(f"node {node!r} failed")
