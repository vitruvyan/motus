# Vitruvyan Axis — Copilot Instructions

## Project Identity

**Vitruvyan Axis** is a minimal cognitive graph kernel.

- It is **NOT** a product
- It is **NOT** an LLM orchestrator
- It is **NOT** a general agent framework

It is a **foundation**: rigid, explicit, and deliberate in what it excludes.

---

## Core Principles

### 1. Implement, Do Not Redesign
- The architectural vision is already defined and correct
- Your role is to faithfully implement that vision
- Do NOT reinterpret or extend the architecture

### 2. Simplicity, Rigidity, Clarity
- These are intentional design goals, not limitations
- Minimize abstraction
- Avoid extensibility hooks
- Favor explicitness over flexibility

### 3. When Uncertain, STOP
- If something is unclear, ask for clarification
- Do NOT introduce assumptions
- Do NOT add "helpful" abstractions

### 4. Success Criteria
- Fidelity to the vision
- Absence of architectural drift
- Minimalism of the core
- Readability over cleverness

---

## Architecture Rules

### GraphState (`axis/state.py`)
- Immutable cognitive trace of execution
- NOT a graph data structure
- Append-only by extension (returns new instances)
- Explicit fields ONLY:
  - `trace_id: str`
  - `intent: Optional[str]`
  - `facts: tuple[Fact, ...]`
  - `decisions: tuple[Decision, ...]`
  - `rejections: tuple[Rejection, ...]`
  - `events: tuple[Event, ...]`
- **FORBIDDEN**: Generic dictionaries, dynamic fields, nested state bags

### Node (`axis/node.py`)
- Protocol-based, NOT abstract base class
- Single responsibility: `(GraphState) -> GraphState`
- Pure callable interface
- No knowledge of runner, policy, or other nodes
- **FORBIDDEN**: Hooks, callbacks, plugins, async, state mutation

### Runner (`axis/runner.py`)
- Executes predefined sequence of Nodes
- Enforces execution order and policy
- Generates timestamps during execution
- Does NOT modify graph structure
- Does NOT contain business logic

### Policy (`axis/policy.py`)
- Constrains behavior
- Does NOT modify graph structure
- Current modes:
  - `STRICT`: Stop on error
  - `EXPLORATION`: Skip on error, record event

---

## Development Constraints

### NEVER Add
- Generic containers or state bags
- Configuration layers
- Extensibility hooks
- Convenience abstractions
- Logging frameworks
- Validation logic beyond type hints
- Task scheduling or workflow engines
- User interfaces or deployment scaffolding

### ALWAYS Maintain
- Immutability (frozen dataclasses)
- Explicit typing
- Single responsibility per module
- Human-readable state representation
- Serializability

### Code Style
- English language ONLY
- Type hints required
- Docstrings for public APIs (brief and factual)
- No clever abstractions
- Prefer composition over inheritance
- Keep functions pure when possible

---

## File Structure (Official Tree)

```
axis/                           # Repository root
├── axis/                       # Python package (pip install axis)
│   ├── __init__.py            # Public API exports
│   ├── state.py               # Core: GraphState + trace types
│   ├── node.py                # Core: Node protocol
│   ├── runner.py              # Core: Runner execution engine
│   ├── policy.py              # Core: Policy enum (STRICT, EXPLORATION)
│   ├── synaptic_bus.py        # Phase 2.1: Dual Memory observer
│   ├── events.py              # Phase 2.1: Event types
│   ├── epistemic_types.py     # Phase 2.2: Epistemic types
│   ├── epistemic_protocols.py # Phase 2.2: Epistemic protocols
│   │
│   ├── persistence/           # Phase 2.3: GraphState storage
│   │   ├── __init__.py
│   │   ├── protocol.py        # PersistenceProvider protocol
│   │   ├── json_adapter.py    # JSON file backend
│   │   ├── sqlite_adapter.py  # SQLite backend
│   │   └── postgresql_adapter.py  # PostgreSQL backend
│   │
│   ├── recovery/              # Phase 2.3: Error resilience
│   │   ├── __init__.py
│   │   ├── retry.py           # Exponential backoff retry
│   │   ├── circuit_breaker.py # Circuit breaker pattern
│   │   └── timeout.py         # Timeout wrapper
│   │
│   ├── observability/         # Phase 2.3: Monitoring
│   │   ├── __init__.py
│   │   ├── metrics.py         # Prometheus metrics
│   │   ├── logging.py         # Structured JSON logs
│   │   └── tracing.py         # OpenTelemetry spans
│   │
│   └── streaming/             # Phase 2.3: Real-time execution
│       ├── __init__.py
│       ├── async_runner.py    # Async Runner
│       ├── event_stream.py    # Server-Sent Events
│       └── websocket.py       # WebSocket support
│
├── orders/                     # Phase 2.4+: Premium Orders (SEPARATE)
│   ├── __init__.py
│   ├── pattern_weaver/        # Semantic pattern detection
│   │   ├── __init__.py
│   │   ├── nodes.py
│   │   ├── graph.py
│   │   ├── config.py
│   │   ├── tests.py
│   │   ├── README.md
│   │   └── requirements.txt
│   ├── orthodoxy_warden/      # Constraint validation
│   ├── codex_hunter/          # Multi-source data fetching
│   └── babel_garden/          # Intent inference
│
├── poc/                        # Orchestrator demo (NOT part of kernel)
│   ├── nodes/                 # Example Node implementations
│   ├── orchestrator.py        # Orchestration layer ON TOP of Axis
│   ├── demo.py
│   └── requirements.txt
│
├── tests/                      # Test suite (root level)
│   ├── test_e2e.py            # Core kernel tests
│   ├── test_synaptic_bus.py   # Synaptic Bus tests
│   ├── test_epistemic_types.py # Epistemic types tests
│   ├── test_persistence.py    # Phase 2.3 (future)
│   ├── test_recovery.py       # Phase 2.3 (future)
│   └── test_streaming.py      # Phase 2.3 (future)
│
├── examples/                   # Usage examples (root level)
│   ├── vitruvyan_mock.py      # Protocol implementation example
│   └── demo_synaptic_bus.py   # Synaptic Bus demo
│
├── docs/                       # Documentation (root level)
│   ├── AXIS_WHITEPAPER.md
│   ├── BUSINESS_PLAN.md
│   ├── architecture.md
│   ├── roadmap_production.md
│   └── ...
│
├── setup.py                    # pip install axis
├── pyproject.toml              # Modern Python packaging
├── README.md
├── LICENSE
└── .gitignore
```

### Key Architectural Decisions

1. **`axis/` package = Complete kernel**
   - All core functionality (state, node, runner, policy, bus)
   - All epistemic types and protocols
   - All production features (persistence, recovery, observability, streaming)
   - Installable: `pip install axis`
   - Zero external dependencies (stdlib only)

2. **`orders/` = Premium capabilities (SEPARATE)**
   - Each Order is independent with own dependencies
   - Installable separately: `pip install axis-pattern-weaver`
   - NOT mixed with kernel (clean separation)
   - Can be monetized independently

3. **`poc/` = Example orchestrator (NOT kernel)**
   - Demonstrates how to BUILD ON TOP of Axis
   - Shows orchestration layer above immutable substrate
   - NOT part of Axis package
   - Has its own dependencies (OpenAI, etc.)

4. **`tests/`, `docs/`, `examples/` = Root level**
   - Standard Python project convention
   - `tests/` include tests for axis/ and orders/
   - `docs/` contains all documentation
   - `examples/` demonstrates Axis usage patterns

---

## When Making Changes

1. **Read existing code first** — understand current state
2. **Check alignment** — does this fit the vision?
3. **Minimize scope** — change only what's necessary
4. **Preserve simplicity** — remove complexity, don't add it
5. **Test conceptually** — would this survive architectural review?

---

## Forbidden Patterns

❌ Adding `@property` decorators unnecessarily  
❌ Creating base classes when Protocol suffices  
❌ Introducing `**kwargs` or `*args` for "flexibility"  
❌ Adding configuration files  
❌ Creating factory patterns or builders  
❌ Implementing query languages or DSLs  
❌ Adding middleware or interceptors  
❌ Creating plugin systems  

---

## Questions to Ask Before Implementing

1. Is this explicitly requested, or am I inferring?
2. Does this add abstraction? (If yes, stop)
3. Could this be simpler?
4. Does this maintain immutability?
5. Is this readable by someone unfamiliar with the codebase?

If you cannot answer these confidently, **STOP and ask for clarification**.

---

---

## Building ON TOP of Axis

### Pattern: Orchestrator Layer

Orchestration logic should be built **above** Axis, not inside it.

**Correct architecture:**
```
Orchestration Layer (routing, LLM calls, tool integration)
    ↓
Axis Kernel (immutable trace)
```

**Example (see poc/ directory):**
- Nodes inspect GraphState to make routing decisions
- All decisions are written to Axis (no silent routing)
- Rejections are explicit (record what was NOT done and why)
- Trace is complete and auditable

**Key principle:**
Every behavior decision must be recorded in the trace.
No "silent" routing or decision-making.

### What Lives Where

**In Axis (core):**
- GraphState definition
- Immutability guarantees
- Append-only extension
- Trace types (Fact, Decision, Rejection, Event)

**Above Axis (orchestration):**
- LLM integration
- Tool calling
- Routing logic
- Error recovery
- Domain-specific nodes

**NEVER mix these layers.**

---

## Version
Last updated: January 6, 2026
