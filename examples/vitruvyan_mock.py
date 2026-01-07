"""Example: How Vitruvyan implements Axis epistemic protocols.

This demonstrates the separation:
- Axis provides TYPES + PROTOCOLS (substrate)
- Vitruvyan provides IMPLEMENTATIONS (intelligence)

This is a MOCK example (no real Qdrant/OpenAI integration).
Real Vitruvyan uses actual infrastructure.
"""

import sys
from pathlib import Path

from datetime import datetime
from typing import Optional
from axis.state import GraphState, Fact, Decision
from axis.epistemic_types import Category, Relation, Intent, Implication
from axis.epistemic_protocols import OntologyProvider, SemanticInterpreter


# Mock implementation (Vitruvyan would use real Qdrant + GPT-4o-mini)

class VitruvyanOntology:
    """Mock Vitruvyan ontology implementation.
    
    Real implementation uses:
    - Qdrant for vector search
    - MiniLM-L6-v2 for embeddings (384-dim)
    - GPT-4o-mini for classification
    """
    
    def categorize(self, facts: tuple[Fact, ...]) -> tuple[Category, ...]:
        """Categorize facts using mock logic.
        
        Real Vitruvyan:
        1. Compute embeddings with MiniLM
        2. Vector search in Qdrant
        3. LLM classification for sector/region/concept
        """
        if not facts:
            return ()
        
        # Mock: Simple keyword-based categorization
        categories = []
        
        # Group by key prefix (mock clustering)
        price_facts = tuple(f for f in facts if "price" in f.key.lower())
        if price_facts:
            categories.append(Category(
                name="market-pricing",
                facts=price_facts,
                confidence=0.85
            ))
        
        volume_facts = tuple(f for f in facts if "volume" in f.key.lower())
        if volume_facts:
            categories.append(Category(
                name="trading-volume",
                facts=volume_facts,
                confidence=0.82
            ))
        
        return tuple(categories)
    
    def relate(self, facts: tuple[Fact, ...]) -> tuple[Relation, ...]:
        """Find relations between facts using mock logic.
        
        Real Vitruvyan:
        1. Embedding similarity (cosine distance)
        2. Temporal proximity analysis
        3. Domain-specific rules (finance correlations)
        """
        relations = []
        
        # Mock: Find price-volume pairs
        for f1 in facts:
            if "price" in f1.key.lower():
                for f2 in facts:
                    if "volume" in f2.key.lower() and f1 != f2:
                        relations.append(Relation(
                            source=f1,
                            target=f2,
                            relation_type="typically_correlated",
                            confidence=0.78
                        ))
        
        return tuple(relations[:3])  # Limit to 3 for demo


class VitruvyanSemantics:
    """Mock Vitruvyan semantic interpreter.
    
    Real implementation uses:
    - MiniLM for semantic embeddings
    - FinBERT for sentiment analysis
    - GPT-4o-mini for intent inference
    """
    
    def infer_intent(self, state: GraphState) -> Optional[Intent]:
        """Infer execution intent from trace.
        
        Real Vitruvyan:
        1. Analyze event sequence patterns
        2. LLM-based intent classification
        3. Confidence scoring via ensemble
        """
        if not state.intent:
            return None
        
        # Mock: Simple intent parsing
        evidence = [f"Declared intent: {state.intent}"]
        
        if state.decisions:
            evidence.append(f"Made {len(state.decisions)} routing decisions")
        
        return Intent(
            description=f"Execute workflow: {state.intent}",
            evidence=tuple(evidence),
            confidence=0.88
        )
    
    def derive_implications(
        self,
        decision: Decision,
        facts: tuple[Fact, ...]
    ) -> tuple[Implication, ...]:
        """Derive logical consequences of decision.
        
        Real Vitruvyan:
        1. LLM-based reasoning (GPT-4o-mini)
        2. Domain-specific inference rules (finance)
        3. Fact-checking against knowledge base
        """
        implications = []
        
        # Mock: Simple rule-based implications
        if "resolve" in decision.description.lower():
            relevant_facts = tuple(f for f in facts if "ticker" in f.key.lower() or "stock" in f.key.lower())
            if relevant_facts:
                implications.append(Implication(
                    premise=decision,
                    consequence="Ticker symbol resolved, can proceed to data fetch",
                    supporting_facts=relevant_facts,
                    confidence=0.90
                ))
        
        return tuple(implications)


# Demonstration

def demo_vitruvyan_implementations():
    """Show how Vitruvyan implements Axis protocols."""
    
    print("="*60)
    print("DEMO: Vitruvyan Implementations of Axis Protocols")
    print("="*60)
    
    # Create mock facts
    facts = (
        Fact(key="stock_price_AAPL", value=150.0, timestamp=datetime.utcnow()),
        Fact(key="trading_volume_AAPL", value=50000000, timestamp=datetime.utcnow()),
        Fact(key="stock_price_MSFT", value=300.0, timestamp=datetime.utcnow()),
    )
    
    # Test OntologyProvider
    print("\n1. OntologyProvider (categorize + relate)")
    print("-" * 60)
    
    ontology = VitruvyanOntology()
    categories = ontology.categorize(facts)
    
    print(f"Found {len(categories)} categories:")
    for cat in categories:
        print(f"  - {cat.name} ({len(cat.facts)} facts, confidence={cat.confidence:.2f})")
    
    relations = ontology.relate(facts)
    print(f"\nFound {len(relations)} relations:")
    for rel in relations:
        print(f"  - {rel.source.key} --[{rel.relation_type}]--> {rel.target.key}")
    
    # Test SemanticInterpreter
    print("\n2. SemanticInterpreter (infer_intent + derive_implications)")
    print("-" * 60)
    
    state = GraphState.empty("trace_demo").with_intent("Analyze AAPL stock performance")
    for f in facts:
        state = state.with_fact(f)
    
    decision = Decision(
        description="ticker_resolver: Resolved AAPL to Apple Inc.",
        timestamp=datetime.utcnow()
    )
    state = state.with_decision(decision)
    
    semantics = VitruvyanSemantics()
    intent = semantics.infer_intent(state)
    
    if intent:
        print(f"Inferred intent: {intent.description}")
        print(f"  Evidence: {', '.join(intent.evidence)}")
        print(f"  Confidence: {intent.confidence:.2f}")
    
    implications = semantics.derive_implications(decision, facts)
    print(f"\nDerived {len(implications)} implications:")
    for imp in implications:
        print(f"  - {imp.consequence}")
        print(f"    (based on {len(imp.supporting_facts)} supporting facts)")
    
    print("\n" + "="*60)
    print("KEY INSIGHT:")
    print("="*60)
    print("✅ Axis provides TYPES (Category, Relation, Intent, Implication)")
    print("✅ Axis provides PROTOCOLS (OntologyProvider, SemanticInterpreter)")
    print("✅ Vitruvyan provides IMPLEMENTATIONS (algorithms, infrastructure)")
    print("\n🎯 Separation is clean. Boundary is clear. Compliance is architectural.")


if __name__ == "__main__":
    demo_vitruvyan_implementations()
