# Epistemic Types & Protocols

**Phase 2.2: Foundational Substrate for Knowledge Organization**

---

## Overview

Axis Phase 2.2 introduces **epistemic types** — immutable data structures for organizing and interpreting execution knowledge.

These types are **substrate only**:
- ✅ Define STRUCTURE (what is stored)
- ❌ No IMPLEMENTATION (how to compute)
- ❌ No ALGORITHMS (no clustering, no inference logic)
- ❌ No INFRASTRUCTURE (no databases, no external dependencies)

**Axis provides the types. Vitruvyan provides the intelligence.**

---

## Design Principles

### 1. Inert Substrate
Types are **frozen dataclasses** with no behavior.
```python
@dataclass(frozen=True)
class Category:
    name: str
    facts: tuple[Fact, ...]
    confidence: float
```

### 2. Domain-Agnostic
Zero finance-specific terminology. Works for medical AI, legal reasoning, any domain.

### 3. Protocol-Based
Protocols define INTERFACES without implementations.
```python
class OntologyProvider(Protocol):
    def categorize(self, facts: tuple[Fact, ...]) -> tuple[Category, ...]: ...
```

### 4. Composable
Orders combine multiple protocols to create epistemic capabilities.

---

## The Types

### Knowledge Organization

**Extracted from:** Vitruvyan Pattern Weavers, Codex Hunters

#### `Category`
Immutable grouping of facts by emergent concept.
- **NOT predefined** (e.g., no "stocks", "commodities" hardcoded)
- **Discovered** during observation
- **Confidence-scored** (0.0-1.0)

```python
@dataclass(frozen=True)
class Category:
    name: str                 # Emergent label (e.g., "market-volatility")
    facts: tuple[Fact, ...]   # Facts in this category
    confidence: float         # How certain is this grouping
```

**Vitruvyan implementation:**
- Uses Qdrant vector search (MiniLM-L6-v2 embeddings)
- GPT-4o-mini for ontology classification
- Finance-specific: "stocks", "bonds", "macro"

**Research implementation might use:**
- Topic modeling (LDA, NMF)
- Hierarchical clustering
- Rule-based keyword matching

#### `Relation`
Immutable link between two facts.
- **Semantic connection** (e.g., "implies", "contradicts", "supports")
- **Confidence-scored**
- **Directed** (source → target)

```python
@dataclass(frozen=True)
class Relation:
    source: Fact
    target: Fact
    relation_type: str        # Symbolic: "implies", "contradicts", etc.
    confidence: float
```

---

### Semantic Interpretation

**Extracted from:** Vitruvyan Babel Gardens, Pattern Weavers

#### `Intent`
Inferred purpose of execution sequence.
- **NOT declared** by user
- **Derived** from event patterns
- **Evidence-backed** (which events support this)

```python
@dataclass(frozen=True)
class Intent:
    description: str                # What was the system trying to do
    evidence: tuple[str, ...]       # Supporting event descriptions
    confidence: float
```

**Example:**
```python
Intent(
    description="Resolve ticker symbol from natural language query",
    evidence=("Intent declared: 'find AAPL price'", "Decision: route to ticker_resolver"),
    confidence=0.92
)
```

#### `Implication`
Logical consequence derived from a decision.
- **NOT executed** (only inferred)
- **Follows from** decisions + facts
- **Supporting facts** explicit

```python
@dataclass(frozen=True)
class Implication:
    premise: Decision
    consequence: str
    supporting_facts: tuple[Fact, ...]
    confidence: float
```

#### `Pattern`
Recurring execution structure detected in trace.
- **Domain-agnostic** structure (not domain semantics)
- **Occurrences counted** across traces
- **Temporal tracking** (first/last seen)

```python
@dataclass(frozen=True)
class Pattern:
    pattern_type: str         # "sequential", "branching", "cyclical"
    elements: tuple[str, ...]  # Node names or decision types
    occurrences: int
    first_seen_trace: str
    last_seen_trace: str
```

---

### Epistemic Validation

**Extracted from:** Vitruvyan Orthodoxy Wardens

#### `Constraint`
Epistemic invariant that should hold.
- **Symbolic** (not executable predicate)
- **NOT enforced** by Axis (observation is post-hoc)
- **Documented** for audit/interpretation

```python
@dataclass(frozen=True)
class Constraint:
    description: str          # Human-readable
    constraint_type: str      # "consistency", "completeness", "coherence"
    scope: str                # "facts", "decisions", "trace"
```

