"""Epistemic types for knowledge organization and interpretation.

This module defines immutable data structures for epistemic reasoning.
These are SUBSTRATE only - no implementations, no logic, no algorithms.

Distilled from Vitruvyan Sacred Orders:
- Category, Relation (from Pattern Weavers, Codex Hunters)
- Intent, Implication (from Babel Gardens, Pattern Weavers)
- Pattern (from Pattern Weavers)
- Constraint, Violation (from Orthodoxy Wardens)
"""

from dataclasses import dataclass
from typing import Any, Optional
import sys
from pathlib import Path

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from state import Fact, Decision


# Knowledge Organization (Pattern Weavers, Codex Hunters)

@dataclass(frozen=True)
class Category:
    """Immutable grouping of facts by emergent concept.
    
    Discovered during observation, not predefined.
    Domain-agnostic: no finance-specific terminology.
    """
    name: str
    facts: tuple[Fact, ...]
    confidence: float  # 0.0-1.0


@dataclass(frozen=True)
class Relation:
    """Immutable link between two facts.
    
    Semantic connection discovered during interpretation.
    relation_type is symbolic (e.g., "implies", "contradicts", "supports").
    """
    source: Fact
    target: Fact
    relation_type: str
    confidence: float  # 0.0-1.0


# Semantic Interpretation (Babel Gardens, Pattern Weavers)

@dataclass(frozen=True)
class Intent:
    """Inferred purpose of execution sequence.
    
    Derived from event patterns, not declared.
    Represents WHAT the system was trying to achieve.
    """
    description: str
    evidence: tuple[str, ...]  # Event descriptions that support this intent
    confidence: float  # 0.0-1.0


@dataclass(frozen=True)
class Implication:
    """Logical consequence derived from a decision.
    
    Represents knowledge that follows from decisions + facts.
    NOT executed, only inferred.
    """
    premise: Decision
    consequence: str
    supporting_facts: tuple[Fact, ...]
    confidence: float  # 0.0-1.0


@dataclass(frozen=True)
class Pattern:
    """Recurring execution structure detected in trace.
    
    Represents REPEATING sequences of events/decisions.
    Domain-agnostic: pattern structure, not domain semantics.
    """
    pattern_type: str  # e.g., "sequential", "branching", "cyclical"
    elements: tuple[str, ...]  # Element identifiers (node names, decision types)
    occurrences: int
    first_seen_trace: str
    last_seen_trace: str


# Epistemic Validation (Orthodoxy Wardens)

@dataclass(frozen=True)
class Constraint:
    """Epistemic invariant that facts/decisions should satisfy.
    
    Symbolic representation, not executable predicate.
    Discovered or declared, but NOT enforced by Axis.
    """
    description: str
    constraint_type: str  # e.g., "consistency", "completeness", "coherence"
    scope: str  # What this applies to: "facts", "decisions", "trace"


@dataclass(frozen=True)
class Violation:
    """Record of constraint violation detected during observation.
    
    Does NOT prevent execution (observation is post-hoc).
    Records inconsistency for interpretation/audit.
    """
    constraint: Constraint
    violating_element: Any  # Fact, Decision, or other trace element
    trace_id: str
    detected_at: str  # ISO 8601 timestamp


# Composite Epistemic State (for Secondary Memory)

@dataclass(frozen=True)
class EpistemicState:
    """Snapshot of Order's interpreted knowledge.
    
    Aggregates all epistemic structures for a trace.
    Stored in Secondary Memory, NOT in Primary (GraphState).
    """
    trace_id: str
    categories: tuple[Category, ...]
    relations: tuple[Relation, ...]
    intents: tuple[Intent, ...]
    implications: tuple[Implication, ...]
    patterns: tuple[Pattern, ...]
    violations: tuple[Violation, ...]
    
    def is_empty(self) -> bool:
        """Check if no epistemic knowledge was derived."""
        return (
            len(self.categories) == 0
            and len(self.relations) == 0
            and len(self.intents) == 0
            and len(self.implications) == 0
            and len(self.patterns) == 0
            and len(self.violations) == 0
        )
