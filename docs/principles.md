# Design Principles

## Core Philosophy

Vitruvyan Axis is **minimal by design**.

Every line of code exists for a reason.
Every constraint serves a purpose.
Every exclusion is deliberate.

---

## 1. Implement, Do Not Redesign

**Principle:** The architectural vision is already defined and correct.

**Implications:**
- Do not reinterpret the architecture
- Do not extend beyond the specification
- Do not add "helpful" abstractions
- When uncertain, STOP and ask

**Rationale:**
Axis is not a framework to be extended. It is a kernel to be composed.

---

## 2. Simplicity, Rigidity, Clarity

**Principle:** These are intentional design goals, not limitations.

### Simplicity
- Minimize abstraction layers
- Prefer functions over classes
- Prefer composition over inheritance
- Total core: ~200 lines

### Rigidity
- No configuration files
- No plugin systems
- No dynamic behavior
- Graph structure is code, not data

### Clarity
- Explicit types everywhere
- No magic behavior
- Human-readable state
- Self-documenting code

**Rationale:**
Complexity is the enemy of correctness. Simplicity enables reasoning.

---

## 3. Immutability Throughout

**Principle:** State is never modified, only extended.

**Implementation:**
- All dataclasses are `frozen=True`
- All collections are tuples (immutable)
- All methods return new instances
- No in-place operations

**Rationale:**
- Immutable data is thread-safe by default
- Time-travel debugging is trivial
- No hidden side effects
- Easier to reason about

---

## 4. Explicitness Over Flexibility

**Principle:** Make the implicit explicit.

**Examples:**

❌ **Forbidden:**
```python
state.data["arbitrary_key"] = value  # Generic bag
```

✅ **Required:**
```python
state.with_fact(Fact(key="name", value=value, timestamp=now))
```

❌ **Forbidden:**
```python
class Node(ABC):
    @abstractmethod
    def execute(self, state: GraphState) -> GraphState:
        ...
```

✅ **Required:**
```python
class Node(Protocol):
    def __call__(self, state: GraphState) -> GraphState:
        ...
```

**Rationale:**
Explicit code is predictable code. No surprises, no magic.

---

## 5. No Generic Containers

**Principle:** Every piece of state must have a named, typed field.

**Forbidden:**
- `dict[str, Any]` for state
- `**kwargs` for flexibility
- Dynamic field addition
- Nested generic containers

**Required:**
- Explicit fields in GraphState
- Constrained types (Union[str, int, float, bool])
- Fixed schema

**Rationale:**
Generic containers enable semantic opacity and architectural drift.

---

## 6. Protocol Over Inheritance

**Principle:** Prefer structural typing over nominal typing.

**Why Protocol:**
- More flexible (functions, classes, lambdas all work)
- No forced inheritance
- Easier testing
- More functional style

**Why NOT ABC:**
- Forces inheritance hierarchy
- Requires explicit subclassing
- Less flexible
- More ceremony

**Rationale:**
Composition and structural typing enable simpler, more testable code.

---

## 7. Timestamps: Separation of Concerns

**Principle:** GraphState never generates time.

**Contract:**
- **Runner** generates execution timestamps (NODE_* events)
- **Nodes** may generate domain timestamps (Fact, Decision, Rejection)
- **GraphState** only stores timestamps, never creates them

**Rationale:**
- State remains pure and testable
- Clear separation between execution time and domain time
- Enables deterministic testing

---

## 8. Graph Structure is Code

**Principle:** The graph does not change shape at runtime.

**What this means:**
- Nodes are defined at compile-time
- Execution order is fixed
- No dynamic node injection
- No conditional graph structure

**How to "change" the graph:**
- Write different Nodes
- Compose them differently
- Conditional logic INSIDE nodes is fine
- Conditional graph STRUCTURE is not

**Rationale:**
Runtime graph modification introduces complexity and makes reasoning impossible.

---

