"""
Axis Synaptic Bus — Passive Observational Substrate

This is NOT:
- A plugin system
- An extensibility mechanism
- Event-driven execution
- Middleware or hooks

This IS:
- A passive observer of completed Axis executions
- A 1:1 event derivation layer from GraphState
- A notification mechanism for incarnated responsibilities (Orders)

Directionality: AXIS → BUS → ORDERS (unidirectional only)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from enum import Enum, auto

from axis.state import GraphState, Fact, Decision, Rejection, Event, EventType


class BusEventType(Enum):
    """Semantic signals derived from GraphState."""
    INTENT_DECLARED = auto()
    FACT_RECORDED = auto()
    DECISION_MADE = auto()
    REJECTION_RECORDED = auto()
    NODE_STARTED = auto()
    NODE_COMPLETED = auto()
    EXECUTION_COMPLETED = auto()


@dataclass(frozen=True)
class BusEvent:
    """
    Immutable observation event.
    
    CRITICAL DISTINCTION:
    - observed_at: when the Bus observed this (observation time)
    - execution_ts: when this happened in Axis execution (from GraphState)
    
    These timestamps MUST NOT be conflated.
    """
    event_type: BusEventType
    trace_id: str
    observed_at: datetime  # Observation timestamp
    execution_ts: datetime | None  # Execution timestamp from GraphState element, if applicable
    
    # Traceable reference to GraphState element
    source_index: int | None = None  # Index in facts/decisions/rejections/events
    content: str | None = None  # Human-readable excerpt


class BusObserver(Protocol):
    """
    Protocol for Orders observing the Bus.
    
    Orders implement this to receive notifications.
    Registration is STATIC (at Bus initialization only).
    """
    def on_event(self, event: BusEvent) -> None:
        """Receive a Bus event. MUST NOT mutate Axis or emit events."""
        ...


class SynapticBus:
    """
    Passive observational substrate for Axis executions.
    
    Design invariants:
    1. Axis core is never modified
    2. Events are derived 1:1 from GraphState
    3. Observer registration is static (init-time only)
    4. No filtering, routing, or interpretation
    5. Sequential, deterministic notification
    6. Append-only event history
    """
    
    def __init__(self, observers: tuple[BusObserver, ...] = ()):
        """
        Initialize Bus with static observers.
        
        Args:
            observers: Tuple of BusObserver implementations (Orders)
        """
        self._observers = observers
        self._history: list[BusEvent] = []
    
    @property
    def history(self) -> tuple[BusEvent, ...]:
        """Return immutable view of all observed events."""
        return tuple(self._history)
    
    def observe(self, state: GraphState) -> None:
        """
        Observe a completed Axis execution.
        
        Derives events from GraphState and notifies all observers.
        This is the ONLY entry point for Bus activity.
        
        Args:
            state: Completed GraphState from Axis execution
        """
        observation_time = datetime.utcnow()
        events = self._derive_events(state, observation_time)
        
        # Append to history
        self._history.extend(events)
        
        # Notify all observers (sequential, deterministic)
        for event in events:
            for observer in self._observers:
                observer.on_event(event)
    
    def _derive_events(
        self, 
        state: GraphState, 
        observation_time: datetime
    ) -> list[BusEvent]:
        """
        Derive events 1:1 from GraphState elements.
        
        Every event MUST be traceable to an exact GraphState element.
        No interpretation, inference, or synthesis allowed.
        """
        events: list[BusEvent] = []
        
        # Intent declaration (if present)
        if state.intent:
            events.append(BusEvent(
                event_type=BusEventType.INTENT_DECLARED,
                trace_id=state.trace_id,
                observed_at=observation_time,
                execution_ts=None,  # Intent has no timestamp
                source_index=None,
                content=state.intent[:100]  # Excerpt
            ))
        
        # Facts recorded
        for idx, fact in enumerate(state.facts):
            events.append(BusEvent(
                event_type=BusEventType.FACT_RECORDED,
                trace_id=state.trace_id,
                observed_at=observation_time,
                execution_ts=fact.timestamp,
                source_index=idx,
                content=f"{fact.key}: {str(fact.value)[:50]}"
            ))
        
        # Decisions made
        for idx, decision in enumerate(state.decisions):
            events.append(BusEvent(
                event_type=BusEventType.DECISION_MADE,
                trace_id=state.trace_id,
                observed_at=observation_time,
                execution_ts=decision.timestamp,
                source_index=idx,
                content=decision.description[:100]
            ))
        
        # Rejections recorded
        for idx, rejection in enumerate(state.rejections):
            events.append(BusEvent(
                event_type=BusEventType.REJECTION_RECORDED,
                trace_id=state.trace_id,
                observed_at=observation_time,
                execution_ts=rejection.timestamp,
                source_index=idx,
                content=f"{rejection.description}: {rejection.reason[:50]}"
            ))
        
        # Execution lifecycle events
        for idx, exec_event in enumerate(state.events):
            if exec_event.type == EventType.NODE_STARTED:
                events.append(BusEvent(
                    event_type=BusEventType.NODE_STARTED,
                    trace_id=state.trace_id,
                    observed_at=observation_time,
                    execution_ts=exec_event.timestamp,
                    source_index=idx,
                    content=exec_event.description
                ))
            elif exec_event.type == EventType.NODE_COMPLETED:
                events.append(BusEvent(
                    event_type=BusEventType.NODE_COMPLETED,
                    trace_id=state.trace_id,
                    observed_at=observation_time,
                    execution_ts=exec_event.timestamp,
                    source_index=idx,
                    content=exec_event.description
                ))
        
        # Execution completed (final synthetic event)
        events.append(BusEvent(
            event_type=BusEventType.EXECUTION_COMPLETED,
            trace_id=state.trace_id,
            observed_at=observation_time,
            execution_ts=None,
            source_index=None,
            content=f"Trace completed: {len(state.facts)} facts, {len(state.decisions)} decisions"
        ))
        
        return events
