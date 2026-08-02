"""
Axis Phase 2.3 - Persistence Tests
Test suite for JSON, SQLite, and PostgreSQL adapters.
"""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import shutil

from axis.state import GraphState, Fact, Decision, Rejection, Event, EventType
from axis.epistemic_types import Category, Pattern, EpistemicState


def _postgresql_available() -> bool:
    """Check if PostgreSQL is available for testing."""
    try:
        import psycopg2
        # Try to connect to a test database
        conn = psycopg2.connect(
            host="localhost",
            database="axis_test",
            user="postgres",
            password="test",
            connect_timeout=5
        )
        conn.close()
        return True
    except Exception:
        return False


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def sample_state():
    """Create a sample GraphState for testing."""
    now = datetime.now(timezone.utc)
    return GraphState(
        trace_id="test-trace-123",
        intent="Test workflow execution",
        facts=(
            Fact("stock_price", 150.25, "yahoo_finance", now),
            Fact("sentiment", "positive", "news_api", now),
        ),
        decisions=(
            Decision("Buy AAPL shares", now),
        ),
        rejections=(
            Rejection("Sell order", "Insufficient balance", now),
        ),
        events=(
            Event(EventType.NODE_STARTED, "Processing started", now),
            Event(EventType.NODE_COMPLETED, "Processing completed", now + timedelta(seconds=5)),
        ),
    )


@pytest.fixture
def sample_epistemic_state():
    """Create a sample EpistemicState for testing."""
    now = datetime.now()
    fact = Fact("test_key", "test_value", "test_source", now)
    
    return EpistemicState(
        trace_id="epistemic-test-123",
        categories=(
            Category("financial", (fact,), 0.95),
        ),
        relations=(),
        intents=(),
        implications=(),
        patterns=(
            Pattern("buy_signal", (fact,), 3, ("trace1", "trace2", "trace3"), now),
        ),
        violations=(),
    )


@pytest.fixture
def temp_dir():
    """Create temporary directory for testing, cleanup after."""
    tmp = tempfile.mkdtemp()
    yield tmp
    shutil.rmtree(tmp)


# ============================================================================
# JSON Adapter Tests
# ============================================================================

def test_json_adapter_save_load(sample_state, temp_dir):
    """Test JSON adapter save and load."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    
    # Save
    adapter.save(sample_state)
    
    # Load
    loaded = adapter.load("test-trace-123")
    
    assert loaded is not None
    assert loaded.trace_id == sample_state.trace_id
    assert loaded.intent == sample_state.intent
    assert len(loaded.facts) == 2
    assert loaded.facts[0].key == "stock_price"


def test_json_adapter_not_found(temp_dir):
    """Test JSON adapter returns None for non-existent trace."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    loaded = adapter.load("nonexistent-trace")
    
    assert loaded is None


def test_json_adapter_list_traces(sample_state, temp_dir):
    """Test JSON adapter list_traces."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    
    # Save multiple states
    adapter.save(sample_state)
    adapter.save(GraphState(
        trace_id="trace-456", 
        intent="Another test",
        facts=(),
        decisions=(),
        rejections=(),
        events=()
    ))
    
    # List traces
    traces = adapter.list_traces()
    
    assert len(traces) >= 2
    assert "test-trace-123" in traces
    assert "trace-456" in traces


def test_json_adapter_delete(sample_state, temp_dir):
    """Test JSON adapter delete."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    
    # Save
    adapter.save(sample_state)
    assert adapter.load("test-trace-123") is not None
    
    # Delete
    result = adapter.delete("test-trace-123")
    assert result is True
    
    # Verify deleted
    assert adapter.load("test-trace-123") is None
    
    # Delete non-existent
    result = adapter.delete("nonexistent")
    assert result is False


def test_json_adapter_query_by_timestamp(sample_state, temp_dir):
    """Test JSON adapter query by timestamp."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    
    # Save state with events
    adapter.save(sample_state)

    # Query by timestamp (should find it). The adapter reloads via
    # GraphState.from_dict, which normalizes naive timestamps to UTC
    # (axis.events.parse_timestamp) — query bounds must be tz-aware too,
    # or the comparison inside query_by_timestamp raises TypeError.
    now = datetime.now(timezone.utc)
    results = adapter.query_by_timestamp(
        start=now - timedelta(hours=1),
        end=now + timedelta(hours=1)
    )
    
    assert len(results) >= 1
    assert any(s.trace_id == "test-trace-123" for s in results)


# ============================================================================
# SQLite Adapter Tests
# ============================================================================

def test_sqlite_adapter_save_load(sample_state, temp_dir):
    """Test SQLite adapter save and load."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    adapter = SQLiteAdapter(db_path=str(db_path))
    
    # Save
    adapter.save(sample_state)
    
    # Load
    loaded = adapter.load("test-trace-123")
    
    assert loaded is not None
    assert loaded.trace_id == sample_state.trace_id
    assert len(loaded.facts) == 2
    
    adapter.close()