## 9. Errors are Values (Policy-Dependent)

**Principle:** How errors are handled is explicit and constrained.

**STRICT Policy:**
- Errors propagate immediately
- Execution stops
- Caller handles recovery

**EXPLORATION Policy:**
- Errors are recorded as events
- Execution continues
- Partial results returned

**No middleware, no hooks, no customization.**

**Rationale:**
Error handling is not extensible behavior. It's a binary choice.

---

## 10. Readability Over Cleverness

**Principle:** Code should be boring.

**Guidelines:**
- Prefer explicit over implicit
- Prefer verbose over terse
- Prefer obvious over elegant
- No language tricks
- No metaprogramming

**Example:**

❌ **Clever:**
```python
def __getattr__(self, name):
    return self._data.get(name)
```

✅ **Boring:**
```python
def with_fact(self, fact: Fact) -> "GraphState":
    return GraphState(
        trace_id=self.trace_id,
        intent=self.intent,
        facts=self.facts + (fact,),
        decisions=self.decisions,
        rejections=self.rejections,
        events=self.events,
    )
```

**Rationale:**
Code is read 10x more than it's written. Optimize for the reader.

---

## 11. Single Responsibility

**Principle:** Each component does one thing.

**Responsibilities:**

| Component | Does | Does NOT |
|-----------|------|----------|
| GraphState | Store cognitive trace | Generate time, validate, query |
| Node | Transform state | Know about other nodes, runner, policy |
| Runner | Execute sequence | Modify graph structure, contain business logic |
| Policy | Constrain behavior | Modify state, implement retry logic |

**Rationale:**
Single responsibility makes components replaceable and testable.

---

## 12. No Extension Points

**Principle:** Axis does not provide hooks, plugins, or extension mechanisms.

**To extend Axis:**
1. Write new Nodes (just functions)
2. Compose them in your application
3. Fork if you need to change the core

**Why no extensions:**
- Extension points add complexity
- They enable architectural drift
- They make reasoning harder
- Forking is better than plugins

**Rationale:**
Axis is a kernel, not a framework. It's meant to be composed, not configured.

---

## Questions to Ask Before Adding

Before implementing any feature, ask:

1. **Is this explicitly requested?**
   - If no: STOP

2. **Does this add abstraction?**
   - If yes: STOP

3. **Could this be simpler?**
   - If yes: simplify

4. **Does this maintain immutability?**
   - If no: STOP

5. **Is this readable by someone unfamiliar with the codebase?**
   - If no: rewrite

If you cannot answer these confidently, **STOP and ask for clarification**.

---

## Success Criteria

Your implementation will be evaluated on:

1. **Fidelity to the vision**
   - Does it match the specification exactly?

2. **Absence of architectural drift**
   - Did you add anything not requested?

3. **Minimalism of the core**
   - Is every line necessary?

4. **Readability over cleverness**
   - Can a junior developer understand it?

---

## Forbidden Patterns

Never add:

❌ `@property` decorators unnecessarily  
❌ Base classes when Protocol suffices  
❌ `**kwargs` or `*args` for "flexibility"  
❌ Configuration files  
❌ Factory patterns or builders  
❌ Query languages or DSLs  
❌ Middleware or interceptors  
❌ Plugin systems  
❌ Retry logic  
❌ Validation frameworks  
❌ Logging frameworks  
❌ ORM-like abstractions  

---

## Required Patterns

Always maintain:

✅ Immutability (`frozen=True`)  
✅ Explicit typing  
✅ Single responsibility  
✅ Protocol-based interfaces  
✅ Human-readable state  
✅ Serializable types  
✅ Pure functions (when possible)  
✅ Composition over inheritance  

---

## Conclusion

Vitruvyan Axis is minimal **by choice**, not by limitation.

Every constraint serves correctness.
Every exclusion serves clarity.
Every simplification serves understanding.

**When in doubt, do less.**
