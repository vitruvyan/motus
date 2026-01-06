# Vitruvyan Axis

**A minimal cognitive graph kernel.**

Axis is a foundation for building systems that maintain an explicit, immutable cognitive trace of execution.

[![License](https://img.shields.io/badge/license-TBD-blue.svg)](LICENSE)

---

## What Axis IS

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

---

## Core Principles

### Immutability
State is never modified. Every transformation returns a new GraphState instance.

### Explicitness
No generic containers. Every piece of state has a named, typed field.

### Simplicity
Minimize abstraction. No hooks, no callbacks, no extensibility layers.

### Rigidity
The graph structure is defined at compile-time, not runtime.

---

## Quick Start

### Basic Example

```python
from datetime import datetime
from state import GraphState, Fact
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

### Run Tests

```bash
python3 tests/test_e2e.py
```

---

## Proof of Concept: Orchestrator

See [poc/](poc/) for a complete demonstration of an LLM orchestrator built on Axis.

The PoC demonstrates:
- ✅ Routing decisions written to trace (no silent decisions)
- ✅ Explicit rejection recording (know what paths were NOT taken)
- ✅ Automatic explainability (generate explanations from trace)
- ✅ Complete auditability (every decision, rejection, and fact recorded)
- ✅ Real OpenAI integration with graceful fallback

**Run the demo:**

```bash
# Mock version (no API key needed)
python3 poc/demo.py

# Real OpenAI integration
cp .env.example .env  # Add your API key
python3 poc/demo_openai.py
```

**Key insight:** The orchestrator tells you not just **what** happened, but **why** (including what didn't happen and why not).

---

## Documentation

- [Architecture](docs/architecture.md) — Core concepts and design
- [API Reference](docs/api.md) — Complete API documentation
- [Examples](docs/examples.md) — Usage patterns
- [Design Principles](docs/principles.md) — Architectural constraints
- [PoC README](poc/README.md) — Orchestrator demonstration

---

## Installation

```bash
# Clone repository
git clone https://github.com/vitruvyan/axis.git
cd axis

# Run tests
python3 tests/test_e2e.py

# Optional: For PoC with OpenAI
pip install -r poc/requirements.txt
```

---

## Philosophy

Axis is **minimal by design**. Every line of code exists for a reason. Every constraint serves a purpose. Every exclusion is deliberate.

Axis is not a framework to be extended. It is a kernel to be composed.

**Total core: ~200 lines of code.**

---

## Project Status

- ✅ Core kernel: Complete
- ✅ Test suite: Complete
- ✅ Documentation: Complete
- ✅ Proof of concept: Complete

Axis is **not** in active development. It is feature-complete by definition.

---

## License

[To be determined]

---

## Contributing

Axis accepts bug fixes but not feature additions. The kernel is intentionally minimal.

To extend Axis, build orchestration layers **on top** of it (see `poc/` for an example).

---

**Built with intention. Minimal by choice.**