def test_sqlite_adapter_not_found(temp_dir):
    """Test SQLite adapter returns None for non-existent trace."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    adapter = SQLiteAdapter(db_path=str(db_path))
    
    loaded = adapter.load("nonexistent-trace")
    assert loaded is None
    
    adapter.close()


def test_sqlite_adapter_list_traces(sample_state, temp_dir):
    """Test SQLite adapter list_traces."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    adapter = SQLiteAdapter(db_path=str(db_path))
    
    # Save multiple states
    adapter.save(sample_state)
    adapter.save(GraphState(
        trace_id="trace-456", 
        intent="Another test",
        facts=(),
        decisions=(),
        rejections=(),
        events=(),
    ))
    
    # List traces
    traces = adapter.list_traces()
    
    assert len(traces) >= 2
    assert "test-trace-123" in traces
    assert "trace-456" in traces
    
    adapter.close()


def test_sqlite_adapter_delete(sample_state, temp_dir):
    """Test SQLite adapter delete."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    adapter = SQLiteAdapter(db_path=str(db_path))
    
    # Save
    adapter.save(sample_state)
    assert adapter.load("test-trace-123") is not None
    
    # Delete
    result = adapter.delete("test-trace-123")
    assert result is True
    
    # Verify deleted
    assert adapter.load("test-trace-123") is None
    
    adapter.close()


def test_sqlite_adapter_concurrent_writes(sample_state, temp_dir):
    """Test SQLite adapter handles concurrent writes."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    
    # Create multiple adapters (simulating concurrent connections)
    adapter1 = SQLiteAdapter(db_path=str(db_path))
    adapter2 = SQLiteAdapter(db_path=str(db_path))
    
    # Write from both
    adapter1.save(sample_state)
    adapter2.save(GraphState(
        trace_id="trace-456", 
        intent="Concurrent",
        facts=(),
        decisions=(),
        rejections=(),
        events=(),
    ))
    
    # Verify both exist
    assert adapter1.load("test-trace-123") is not None
    assert adapter2.load("trace-456") is not None
    
    adapter1.close()
    adapter2.close()


def test_sqlite_adapter_query_by_timestamp(sample_state, temp_dir):
    """Test SQLite adapter query_by_timestamp."""
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    db_path = Path(temp_dir) / "test.db"
    adapter = SQLiteAdapter(db_path=str(db_path))
    
    # Save state
    adapter.save(sample_state)
    
    # Query with wide range
    start = datetime(2020, 1, 1)
    end = datetime(2030, 1, 1)
    results = adapter.query_by_timestamp(start, end)
    
    assert len(results) == 1
    assert results[0].trace_id == sample_state.trace_id
    
    adapter.close()


# ============================================================================
# PostgreSQL Adapter Tests (requires running PostgreSQL)
# ============================================================================

@pytest.mark.skipif(
    not _postgresql_available(),
    reason="PostgreSQL not available for testing"
)
def test_postgresql_adapter_save_load(sample_state):
    """Test PostgreSQL adapter save and load."""
    from axis.persistence.postgresql_adapter import PostgreSQLAdapter
    
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    
    # Save
    adapter.save(sample_state)
    
    # Load
    loaded = adapter.load("test-trace-123")
    
    assert loaded is not None
    assert loaded.trace_id == sample_state.trace_id
    assert loaded.intent == sample_state.intent
    assert len(loaded.facts) == 2
    assert loaded.facts[0].key == "stock_price"
    
    # Cleanup
    adapter.delete("test-trace-123")
    adapter.close()


@pytest.mark.skipif(
    not _postgresql_available(),
    reason="PostgreSQL not available for testing"
)
def test_postgresql_adapter_query_by_timestamp(sample_state):
    """Test PostgreSQL adapter query_by_timestamp."""
    from axis.persistence.postgresql_adapter import PostgreSQLAdapter
    
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    
    # Save
    adapter.save(sample_state)
    
    # Query with wide range
    start = datetime.now() - timedelta(hours=1)
    end = datetime.now() + timedelta(hours=1)
    results = adapter.query_by_timestamp(start, end)
    
    assert len(results) >= 1
    assert any(r.trace_id == "test-trace-123" for r in results)
    
    # Query with narrow range (should exclude)
    start = datetime.now() + timedelta(hours=1)
    end = datetime.now() + timedelta(hours=2)
    results = adapter.query_by_timestamp(start, end)
    
    assert len(results) == 0
    
    # Cleanup
    adapter.delete("test-trace-123")
    adapter.close()


