import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import GraphState, EventType


def explain_trace(state: GraphState) -> str:
    """
    Generate human-readable explanation from Axis trace.
    
    This demonstrates the key value: complete visibility into
    what happened and why.
    """
    lines = []
    lines.append("=" * 60)
    lines.append(f"TRACE EXPLANATION")
    lines.append("=" * 60)
    lines.append(f"Trace ID: {state.trace_id}")
    lines.append(f"Intent: {state.intent}")
    lines.append("")
    
    # Input
    user_inputs = [f for f in state.facts if f.key == "user_input"]
    if user_inputs:
        lines.append(f"User Input: {user_inputs[0].value}")
        lines.append("")
    
    # Complexity assessment
    complexity = [f for f in state.facts if f.key == "input_complexity"]
    if complexity:
        lines.append(f"Input Complexity: {complexity[0].value}")
        lines.append("")
    
    # Decisions made
    if state.decisions:
        lines.append("DECISIONS MADE:")
        for d in state.decisions:
            lines.append(f"  ✓ {d.description}")
            lines.append(f"    at {d.timestamp.strftime('%H:%M:%S.%f')[:-3]}")
        lines.append("")
    
    # Paths rejected (critical for explainability)
    if state.rejections:
        lines.append("PATHS REJECTED:")
        for r in state.rejections:
            lines.append(f"  ✗ {r.description}")
            lines.append(f"    reason: {r.reason}")
            lines.append(f"    at {r.timestamp.strftime('%H:%M:%S.%f')[:-3]}")
        lines.append("")
    
    # Facts collected
    fact_types = ["input_complexity", "llm_response", "direct_response"]
    relevant_facts = [f for f in state.facts if f.key in fact_types]
    
    if relevant_facts:
        lines.append("FACTS COLLECTED:")
        for f in relevant_facts:
            if f.key == "llm_response":
                lines.append(f"  • LLM Response: {f.value}")
            elif f.key == "direct_response":
                lines.append(f"  • Direct Response: {f.value}")
            elif f.key == "input_complexity":
                lines.append(f"  • Complexity: {f.value}")
        lines.append("")
    
    # Execution timeline
    if state.events:
        lines.append("EXECUTION TIMELINE:")
        for e in state.events:
            symbol = {
                EventType.NODE_STARTED: "▶",
                EventType.NODE_COMPLETED: "✓",
                EventType.NODE_SKIPPED: "⊘"
            }.get(e.type, "•")
            
            time_str = e.timestamp.strftime('%H:%M:%S.%f')[:-3]
            lines.append(f"  {symbol} [{time_str}] {e.description}")
        lines.append("")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


def explain_comparison(state: GraphState) -> str:
    """
    Generate explanation focused on the key difference:
    why decisions were made.
    """
    lines = []
    lines.append("\n" + "=" * 60)
    lines.append("WHY THIS HAPPENED")
    lines.append("=" * 60)
    
    # Check if LLM was used
    llm_used = any(d.description == "called_llm" for d in state.decisions)
    llm_rejected = any(r.description == "llm_not_needed" for r in state.rejections)
    
    if llm_used:
        lines.append("\n✓ LLM WAS CALLED because:")
        needs_llm = [d for d in state.decisions if d.description == "needs_llm"]
        if needs_llm:
            lines.append("  • Input complexity required deep knowledge")
            lines.append("  • Direct response would be insufficient")
    
    if llm_rejected:
        lines.append("\n✗ LLM WAS NOT CALLED because:")
        rejection = [r for r in state.rejections if r.description == "llm_not_needed"][0]
        lines.append(f"  • {rejection.reason}")
        lines.append("  • Direct answer was sufficient")
    
    lines.append("\n" + "=" * 60)
    lines.append("This explanation was generated automatically from the trace.")
    lines.append("LangGraph cannot provide this level of visibility without")
    lines.append("manual instrumentation.")
    lines.append("=" * 60)
    
    return "\n".join(lines)
