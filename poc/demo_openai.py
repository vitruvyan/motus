#!/usr/bin/env python3
"""
PoC with real OpenAI integration.

Set OPENAI_API_KEY environment variable before running:
  export OPENAI_API_KEY="sk-..."
  python3 poc/demo_openai.py

Falls back to mock responses if API key is not available.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from state import GraphState, Fact
from runner import GraphRunner
from policy import Policy
from nodes.parse_input import parse_input
from nodes.evaluate import evaluate
from nodes.call_llm_openai import call_llm_openai
from nodes.respond_direct import respond_direct
from explain import explain_trace, explain_comparison


class OrchestratorWithOpenAI:
    """Orchestrator using real OpenAI API."""
    
    def __init__(self):
        self.nodes = [
            parse_input,
            evaluate,
            call_llm_openai,  # Real OpenAI integration
            respond_direct
        ]
        self.runner = GraphRunner(self.nodes, policy=Policy.STRICT)
    
    def run(self, user_input: str, trace_id: str) -> GraphState:
        initial_state = GraphState.empty(trace_id)
        initial_state = initial_state.with_intent("answer_question")
        initial_state = initial_state.with_fact(Fact(
            key="user_input",
            value=user_input,
            timestamp=datetime.utcnow()
        ))
        return self.runner.run(initial_state)
    
    def get_response(self, state: GraphState) -> str:
        llm_responses = [f for f in state.facts if f.key == "llm_response"]
        if llm_responses:
            return llm_responses[0].value
        
        direct_responses = [f for f in state.facts if f.key == "direct_response"]
        if direct_responses:
            return direct_responses[0].value
        
        return "No response generated"


def run_scenario(orchestrator, user_input: str, trace_id: str):
    """Run a single scenario with OpenAI."""
    print("\n" + "█" * 60)
    print(f"SCENARIO: {trace_id}")
    print("█" * 60)
    print(f"\nUser: {user_input}")
    print("\n[Orchestrator executing with OpenAI...]")
    
    final_state = orchestrator.run(user_input, trace_id)
    response = orchestrator.get_response(final_state)
    
    print(f"\nAssistant: {response}")
    print(explain_trace(final_state))
    print(explain_comparison(final_state))
    
    return final_state


def main():
    import os
    
    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv not required
    
    print("╔" + "═" * 58 + "╗")
    print("║" + " AXIS-BACKED ORCHESTRATOR - OPENAI INTEGRATION ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        print("\n✓ OPENAI_API_KEY found - using real OpenAI API")
    else:
        print("\n⚠️  OPENAI_API_KEY not set - will use mock responses")
        print("   Set it with: export OPENAI_API_KEY='sk-...'")
    
    print("\nThis demonstrates:")
    print("  • Real LLM integration built ON TOP of Axis")
    print("  • All LLM calls recorded in trace")
    print("  • Model selection visible in trace")
    print("  • Complete auditability of AI interactions")
    
    orchestrator = OrchestratorWithOpenAI()
    
    # Scenario A: Complex question (will call OpenAI)
    print("\n\n")
    state_a = run_scenario(
        orchestrator,
        "Explain the concept of emergent behavior in complex systems",
        "trace_openai_001"
    )
    
    # Scenario B: Simple question (direct answer)
    print("\n\n")
    state_b = run_scenario(
        orchestrator,
        "What is 5+3?",
        "trace_openai_002"
    )
    
    # Summary
    print("\n\n" + "╔" + "═" * 58 + "╗")
    print("║" + " TRACE COMPARISON ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    
    print("\nScenario A (Complex):")
    print(f"  • Decisions: {len(state_a.decisions)}")
    print(f"  • Rejections: {len(state_a.rejections)}")
    model_facts = [f for f in state_a.facts if f.key == "llm_model"]
    if model_facts:
        print(f"  • Model used: {model_facts[0].value}")
    
    print("\nScenario B (Simple):")
    print(f"  • Decisions: {len(state_b.decisions)}")
    print(f"  • Rejections: {len(state_b.rejections)}")
    print(f"  • No LLM call (explicit rejection)")
    
    print("\nBoth traces include:")
    print("  ✓ Why decisions were made")
    print("  ✓ What paths were rejected")
    print("  ✓ Which models were used (when applicable)")
    print("  ✓ Complete execution timeline")
    
    print("\n" + "=" * 60)
    print("This level of traceability is built-in, not bolted-on.")
    print("=" * 60)


if __name__ == "__main__":
    main()
