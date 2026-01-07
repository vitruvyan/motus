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
from axis.state import Fact, Decision


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

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "facts": [f.to_dict() for f in self.facts],
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Category":
        return cls(
            name=data["name"],
            facts=tuple(Fact.from_dict(f) for f in data.get("facts", [])),
            confidence=data["confidence"],
        )


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

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "relation_type": self.relation_type,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Relation":
        return cls(
            source=Fact.from_dict(data["source"]),
            target=Fact.from_dict(data["target"]),
            relation_type=data["relation_type"],
            confidence=data["confidence"],
        )


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

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Intent":
        return cls(
            description=data["description"],
            evidence=tuple(data.get("evidence", [])),
            confidence=data["confidence"],
        )


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

    def to_dict(self) -> dict:
        return {
            "premise": self.premise.to_dict(),
            "consequence": self.consequence,
            "supporting_facts": [f.to_dict() for f in self.supporting_facts],
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Implication":
        return cls(
            premise=Decision.from_dict(data["premise"]),
            consequence=data["consequence"],
            supporting_facts=tuple(Fact.from_dict(f) for f in data.get("supporting_facts", [])),
            confidence=data["confidence"],
        )


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

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type,
            "elements": list(self.elements),
            "occurrences": self.occurrences,
            "first_seen_trace": self.first_seen_trace,
            "last_seen_trace": self.last_seen_trace,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Pattern":
        return cls(
            pattern_type=data["pattern_type"],
            elements=tuple(data.get("elements", [])),
            occurrences=data["occurrences"],
            first_seen_trace=data["first_seen_trace"],
            last_seen_trace=data["last_seen_trace"],
        )


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

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "constraint_type": self.constraint_type,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Constraint":
        return cls(
            description=data["description"],
            constraint_type=data["constraint_type"],
            scope=data["scope"],
        )


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

    def to_dict(self) -> dict:
        # Handle violating_element: if it has to_dict, use it, else serialize as is
        if hasattr(self.violating_element, 'to_dict'):
            violating_element = self.violating_element.to_dict()
        else:
            violating_element = self.violating_element
        return {
            "constraint": self.constraint.to_dict(),
            "violating_element": violating_element,
            "trace_id": self.trace_id,
            "detected_at": self.detected_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Violation":
        # For violating_element, try to reconstruct if it's a dict with known structure
        violating_element = data["violating_element"]
        if isinstance(violating_element, dict):
            # Try to determine type from keys
            if "key" in violating_element and "value" in violating_element:
                violating_element = Fact.from_dict(violating_element)
            elif "description" in violating_element and "reason" in violating_element:
                violating_element = Rejection.from_dict(violating_element)
            elif "description" in violating_element and "timestamp" in violating_element and "type" not in violating_element:
                violating_element = Decision.from_dict(violating_element)
            # Add more if needed
        return cls(
            constraint=Constraint.from_dict(data["constraint"]),
            violating_element=violating_element,
            trace_id=data["trace_id"],
            detected_at=data["detected_at"],
        )


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

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "categories": [c.to_dict() for c in self.categories],
            "relations": [r.to_dict() for r in self.relations],
            "intents": [i.to_dict() for i in self.intents],
            "implications": [i.to_dict() for i in self.implications],
            "patterns": [p.to_dict() for p in self.patterns],
            "violations": [v.to_dict() for v in self.violations],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EpistemicState":
        return cls(
            trace_id=data["trace_id"],
            categories=tuple(Category.from_dict(c) for c in data.get("categories", [])),
            relations=tuple(Relation.from_dict(r) for r in data.get("relations", [])),
            intents=tuple(Intent.from_dict(i) for i in data.get("intents", [])),
            implications=tuple(Implication.from_dict(i) for i in data.get("implications", [])),
            patterns=tuple(Pattern.from_dict(p) for p in data.get("patterns", [])),
            violations=tuple(Violation.from_dict(v) for v in data.get("violations", [])),
        )
    
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
