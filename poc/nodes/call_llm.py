from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from state import GraphState, Fact, Decision


def call_llm(state: GraphState) -> GraphState:
    """Call LLM to generate response (mocked for PoC)."""
    
    # Check if we should actually call LLM
    needs_llm = any(d.description == "needs_llm" for d in state.decisions)
    if not needs_llm:
        # Skip if not needed
        return state
    
    input_facts = [f for f in state.facts if f.key == "user_input"]
    if not input_facts:
        return state
    
    user_input = input_facts[0].value
    
    # Mock LLM call (in real system, this would call OpenAI/Anthropic/etc)
    mock_response = f"[LLM Response] This is a generated explanation for: {user_input}"
    
    # Record that we called the LLM
    state = state.with_decision(Decision(
        description="called_llm",
        timestamp=datetime.utcnow()
    ))
    
    # Store the response
    state = state.with_fact(Fact(
        key="llm_response",
        value=mock_response,
        timestamp=datetime.utcnow()
    ))
    
    return state
