# Examples

## Basic Usage

### Simple Node Execution

```python
from datetime import datetime
from state import GraphState, Fact
from runner import GraphRunner
from policy import Policy

# Define a node
def observe_temperature(state: GraphState) -> GraphState:
    fact = Fact(
        key="temperature",
        value=23.5,
        timestamp=datetime.utcnow()
    )
    return state.with_fact(fact)

# Execute
runner = GraphRunner([observe_temperature], policy=Policy.STRICT)
initial = GraphState.empty("trace_001")
final = runner.run(initial)

print(f"Temperature: {final.facts[0].value}°C")
```

---

## Setting Intent

```python
def set_analysis_intent(state: GraphState) -> GraphState:
    return state.with_intent("analyze_sensor_data")

def observe_data(state: GraphState) -> GraphState:
    # Access intent
    if state.intent == "analyze_sensor_data":
        fact = Fact(key="sensor_id", value="A1", timestamp=datetime.utcnow())
        return state.with_fact(fact)
    return state

runner = GraphRunner([set_analysis_intent, observe_data])
final = runner.run(GraphState.empty("trace_002"))
```

---

## Recording Decisions

```python
from state import Decision

def decide_threshold(state: GraphState) -> GraphState:
    # Make a decision based on facts
    temp_facts = [f for f in state.facts if f.key == "temperature"]
    
    if temp_facts and temp_facts[0].value > 25:
        decision = Decision(
            description="activate_cooling",
            timestamp=datetime.utcnow()
        )
        return state.with_decision(decision)
    
    return state

nodes = [observe_temperature, decide_threshold]
runner = GraphRunner(nodes)
final = runner.run(GraphState.empty("trace_003"))

if final.decisions:
    print(f"Decision: {final.decisions[0].description}")
```

---

## Recording Rejections

```python
from state import Rejection

def evaluate_options(state: GraphState) -> GraphState:
    # Consider option A
    if not meets_criteria_a(state):
        rejection = Rejection(
            description="option_a",
            reason="criteria_not_met",
            timestamp=datetime.utcnow()
        )
        state = state.with_rejection(rejection)
    
    # Consider option B
    if meets_criteria_b(state):
        decision = Decision(
            description="selected_option_b",
            timestamp=datetime.utcnow()
        )
        state = state.with_decision(decision)
    
    return state
```

---

## Node as Class

```python
class TemperatureMonitor:
    def __init__(self, threshold: float):
        self.threshold = threshold
    
    def __call__(self, state: GraphState) -> GraphState:
        fact = Fact(
            key="temp_threshold",
            value=self.threshold,
            timestamp=datetime.utcnow()
        )
        return state.with_fact(fact)

# Use as node
monitor = TemperatureMonitor(threshold=25.0)
runner = GraphRunner([monitor])
final = runner.run(GraphState.empty("trace_004"))
```

---

## Error Handling: STRICT Policy

```python
def failing_node(state: GraphState) -> GraphState:
    raise ValueError("Sensor malfunction")

def recovery_node(state: GraphState) -> GraphState:
    # This will NOT execute in STRICT mode
    return state.with_intent("recovered")

runner = GraphRunner(
    [observe_temperature, failing_node, recovery_node],
    policy=Policy.STRICT
)

try:
    final = runner.run(GraphState.empty("trace_005"))
except ValueError as e:
    print(f"Execution stopped: {e}")
    # recovery_node never executed
```

---

## Error Handling: EXPLORATION Policy

```python
def failing_node(state: GraphState) -> GraphState:
    raise ValueError("Sensor malfunction")

def recovery_node(state: GraphState) -> GraphState:
    # This WILL execute in EXPLORATION mode
    return state.with_intent("recovered")

runner = GraphRunner(
    [observe_temperature, failing_node, recovery_node],
    policy=Policy.EXPLORATION
)

final = runner.run(GraphState.empty("trace_006"))

# Check events to see what happened
skipped = [e for e in final.events if e.type == EventType.NODE_SKIPPED]
print(f"Skipped nodes: {len(skipped)}")
print(f"Final intent: {final.intent}")  # "recovered"
```

---

## Composing Multiple Nodes

```python
# Data collection nodes
def collect_sensor_a(state: GraphState) -> GraphState:
    fact = Fact(key="sensor_a", value=23.5, timestamp=datetime.utcnow())
    return state.with_fact(fact)

def collect_sensor_b(state: GraphState) -> GraphState:
    fact = Fact(key="sensor_b", value=24.1, timestamp=datetime.utcnow())
    return state.with_fact(fact)

# Analysis node
def analyze_sensors(state: GraphState) -> GraphState:
    sensors = [f for f in state.facts if f.key.startswith("sensor_")]
    avg = sum(f.value for f in sensors) / len(sensors) if sensors else 0
    
    fact = Fact(key="average_temp", value=avg, timestamp=datetime.utcnow())
    return state.with_fact(fact)

# Decision node
def decide_action(state: GraphState) -> GraphState:
    avg_facts = [f for f in state.facts if f.key == "average_temp"]
    
    if avg_facts and avg_facts[0].value > 24:
        decision = Decision(
            description="activate_cooling",
            timestamp=datetime.utcnow()
        )
        return state.with_decision(decision)
    
    return state

# Compose pipeline
pipeline = [
    collect_sensor_a,
    collect_sensor_b,
    analyze_sensors,
    decide_action
]

runner = GraphRunner(pipeline)
final = runner.run(GraphState.empty("trace_007"))

print(f"Facts collected: {len(final.facts)}")
print(f"Decisions made: {len(final.decisions)}")
```

---

## Auditing Execution

```python
from state import EventType

runner = GraphRunner([node1, node2, node3])
final = runner.run(GraphState.empty("trace_008"))

# Analyze execution trace
started = [e for e in final.events if e.type == EventType.NODE_STARTED]
completed = [e for e in final.events if e.type == EventType.NODE_COMPLETED]
skipped = [e for e in final.events if e.type == EventType.NODE_SKIPPED]

print(f"Nodes started: {len(started)}")
print(f"Nodes completed: {len(completed)}")
print(f"Nodes skipped: {len(skipped)}")

# Execution timeline
for event in final.events:
    print(f"[{event.timestamp}] {event.type}: {event.description}")
```

---

## Pattern: Conditional Execution

```python
def conditional_logic(state: GraphState) -> GraphState:
    # Nodes can inspect state and decide what to add
    if state.intent == "critical_analysis":
        # Add more facts
        fact = Fact(key="priority", value="high", timestamp=datetime.utcnow())
        state = state.with_fact(fact)
    
    # Make decision based on accumulated facts
    if len(state.facts) > 5:
        decision = Decision(
            description="sufficient_data",
            timestamp=datetime.utcnow()
        )
        state = state.with_decision(decision)
    
    return state
```

---

## Pattern: Pure Data Pipeline

```python
# All nodes are pure functions
def ingest(state: GraphState) -> GraphState:
    return state.with_fact(Fact("raw_data", 42, datetime.utcnow()))

def transform(state: GraphState) -> GraphState:
    raw = [f for f in state.facts if f.key == "raw_data"]
    if raw:
        processed = raw[0].value * 2
        return state.with_fact(Fact("processed", processed, datetime.utcnow()))
    return state

def validate(state: GraphState) -> GraphState:
    processed = [f for f in state.facts if f.key == "processed"]
    if processed and processed[0].value < 100:
        return state.with_decision(Decision("valid", datetime.utcnow()))
    return state

# Pure pipeline: ingest -> transform -> validate
runner = GraphRunner([ingest, transform, validate])
```
