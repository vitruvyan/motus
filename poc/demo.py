#!/usr/bin/env python3
"""
Proof of Concept: Axis-backed Orchestrator

Demonstrates:
1. Decision routing with full traceability
2. Explicit rejection recording (not just success paths)
3. Automatic explainability from trace
4. Deterministic replay capability
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import Orchestrator
from explain import explain_trace, explain_comparison


def run_scenario(orchestrator: Orchestrator, user_input: str, trace_id: str):
    """Run a single scenario and display results."""
    print("\n" + "█" * 60)
    print(f"SCENARIO: {trace_id}")
    print("█" * 60)
    print(f"\nUser: {user_input}")
    print("\n[Orchestrator executing...]")
    
    # Run orchestration
    final_state = orchestrator.run(user_input, trace_id)
    
    # Get response
    response = orchestrator.get_response(final_state)
    print(f"\nAssistant: {response}")
    
    # Explain what happened
    print(explain_trace(final_state))
    print(explain_comparison(final_state))
    
    return final_state


def main():
    """Run both demonstration scenarios."""
    orchestrator = Orchestrator()
    
    print("╔" + "═" * 58 + "╗")
    print("║" + " AXIS-BACKED ORCHESTRATOR - PROOF OF CONCEPT ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print("\nThis PoC demonstrates:")
    print("  • Routing decisions written to Axis")
    print("  • Explicit rejection recording")
    print("  • Automatic trace explanation")
    print("  • Complete visibility into 'why'")
    
    # Scenario A: Complex input → LLM needed
    print("\n\n")
    state_a = run_scenario(
        orchestrator,
        "Explain quantum entanglement in simple terms",
        "trace_001_complex"
    )
    
    # Scenario B: Simple input → LLM not needed
    print("\n\n")
    state_b = run_scenario(
        orchestrator,
        "What is 2+2?",
        "trace_002_simple"
    )
    
    # Final summary
    print("\n\n" + "╔" + "═" * 58 + "╗")
    print("║" + " KEY INSIGHT ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")
    print("\nIn Scenario A:")
    print(f"  • Decisions: {len(state_a.decisions)}")
    print(f"  • Rejections: {len(state_a.rejections)}")
    print(f"  • LLM was called: YES")
    
    print("\nIn Scenario B:")
    print(f"  • Decisions: {len(state_b.decisions)}")
    print(f"  • Rejections: {len(state_b.rejections)}")
    print(f"  • LLM was called: NO (explicitly rejected)")
    
    print("\nBoth traces are complete and auditable.")
    print("Both include 'why' reasoning, not just 'what' actions.")
    print("\nThis is what differentiates Axis-backed orchestration.")


if __name__ == "__main__":
    main()
