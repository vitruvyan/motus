"""Axis 0.4 compatibility adapters and legacy data types.

This module intentionally contains no Motus-native execution semantics.  It
keeps the audited Terraveler surface readable while migration remains an
explicit import change.  In particular, there is no public ``Decision`` name:
the legacy type is :class:`LegacyDecision`.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from vitruvyan_motus.errors import NodeFailed
from vitruvyan_motus.runtime import Policy

__all__ = [
    "GraphState", "Runner", "Policy", "NodeFailed", "Fact",
    "LegacyDecision", "Rejection", "FileTraceObserver", "retry",
    "ConcurrentRunner",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


class _EventType(str, Enum):
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_SKIPPED = "node_skipped"
    NODE_RETRIED = "node_retried"
    ERROR = "error"
    GRAPH_START = "graph_start"
    GRAPH_END = "graph_end"


@dataclass(frozen=True)
class _Event:
    event_type: _EventType
    description: str
    timestamp: datetime
    metadata: Any = None
    node_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "node_name": self.node_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_Event":
        return cls(
            _EventType(data["event_type"]), data["description"],
            _parse_timestamp(data["timestamp"]), data.get("metadata"),
            data.get("node_name"),
        )


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    source: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "value": self.value, "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        return cls(data["key"], data["value"], data["source"], _parse_timestamp(data["timestamp"]))


@dataclass(frozen=True)
class LegacyDecision:
    description: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {"description": self.description, "timestamp": self.timestamp.isoformat()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LegacyDecision":
        return cls(data["description"], _parse_timestamp(data["timestamp"]))


@dataclass(frozen=True)
class Rejection:
    description: str
    reason: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description, "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rejection":
        return cls(data["description"], data["reason"], _parse_timestamp(data["timestamp"]))


def _freeze_metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(metadata) if metadata else {})


@dataclass(frozen=True)
class GraphState:
    trace_id: str
    intent: str | None
    facts: tuple[Fact, ...]
    decisions: tuple[LegacyDecision, ...]
    rejections: tuple[Rejection, ...]
    events: tuple[_Event, ...]
    metadata: Mapping[str, Any] = MappingProxyType({})

    @staticmethod
    def empty(trace_id: str, metadata: Mapping[str, Any] | None = None) -> "GraphState":
        return GraphState(trace_id, None, (), (), (), (), _freeze_metadata(metadata))

    @classmethod
    def new(cls, prefix: str = "", metadata: Mapping[str, Any] | None = None) -> "GraphState":
        suffix = uuid.uuid4().hex[:12]
        return cls.empty(f"{prefix}-{suffix}" if prefix else suffix, metadata)

    def with_intent(self, intent: str) -> "GraphState":
        return replace(self, intent=intent)

    def with_fact(self, fact: Fact) -> "GraphState":
        return replace(self, facts=self.facts + (fact,))

    def with_decision(self, decision: LegacyDecision) -> "GraphState":
        return replace(self, decisions=self.decisions + (decision,))

    def with_rejection(self, rejection: Rejection) -> "GraphState":
        return replace(self, rejections=self.rejections + (rejection,))

    def with_event(self, event: _Event) -> "GraphState":
        return replace(self, events=self.events + (event,))

    def fact(self, key: str, default: Any = None) -> Any:
        for fact in reversed(self.facts):
            if fact.key == key:
                return fact.value
        return default

    def to_dict(self) -> dict[str, Any]:
        data = {
            "trace_id": self.trace_id,
            "intent": self.intent,
            "facts": [item.to_dict() for item in self.facts],
            "decisions": [item.to_dict() for item in self.decisions],
            "rejections": [item.to_dict() for item in self.rejections],
            "events": [item.to_dict() for item in self.events],
            "metadata": dict(self.metadata),
        }
        try:
            json.dumps(data)
        except TypeError as exc:
            raise TypeError(f"GraphState({self.trace_id!r}) is not JSON-serializable: {exc}") from exc
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphState":
        return cls(
            trace_id=data["trace_id"], intent=data.get("intent"),
            facts=tuple(Fact.from_dict(item) for item in data.get("facts", [])),
            decisions=tuple(LegacyDecision.from_dict(item) for item in data.get("decisions", [])),
            rejections=tuple(Rejection.from_dict(item) for item in data.get("rejections", [])),
            events=tuple(_Event.from_dict(item) for item in data.get("events", [])),
            metadata=_freeze_metadata(data.get("metadata")),
        )

    @classmethod
    def from_json(cls, data: str) -> "GraphState":
        return cls.from_dict(json.loads(data))


def _node_name(node: Callable[..., Any]) -> str:
    return getattr(node, "__name__", node.__class__.__name__)


def _start_metadata(policy: Policy, state: GraphState) -> dict[str, Any]:
    metadata: dict[str, Any] = {"policy": policy.value}
    if state.metadata:
        metadata["run"] = dict(state.metadata)
    return metadata


class Runner:
    """Legacy linear runner retained as a compatibility adapter."""

    def __init__(self, nodes: Iterable[Callable[[GraphState], GraphState]], policy: Policy = Policy.STRICT, bus=None) -> None:
        self._nodes = list(nodes)
        self._policy = Policy(policy)
        self._observers = [] if bus is None else [bus]

    def attach(self, observer: Any) -> None:
        self._observers.append(observer)

    def _notify(self, event_type: _EventType, state: GraphState, **kwargs: Any) -> None:
        for observer in self._observers:
            try:
                observer.observe(event_type.value, state, **kwargs)
            except Exception:
                if getattr(observer, "critical", False):
                    raise

    def run(self, state: GraphState) -> GraphState:
        current = state.with_event(_Event(
            _EventType.GRAPH_START, f"Graph started under policy {self._policy.value}",
            _now(), _start_metadata(self._policy, state),
        ))
        self._notify(_EventType.GRAPH_START, current)
        nodes_run = nodes_skipped = nodes_failed = 0
        for node in self._nodes:
            name = _node_name(node)
            current = current.with_event(_Event(
                _EventType.NODE_STARTED, f"Node {name} started", _now(), node_name=name,
            ))
            self._notify(_EventType.NODE_STARTED, current, node_name=name)
            started = time.monotonic()
            try:
                returned = node(current)
            except Exception as exc:
                nodes_failed += 1
                current = getattr(exc, "__axis_state__", None) or current
                duration_ms = round((time.monotonic() - started) * 1000)
                current = current.with_event(_Event(
                    _EventType.ERROR, f"Node {name} failed: {exc}", _now(),
                    {"error_type": type(exc).__name__, "error": str(exc), "duration_ms": duration_ms}, name,
                ))
                self._notify(_EventType.ERROR, current, node_name=name, error=exc)
                if self._policy is Policy.STRICT:
                    raise NodeFailed(name, current, cause=exc) from exc
                current = current.with_event(_Event(
                    _EventType.NODE_SKIPPED, f"Node {name} skipped due to error: {exc}",
                    _now(), {"duration_ms": duration_ms}, name,
                ))
                nodes_skipped += 1
                continue
            duration_ms = round((time.monotonic() - started) * 1000)
            current = returned.with_event(_Event(
                _EventType.NODE_COMPLETED, f"Node {name} completed", _now(),
                {"duration_ms": duration_ms}, name,
            ))
            self._notify(_EventType.NODE_COMPLETED, current, node_name=name)
            nodes_run += 1
        current = current.with_event(_Event(
            _EventType.GRAPH_END,
            f"Graph ended: {nodes_run} run, {nodes_skipped} skipped, {nodes_failed} failed",
            _now(), {"policy": self._policy.value, "nodes_run": nodes_run,
                     "nodes_skipped": nodes_skipped, "nodes_failed": nodes_failed},
        ))
        self._notify(_EventType.GRAPH_END, current)
        return current


def retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    jitter: bool = False,
    max_delay: float | None = None,
    on_retry: Callable[[Exception, int], None] | None = None,
):
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    def decorator(node):
        name = _node_name(node)

        @wraps(node)
        def wrapper(state: GraphState):
            current, delay = state, initial_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return node(current)
                except exceptions as exc:
                    if attempt == max_attempts:
                        exc.__axis_state__ = current
                        raise
                    actual = delay * (0.5 + 0.5 * random.random()) if jitter else delay
                    actual = min(actual, max_delay) if max_delay is not None else actual
                    if on_retry:
                        on_retry(exc, attempt)
                    current = current.with_event(_Event(
                        _EventType.NODE_RETRIED, f"Node {name} retry {attempt}/{max_attempts}",
                        _now(), {"attempt": attempt, "max_attempts": max_attempts,
                                 "delay": actual, "error_type": type(exc).__name__, "error": str(exc)}, name,
                    ))
                    time.sleep(actual)
                    delay *= backoff_factor
        return wrapper
    return decorator


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class FileTraceObserver:
    critical = True

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def observe(self, event_type: str, state: GraphState, **kwargs: Any) -> None:
        if event_type not in (_EventType.GRAPH_END.value, _EventType.ERROR.value):
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        flattened = _UNSAFE_FILENAME_CHARS.sub("-", state.trace_id)
        target = self._directory / f"{flattened or 'trace'}.json"
        temporary = target.parent / f"{target.name}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(state.to_json())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)


@dataclass
class _Outcome:
    name: str
    state: GraphState | None
    error: Exception | None
    duration_ms: int


class ConcurrentRunner:
    """Legacy concurrent adapter with deterministic declared-order merge."""

    def __init__(self, nodes, policy: Policy = Policy.STRICT, bus=None) -> None:
        self.nodes = list(nodes)
        self.policy = Policy(policy)
        self._observers = [] if bus is None else [bus]

    def attach(self, observer: Any) -> None:
        self._observers.append(observer)

    def _notify(self, kind: _EventType, state: GraphState, **kwargs: Any) -> None:
        for observer in self._observers:
            try:
                observer.observe(kind.value, state, **kwargs)
            except Exception:
                if getattr(observer, "critical", False):
                    raise

    async def _one(self, node, seed) -> _Outcome:
        started = time.monotonic()
        try:
            result = await node(seed) if asyncio.iscoroutinefunction(node) else await asyncio.to_thread(node, seed)
            return _Outcome(_node_name(node), result, None, round((time.monotonic() - started) * 1000))
        except Exception as exc:
            return _Outcome(_node_name(node), None, exc, round((time.monotonic() - started) * 1000))

    async def run(self, state: GraphState) -> GraphState:
        current = state.with_event(_Event(
            _EventType.GRAPH_START, f"Graph started under policy {self.policy.value} (concurrent)",
            _now(), _start_metadata(self.policy, state),
        ))
        self._notify(_EventType.GRAPH_START, current)
        seed = current
        outcomes = await asyncio.gather(*(self._one(node, seed) for node in self.nodes))
        run = skipped = failed = 0
        for outcome in outcomes:
            current = current.with_event(_Event(
                _EventType.NODE_STARTED, f"Node {outcome.name} started", _now(), node_name=outcome.name,
            ))
            self._notify(_EventType.NODE_STARTED, current, node_name=outcome.name)
            if outcome.error is None:
                branch = outcome.state
                assert branch is not None
                current = GraphState(
                    current.trace_id,
                    branch.intent if branch.intent != seed.intent else current.intent,
                    current.facts + branch.facts[len(seed.facts):],
                    current.decisions + branch.decisions[len(seed.decisions):],
                    current.rejections + branch.rejections[len(seed.rejections):],
                    current.events + branch.events[len(seed.events):],
                    current.metadata,
                )
                current = current.with_event(_Event(
                    _EventType.NODE_COMPLETED, f"Node {outcome.name} completed", _now(),
                    {"duration_ms": outcome.duration_ms}, outcome.name,
                ))
                self._notify(
                    _EventType.NODE_COMPLETED, current, node_name=outcome.name
                )
                run += 1
                continue
            failed += 1
            current = current.with_event(_Event(
                _EventType.ERROR, f"Node {outcome.name} failed: {outcome.error}", _now(),
                {"error_type": type(outcome.error).__name__, "error": str(outcome.error),
                 "duration_ms": outcome.duration_ms}, outcome.name,
            ))
            self._notify(
                _EventType.ERROR, current, node_name=outcome.name,
                error=outcome.error,
            )
            if self.policy is Policy.STRICT:
                raise NodeFailed(outcome.name, current, cause=outcome.error) from outcome.error
            current = current.with_event(_Event(
                _EventType.NODE_SKIPPED, f"Node {outcome.name} skipped due to error: {outcome.error}",
                _now(), {"duration_ms": outcome.duration_ms}, outcome.name,
            ))
            skipped += 1
        current = current.with_event(_Event(
            _EventType.GRAPH_END,
            f"Graph ended (concurrent): {run} run, {skipped} skipped, {failed} failed",
            _now(), {"policy": self.policy.value, "nodes_run": run,
                     "nodes_skipped": skipped, "nodes_failed": failed},
        ))
        self._notify(_EventType.GRAPH_END, current)
        return current
