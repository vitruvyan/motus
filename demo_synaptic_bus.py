"""
Demonstration: Axis Synaptic Bus

Shows:
1. Bus observing Axis executions
2. Orders receiving notifications
3. Complete separation of concerns
4. No mutation of Axis core
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from synaptic_bus import AxisSynapticBus, BusEvent, BusEventType, BusObserver
from state import GraphState, Fact, Decision, Rejection
from runner import GraphRunner, Policy
from node import Node
from datetime import datetime


# Example Order: Audit Logger
class AuditLogger:
    """
    Order that maintains audit trail.
    
    This is an incarnated responsibility:
    - Observes all Bus events
    - Records critical decisions
    - Does NOT mutate Axis
    - Does NOT influence execution
    """
    
    def __init__(self):
        self.audit_trail: list[str] = []
    
    def on_event(self, event: BusEvent) -> None:
        if event.event_type == BusEventType.DECISION_MADE:
            entry = f"[{event.observed_at.isoformat()}] Decision in {event.trace_id}: {event.content}"
            self.audit_trail.append(entry)
            print(f"📝 AUDIT: {entry}")
        
        elif event.event_type == BusEventType.REJECTION_RECORDED:
            entry = f"[{event.observed_at.isoformat()}] Rejection in {event.trace_id}: {event.content}"
            self.audit_trail.append(entry)
            print(f"🚫 AUDIT: {entry}")


# Example Order: Metrics Collector
class MetricsCollector:
    """
    Order that collects execution metrics.
    
    Observes patterns across executions.
    """
    
    def __init__(self):
        self.execution_count = 0
        self.decision_count = 0
        self.rejection_count = 0
    
    def on_event(self, event: BusEvent) -> None:
        if event.event_type == BusEventType.EXECUTION_COMPLETED:
            self.execution_count += 1
        elif event.event_type == BusEventType.DECISION_MADE:
            self.decision_count += 1
        elif event.event_type == BusEventType.REJECTION_RECORDED:
            self.rejection_count += 1
    
    def report(self) -> str:
        return (
            f"📊 Metrics: {self.execution_count} executions, "
            f"{self.decision_count} decisions, {self.rejection_count} rejections"
        )


# Example Nodes
class AnalyzeInputNode:
    """Node that analyzes input complexity."""
    
    def __call__(self, state: GraphState) -> GraphState:
        if not state.intent:
            return state
        
        ts = datetime.utcnow()
        complexity = "high" if len(state.intent) > 50 else "low"
        
        fact = Fact("input_complexity", complexity, ts)
        state = state.with_fact(fact)
        return state


class MakeDecisionNode:
    """Node that makes routing decision."""
    
    def __call__(self, state: GraphState) -> GraphState:
        ts = datetime.utcnow()
        
        # Check complexity
        complexity_fact = [f for f in state.facts if f.key == "input_complexity"]
        
        if complexity_fact and complexity_fact[0].value == "high":
            decision = Decision(
                "route_to_advanced_processor: Input complexity requires advanced processing",
                ts
            )
            state = state.with_decision(decision)
        else:
            rejection = Rejection(
                "route_to_advanced_processor",
                "Input is simple enough for basic processing",
                ts
            )
            state = state.with_rejection(rejection)
            
            decision = Decision(
                "route_to_basic_processor: Input complexity is low",
                ts
            )
            state = state.with_decision(decision)
        
        return state


def demo_simple_observation():
    """Demonstrate basic Bus observation."""
    print("\n" + "="*60)
    print("DEMO 1: Simple Observation")
    print("="*60)
    
    # Create Orders
    audit = AuditLogger()
    metrics = MetricsCollector()
    
    # Create Bus with Orders as observers
    bus = AxisSynapticBus(observers=(audit, metrics))
    
    # Run Axis execution (simple)
    state = GraphState.empty("demo-trace-1")
    state = state.with_intent("Calculate 2+2")
    runner = GraphRunner([AnalyzeInputNode(), MakeDecisionNode()], Policy.STRICT)
    final_state = runner.run(state)
    
    # Bus observes
    print("\n🔍 Bus observing execution...")
    bus.observe(final_state)
    
    print(f"\n{metrics.report()}")
    print(f"Bus history: {len(bus.history)} events")


def demo_complex_observation():
    """Demonstrate observation with complex input."""
    print("\n" + "="*60)
    print("DEMO 2: Complex Input Observation")
    print("="*60)
    
    # Reuse Orders from previous demo
    audit = AuditLogger()
    metrics = MetricsCollector()
    bus = AxisSynapticBus(observers=(audit, metrics))
    
    # Run Axis execution (complex)
    complex_intent = (
        "Analyze the emergent behavior patterns in distributed systems "
        "with multiple autonomous agents and determine optimal coordination strategies"
    )
    
    state = GraphState.empty("demo-trace-2")
    state = state.with_intent(complex_intent)
    runner = GraphRunner([AnalyzeInputNode(), MakeDecisionNode()], Policy.STRICT)
    final_state = runner.run(state)
    
    # Bus observes
    print("\n🔍 Bus observing execution...")
    bus.observe(final_state)
    
    print(f"\n{metrics.report()}")


def demo_directionality():
    """Demonstrate unidirectional flow: AXIS → BUS → ORDERS."""
    print("\n" + "="*60)
    print("DEMO 3: Directionality (AXIS → BUS → ORDERS)")
    print("="*60)
    
    print("\n1️⃣ AXIS executes (knows nothing about Bus)")
    state = GraphState.empty("demo-trace-3")
    state = state.with_intent("Test directionality")
    runner = GraphRunner([AnalyzeInputNode()], Policy.STRICT)
    final_state = runner.run(state)
    print(f"   Trace ID: {final_state.trace_id}")
    print(f"   Facts: {len(final_state.facts)}")
    
    print("\n2️⃣ BUS observes completed execution")
    audit = AuditLogger()
    bus = AxisSynapticBus(observers=(audit,))
    bus.observe(final_state)
    print(f"   Bus history: {len(bus.history)} events")
    
    print("\n3️⃣ ORDERS receive notifications (cannot write back)")
    print(f"   Audit trail entries: {len(audit.audit_trail)}")
    
    print("\n✅ No backward flow: Orders cannot affect Axis")
    print("✅ No feedback loops: Bus is passive observer only")


def demo_timestamp_semantics():
    """Demonstrate distinction between execution and observation time."""
    print("\n" + "="*60)
    print("DEMO 4: Timestamp Semantics")
    print("="*60)
    
    # Create execution at specific time
    exec_time = datetime(2026, 1, 6, 10, 0, 0)
    state = GraphState.empty("demo-trace-4")
    state = state.with_fact(Fact("critical_fact", "important_value", exec_time))
    
    print(f"\n⏱️  Execution timestamp: {exec_time.isoformat()}")
    
    # Bus observes later
    class TimestampInspector:
        def on_event(self, event: BusEvent) -> None:
            if event.event_type == BusEventType.FACT_RECORDED:
                print(f"\n📡 Bus Event:")
                print(f"   Observed at: {event.observed_at.isoformat()}")
                print(f"   Execution at: {event.execution_ts.isoformat()}")
                print(f"   Δ These are DIFFERENT timestamps")
                print(f"   ✓ Execution time: when fact was recorded in Axis")
                print(f"   ✓ Observation time: when Bus derived the event")
    
    bus = AxisSynapticBus(observers=(TimestampInspector(),))
    bus.observe(state)


# Run all demos
if __name__ == "__main__":
    print("="*60)
    print("AXIS SYNAPTIC BUS — DEMONSTRATION")
    print("="*60)
    print("\nPassive observational substrate for Axis executions")
    print("Unidirectional: AXIS → BUS → ORDERS")
    
    demo_simple_observation()
    demo_complex_observation()
    demo_directionality()
    demo_timestamp_semantics()
    
    print("\n" + "="*60)
    print("✅ DEMONSTRATION COMPLETE")
    print("="*60)
    print("\nKey takeaways:")
    print("• Bus is passive (observes, never controls)")
    print("• Events are 1:1 derived from GraphState")
    print("• Orders are notified (cannot write back)")
    print("• Axis core remains untouched")
    print("• Two timestamp types: execution vs observation")
