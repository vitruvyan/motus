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


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp, normalizing a naive one to UTC.

    Traces written before this kernel had a single clock — or by any
    consumer that stamped datetime.utcnow() directly — are naive. Loading
    one and comparing it against a fresh now() timestamp must not raise
    "can't compare offset-naive and offset-aware datetimes"; a naive
    timestamp from this kernel was always meant as UTC, so that's what it
    becomes on load.
    """
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


# A value placed in Fact.value or Event.metadata must survive json.dumps()
# unchanged — the trace is persisted as JSON. GraphState.to_dict() checks
# this at write time so a bad value fails with a pointed error, not a
# mystery TypeError three layers away at persist time.
#
# "Survives json.dumps() unchanged" is the actual contract — not merely
# "doesn't raise". A tuple dumps fine but loads back as a list; a dict
# with an int key dumps fine but loads back with a string key. Both pass
# the write-time probe and both break `state == GraphState.from_dict(state.to_dict())`.
# Use lists and str-keyed dicts if round-trip equality matters to you.
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
            timestamp=parse_timestamp(data["timestamp"]),
            metadata=data.get("metadata"),
            node_name=data.get("node_name"),
        )
