"""Qdrant vector database adapter for GraphState persistence and embeddings."""

import json
import logging
from typing import Optional, Sequence, Dict, Any, List
from datetime import datetime
import uuid
import httpx

from axis.persistence.protocol import PersistenceProvider
from axis.state import GraphState

logger = logging.getLogger(__name__)


class QdrantAdapter(PersistenceProvider):
    """Qdrant vector database adapter for GraphState persistence and embeddings.

    Uses httpx for HTTP requests (stdlib only, no external dependencies).
    Stores GraphStates in 'graph_states' collection.
    Stores embeddings in 'embeddings' collection.
    """

    def __init__(self, url: str = "http://localhost:6333", api_key: Optional[str] = None, timeout: float = 30.0):
        """
        Initialize the Qdrant adapter.

        Args:
            url: Qdrant server URL
            api_key: Optional API key for authentication
            timeout: Request timeout in seconds
        """
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout)
        if api_key:
            self.client.headers.update({"api-key": api_key})

        # Collection names
        self.states_collection = "graph_states"
        self.embeddings_collection = "embeddings"

        # Ensure collections exist
        try:
            self.ensure_collection(self.states_collection, 1)  # Dummy vector for states
            self.ensure_collection(self.embeddings_collection, 384)  # Default embedding size
        except Exception as e:
            logger.warning(f"Could not ensure collections on init: {e}")

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """Make HTTP request to Qdrant API."""
        url = f"{self.url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key

        response = self.client.request(method, url, json=data, headers=headers)
        response.raise_for_status()
        return response.json()

    def health_check(self) -> Dict[str, Any]:
        """Check Qdrant server health."""
        try:
            result = self._request("GET", "/")
            return {"status": "ok", "version": result.get("version", "unknown")}
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {"status": "error", "error": str(e)}

    def ensure_collection(self, name: str, vector_size: int, distance: str = "Cosine") -> Dict[str, Any]:
        """
        Ensure collection exists.

        Args:
            name: Collection name
            vector_size: Vector dimension (use 1 for non-vector collections)
            distance: Distance metric ("Cosine" or "Dot")

        Returns:
            Dict with status
        """
        try:
            # Check if collection exists
            try:
                result = self._request("GET", f"/collections/{name}")
                points_count = result["result"]["points_count"]
                return {"status": "exists", "points_count": points_count}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Create collection
                    data = {
                        "vectors": {
                            "size": vector_size,
                            "distance": distance
                        }
                    }
                    self._request("PUT", f"/collections/{name}", data)
                    return {"status": "created"}
                else:
                    raise
        except Exception as e:
            logger.error(f"Error ensuring collection {name}: {e}")
            return {"status": "error", "error": str(e)}

    def save(self, state: GraphState) -> None:
        """Save a GraphState. Idempotent (same trace_id overwrites)."""
        try:
            data = state.to_dict()
            # Add saved timestamp
            data["saved_at"] = datetime.now().isoformat()

            # Use UUID based on trace_id
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, state.trace_id))

            payload = {
                "points": [{
                    "id": point_id,
                    "vector": [0.0],  # Dummy vector for non-vector data
                    "payload": data
                }]
            }
            self._request("PUT", f"/collections/{self.states_collection}/points", payload)
            logger.debug(f"Saved GraphState {state.trace_id}")
        except Exception as e:
            logger.error(f"Error saving GraphState {state.trace_id}: {e}")
            raise

    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load a GraphState by trace_id. Returns None if not found."""
        try:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, trace_id))
            result = self._request("GET", f"/collections/{self.states_collection}/points/{point_id}")
            payload = result["result"]["payload"]
            return GraphState.from_dict(payload)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        except Exception as e:
            logger.error(f"Error loading GraphState {trace_id}: {e}")
            raise

    def delete(self, trace_id: str) -> bool:
        """Delete a trace. Returns True if existed."""
        try:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, trace_id))
            payload = {"points": [point_id]}
            self._request("POST", f"/collections/{self.states_collection}/points/delete", payload)
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return False
            raise
        except Exception as e:
            logger.error(f"Error deleting GraphState {trace_id}: {e}")
            raise

    def query_by_timestamp(self, start: datetime, end: datetime) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range."""
        # This is a simplified implementation
        # In practice, might need scrolling through all points with filter
        # For now, return empty as it's complex without proper indexing
        logger.warning("query_by_timestamp not fully implemented for Qdrant")
        return []

    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs, most recent first."""
        # Simplified: scroll through points
        try:
            payload = {
                "limit": limit,
                "with_payload": True,
                "with_vectors": False
            }
            result = self._request("POST", f"/collections/{self.states_collection}/points/scroll", payload)
            points = result["result"]["points"]
            return [str(p["payload"]["trace_id"]) for p in points if "trace_id" in p["payload"]]
        except Exception as e:
            logger.error(f"Error listing traces: {e}")
            return []

    def save_embedding(self, trace_id: str, vector: List[float], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Save an embedding vector with metadata."""
        try:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, trace_id))
            payload_data = {"trace_id": trace_id}
            if metadata:
                payload_data.update(metadata)
            payload = {
                "points": [{
                    "id": point_id,
                    "vector": vector,
                    "payload": payload_data
                }]
            }
            self._request("PUT", f"/collections/{self.embeddings_collection}/points", payload)
            logger.debug(f"Saved embedding for {trace_id}")
        except Exception as e:
            logger.error(f"Error saving embedding for {trace_id}: {e}")
            raise

    def search_similar_traces(self, query_vector: List[float], limit: int = 10, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Search for similar traces by vector similarity."""
        try:
            payload = {
                "vector": query_vector,
                "limit": limit,
                "with_payload": True,
                "with_vectors": False
            }
            if filters:
                payload["filter"] = filters

            result = self._request("POST", f"/collections/{self.embeddings_collection}/points/search", payload)
            points = result["result"]
            return [
                {
                    "trace_id": str(p["payload"].get("trace_id", p["id"])),
                    "score": p["score"],
                    "metadata": p["payload"]
                }
                for p in points
            ]
        except Exception as e:
            logger.error(f"Error searching similar traces: {e}")
            return []

    # Alias methods as per requirements
    def save_state(self, state: GraphState) -> None:
        """Alias for save."""
        self.save(state)

    def load_state(self, trace_id: str) -> Optional[GraphState]:
        """Alias for load."""
        return self.load(trace_id)

    def delete_state(self, trace_id: str) -> bool:
        """Alias for delete."""
        return self.delete(trace_id)
