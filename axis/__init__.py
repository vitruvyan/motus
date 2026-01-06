"""Axis epistemic types and protocols.

This submodule provides foundational types and interfaces for epistemic reasoning.
These are SUBSTRATE ONLY - no implementations.

Types (immutable structures):
    Category, Relation, Intent, Implication, Pattern, Constraint, Violation, EpistemicState

Protocols (empty interfaces):
    OntologyProvider, SemanticInterpreter, PatternDetector, ConstraintChecker, EpistemicMemory
"""

from axis.epistemic_types import (
    Category,
    Relation,
    Intent,
    Implication,
    Pattern,
    Constraint,
    Violation,
    EpistemicState,
)

from axis.epistemic_protocols import (
    OntologyProvider,
    SemanticInterpreter,
    PatternDetector,
    ConstraintChecker,
    EpistemicMemory,
)

__all__ = [
    # Types
    "Category",
    "Relation",
    "Intent",
    "Implication",
    "Pattern",
    "Constraint",
    "Violation",
    "EpistemicState",
    # Protocols
    "OntologyProvider",
    "SemanticInterpreter",
    "PatternDetector",
    "ConstraintChecker",
    "EpistemicMemory",
]
