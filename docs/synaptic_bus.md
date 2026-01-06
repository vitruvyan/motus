# Axis Synaptic Bus — Architecture

**Phase 2.1 Implementation**

**For complete context and compliance mapping, see [White Paper](AXIS_WHITEPAPER.md).**

---

## Conceptual Role

The Axis Synaptic Bus is a **passive observational substrate**.

It observes completed Axis executions and derives semantic signals from GraphState.

**Critical constraint:** The Bus does NOT influence execution, mutate Axis, or trigger behavior.

---

## Design Metaphor

Think **EEG**, not neuron firing.

The term "synaptic" is used in a STRICTLY OBSERVATIONAL sense:
- This is NOT a biological simulation
- This is NOT an active signal system
- This is NOT event-driven execution

The Bus exists so that **incarnated responsibilities (Orders)** can observe what happened.

---

## Directionality

**Unidirectional flow only:**

```
AXIS → BUS → ORDERS
```

There is NO:
- BUS → AXIS (Bus cannot affect execution)
- ORDERS → AXIS (Orders cannot mutate GraphState)
- ORDERS → BUS (Orders cannot emit events)
- AXIS → ORDERS (No direct calls)

---

## Event Semantics

Every Bus event MUST be:
1. **Derived 1:1 from GraphState content**
2. **Traceable back to an exact element in GraphState**
3. **Non-interpretive** (no inference or synthesis)

If an event cannot be mapped directly to a GraphState element, IT MUST NOT EXIST.

---

## Timestamp Semantics

There are TWO kinds of time:

1. **GraphState timestamps** → when something happened during execution
2. **BusEvent timestamps** → when that fact was OBSERVED by the Bus

These timestamps MUST NOT be conflated.

`BusEvent.observed_at` represents observation time, not execution time.

---

## Event Categories

Bus events may ONLY represent:

- **INTENT_DECLARED** — Intent from GraphState.intent
- **FACT_RECORDED** — Each Fact in GraphState.facts
- **DECISION_MADE** — Each Decision in GraphState.decisions
- **REJECTION_RECORDED** — Each Rejection in GraphState.rejections
- **NODE_STARTED** — Events with type NODE_STARTED
- **NODE_COMPLETED** — Events with type NODE_COMPLETED
- **EXECUTION_COMPLETED** — Final synthetic event marking observation complete

No speculative, inferred, or synthetic events are allowed beyond these categories.

---

## Data Model

### BusEvent (immutable)
```python
@dataclass(frozen=True)
class BusEvent:
    event_type: BusEventType
    trace_id: str
    observed_at: datetime          # Observation time
    execution_ts: datetime | None  # Execution time (from GraphState)
    source_index: int | None       # Index in GraphState collection
    content: str | None            # Human-readable excerpt
```

### BusObserver (Protocol)
```python
class BusObserver(Protocol):
    def on_event(self, event: BusEvent) -> None:
        """Receive a Bus event. MUST NOT mutate Axis or emit events."""
        ...
```

### AxisSynapticBus
```python
class AxisSynapticBus:
    def __init__(self, observers: tuple[BusObserver, ...] = ()): ...
    
    @property
    def history(self) -> tuple[BusEvent, ...]: ...
    
    def observe(self, state: GraphState) -> None:
        """Observe a completed Axis execution."""
        ...
```

---

## Invariants

The following invariants are enforced by design:

### 1. Axis Core Untouched
- **Enforcement:** Bus never receives mutable references to GraphState
- **Validation:** GraphState is frozen (immutable dataclass)
- **Consequence:** Bus observation cannot affect Axis execution

### 2. 1:1 Event Derivation
- **Enforcement:** `_derive_events()` iterates GraphState collections
- **Validation:** Every BusEvent has `source_index` to trace origin
- **Consequence:** No synthetic or inferred events exist

### 3. Static Observer Registration
- **Enforcement:** Observers tuple is passed to `__init__` only
- **Validation:** No `register()` or `subscribe()` methods exist
- **Consequence:** No dynamic subscriptions or plugins

### 4. Unidirectional Flow
- **Enforcement:** BusObserver Protocol has no return value
- **Validation:** Observers receive events, cannot emit
- **Consequence:** No feedback loops or bidirectional communication

### 5. Append-Only History
- **Enforcement:** `history` property returns immutable tuple
- **Validation:** Internal `_history` list is never exposed
- **Consequence:** Bus observations are traceable and auditable

### 6. Timestamp Distinction
- **Enforcement:** BusEvent has separate `observed_at` and `execution_ts` fields
- **Validation:** `observed_at` is set during observation, `execution_ts` from GraphState
- **Consequence:** Observation time ≠ execution time (properly separated)

### 7. No Smart Behavior
- **Enforcement:** `_derive_events()` is purely iterative (no conditionals beyond type checking)
- **Validation:** No filtering, routing, or decision logic
- **Consequence:** Bus is truly passive

---

## Orders (Context)

Orders are **NOT** implemented in Phase 2.1.

An Order is:
- A permanent system role (not a plugin)
- With an explicit mandate (defined responsibility)
- With defined read/write boundaries
- Observing the Bus (implementing BusObserver)
- Never controlling execution

Orders may:
- Read Axis (inspect GraphState)
- Observe Bus events (receive notifications)
- Write to secondary memory (future phase)

Orders may NEVER:
- Modify Axis (GraphState is immutable)
- Emit Bus events (observation is unidirectional)
- Influence execution (no feedback to Runner)

---

## Integration Pattern

```python
from synaptic_bus import AxisSynapticBus, BusObserver
from state import GraphState
from runner import GraphRunner, Policy

# Define an Order
class MyOrder:
    def on_event(self, event: BusEvent) -> None:
        # Observe and react (no mutation of Axis)
        ...

# Setup
bus = AxisSynapticBus(observers=(MyOrder(),))

# Axis executes normally
runner = GraphRunner(nodes=[...], policy=Policy.STRICT)
final_state = runner.run(initial_state)

# Bus observes after completion
bus.observe(final_state)
```

Key points:
- Axis runs first (knows nothing about Bus)
- Bus observes after execution completes
- Orders receive notifications
- Nothing flows backward

---

## Explicitly Forbidden

The following patterns are FORBIDDEN:

❌ Dynamic subscriptions (`bus.subscribe(observer)`)  
❌ Filtering inside the Bus (no `if` logic in event derivation)  
❌ Async / threading (sequential notification only)  
❌ Message brokers or queues  
❌ External dependencies  
❌ Smart behavior (routing, inference, synthesis)  
❌ Plugins or extensibility hooks  
❌ Middleware or interceptors  

---

## Implementation Size

- **synaptic_bus.py**: ~200 lines
- **tests/test_synaptic_bus.py**: ~290 lines
- **demo_synaptic_bus.py**: ~230 lines

**Total Phase 2.1**: ~720 lines

Axis Core remains unchanged (~200 lines).

---

## What's Next

**Phase 2.2**: Orders contract and dual memory  
**Phase 2.3**: Concrete Orders (Pattern Weaver, Orthodoxy, Vault Keeper, Explainer)

---

*Last updated: January 6, 2026*