@pytest.mark.skipif(
    not _postgresql_available(),
    reason="PostgreSQL not available for testing"
)
def test_postgresql_adapter_jsonb_query(sample_state):
    """Test PostgreSQL JSONB query capabilities."""
    from axis.persistence.postgresql_adapter import PostgreSQLAdapter
    
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    
    # Save
    adapter.save(sample_state)
    
    # Query by intent using JSONB containment
    results = adapter.query_by_jsonb({"intent": "Test workflow execution"})
    
    assert len(results) >= 1
    assert any(s.trace_id == "test-trace-123" for s in results)
    
    # Query by fact key
    results = adapter.query_by_jsonb({"facts": [{"key": "stock_price"}]})
    
    assert len(results) >= 1
    assert any(s.trace_id == "test-trace-123" for s in results)
    
    # Cleanup
    adapter.delete("test-trace-123")
    adapter.close()


@pytest.mark.skipif(
    not _postgresql_available(),
    reason="PostgreSQL not available for testing"
)
def test_postgresql_adapter_list_traces(sample_state):
    """Test PostgreSQL adapter list_traces."""
    from axis.persistence.postgresql_adapter import PostgreSQLAdapter
    
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    
    # Save
    adapter.save(sample_state)
    
    # List traces
    traces = adapter.list_traces(limit=10)
    
    assert "test-trace-123" in traces
    
    # Cleanup
    adapter.delete("test-trace-123")
    adapter.close()


@pytest.mark.skipif(
    not _postgresql_available(),
    reason="PostgreSQL not available for testing"
)
def test_postgresql_adapter_delete(sample_state):
    """Test PostgreSQL adapter delete."""
    from axis.persistence.postgresql_adapter import PostgreSQLAdapter
    
    adapter = PostgreSQLAdapter(
        host="localhost",
        database="axis_test",
        user="postgres",
        password="test"
    )
    
    # Save
    adapter.save(sample_state)
    
    # Verify exists
    loaded = adapter.load("test-trace-123")
    assert loaded is not None
    
    # Delete
    deleted = adapter.delete("test-trace-123")
    assert deleted is True
    
    # Verify gone
    loaded = adapter.load("test-trace-123")
    assert loaded is None
    
    # Delete non-existent
    deleted = adapter.delete("nonexistent")
    assert deleted is False
    
    adapter.close()


# ============================================================================
# Cross-Adapter Compatibility Tests
# ============================================================================

def test_round_trip_all_adapters(sample_state, temp_dir):
    """Test round-trip serialization across all adapters."""
    from axis.persistence.json_adapter import JSONAdapter
    from axis.persistence.sqlite_adapter import SQLiteAdapter
    
    # JSON round-trip
    json_adapter = JSONAdapter(base_path=temp_dir)
    json_adapter.save(sample_state)
    json_loaded = json_adapter.load("test-trace-123")
    
    # SQLite round-trip
    db_path = Path(temp_dir) / "test.db"
    sqlite_adapter = SQLiteAdapter(db_path=str(db_path))
    sqlite_adapter.save(sample_state)
    sqlite_loaded = sqlite_adapter.load("test-trace-123")
    
    # Verify both match original
    assert json_loaded.trace_id == sample_state.trace_id
    assert sqlite_loaded.trace_id == sample_state.trace_id
    assert json_loaded.intent == sqlite_loaded.intent
    assert len(json_loaded.facts) == len(sqlite_loaded.facts)
    
    sqlite_adapter.close()


def test_epistemic_state_persistence(sample_epistemic_state, temp_dir):
    """Test that epistemic types persist correctly."""
    from axis.persistence.json_adapter import JSONAdapter
    
    adapter = JSONAdapter(base_path=temp_dir)
    
    # Note: This would need a wrapper or adapter modification
    # to handle EpistemicState directly, as current interface
    # expects GraphState. This is a design decision placeholder.
    
    # For now, verify serialization works
    data = sample_epistemic_state.to_dict()
    restored = EpistemicState.from_dict(data)
    
    assert restored.trace_id == sample_epistemic_state.trace_id
    assert len(restored.categories) == 1
    assert len(restored.patterns) == 1
