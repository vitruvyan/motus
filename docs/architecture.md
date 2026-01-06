# Architecture

**For complete technical overview and business case, see [White Paper](AXIS_WHITEPAPER.md).**

## Overview

Vitruvyan Axis implements a cognitive graph kernel where:

1. **Graph structure is code** — defined at compile-time through Node composition
2. **State is trace** — GraphState records what happened, not what exists
3. **Execution is sequential** — Runner executes Nodes in order
4. **Behavior is constrained** — Policy determines error handling

## Core Components

### GraphState

Immutable cognitive trace containing:

```python
@dataclass(frozen=True)
class GraphState:
    trace_id: str              # Unique execution identifier
    intent: Optional[str]      # What the system is trying to accomplish
    facts: tuple[Fact, ...]    # Known truths
    decisions: tuple[Decision, ...]  # Choices made
    rejections: tuple[Rejection, ...]  # Paths not taken
    events: tuple[Event, ...]  # Execution events
```

**Key properties:**
- Frozen (immutable)
- Append-only through `with_*` methods
- Each method returns a new instance
- No in-place mutation
- Serializable (constrained types)

### Node

Protocol defining a state transformer:

```python
class Node(Protocol):
    def __call__(self, state: GraphState) -> GraphState:
        ...
```

**Nodes can be:**
- Functions
- Callable classes
- Lambdas
- Any callable with correct signature

**Nodes must:**
- Receive GraphState
- Return new GraphState
- Never mutate input state
- Have no side effects (ideally)

**Nodes must NOT:**
- Know about other nodes
- Know about the runner
- Know about policies
- Contain control flow logic

### Runner

Executes a predefined sequence of Nodes:

```python
class GraphRunner:
    def __init__(self, nodes: Iterable[Node], policy: Policy):
        ...
    
    def run(self, state: GraphState) -> GraphState:
        ...
```

**Responsibilities:**
- Execute nodes in order
- Generate execution timestamps
- Emit execution events (NODE_STARTED, NODE_COMPLETED, NODE_SKIPPED)
- Enforce policy

**NOT responsible for:**
- Graph structure modification
- Business logic
- State validation
- Retry logic

### Policy

Execution constraint enum:

```python
class Policy(str, Enum):
    STRICT = "strict"          # Stop on first error
    EXPLORATION = "exploration"  # Skip failing nodes
```

**STRICT mode:**
- First exception propagates
- Execution stops immediately
- Use for production/critical paths

**EXPLORATION mode:**
- Failing nodes are skipped
- Errors recorded as NODE_SKIPPED events
- Execution continues
- Use for discovery/experimentation

## Timestamp Generation

**Contract:**
- GraphState never generates timestamps
- Runner generates execution timestamps (NODE_* events)
- Nodes may generate domain-specific timestamps (Fact, Decision, Rejection)

This separation ensures:
- State remains pure
- Execution trace is auditable
- Domain time vs execution time are explicit

## Data Flow

```
Initial State
     ↓
   Node 1  ──→  State₁  (immutable)
     ↓
   Node 2  ──→  State₂  (immutable)
     ↓
   Node 3  ──→  State₃  (immutable)
     ↓
  Final State
```

Each node receives the previous state and returns a new one.
State flows forward, never backward.

## Cognitive Trace Types

### Fact
Something known to be true:
```python
@dataclass(frozen=True)
class Fact:
    key: str
    value: Union[str, int, float, bool]  # Serializable types only
    timestamp: datetime
```

### Decision
A choice that was made:
```python
@dataclass(frozen=True)
class Decision:
    description: str
    timestamp: datetime
```

### Rejection
A path explicitly not taken:
```python
@dataclass(frozen=True)
class Rejection:
    description: str
    reason: str
    timestamp: datetime
```

### Event
Something that happened during execution:
```python
@dataclass(frozen=True)
class Event:
    type: EventType  # NODE_STARTED, NODE_COMPLETED, NODE_SKIPPED
    description: str
    timestamp: datetime
```

## Design Constraints

### What is Forbidden

❌ Generic state containers (`dict[str, Any]`)  
❌ Dynamic field addition  
❌ Nested state bags  
❌ In-place mutation  
❌ Abstract base classes for nodes  
❌ Hooks, callbacks, plugins  
❌ Configuration layers  
❌ Query languages  

### What is Required

✅ Explicit typing  
✅ Immutability  
✅ Single responsibility  
✅ Protocol-based interfaces  
✅ Compile-time graph structure  
✅ Human-readable state  
✅ Serializable types  

## Extension Model

Axis does NOT provide extension hooks.

To extend Axis:
1. Write new Nodes (just functions)
2. Compose them in your Runner
3. Add domain-specific fields to GraphState (fork if needed)

Axis is a kernel, not a framework.
Composition over configuration.
Forking over plugins.

## File Structure

```
axis/
├── state.py   # GraphState and cognitive trace types
├── node.py    # Node protocol
├── runner.py  # Graph execution
├── policy.py  # Execution policies
├── tests/     # Test suite
└── docs/      # Documentation
```

Each file is self-contained and minimal.
Total core: ~200 lines of code.
