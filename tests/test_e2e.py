"""
End-to-end tests for Vitruvyan Axis.

Tests verify the complete cognitive trace flow:
- GraphState creation and immutability
- Node execution through Runner
- Policy enforcement
- Event recording
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from state import GraphState, Fact, Decision, Rejection, Event, EventType
from node import Node
from runner import GraphRunner
from policy import Policy


# Test Nodes (simple callables)
def node_set_intent(state: GraphState) -> GraphState:
    """Node that sets the intent."""
    return state.with_intent("test_intent")


def node_add_fact(state: GraphState) -> GraphState:
    """Node that adds a fact."""
    fact = Fact(
        key="test_key",
        value="test_value",
        timestamp=datetime.utcnow()
    )
    return state.with_fact(fact)


def node_make_decision(state: GraphState) -> GraphState:
    """Node that records a decision."""
    decision = Decision(
        description="test_decision",
        timestamp=datetime.utcnow()
    )
    return state.with_decision(decision)


def node_record_rejection(state: GraphState) -> GraphState:
    """Node that records a rejection."""
    rejection = Rejection(
        description="test_rejection",
        reason="test_reason",
        timestamp=datetime.utcnow()
    )
    return state.with_rejection(rejection)


def node_that_fails(state: GraphState) -> GraphState:
    """Node that always raises an exception."""
    raise ValueError("Intentional failure")


# Test: Basic state creation and immutability
def test_state_creation():
    """Test GraphState.empty() and immutability."""
    state = GraphState.empty("trace_001")
    
    assert state.trace_id == "trace_001"
    assert state.intent is None
    assert len(state.facts) == 0
    assert len(state.decisions) == 0
    assert len(state.rejections) == 0
    assert len(state.events) == 0
    
    print("✓ State creation test passed")


# Test: State extension returns new instances
def test_state_immutability():
    """Test that state modifications return new instances."""
    state1 = GraphState.empty("trace_002")
    state2 = state1.with_intent("intent_a")
    
    assert state1.intent is None
    assert state2.intent == "intent_a"
    assert state1 is not state2
    
    print("✓ State immutability test passed")


# Test: Runner with STRICT policy (success case)
def test_runner_strict_success():
    """Test successful execution with STRICT policy."""
    nodes = [
        node_set_intent,
        node_add_fact,
        node_make_decision,
        node_record_rejection,
    ]
    
    runner = GraphRunner(nodes, policy=Policy.STRICT)
    initial_state = GraphState.empty("trace_003")
    final_state = runner.run(initial_state)
    
    # Verify cognitive trace
    assert final_state.intent == "test_intent"
    assert len(final_state.facts) == 1
    assert len(final_state.decisions) == 1
    assert len(final_state.rejections) == 1
    
    # Verify events (4 nodes * 2 events = 8 events)
    assert len(final_state.events) == 8
    
    started_events = [e for e in final_state.events if e.type == EventType.NODE_STARTED]
    completed_events = [e for e in final_state.events if e.type == EventType.NODE_COMPLETED]
    
    assert len(started_events) == 4
    assert len(completed_events) == 4
    
    print("✓ Runner STRICT policy (success) test passed")


# Test: Runner with STRICT policy (failure case)
def test_runner_strict_failure():
    """Test that STRICT policy stops execution on error."""
    nodes = [
        node_set_intent,
        node_that_fails,
        node_add_fact,  # Should never execute
    ]
    
    runner = GraphRunner(nodes, policy=Policy.STRICT)
    initial_state = GraphState.empty("trace_004")
    
    try:
        runner.run(initial_state)
        assert False, "Expected ValueError to be raised"
    except ValueError as e:
        assert str(e) == "Intentional failure"
        print("✓ Runner STRICT policy (failure) test passed")


# Test: Runner with EXPLORATION policy (skip on error)
def test_runner_exploration_skip():
    """Test that EXPLORATION policy skips failing nodes."""
    nodes = [
        node_set_intent,
        node_that_fails,
        node_add_fact,  # Should execute despite previous failure
    ]
    
    runner = GraphRunner(nodes, policy=Policy.EXPLORATION)
    initial_state = GraphState.empty("trace_005")
    final_state = runner.run(initial_state)
    
    # Verify execution continued
    assert final_state.intent == "test_intent"
    assert len(final_state.facts) == 1
    
    # Verify events include NODE_SKIPPED
    skipped_events = [e for e in final_state.events if e.type == EventType.NODE_SKIPPED]
    assert len(skipped_events) == 1
    assert "error" in skipped_events[0].description.lower()
    
    print("✓ Runner EXPLORATION policy (skip) test passed")


# Test: Node as class (Protocol compliance)
class NodeAsClass:
    """Test that classes implementing __call__ work as Nodes."""
    
    def __call__(self, state: GraphState) -> GraphState:
        return state.with_intent("from_class")


def test_node_protocol_class():
    """Test that Protocol allows class-based nodes."""
    node = NodeAsClass()
    runner = GraphRunner([node], policy=Policy.STRICT)
    initial_state = GraphState.empty("trace_006")
    final_state = runner.run(initial_state)
    
    assert final_state.intent == "from_class"
    
    print("✓ Node Protocol (class) test passed")


# Test: Fact value type constraints
def test_fact_value_types():
    """Test that Fact values are constrained to serializable types."""
    state = GraphState.empty("trace_007")
    now = datetime.utcnow()
    
    # Valid types
    fact_str = Fact(key="k1", value="string", timestamp=now)
    fact_int = Fact(key="k2", value=42, timestamp=now)
    fact_float = Fact(key="k3", value=3.14, timestamp=now)
    fact_bool = Fact(key="k4", value=True, timestamp=now)
    
    state = state.with_fact(fact_str)
    state = state.with_fact(fact_int)
    state = state.with_fact(fact_float)
    state = state.with_fact(fact_bool)
    
    assert len(state.facts) == 4
    
    print("✓ Fact value types test passed")


if __name__ == "__main__":
    print("Running Vitruvyan Axis E2E tests...\n")
    
    test_state_creation()
    test_state_immutability()
    test_runner_strict_success()
    test_runner_strict_failure()
    test_runner_exploration_skip()
    test_node_protocol_class()
    test_fact_value_types()
    
    print("\n✅ All E2E tests passed")
