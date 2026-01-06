from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from state import GraphState, Fact


def parse_input(state: GraphState) -> GraphState:
    """Parse input and extract basic characteristics."""
    # In a real system, this would analyze the input
    # For now, we extract from a fact that should be set by the orchestrator
    
    input_facts = [f for f in state.facts if f.key == "user_input"]
    if not input_facts:
        return state
    
    user_input = input_facts[0].value
    
    # Simple heuristic: short inputs are likely simple
    is_simple = len(str(user_input).split()) <= 5 and any(
        op in str(user_input).lower() 
        for op in ['+', '-', '*', '/', 'what is', 'how much']
    )
    
    complexity = "trivial" if is_simple else "high"
    
    state = state.with_fact(Fact(
        key="input_complexity",
        value=complexity,
        timestamp=datetime.utcnow()
    ))
    
    return state
