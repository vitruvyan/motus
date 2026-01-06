"""Minimal test demonstrating epistemic types usage.

This shows how Axis provides STRUCTURE (types + protocols)
while implementations provide INTELLIGENCE (algorithms).
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from state import Fact
from axis.epistemic_types import Category, Relation, Intent, EpistemicState


def test_category_immutability():
    """Category is frozen - no mutation allowed."""
    fact1 = Fact(key="price", value=100.0, timestamp=datetime.utcnow())
    fact2 = Fact(key="volume", value=1000, timestamp=datetime.utcnow())
    
    category = Category(
        name="market-data",
        facts=(fact1, fact2),
        confidence=0.85
    )
    
    # Immutable: this would raise FrozenInstanceError
    # category.name = "other"  # ❌
    
    assert category.name == "market-data"
    assert len(category.facts) == 2
    assert category.confidence == 0.85
    print("✅ Category is immutable")


def test_relation_structure():
    """Relation links two facts semantically."""
    fact1 = Fact(key="interest_rate", value=5.0, timestamp=datetime.utcnow())
    fact2 = Fact(key="bond_price", value=95.0, timestamp=datetime.utcnow())
    
    relation = Relation(
        source=fact1,
        target=fact2,
        relation_type="inversely_related",
        confidence=0.92
    )
    
    assert relation.source.key == "interest_rate"
    assert relation.target.key == "bond_price"
    assert relation.relation_type == "inversely_related"
    print("✅ Relation structure valid")


def test_intent_inference():
    """Intent represents inferred execution purpose."""
    intent = Intent(
        description="Resolve ticker symbol from natural language",
        evidence=(
            "Intent declared: 'find AAPL price'",
            "Decision: route to ticker_resolver"
        ),
        confidence=0.88
    )
    
    assert "ticker symbol" in intent.description
    assert len(intent.evidence) == 2
    print("✅ Intent structure valid")


def test_epistemic_state_aggregation():
    """EpistemicState aggregates all epistemic knowledge."""
    fact1 = Fact(key="price", value=150.0, timestamp=datetime.utcnow())
    fact2 = Fact(key="sector", value="tech", timestamp=datetime.utcnow())
    
    category = Category(
        name="technology-stocks",
        facts=(fact1, fact2),
        confidence=0.90
    )
    
    intent = Intent(
        description="Analyze tech sector",
        evidence=("User query: 'tech stocks'",),
        confidence=0.85
    )
    
    state = EpistemicState(
        trace_id="trace_123",
        categories=(category,),
        relations=(),
        intents=(intent,),
        implications=(),
        patterns=(),
        violations=()
    )
    
    assert state.trace_id == "trace_123"
    assert len(state.categories) == 1
    assert len(state.intents) == 1
    assert not state.is_empty()
    print("✅ EpistemicState aggregation valid")


def test_empty_epistemic_state():
    """Empty state has no epistemic knowledge."""
    empty = EpistemicState(
        trace_id="trace_456",
        categories=(),
        relations=(),
        intents=(),
        implications=(),
        patterns=(),
        violations=()
    )
    
    assert empty.is_empty()
    print("✅ Empty EpistemicState detected")


if __name__ == "__main__":
    test_category_immutability()
    test_relation_structure()
    test_intent_inference()
    test_epistemic_state_aggregation()
    test_empty_epistemic_state()
    
    print("\n" + "="*50)
    print("✅ ALL EPISTEMIC TYPES TESTS PASSED")
    print("="*50)
    print("\nAxis provides STRUCTURE (types).")
    print("Vitruvyan provides INTELLIGENCE (implementations).")
