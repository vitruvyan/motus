from datetime import datetime
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import GraphState, Fact
from runner import GraphRunner
from policy import Policy
from nodes.parse_input import parse_input
from nodes.evaluate import evaluate
from nodes.call_llm import call_llm
from nodes.respond_direct import respond_direct


class Orchestrator:
    """
    Minimal orchestrator that routes execution based on Axis trace.
    
    Key principle: All routing decisions are written to Axis.
    No silent decisions.
    """
    
    def __init__(self):
        # Define the complete node sequence
        # Routing happens INSIDE nodes based on trace inspection
        self.nodes = [
            parse_input,
            evaluate,
            call_llm,      # Only executes if decision says needs_llm
            respond_direct  # Only executes if rejection says llm_not_needed
        ]
        
        self.runner = GraphRunner(self.nodes, policy=Policy.STRICT)
    
    def run(self, user_input: str, trace_id: str) -> GraphState:
        """
        Execute orchestration for given input.
        
        Returns complete GraphState with full trace.
        """
        # Initialize state
        initial_state = GraphState.empty(trace_id)
        
        # Set intent
        initial_state = initial_state.with_intent("answer_question")
        
        # Add user input as fact
        initial_state = initial_state.with_fact(Fact(
            key="user_input",
            value=user_input,
            timestamp=datetime.utcnow()
        ))
        
        # Execute through runner
        final_state = self.runner.run(initial_state)
        
        return final_state
    
    def get_response(self, state: GraphState) -> str:
        """Extract final response from state."""
        # Check for LLM response
        llm_responses = [f for f in state.facts if f.key == "llm_response"]
        if llm_responses:
            return llm_responses[0].value
        
        # Check for direct response
        direct_responses = [f for f in state.facts if f.key == "direct_response"]
        if direct_responses:
            return direct_responses[0].value
        
        return "No response generated"
