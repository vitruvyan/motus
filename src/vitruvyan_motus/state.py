"""Immutable node state as a view over the append-only state log."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable

from vitruvyan_motus.trace import Decision, Fact, Rejection, RedactedValue, _ChunkedLog, _strict_plain_json, _value

__all__ = ["State"]

_STRING_METADATA_KEYS = {
    "actor", "causation_id", "correlation_id", "ruleset_version",
}


@dataclass(frozen=True, slots=True)
class _StateItem:
    collection: str
    value: Fact | Decision | Rejection
    origin: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _IndexLeaf:
    hash_value: int
    entries: tuple[tuple[tuple[str, str], _StateItem], ...]


@dataclass(frozen=True, slots=True)
class _IndexBranch:
    children: dict[int, "_IndexLeaf | _IndexBranch"]


_IndexNode = _IndexLeaf | _IndexBranch
_INDEX_BITS = 6
_INDEX_MASK = (1 << _INDEX_BITS) - 1


def _index_hash(key: tuple[str, str]) -> int:
    return hash(key) & ((1 << 64) - 1)


def _index_merge_leaves(
    left: _IndexLeaf, right: _IndexLeaf, shift: int
) -> _IndexBranch:
    left_slot = (left.hash_value >> shift) & _INDEX_MASK
    right_slot = (right.hash_value >> shift) & _INDEX_MASK
    if left_slot != right_slot:
        return _IndexBranch({left_slot: left, right_slot: right})
    child = _index_merge_leaves(left, right, shift + _INDEX_BITS)
    return _IndexBranch({left_slot: child})


def _index_set_at(
    root: _IndexNode | None,
    key: tuple[str, str],
    value: _StateItem,
    hash_value: int,
    shift: int,
) -> _IndexNode:
    if root is None:
        return _IndexLeaf(hash_value, ((key, value),))
    if isinstance(root, _IndexLeaf):
        if root.hash_value != hash_value:
            return _index_merge_leaves(
                root, _IndexLeaf(hash_value, ((key, value),)), shift
            )
        entries = list(root.entries)
        for index, (existing, _) in enumerate(entries):
            if existing == key:
                entries[index] = (existing, value)
                return _IndexLeaf(hash_value, tuple(entries))
        entries.append((key, value))
        return _IndexLeaf(hash_value, tuple(entries))
    slot = (hash_value >> shift) & _INDEX_MASK
    children = root.children.copy()
    children[slot] = _index_set_at(
        children.get(slot), key, value, hash_value, shift + _INDEX_BITS
    )
    return _IndexBranch(children)


def _index_set(
    root: _IndexNode | None, key: tuple[str, str], value: _StateItem
) -> _IndexNode:
    return _index_set_at(root, key, value, _index_hash(key), 0)


def _index_get(root: _IndexNode | None, key: tuple[str, str]) -> _StateItem | None:
    hash_value = _index_hash(key)
    shift = 0
    while isinstance(root, _IndexBranch):
        root = root.children.get((hash_value >> shift) & _INDEX_MASK)
        shift += _INDEX_BITS
    if root is not None and root.hash_value == hash_value:
        for existing, value in root.entries:
            if existing == key:
                return value
    return None


def _index_add(root: _IndexNode | None, item: _StateItem) -> _IndexNode | None:
    if item.collection not in ("facts", "decisions"):
        return root
    return _index_set(root, (item.collection, item.value.key), item)  # type: ignore[attr-defined]


class _ReadCapture:
    __slots__ = ("records",)

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(self, key: str, origin: dict[str, Any]) -> None:
        self.records.append({"key": key, "origin": copy.deepcopy(origin)})


def _isolate_item_value(value: Fact | Decision | Rejection) -> Fact | Decision | Rejection:
    """Copy a validated value without invoking generic dataclass reconstruction."""
    if isinstance(value, Fact):
        isolated = object.__new__(Fact)
        object.__setattr__(isolated, "key", value.key)
        object.__setattr__(isolated, "value", copy.deepcopy(value.value))
        object.__setattr__(isolated, "source", value.source)
        object.__setattr__(isolated, "ts", value.ts)
        return isolated
    if isinstance(value, Decision):
        isolated = object.__new__(Decision)
        object.__setattr__(isolated, "key", value.key)
        object.__setattr__(isolated, "value", copy.deepcopy(value.value))
        object.__setattr__(isolated, "ts", value.ts)
        object.__setattr__(isolated, "reason", value.reason)
        return isolated
    return copy.deepcopy(value)


class State:
    """An immutable state snapshot.

    Writes create a new state sharing the committed log and extending a small
    attempt-local pending tuple.  The executor atomically promotes that tuple
    only after a returned attempt.  Reads copy values and capture their exact
    origin in the attempt-local recorder.
    """

    __slots__ = (
        "_log", "_pending", "_intent", "_metadata", "_reads", "_events",
        "_lineage", "_index",
    )

    def __init__(
        self,
        *,
        log: _ChunkedLog[_StateItem] | None = None,
        pending: _ChunkedLog[_StateItem] | None = None,
        intent: str = "",
        metadata: dict[str, Any] | None = None,
        reads: _ReadCapture | None = None,
        events: _ChunkedLog[dict[str, Any]] | None = None,
        lineage: object | None = None,
        index: _IndexNode | None = None,
    ) -> None:
        if not isinstance(intent, str):
            raise TypeError("intent must be a string")
        self._log = _ChunkedLog() if log is None else log
        self._pending = _ChunkedLog() if pending is None else pending
        self._intent = intent
        raw_metadata = {} if metadata is None else metadata
        if not isinstance(raw_metadata, dict) or not all(isinstance(key, str) for key in raw_metadata):
            raise TypeError("metadata must be a JSON object with string keys")
        for key in _STRING_METADATA_KEYS:
            if key in raw_metadata and not isinstance(raw_metadata[key], str):
                raise TypeError(f"standard metadata field {key!r} must be a string")
        self._metadata = {key: _value(value) for key, value in raw_metadata.items()}
        self._reads = reads
        self._events = _ChunkedLog() if events is None else events
        self._lineage = lineage or object()
        self._index = index

    @classmethod
    def _from_parts(
        cls,
        *,
        log: _ChunkedLog[_StateItem],
        pending: _ChunkedLog[_StateItem],
        intent: str,
        metadata: dict[str, Any],
        reads: _ReadCapture | None,
        events: _ChunkedLog[dict[str, Any]],
        lineage: object,
        index: _IndexNode | None,
    ) -> "State":
        state = object.__new__(cls)
        state._log = log
        state._pending = pending
        state._intent = intent
        state._metadata = metadata
        state._reads = reads
        state._events = events
        state._lineage = lineage
        state._index = index
        return state

    @classmethod
    def empty(cls, intent: str = "", metadata: dict[str, Any] | None = None) -> "State":
        return cls(intent=intent, metadata=metadata)

    @classmethod
    def new(
        cls,
        intent: str = "",
        *,
        facts: Iterable[Fact] = (),
        decisions: Iterable[Decision] = (),
        rejections: Iterable[Rejection] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "State":
        state = cls.empty(intent, metadata)
        initial: list[_StateItem] = []
        for collection, values in (
            ("facts", facts), ("decisions", decisions), ("rejections", rejections)
        ):
            for index, value in enumerate(values):
                expected = {
                    "facts": Fact,
                    "decisions": Decision,
                    "rejections": Rejection,
                }[collection]
                if not isinstance(value, expected):
                    raise TypeError(
                        f"State.new {collection} must contain only {expected.__name__} values"
                    )
                initial.append(_StateItem(collection, _isolate_item_value(value), {
                    "kind": "initial", "collection": collection, "index": index,
                }))
        state._log = state._log.extend(initial)
        for item in initial:
            state._index = _index_add(state._index, item)
        return state

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, list[dict[str, Any]]],
        *,
        intent: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "State":
        """Re-seed state from a trace snapshot for playback or resume."""
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be an object")
        facts = [Fact(**item) for item in snapshot.get("facts", [])]
        decisions = [Decision(**item) for item in snapshot.get("decisions", [])]
        rejections = [Rejection(**item) for item in snapshot.get("rejections", [])]
        return cls.new(
            intent, facts=facts, decisions=decisions, rejections=rejections,
            metadata=metadata,
        )

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return the complete committed state as a portable wire snapshot."""
        out: dict[str, list[dict[str, Any]]] = {
            "facts": [], "decisions": [], "rejections": []
        }
        for item in self._log:
            out[item.collection].append(item.value.to_dict())
        return out

    def _replay_commit(
        self, writes: dict[str, list[dict[str, Any]]], transition_seq: int
    ) -> "State":
        """Apply already-recorded committed writes with their original origins."""
        additions: list[_StateItem] = []
        constructors = {
            "facts": Fact, "decisions": Decision, "rejections": Rejection,
        }
        for collection in ("facts", "decisions", "rejections"):
            for index, wire in enumerate(writes.get(collection, [])):
                value = constructors[collection](**wire)
                additions.append(_StateItem(collection, value, {
                    "kind": "transition", "seq": transition_seq,
                    "collection": collection, "index": index,
                }))
        index = self._index
        for item in additions:
            index = _index_add(index, item)
        return State._from_parts(
            log=self._log.extend(additions), pending=_ChunkedLog(),
            intent=self._intent, metadata=self._metadata, reads=None,
            events=self._events, lineage=self._lineage, index=index,
        )

    def _spawn(self, *, pending: _ChunkedLog[_StateItem] | None = None) -> "State":
        return State._from_parts(
            log=self._log,
            pending=self._pending if pending is None else pending,
            intent=self._intent,
            metadata=self._metadata,
            reads=self._reads,
            events=self._events,
            lineage=self._lineage, index=self._index,
        )

    def _attempt_view(self, events: _ChunkedLog[dict[str, Any]]) -> "State":
        return State._from_parts(
            log=self._log, pending=_ChunkedLog(), intent=self._intent,
            metadata=self._metadata, reads=_ReadCapture(), events=events,
            lineage=self._lineage, index=self._index,
        )

    def _committed(self, returned: "State", transition_seq: int) -> "State":
        if not isinstance(returned, State):
            raise TypeError("a node must return State")
        if returned._lineage is not self._lineage or returned._log is not self._log:
            raise ValueError("returned state does not preserve the input state prefix")
        committed: list[_StateItem] = []
        indexes = {"facts": 0, "decisions": 0, "rejections": 0}
        for item in returned._pending:
            index = indexes[item.collection]
            indexes[item.collection] += 1
            committed.append(_StateItem(item.collection, item.value, {
                "kind": "transition", "seq": transition_seq,
                "collection": item.collection, "index": index,
            }))
        index = self._index
        for item in committed:
            index = _index_add(index, item)
        return State._from_parts(
            log=self._log.extend(committed), pending=_ChunkedLog(),
            intent=self._intent, metadata=self._metadata, reads=None,
            events=self._events, lineage=self._lineage, index=index,
        )

    @property
    def intent(self) -> str:
        if self._reads is not None:
            self._reads.add("intent", {"kind": "header", "field": "intent"})
        return self._intent

    def metadata(self, key: str, default: Any = None) -> Any:
        if not isinstance(key, str):
            raise TypeError("metadata key must be a string")
        if key in self._metadata:
            if self._reads is not None:
                self._reads.add(key, {"kind": "header", "field": "metadata"})
            return copy.deepcopy(self._metadata[key])
        if self._reads is not None:
            self._reads.add(key, {"kind": "absent", "surface": "metadata"})
        return copy.deepcopy(default)

    def _items(self, collection: str) -> list[_StateItem]:
        return [item for item in self._log if item.collection == collection] + [
            item for item in self._pending if item.collection == collection
        ]

    def _scan(self, collection: str) -> tuple[Any, ...]:
        if self._reads is not None:
            self._reads.add(collection, {"kind": "scan", "collection": collection})
        if collection == "events":
            return tuple(copy.deepcopy(item) for item in self._events)
        if len(self._pending):
            raise RuntimeError(
                "attempt-local writes are outputs and cannot be scanned before commit"
            )
        return tuple(copy.deepcopy(item.value) for item in self._items(collection))

    @property
    def facts(self) -> tuple[Fact, ...]:
        return self._scan("facts")

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return self._scan("decisions")

    @property
    def rejections(self) -> tuple[Rejection, ...]:
        return self._scan("rejections")

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return self._scan("events")

    def _lookup(self, collection: str, key: str, default: Any) -> Any:
        if not isinstance(key, str):
            raise TypeError("state keys must be strings")
        for item in reversed(tuple(self._pending)):
            if item.collection == collection and item.value.key == key:  # type: ignore[attr-defined]
                raise RuntimeError(
                    "attempt-local writes cannot be read before commit; keep the local value"
                )
        item = _index_get(self._index, (collection, key))
        if item is not None:
            if self._reads is not None:
                self._reads.add(key, item.origin)
            return copy.deepcopy(item.value.value)  # type: ignore[attr-defined]
        if self._reads is not None:
            self._reads.add(key, {"kind": "absent", "surface": collection})
        return copy.deepcopy(default)

    def fact(self, key: str, default: Any = None) -> Any:
        return self._lookup("facts", key, default)

    def decision(self, key: str, default: Any = None) -> Any:
        return self._lookup("decisions", key, default)

    def with_fact(self, fact: Fact) -> "State":
        if not isinstance(fact, Fact):
            raise TypeError("with_fact requires Fact")
        item = _StateItem("facts", _isolate_item_value(fact), {"kind": "pending"})
        return self._spawn(pending=self._pending.append(item))

    def with_decision(self, decision: Decision) -> "State":
        if not isinstance(decision, Decision):
            raise TypeError("with_decision requires Decision")
        item = _StateItem("decisions", _isolate_item_value(decision), {"kind": "pending"})
        return self._spawn(pending=self._pending.append(item))

    def with_rejection(self, rejection: Rejection) -> "State":
        if not isinstance(rejection, Rejection):
            raise TypeError("with_rejection requires Rejection")
        item = _StateItem("rejections", copy.deepcopy(rejection), {"kind": "pending"})
        return self._spawn(pending=self._pending.append(item))

    def _reads_wire(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._reads.records if self._reads is not None else [])

    def _writes_wire(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {"facts": [], "decisions": [], "rejections": []}
        for item in self._pending:
            out[item.collection].append(item.value.to_dict())
        return out

    def _initial_wire(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {"facts": [], "decisions": [], "rejections": []}
        for item in self._log:
            if item.origin.get("kind") == "initial":
                out[item.collection].append(item.value.to_dict())
        return out

    def _latest_decision(self, key: str) -> tuple[Any, dict[str, Any]] | None:
        item = _index_get(self._index, ("decisions", key))
        if item is not None:
            decision = item.value
            origin = item.origin
            if origin["kind"] == "transition":
                wire = {"kind": "transition", "seq": origin["seq"], "index": origin["index"]}
            elif origin["kind"] == "initial":
                wire = {"kind": "initial", "index": origin["index"]}
            else:
                return None
            return copy.deepcopy(decision.value), wire  # type: ignore[attr-defined]
        return None
