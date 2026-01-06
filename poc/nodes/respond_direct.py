from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from state import GraphState, Fact


def respond_direct(state: GraphState) -> GraphState:
    """Provide direct response without LLM."""
    
    # Check if LLM was rejected
    llm_rejected = any(
        r.description == "llm_not_needed" 
        for r in state.rejections
    )
    
    if not llm_rejected:
        # Skip if we used LLM
        return state
    
    input_facts = [f for f in state.facts if f.key == "user_input"]
    if not input_facts:
        return state
    
    user_input = str(input_facts[0].value).lower()
    
    # Simple direct responses
    if "2+2" in user_input or "2 + 2" in user_input:
        response = "4"
    elif "what is" in user_input and "+" in user_input:
        response = "I can calculate that: the answer is in the expression"
    else:
        response = "Simple query handled directly"
    
    state = state.with_fact(Fact(
        key="direct_response",
        value=response,
        timestamp=datetime.utcnow()
    ))
    
    return state
