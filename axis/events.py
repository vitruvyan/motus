from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """
    Canonical event types emitted by the Axis graph.

    Events are facts, not commands.
    """

    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_EXECUTED = "node_executed"
    NODE_SKIPPED = "node_skipped"
    INTENT_SET = "intent_set"
    FACT_ADDED = "fact_added"
    DECISION_RECORDED = "decision_recorded"
    REJECTION_RECORDED = "rejection_recorded"
    ERROR = "error"
    GRAPH_START = "graph_start"
    GRAPH_END = "graph_end"


@dataclass(frozen=True)
class Event:
    event_type: EventType
    description: str
    timestamp: datetime
    metadata: dict = None
    node_name: str = None

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
