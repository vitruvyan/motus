# Axis Phase 2.3 - Persistence Layer Prompts

**Context:** You are implementing persistence for Axis, a minimal cognitive graph kernel.  
**Architecture:** Immutable GraphState, frozen dataclasses, zero external dependencies in core.  
**Goal:** Enable GraphState storage and retrieval for audit trails (MiFID II compliance requires 5-year retention).

---

## PROMPT 1: Serialization Methods (Foundation)

**Task:** Add `to_dict()` and `from_dict()` methods to all Axis types for JSON serialization.

**Files to modify:**
- `axis/state.py` (GraphState, Fact, Decision, Rejection, Event)
- `axis/epistemic_types.py` (Category, Relation, Intent, Implication, Pattern, Constraint, Violation, EpistemicState)
- `axis/events.py` (EventType enum if needed)

**Current GraphState structure:**
```python
@dataclass(frozen=True)
class GraphState:
    trace_id: str
    intent: Optional[str] = None
    facts: tuple[Fact, ...] = ()
    decisions: tuple[Decision, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    events: tuple[Event, ...] = ()
```

**Requirements:**

1. **Add `to_dict()` method to each dataclass:**
   - Convert all fields to JSON-serializable types
   - Handle tuples → lists (JSON doesn't support tuples)
   - Handle datetime → ISO 8601 strings
   - Handle nested types (recursive calls to `to_dict()`)
   - Handle Optional fields (None → null)

2. **Add `from_dict(cls, data: dict)` classmethod:**
   - Reconstruct immutable dataclass from dict
   - Convert lists → tuples
   - Convert ISO strings → datetime
   - Reconstruct nested types (recursive `from_dict()`)
   - Validate required fields are present

3. **Example pattern:**
```python
@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    source: str
    timestamp: datetime
    
    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Fact":
        return cls(
            key=data["key"],
            value=data["value"],
            source=data["source"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )
```

4. **Handle EventType enum:**
```python
# In to_dict()
"event_type": self.event_type.value

# In from_dict()
event_type=EventType(data["event_type"])
```

5. **Handle nested tuples (GraphState):**
```python
def to_dict(self) -> dict:
    return {
        "trace_id": self.trace_id,
        "intent": self.intent,
        "facts": [f.to_dict() for f in self.facts],
        "decisions": [d.to_dict() for d in self.decisions],
        # ...
    }

@classmethod
def from_dict(cls, data: dict) -> "GraphState":
    return cls(
        trace_id=data["trace_id"],
        intent=data.get("intent"),
        facts=tuple(Fact.from_dict(f) for f in data.get("facts", [])),
        decisions=tuple(Decision.from_dict(d) for d in data.get("decisions", [])),
        # ...
    )
```

**Acceptance Criteria:**
- [ ] All 13 types have `to_dict()` and `from_dict()`
- [ ] Round-trip test passes: `state == GraphState.from_dict(state.to_dict())`
- [ ] No external dependencies (use stdlib only: `datetime`, `json`)
- [ ] Immutability preserved (frozen=True)
- [ ] Handles empty tuples correctly
- [ ] Handles None/Optional fields

**Test to validate:**
```python
def test_graphstate_serialization():
    state = GraphState(
        trace_id="test-123",
        intent="Test intent",
        facts=(
            Fact("key1", "value1", "test", datetime.now()),
        ),
    )
    
    # Serialize
    data = state.to_dict()
    assert isinstance(data, dict)
    assert data["trace_id"] == "test-123"
    
    # Deserialize
    restored = GraphState.from_dict(data)
    assert restored.trace_id == state.trace_id
    assert len(restored.facts) == 1
    assert restored.facts[0].key == "key1"
```

---

## PROMPT 2: JSON Adapter

**Task:** Implement file-based JSON persistence adapter.

**File to create:** `axis/persistence/json_adapter.py` (~300 lines)

**Protocol to implement:**
```python
# axis/persistence/protocol.py (already exists as stub)
from typing import Protocol, Optional, Sequence
from axis.state import GraphState

class PersistenceProvider(Protocol):
    """Protocol for GraphState persistence backends."""
    
    def save(self, state: GraphState) -> None:
        """Save a GraphState. Idempotent (same trace_id overwrites)."""
        ...
    
    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load a GraphState by trace_id. Returns None if not found."""
        ...
    
    def query_by_timestamp(
        self, 
        start: datetime, 
        end: datetime
    ) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range."""
        ...
    
    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs, most recent first."""
        ...
    
    def delete(self, trace_id: str) -> bool:
        """Delete a trace. Returns True if existed."""
        ...
```

**Implementation requirements:**

1. **File structure:**
```python
class JSONAdapter:
    def __init__(self, base_path: str = "./axis_data"):
        """
        Args:
            base_path: Directory where JSON files are stored
                       Creates structure: base_path/traces/<trace_id>.json
        """
        self.base_path = Path(base_path)
        self.traces_dir = self.base_path / "traces"
        self.traces_dir.mkdir(parents=True, exist_ok=True)
```

2. **File naming:** `{base_path}/traces/{trace_id}.json`

3. **save() implementation:**
   - Call `state.to_dict()`
   - Write to `{trace_id}.json` with pretty printing
   - Use `json.dump()` with `indent=2`
   - Atomic write (write to temp file, then rename)
   - Handle file permissions

4. **load() implementation:**
   - Read JSON file
   - Call `GraphState.from_dict()`
   - Return None if file doesn't exist
   - Handle JSON parse errors gracefully

5. **query_by_timestamp():**
   - List all JSON files
   - Read each, parse timestamp from events
   - Filter by range
   - Return matching states

6. **list_traces():**
   - List files in traces_dir
   - Sort by mtime (most recent first)
   - Return trace IDs (filenames without .json)
   - Limit results

7. **delete():**
   - Remove JSON file
   - Return True if file existed
   - Return False if not found

**Error handling:**
- Wrap I/O operations in try/except
- Log errors (use stdlib `logging`)
- Don't crash on corrupted JSON

**Acceptance Criteria:**
- [ ] Save/load round-trip works
- [ ] Handles non-existent traces gracefully
- [ ] Query by timestamp filters correctly
- [ ] Atomic writes (no partial files)
- [ ] Thread-safe (use file locking if needed)
- [ ] No external dependencies (stdlib only)

**Test cases:**
```python
def test_json_adapter_save_load():
    adapter = JSONAdapter(base_path="/tmp/axis_test")
    state = GraphState(trace_id="test-123", intent="Test")
    
    adapter.save(state)
    loaded = adapter.load("test-123")
    
    assert loaded is not None
    assert loaded.trace_id == "test-123"

def test_json_adapter_not_found():
    adapter = JSONAdapter()
    assert adapter.load("nonexistent") is None
```

---

## PROMPT 3: SQLite Adapter

**Task:** Implement SQLite persistence adapter.

**File to create:** `axis/persistence/sqlite_adapter.py` (~400 lines)

**Schema design:**
```sql
CREATE TABLE traces (
    trace_id TEXT PRIMARY KEY,
    intent TEXT,
    data TEXT NOT NULL,  -- Full JSON serialization
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_traces_created_at ON traces(created_at DESC);
```

**Implementation requirements:**

1. **Initialization:**
```python
class SQLiteAdapter:
    def __init__(self, db_path: str = "./axis_data/axis.db"):
        """
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_schema()
    
    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                intent TEXT,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_traces_created_at 
            ON traces(created_at DESC)
        """)
        self.conn.commit()
```

2. **save() implementation:**
   - Serialize state to JSON string
   - INSERT OR REPLACE into traces table
   - Store trace_id, intent, full JSON data
   - Update updated_at timestamp

3. **load() implementation:**
   - SELECT data FROM traces WHERE trace_id = ?
   - Parse JSON string
   - Call `GraphState.from_dict()`
   - Return None if not found

4. **query_by_timestamp():**
   - Use created_at column for filtering
   - Parse timestamps from first event in each trace
   - More efficient than file scanning

5. **Connection management:**
   - Context manager support
   - Close connection on `__del__` or explicit `close()`
   - Handle concurrent access (SQLite write locks)

**Acceptance Criteria:**
- [ ] Schema auto-created on first connection
- [ ] Save/load round-trip works
- [ ] Query by timestamp uses SQL efficiently
- [ ] Handles concurrent writes gracefully
- [ ] No external dependencies (stdlib `sqlite3`)
- [ ] Connection cleanup on exit

**Test cases:**
```python
def test_sqlite_adapter_save_load():
    adapter = SQLiteAdapter(db_path="/tmp/axis_test.db")
    state = GraphState(trace_id="test-123", intent="Test")
    
    adapter.save(state)
    loaded = adapter.load("test-123")
    
    assert loaded is not None
    assert loaded.trace_id == "test-123"
    adapter.close()

def test_sqlite_adapter_query_timestamp():
    adapter = SQLiteAdapter()
    # Save multiple states
    # Query by date range
    # Verify filtering
    adapter.close()
```

---

## PROMPT 4: PostgreSQL Adapter

**Task:** Implement PostgreSQL persistence adapter.

**File to create:** `axis/persistence/postgresql_adapter.py` (~500 lines)

**Schema design:**
```sql
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY,
    intent TEXT,
    data JSONB NOT NULL,  -- PostgreSQL native JSON type
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traces_created_at ON traces(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_data_gin ON traces USING GIN (data);
```

**Implementation requirements:**

1. **Initialization:**
```python
import psycopg2
from psycopg2.extras import RealDictCursor

class PostgreSQLAdapter:
    def __init__(
        self, 
        host: str = "localhost",
        port: int = 5432,
        database: str = "axis",
        user: str = "axis",
        password: str = "",
    ):
        """
        Args:
            Connection parameters for PostgreSQL
        """
        self.conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        self._init_schema()
    
    def _init_schema(self):
        """Create tables if they don't exist."""
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    intent TEXT,
                    data JSONB NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_created_at 
                ON traces(created_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_data_gin 
                ON traces USING GIN (data)
            """)
        self.conn.commit()
```

2. **save() implementation:**
   - Use JSONB for efficient storage and querying
   - INSERT ... ON CONFLICT (trace_id) DO UPDATE
   - Use parameterized queries (prevent SQL injection)
   - Update updated_at on conflict

3. **load() implementation:**
   - SELECT data FROM traces WHERE trace_id = %s
   - PostgreSQL returns JSONB as dict directly
   - Call `GraphState.from_dict()`

4. **query_by_timestamp():**
   - Leverage GIN index on JSONB data
   - Query: `WHERE created_at BETWEEN %s AND %s`
   - Order by created_at DESC
   - LIMIT support

5. **Connection pooling (optional but recommended):**
```python
from psycopg2 import pool

class PostgreSQLAdapter:
    def __init__(self, ..., pool_size: int = 5):
        self.pool = pool.SimpleConnectionPool(
            1, pool_size,
            host=host, port=port, database=database,
            user=user, password=password
        )
```

**External dependency:** `psycopg2-binary`

**Note:** This is the ONLY adapter with external dependency. Document in `requirements.txt`:
```
# orders/requirements.txt (not in core axis/)
psycopg2-binary>=2.9.0
```

**Acceptance Criteria:**
- [ ] Schema auto-created on first connection
- [ ] Save/load round-trip works
- [ ] JSONB type used for efficient storage
- [ ] Query by timestamp uses indexed column
- [ ] Parameterized queries (no SQL injection)
- [ ] Connection pooling optional
- [ ] Document external dependency clearly

**Test cases:**
```python
def test_postgresql_adapter_save_load():
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    state = GraphState(trace_id="test-123", intent="Test")
    
    adapter.save(state)
    loaded = adapter.load("test-123")
    
    assert loaded is not None
    assert loaded.trace_id == "test-123"
    adapter.close()

def test_postgresql_jsonb_query():
    # Test JSONB query capabilities
    # e.g., WHERE data @> '{"intent": "specific value"}'
    pass
```

---

## Common Guidelines for All Adapters

1. **No mutation:** All methods return new GraphState instances or None
2. **Immutability:** Never modify loaded states
3. **Error handling:** Graceful degradation, log errors, don't crash
4. **Testing:** Each adapter needs 5+ tests minimum
5. **Documentation:** Docstrings with examples
6. **Type hints:** Full type annotations
7. **Logging:** Use stdlib `logging` for debugging
8. **Thread safety:** Document if NOT thread-safe

**Imports allowed:**
- stdlib only for JSON/SQLite adapters
- `psycopg2-binary` ONLY for PostgreSQL adapter
- No other external dependencies

**Testing approach:**
```python
# tests/test_persistence.py
import pytest
from axis.persistence.json_adapter import JSONAdapter
from axis.persistence.sqlite_adapter import SQLiteAdapter
from axis.state import GraphState, Fact
from datetime import datetime

@pytest.fixture
def sample_state():
    return GraphState(
        trace_id="test-123",
        intent="Test workflow",
        facts=(
            Fact("key1", "value1", "test", datetime.now()),
        ),
    )

def test_json_adapter(sample_state):
    adapter = JSONAdapter(base_path="/tmp/axis_json_test")
    adapter.save(sample_state)
    loaded = adapter.load("test-123")
    assert loaded.trace_id == sample_state.trace_id

def test_sqlite_adapter(sample_state):
    adapter = SQLiteAdapter(db_path="/tmp/axis_sqlite_test.db")
    adapter.save(sample_state)
    loaded = adapter.load("test-123")
    assert loaded.trace_id == sample_state.trace_id
    adapter.close()

# Add 8 more tests for full coverage
```

---

## Deliverables

**Agent 1 (Serialization):**
- Modified files: `axis/state.py`, `axis/epistemic_types.py`, `axis/events.py`
- Lines: ~150 new lines (methods added to existing classes)

**Agent 2 (JSON):**
- New file: `axis/persistence/json_adapter.py` (~300 lines)
- New file: `axis/persistence/protocol.py` (~50 lines, protocol definition)

**Agent 3 (SQLite):**
- New file: `axis/persistence/sqlite_adapter.py` (~400 lines)

**Agent 4 (PostgreSQL):**
- New file: `axis/persistence/postgresql_adapter.py` (~500 lines)
- New file: `axis/persistence/requirements.txt` (document psycopg2 dependency)

**All agents:**
- Add tests to `tests/test_persistence.py` (each agent contributes 3-5 tests)
- Update `axis/persistence/__init__.py` with exports

**Integration:**
Once all 4 agents complete, verify:
```python
# axis/persistence/__init__.py
from axis.persistence.protocol import PersistenceProvider
from axis.persistence.json_adapter import JSONAdapter
from axis.persistence.sqlite_adapter import SQLiteAdapter
from axis.persistence.postgresql_adapter import PostgreSQLAdapter

__all__ = [
    "PersistenceProvider",
    "JSONAdapter",
    "SQLiteAdapter",
    "PostgreSQLAdapter",
]
```

**Timeline:** Each agent should complete in parallel. Total: 1-2 days for all 4.

---

## Validation Checklist

After all agents complete:

- [ ] All 20 existing tests still pass
- [ ] 10+ new persistence tests pass
- [ ] Round-trip serialization works for all types
- [ ] JSON adapter stores/retrieves correctly
- [ ] SQLite adapter with SQL queries works
- [ ] PostgreSQL adapter with JSONB works
- [ ] No regressions in core (state.py still frozen)
- [ ] Documentation updated (README mentions persistence)
- [ ] Copilot instructions note Phase 2.3 complete

**Success metric:** 
```bash
cd /home/caravaggio/axis
python3 -m pytest tests/ -v
# Expected: 30+ tests passing (20 existing + 10 persistence)
```
