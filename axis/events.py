from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Union


def now() -> datetime:
    """The kernel's one clock: tz-aware UTC.

    Every timestamp the Runner stamps comes from here. Nodes may import it
    too, so a trace never mixes naive and aware datetimes.
    """
    return datetime.now(timezone.utc)


# A value placed in Fact.value or Event.metadata must survive json.dumps()
# unchanged — the trace is persisted as JSON. GraphState.to_dict() checks
# this at write time so a bad value fails with a pointed error, not a
# mystery TypeError three layers away at persist time.
Json = Union[str, int, float, bool, None, list, dict]


class EventType(str, Enum):
    """
    Canonical event types emitted by the Axis graph.

    Events are facts, not commands.
    """

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_SKIPPED = "node_skipped"
    NODE_RETRIED = "node_retried"
    ERROR = "error"
    GRAPH_START = "graph_start"
    GRAPH_END = "graph_end"


@dataclass(frozen=True)
class Event:
    event_type: EventType
    description: str
    timestamp: datetime
    metadata: Optional[Json] = None
    node_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "node_name": self.node_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            event_type=EventType(data["event_type"]),
            description=data["description"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata"),
            node_name=data.get("node_name"),
        )
