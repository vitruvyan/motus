"""Protocol for GraphState persistence backends."""

from typing import Protocol, Optional, Sequence
from datetime import datetime
from axis.state import GraphState


class PersistenceProvider(Protocol):
    """Protocol for GraphState persistence backends."""

    def save(self, state: GraphState) -> None:
        """Save a GraphState. Idempotent (same trace_id overwrites)."""
        pass

    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load a GraphState by trace_id. Returns None if not found."""
        pass

    def query_by_timestamp(
        self,
        start: datetime,
        end: datetime
    ) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range."""
        pass

    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs, most recent first."""
        pass

    def delete(self, trace_id: str) -> bool:
        """Delete a trace. Returns True if existed."""
        ...
        pass