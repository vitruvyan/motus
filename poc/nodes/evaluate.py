from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from state import GraphState, Decision, Rejection


def evaluate(state: GraphState) -> GraphState:
    """Decide whether LLM is needed based on input complexity."""
    
    complexity_facts = [f for f in state.facts if f.key == "input_complexity"]
    if not complexity_facts:
        # Default: needs LLM if we can't determine
        state = state.with_decision(Decision(
            description="needs_llm_default",
            timestamp=datetime.utcnow()
        ))
        return state
    
    complexity = complexity_facts[0].value
    
    if complexity == "trivial":
        # Simple input: reject LLM usage
        state = state.with_rejection(Rejection(
            description="llm_not_needed",
            reason="simple operation can be handled directly",
            timestamp=datetime.utcnow()
        ))
    else:
        # Complex input: decide to use LLM
        state = state.with_decision(Decision(
            description="needs_llm",
            timestamp=datetime.utcnow()
        ))
    
    return state
