"""
End-to-end tests for Axis Synaptic Bus.

Validates:
1. Event derivation from GraphState
2. Observer notification
3. Immutability
4. 1:1 traceability
5. Timestamp distinction
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from synaptic_bus import (
    AxisSynapticBus,
    BusEvent,
    BusEventType,
    BusObserver
)
from state import GraphState, Fact, Decision, Rejection
from runner import GraphRunner, Policy
from node import Node


# Test observer implementation
class TestObserver:
    """Simple observer that records events."""
    
    def __init__(self):
        self.received: list[BusEvent] = []
    
    def on_event(self, event: BusEvent) -> None:
        self.received.append(event)


# Test node
class RecordFactNode:
    """Node that records a fact."""
    
    def __call__(self, state: GraphState) -> GraphState:
        fact = Fact(
            key="test_key",
            value="test_value",
            timestamp=datetime.utcnow()
        )
        return state.with_fact(fact)


def test_bus_observes_simple_execution():
    """Bus correctly observes a simple Axis execution."""
    print("\n🧪 Test: Simple observation")
    
    # Create observer
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    # Run simple Axis execution
    ts = datetime.utcnow()
    state = GraphState.empty("test-trace-1")
    state = state.with_intent("Test intent")
    state = state.with_fact(Fact("key1", "value1", ts))
    state = state.with_decision(Decision("test_reasoning", ts))
    
    # Bus observes completed execution
    bus.observe(state)
    
    # Verify events derived
    assert len(observer.received) > 0, "Observer should receive events"
    
    # Check event types
    event_types = [e.event_type for e in observer.received]
    assert BusEventType.INTENT_DECLARED in event_types
    assert BusEventType.FACT_RECORDED in event_types
    assert BusEventType.DECISION_MADE in event_types
    assert BusEventType.EXECUTION_COMPLETED in event_types
    
    # Verify trace_id propagation
    for event in observer.received:
        assert event.trace_id == "test-trace-1"
    
    print("✅ Simple observation working")


def test_timestamp_distinction():
    """Bus distinguishes observation time from execution time."""
    print("\n🧪 Test: Timestamp distinction")
    
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    exec_time = datetime(2026, 1, 1, 12, 0, 0)
    state = GraphState.empty("test-trace-2")
    state = state.with_fact(Fact("key1", "value1", exec_time))
    
    # Observe at different time
    bus.observe(state)
    
    fact_event = [e for e in observer.received if e.event_type == BusEventType.FACT_RECORDED][0]
    
    # Execution timestamp should match original
    assert fact_event.execution_ts == exec_time
    
    # Observation timestamp should be different (current time)
    assert fact_event.observed_at != exec_time
    assert fact_event.observed_at.year == 2026  # Current time
    
    print("✅ Timestamps correctly distinguished")


def test_event_traceability():
    """Every Bus event is traceable to GraphState element."""
    print("\n🧪 Test: Event traceability")
    
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    ts = datetime.utcnow()
    state = GraphState.empty("test-trace-3")
    state = state.with_fact(Fact("fact1", "value1", ts))
    state = state.with_fact(Fact("fact2", "value2", ts))
    state = state.with_decision(Decision("reason1", ts))
    
    bus.observe(state)
    
    # Find fact events
    fact_events = [e for e in observer.received if e.event_type == BusEventType.FACT_RECORDED]
    assert len(fact_events) == 2
    
    # Verify source indices
    assert fact_events[0].source_index == 0
    assert fact_events[1].source_index == 1
    
    # Verify content excerpts
    assert "fact1" in fact_events[0].content
    assert "fact2" in fact_events[1].content
    
    print("✅ Events traceable to source")


def test_rejection_derivation():
    """Bus derives rejection events correctly."""
    print("\n🧪 Test: Rejection derivation")
    
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    ts = datetime.utcnow()
    state = GraphState.empty("test-trace-4")
    state = state.with_rejection(Rejection(
        "call_expensive_api",
        "Cost threshold exceeded",
        ts
    ))
    
    bus.observe(state)
    
    rejection_events = [e for e in observer.received if e.event_type == BusEventType.REJECTION_RECORDED]
    assert len(rejection_events) == 1
    
    event = rejection_events[0]
    assert event.execution_ts == ts
    assert "call_expensive_api" in event.content
    assert "Cost threshold exceeded" in event.content
    
    print("✅ Rejections correctly derived")


def test_bus_immutability():
    """Bus history is append-only and immutable."""
    print("\n🧪 Test: Bus immutability")
    
    bus = AxisSynapticBus()
    
    state1 = GraphState.empty("trace-1")
    state1 = state1.with_fact(Fact("key1", "value1", datetime.utcnow()))
    
    bus.observe(state1)
    history1 = bus.history
    initial_count = len(history1)
    
    # Observe another execution
    state2 = GraphState.empty("trace-2")
    state2 = state2.with_fact(Fact("key2", "value2", datetime.utcnow()))
    
    bus.observe(state2)
    history2 = bus.history
    
    # History should grow
    assert len(history2) > initial_count
    
    # Original history reference unchanged (immutable)
    assert len(history1) == initial_count
    
    print("✅ Bus history is append-only and immutable")


def test_multiple_observers():
    """Bus notifies all observers sequentially."""
    print("\n🧪 Test: Multiple observers")
    
    observer1 = TestObserver()
    observer2 = TestObserver()
    bus = AxisSynapticBus(observers=(observer1, observer2))
    
    state = GraphState.empty("test-trace-5")
    state = state.with_fact(Fact("key1", "value1", datetime.utcnow()))
    
    bus.observe(state)
    
    # Both observers should receive same events
    assert len(observer1.received) == len(observer2.received)
    
    # Events should be identical
    for e1, e2 in zip(observer1.received, observer2.received):
        assert e1.event_type == e2.event_type
        assert e1.trace_id == e2.trace_id
        assert e1.observed_at == e2.observed_at
    
    print("✅ Multiple observers notified")


def test_integration_with_runner():
    """Bus observes GraphRunner execution."""
    print("\n🧪 Test: Integration with GraphRunner")
    
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    # Run Axis execution
    runner = GraphRunner([RecordFactNode()], Policy.STRICT)
    initial_state = GraphState.empty("test-trace-6")
    initial_state = initial_state.with_intent("Record a fact")
    final_state = runner.run(initial_state)
    
    # Bus observes completed execution
    bus.observe(final_state)
    
    # Verify lifecycle events present
    event_types = [e.event_type for e in observer.received]
    assert BusEventType.NODE_STARTED in event_types
    assert BusEventType.NODE_COMPLETED in event_types
    assert BusEventType.EXECUTION_COMPLETED in event_types
    
    # Verify fact recorded
    assert BusEventType.FACT_RECORDED in event_types
    
    print("✅ Integration with GraphRunner working")


def test_no_axis_mutation():
    """Bus never mutates Axis GraphState."""
    print("\n🧪 Test: No Axis mutation")
    
    observer = TestObserver()
    bus = AxisSynapticBus(observers=(observer,))
    
    original_state = GraphState.empty("test-trace-7")
    original_state = original_state.with_fact(Fact("key1", "value1", datetime.utcnow()))
    
    # Capture original state
    original_fact_count = len(original_state.facts)
    original_trace_id = original_state.trace_id
    
    # Bus observation
    bus.observe(original_state)
    
    # State should be unchanged
    assert len(original_state.facts) == original_fact_count
    assert original_state.trace_id == original_trace_id
    assert original_state.intent is None  # Still None
    
    print("✅ Axis state never mutated")


# Run all tests
if __name__ == "__main__":
    print("="*60)
    print("AXIS SYNAPTIC BUS — E2E TEST SUITE")
    print("="*60)
    
    test_bus_observes_simple_execution()
    test_timestamp_distinction()
    test_event_traceability()
    test_rejection_derivation()
    test_bus_immutability()
    test_multiple_observers()
    test_integration_with_runner()
    test_no_axis_mutation()
    
    print("\n" + "="*60)
    print("✅ ALL SYNAPTIC BUS TESTS PASSED")
    print("="*60)
