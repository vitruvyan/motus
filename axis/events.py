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
    NODE_SKIPPED = "node_skipped"
    INTENT_SET = "intent_set"
    FACT_ADDED = "fact_added"
    DECISION_RECORDED = "decision_recorded"
    REJECTION_RECORDED = "rejection_recorded"


@dataclass(frozen=True)
class Event:
    type: EventType
    description: str
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            type=EventType(data["type"]),
            description=data["description"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )
