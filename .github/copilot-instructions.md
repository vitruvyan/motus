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

## File Structure

```
axis/
├── state.py     # GraphState and cognitive trace types
├── node.py      # Node protocol
├── runner.py    # Graph execution
└── policy.py    # Execution policies
```

Each file is self-contained and minimal.

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

## Version
Last updated: January 6, 2026
