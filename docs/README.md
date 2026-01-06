# Vitruvyan Axis

**A minimal cognitive graph kernel.**

## What Axis IS

Axis is a foundation for building systems that maintain an explicit, immutable cognitive trace of execution. It provides:

- **GraphState**: Immutable cognitive trace (facts, decisions, rejections, events)
- **Node**: Pure transformation protocol `(GraphState) -> GraphState`
- **Runner**: Sequential execution with policy enforcement
- **Policy**: Execution constraints (STRICT, EXPLORATION)

## What Axis IS NOT

- ❌ Not a product
- ❌ Not an LLM orchestrator
- ❌ Not a general agent framework
- ❌ Not a workflow engine
- ❌ Not a plugin system

## Core Principles

### Immutability
State is never modified. Every transformation returns a new GraphState instance.

### Explicitness
No generic containers. Every piece of state has a named, typed field.

### Simplicity
Minimize abstraction. No hooks, no callbacks, no extensibility layers.

### Rigidity
The graph structure is defined at compile-time, not runtime.

## Quick Example

```python
from datetime import datetime
from state import GraphState, Fact
from node import Node
from runner import GraphRunner
from policy import Policy

# Define a node (simple function)
def add_observation(state: GraphState) -> GraphState:
    fact = Fact(
        key="temperature",
        value=23.5,
        timestamp=datetime.utcnow()
    )
    return state.with_fact(fact)

# Create runner
runner = GraphRunner(
    nodes=[add_observation],
    policy=Policy.STRICT
)

# Execute
initial = GraphState.empty("trace_001")
final = runner.run(initial)

print(f"Facts: {len(final.facts)}")  # Facts: 1
```

## Documentation

- [Architecture](architecture.md) — Core concepts and design
- [API Reference](api.md) — Complete API documentation
- [Examples](examples.md) — Usage patterns
- [Design Principles](principles.md) — Architectural constraints

## Installation

```bash
# Clone repository
git clone <repository-url>
cd axis

# Run tests
python3 tests/test_e2e.py
```

## License

[To be determined]
