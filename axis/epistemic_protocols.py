"""Protocols for epistemic reasoning capabilities.

This module defines INTERFACES for Orders that interpret execution.
NO implementations - only method signatures.

Vitruvyan Sacred Orders implement these protocols with:
- Algorithms (clustering, inference, validation)
- Infrastructure (Qdrant, PostgreSQL, Redis)
- Domain logic (finance-specific)

Axis provides STRUCTURE. Vitruvyan provides INTELLIGENCE.
"""

from typing import Protocol, Optional
import sys
from pathlib import Path

# Add parent directory to path to import core modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from axis.state import GraphState, Fact, Decision
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


class OntologyProvider(Protocol):
    """Organizes facts into emergent categories and relations.
    
    Implementations decide HOW to categorize (clustering, embeddings, LLM, rules).
    Axis only defines WHAT is produced (Category, Relation types).
    
    Example implementations:
    - Vitruvyan: Qdrant vector search + GPT-4o-mini classification
    - Research: Topic modeling (LDA, NMF)
    - Simple: Rule-based keyword matching
    """
    
    def categorize(self, facts: tuple[Fact, ...]) -> tuple[Category, ...]:
        """Group facts into emergent categories.
        
        Returns empty tuple if no categories discovered.
        """
        ...
    
    def relate(self, facts: tuple[Fact, ...]) -> tuple[Relation, ...]:
        """Discover relationships between facts.
        
        Returns empty tuple if no relations found.
        """
        ...


class SemanticInterpreter(Protocol):
    """Derives implicit meaning from explicit execution events.
    
    Implementations decide HOW to infer (pattern matching, LLM, symbolic logic).
    Axis only defines WHAT is inferred (Intent, Implication types).
    
    Example implementations:
    - Vitruvyan: MiniLM embeddings + FinBERT sentiment
    - Research: Symbolic reasoning (Prolog-style)
    - Simple: Template-based inference
    """
    
    def infer_intent(self, state: GraphState) -> Optional[Intent]:
        """Deduce execution purpose from trace.
        
        Returns None if intent unclear.
        """
        ...
    
    def derive_implications(
        self,
        decision: Decision,
        facts: tuple[Fact, ...]
    ) -> tuple[Implication, ...]:
        """Find logical consequences of a decision.
        
        Returns empty tuple if no implications derived.
        """
        ...


class PatternDetector(Protocol):
    """Identifies recurring structures in execution traces.
    
    Implementations decide HOW to detect (sequence mining, neural nets, regex).
    Axis only defines WHAT is detected (Pattern type).
    
    Example implementations:
    - Vitruvyan: Custom pattern matching on trace structure
    - Research: Sequential pattern mining (PrefixSpan)
    - Simple: Exact sequence matching
    """
    
    def detect_patterns(
        self,
        current_state: GraphState,
        historical_states: tuple[GraphState, ...]
    ) -> tuple[Pattern, ...]:
        """Find recurring execution patterns.
        
        historical_states are previous traces for comparison.
        Returns empty tuple if no patterns found.
        """
        ...


class ConstraintChecker(Protocol):
    """Validates epistemic consistency of trace.
    
    Implementations decide WHAT constraints to check (domain-specific invariants).
    Axis only defines HOW violations are reported (Violation type).
    
    Example implementations:
    - Vitruvyan: Pandas DataFrame schema validation + financial rules
    - Research: First-order logic constraints
    - Simple: Assertion-based checks
    """
    
    def check_consistency(self, state: GraphState) -> tuple[Violation, ...]:
        """Verify epistemic invariants hold.
        
        Returns empty tuple if no violations.
        Does NOT halt execution (observation is post-hoc).
        """
        ...
    
    def get_constraints(self) -> tuple[Constraint, ...]:
        """Return constraints this checker validates.
        
        For introspection/documentation.
        """
        ...


class EpistemicMemory(Protocol):
    """Manages Secondary Memory for epistemic state.
    
    Implementations decide WHERE to store (PostgreSQL, files, in-memory).
    Axis only defines WHAT is stored (EpistemicState type).
    
    Example implementations:
    - Vitruvyan: PostgreSQL + Qdrant for vector search
    - Research: RDF triple store
    - Simple: JSON files or in-memory dict
    """
    
    def persist(self, trace_id: str, state: EpistemicState) -> None:
        """Store epistemic knowledge for a trace.
        
        Overwrites existing state for trace_id.
        """
        ...
    
    def retrieve(self, trace_id: str) -> Optional[EpistemicState]:
        """Load epistemic knowledge for a trace.
        
        Returns None if trace_id not found.
        """
        ...
    
    def query_patterns(self, pattern_type: str) -> tuple[Pattern, ...]:
        """Search for patterns across all traces.
        
        Returns empty tuple if no matches.
        """
        ...
    
    def query_violations(self, constraint_type: str) -> tuple[Violation, ...]:
        """Search for violations across all traces.
        
        Returns empty tuple if no matches.
        """
        ...