**Example:**
```python
Constraint(
    description="All market data facts must have timestamp",
    constraint_type="completeness",
    scope="facts"
)
```

#### `Violation`
Record of constraint violation detected.
- **Does NOT halt execution** (observation is post-hoc)
- **Records inconsistency** for audit
- **Traced** to specific element

```python
@dataclass(frozen=True)
class Violation:
    constraint: Constraint
    violating_element: Any    # Fact, Decision, or other
    trace_id: str
    detected_at: str          # ISO 8601 timestamp
```

---

### Composite State

#### `EpistemicState`
Snapshot of Order's interpreted knowledge.
- **Aggregates** all epistemic structures for a trace
- **Stored in Secondary Memory** (NOT Primary/GraphState)
- **Serializable** for persistence

```python
@dataclass(frozen=True)
class EpistemicState:
    trace_id: str
    categories: tuple[Category, ...]
    relations: tuple[Relation, ...]
    intents: tuple[Intent, ...]
    implications: tuple[Implication, ...]
    patterns: tuple[Pattern, ...]
    violations: tuple[Violation, ...]
    
    def is_empty(self) -> bool: ...
```

---

## The Protocols

### `OntologyProvider`
Organizes facts into emergent categories and relations.

```python
class OntologyProvider(Protocol):
    def categorize(self, facts: tuple[Fact, ...]) -> tuple[Category, ...]: ...
    def relate(self, facts: tuple[Fact, ...]) -> tuple[Relation, ...]: ...
```

**Vitruvyan implementation:**
- `categorize()`: Qdrant vector search + GPT-4o-mini classification
- `relate()`: Semantic similarity via embeddings

**Alternative implementations:**
- Topic modeling (LDA)
- Rule-based keyword matching
- Graph neural networks

---

### `SemanticInterpreter`
Derives implicit meaning from explicit events.

```python
class SemanticInterpreter(Protocol):
    def infer_intent(self, state: GraphState) -> Optional[Intent]: ...
    def derive_implications(
        self,
        decision: Decision,
        facts: tuple[Fact, ...]
    ) -> tuple[Implication, ...]: ...
```

**Vitruvyan implementation:**
- `infer_intent()`: MiniLM embeddings + pattern matching
- `derive_implications()`: LLM-based reasoning (GPT-4o-mini)

**Alternative implementations:**
- Template-based inference
- Symbolic logic (Prolog-style)
- Bayesian reasoning

---

### `PatternDetector`
Identifies recurring structures in execution traces.

```python
class PatternDetector(Protocol):
    def detect_patterns(
        self,
        current_state: GraphState,
        historical_states: tuple[GraphState, ...]
    ) -> tuple[Pattern, ...]: ...
```

**Vitruvyan implementation:**
- Custom pattern matching on trace structure
- Threshold-based recurrence detection

**Alternative implementations:**
- Sequential pattern mining (PrefixSpan, SPADE)
- Neural sequence models (LSTM, Transformers)
- Exact sequence matching

---

### `ConstraintChecker`
Validates epistemic consistency of trace.

```python
class ConstraintChecker(Protocol):
    def check_consistency(self, state: GraphState) -> tuple[Violation, ...]: ...
    def get_constraints(self) -> tuple[Constraint, ...]: ...
```

**Vitruvyan implementation (Orthodoxy Wardens):**
- Pandas DataFrame schema validation
- Financial rules: OHLC data completeness, date ranges

**Alternative implementations:**
- First-order logic constraints
- Assertion-based checks
- Statistical anomaly detection

---

### `EpistemicMemory`
Manages Secondary Memory for epistemic state.

```python
class EpistemicMemory(Protocol):
    def persist(self, trace_id: str, state: EpistemicState) -> None: ...
    def retrieve(self, trace_id: str) -> Optional[EpistemicState]: ...
    def query_patterns(self, pattern_type: str) -> tuple[Pattern, ...]: ...
    def query_violations(self, constraint_type: str) -> tuple[Violation, ...]: ...
```

**Vitruvyan implementation:**
- PostgreSQL for structured data
- Qdrant for vector search
- Redis for fast lookup

**Alternative implementations:**
- JSON files (simple)
- SQLite (embedded)
- RDF triple store (semantic web)

---

## Dual Memory Model

**Primary Memory (GraphState):**
- Immutable execution truth
- Facts, Decisions, Rejections, Events
- Written ONLY by Runner during execution
- Authority: What actually happened

