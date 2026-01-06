# API Reference

## state

### GraphState

Immutable cognitive trace of execution.

```python
@dataclass(frozen=True)
class GraphState:
    trace_id: str
    intent: Optional[str]
    facts: tuple[Fact, ...]
    decisions: tuple[Decision, ...]
    rejections: tuple[Rejection, ...]
    events: tuple[Event, ...]
```

#### Methods

##### `GraphState.empty(trace_id: str) -> GraphState`

Create an empty GraphState.

**Parameters:**
- `trace_id`: Unique identifier for this execution trace

**Returns:** New GraphState with no intent, facts, decisions, rejections, or events

**Example:**
```python
state = GraphState.empty("trace_001")
```

##### `with_intent(intent: str) -> GraphState`

Return new GraphState with updated intent.

**Parameters:**
- `intent`: What the system is trying to accomplish

**Returns:** New GraphState instance

**Example:**
```python
state2 = state.with_intent("analyze_temperature")
```

##### `with_fact(fact: Fact) -> GraphState`

Return new GraphState with appended fact.

**Parameters:**
- `fact`: Fact instance to append

**Returns:** New GraphState instance

**Example:**
```python
fact = Fact(key="temp", value=23.5, timestamp=datetime.utcnow())
state2 = state.with_fact(fact)
```

##### `with_decision(decision: Decision) -> GraphState`

Return new GraphState with appended decision.

**Parameters:**
- `decision`: Decision instance to append

**Returns:** New GraphState instance

##### `with_rejection(rejection: Rejection) -> GraphState`

Return new GraphState with appended rejection.

**Parameters:**
- `rejection`: Rejection instance to append

**Returns:** New GraphState instance

##### `with_event(event: Event) -> GraphState`

Return new GraphState with appended event.

**Parameters:**
- `event`: Event instance to append

**Returns:** New GraphState instance

---

### Fact

Something known to be true.

```python
@dataclass(frozen=True)
class Fact:
    key: str
    value: Union[str, int, float, bool]
    timestamp: datetime
```

**Fields:**
- `key`: Identifier for this fact
- `value`: The fact's value (constrained to serializable types)
- `timestamp`: When this fact was observed

---

### Decision

A choice that was made.

```python
@dataclass(frozen=True)
class Decision:
    description: str
    timestamp: datetime
```

**Fields:**
- `description`: What was decided
- `timestamp`: When the decision was made

---

### Rejection

A path explicitly not taken.

```python
@dataclass(frozen=True)
class Rejection:
    description: str
    reason: str
    timestamp: datetime
```

**Fields:**
- `description`: What was rejected
- `reason`: Why it was rejected
- `timestamp`: When the rejection occurred

---

### Event

Something that happened during execution.

```python
@dataclass(frozen=True)
class Event:
    type: EventType
    description: str
    timestamp: datetime
```

**Fields:**
- `type`: Event type (NODE_STARTED, NODE_COMPLETED, NODE_SKIPPED)
- `description`: Human-readable description
- `timestamp`: When the event occurred

---

### EventType

Canonical event types.

```python
class EventType(str, Enum):
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_SKIPPED = "node_skipped"
```

---

## node

### Node

Protocol defining a state transformer.

```python
class Node(Protocol):
    def __call__(self, state: GraphState) -> GraphState:
        ...
```

**Contract:**
- Receives GraphState
- Returns new GraphState
- Never mutates input state

**Implementation:**

Any callable matching the signature is a valid Node:

```python
# Function
def my_node(state: GraphState) -> GraphState:
    return state.with_intent("example")

# Class
class MyNode:
    def __call__(self, state: GraphState) -> GraphState:
        return state.with_intent("example")

# Lambda (not recommended for readability)
my_node = lambda state: state.with_intent("example")
```

---

## runner

### GraphRunner

Executes a predefined sequence of Nodes.

```python
class GraphRunner:
    def __init__(self, nodes: Iterable[Node], policy: Policy = Policy.STRICT):
        ...
    
    def run(self, state: GraphState) -> GraphState:
        ...
```

#### Constructor

**Parameters:**
- `nodes`: Iterable of Node callables to execute in order
- `policy`: Execution policy (default: Policy.STRICT)

#### Methods

##### `run(state: GraphState) -> GraphState`

Execute all nodes sequentially.

**Parameters:**
- `state`: Initial GraphState

**Returns:** Final GraphState after all nodes execute

**Behavior:**
- Executes nodes in order
- Generates timestamps for events
- Emits NODE_STARTED before each node
- Emits NODE_COMPLETED after successful execution
- Emits NODE_SKIPPED on error (EXPLORATION mode only)
- Propagates exceptions (STRICT mode)

**Example:**
```python
runner = GraphRunner([node1, node2, node3], policy=Policy.STRICT)
initial = GraphState.empty("trace_001")
final = runner.run(initial)
```

---

## policy

### Policy

Execution policy enum.

```python
class Policy(str, Enum):
    STRICT = "strict"
    EXPLORATION = "exploration"
```

#### Policy.STRICT

**Behavior:**
- First exception propagates immediately
- Execution stops
- No recovery

**Use when:**
- Production execution
- Critical paths
- Errors must be handled explicitly

#### Policy.EXPLORATION

**Behavior:**
- Failing nodes are skipped
- Exception recorded as NODE_SKIPPED event
- Execution continues with remaining nodes

**Use when:**
- Discovery/experimentation
- Partial results are acceptable
- Want to see which nodes succeed

**Example:**
```python
# STRICT: fail fast
runner = GraphRunner(nodes, policy=Policy.STRICT)

# EXPLORATION: continue on error
runner = GraphRunner(nodes, policy=Policy.EXPLORATION)
```
