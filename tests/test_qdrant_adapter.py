"""
Test suite for QdrantAdapter.
Tests persistence and embedding functionality.
"""

import pytest
from datetime import datetime
from typing import List

from axis.state import GraphState, Fact, Decision, Rejection, Event, EventType


def _qdrant_available() -> bool:
    """Check if Qdrant is available for testing."""
    try:
        import httpx
        client = httpx.Client(timeout=5.0)
        response = client.get("http://localhost:6333/")
        return response.status_code == 200
    except Exception:
        return False


@pytest.fixture
def sample_state():
    """Create a sample GraphState for testing."""
    now = datetime.now()
    return GraphState(
        trace_id="test-trace-qdrant",
        intent="Test Qdrant workflow",
        facts=(
            Fact("test_key", "test_value", "test_source", now),
        ),
        decisions=(),
        rejections=(),
        events=(),
    )


@pytest.fixture
def sample_vector() -> List[float]:
    """Create a sample vector for testing."""
    return [0.1, 0.2, 0.3] * 128  # 384-dim vector


@pytest.mark.skipif(not _qdrant_available(), reason="Qdrant server not available")
class TestQdrantAdapter:
    """Test QdrantAdapter functionality."""

    def test_health_check(self):
        """Test health check."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()
        result = adapter.health_check()
        assert result["status"] == "ok"

    def test_ensure_collection(self):
        """Test ensure collection."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()
        result = adapter.ensure_collection("test_collection", 384)
        assert result["status"] in ["created", "exists"]

    def test_save_load_state(self, sample_state):
        """Test save and load state."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        # Save
        adapter.save_state(sample_state)

        # Load
        loaded = adapter.load_state("test-trace-qdrant")

        assert loaded is not None
        assert loaded.trace_id == sample_state.trace_id
        assert loaded.intent == sample_state.intent

    def test_delete_state(self, sample_state):
        """Test delete state."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        # Save first
        adapter.save_state(sample_state)

        # Delete
        deleted = adapter.delete_state("test-trace-qdrant")
        assert deleted is True

        # Try to load - should be None
        loaded = adapter.load_state("test-trace-qdrant")
        assert loaded is None

    def test_save_embedding(self, sample_vector):
        """Test save embedding."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        # Ensure collection exists
        adapter.ensure_collection(adapter.embeddings_collection, 384)

        # Save embedding
        adapter.save_embedding("test-embedding-123", sample_vector, {"test": "metadata"})

        # Should not raise exception

    def test_search_similar_traces(self, sample_vector):
        """Test search similar traces."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        # Save an embedding first
        adapter.save_embedding("test-search-123", sample_vector, {"type": "test"})

        # Search
        results = adapter.search_similar_traces(sample_vector, limit=5)

        assert isinstance(results, list)
        if results:  # If we have results
            assert "trace_id" in results[0]
            assert "score" in results[0]
            assert "metadata" in results[0]

    def test_list_traces(self, sample_state):
        """Test list traces."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        # Save a state
        adapter.save_state(sample_state)

        # List traces
        traces = adapter.list_traces(limit=10)

        assert isinstance(traces, list)
        assert "test-trace-qdrant" in traces

    def test_query_by_timestamp(self):
        """Test query by timestamp (simplified)."""
        from axis.persistence.qdrant_adapter import QdrantAdapter

        adapter = QdrantAdapter()

        start = datetime.now()
        end = datetime.now()

        # This might return empty for now
        results = adapter.query_by_timestamp(start, end)

        assert isinstance(results, list)