**Secondary Memory (EpistemicState):**
- Mutable interpretation layer
- Categories, Relations, Patterns, Violations
- Written by Orders AFTER observation
- Authority: What it might mean

**Separation ensures:**
- ✅ Execution truth is never corrupted by interpretation
- ✅ Multiple interpretations coexist (different Orders)
- ✅ Audit trail distinguishes fact from opinion
- ✅ Compliance: Clear ownership (who wrote what)

---

## Why This Matters for MiFID II

### Audit Trail Requirements

**MiFID II Article 17:**
> "Investment firms shall keep at the disposal of the competent authority, for at least five years, the relevant data relating to all orders and transactions in financial instruments."

**Axis compliance:**
- ✅ Immutable Primary Memory (GraphState)
- ✅ Dual timestamp (observed_at + execution_ts)
- ✅ Violations recorded, not hidden
- ✅ Clear separation execution/interpretation

### Explainability Requirements

**GDPR Article 22 + MiFID II:**
> "Right to obtain human intervention, to express his or her point of view and to contest the decision."

**Axis compliance:**
- ✅ Intent inference (why did system act this way)
- ✅ Implication derivation (what follows from decisions)
- ✅ Constraint violations explicit (what rules were broken)

---

## Comparison: LangGraph vs Axis

| Feature | LangGraph | Axis |
|---------|-----------|------|
| **State mutability** | Mutable dict | Immutable frozen dataclasses |
| **Epistemic types** | None (custom dict keys) | Built-in (Category, Pattern, etc.) |
| **Observation** | Callbacks (can modify) | SynapticBus (passive) |
| **Memory model** | Single state object | Dual (Primary + Secondary) |
| **Compliance** | Via instrumentation | By design |

---

## Usage Example

```python
from axis.epistemic_types import Category, Intent, EpistemicState
from axis.epistemic_protocols import OntologyProvider, SemanticInterpreter

# Vitruvyan implements protocols
class VitruvyanOntology:
    """Concrete implementation with Qdrant + GPT-4o-mini."""
    
    def __init__(self, qdrant_client, openai_client):
        self.qdrant = qdrant_client
        self.llm = openai_client
    
    def categorize(self, facts: tuple[Fact, ...]) -> tuple[Category, ...]:
        # Real implementation: embeddings, vector search, LLM classification
        embeddings = self._compute_embeddings(facts)
        clusters = self.qdrant.search(embeddings)
        categories = self.llm.classify(clusters)
        return tuple(Category(name=c, facts=f, confidence=s) 
                     for c, f, s in categories)

# Order uses protocols
class PatternWeaverOrder:
    def __init__(
        self,
        ontology: OntologyProvider,
        semantics: SemanticInterpreter
    ):
        self.ontology = ontology
        self.semantics = semantics
    
    def on_event(self, event: BusEvent) -> None:
        if event.event_type == BusEventType.EXECUTION_COMPLETED:
            # Organize knowledge
            categories = self.ontology.categorize(event.state.facts)
            
            # Infer intent
            intent = self.semantics.infer_intent(event.state)
            
            # Store in Secondary Memory
            epistemic_state = EpistemicState(
                trace_id=event.state.trace_id,
                categories=categories,
                intents=(intent,) if intent else (),
                relations=(),
                implications=(),
                patterns=(),
                violations=()
            )
            self.memory.persist(event.state.trace_id, epistemic_state)
```

---

## Next Steps

**Phase 2.3: Reference Implementations**
- Example OntologyProvider (simple rule-based)
- Example SemanticInterpreter (template-based)
- Example Order combining protocols

**Phase 2.4: Vitruvyan Integration**
- Migrate Vitruvyan Sacred Orders to Axis protocols
- Replace LangGraph with Axis Runner
- Demonstrate MiFID II compliance

---

## Summary

**What Axis provides (Phase 2.2):**
- 8 immutable types (Category, Relation, Intent, Implication, Pattern, Constraint, Violation, EpistemicState)
- 5 protocols (OntologyProvider, SemanticInterpreter, PatternDetector, ConstraintChecker, EpistemicMemory)
- Total: ~300 lines of pure structure (no logic)

**What Vitruvyan provides (implementations):**
- Algorithms (clustering, inference, validation)
- Infrastructure (Qdrant, PostgreSQL, Redis)
- Domain logic (finance-specific semantics)
- Total: ~21,000 lines of intelligence

**The separation is clean. The boundary is clear. The compliance is architectural.**

---

**Axis is the index. Vitruvyan is the story.**
