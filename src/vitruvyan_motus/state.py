"""Immutable node state as a view over the append-only state log."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable

from vitruvyan_motus.trace import (
    Decision, Fact, NonIntegerNumber, Rejection, RedactedValue, _ChunkedLog,
    _integer_only, _strict_plain_json, _value,
)

__all__ = ["State"]

_STRING_METADATA_KEYS = {
    "actor", "causation_id", "correlation_id", "ruleset_version",
}


@dataclass(frozen=True, slots=True)
class _StateItem:
    collection: str
    value: Fact | Decision | Rejection
    origin: dict[str, Any]


_StateIndex = dict[tuple[str, str], _StateItem]


def _index_item(index: _StateIndex, item: _StateItem) -> None:
    if item.collection in ("facts", "decisions"):
        index[(item.collection, item.value.key)] = item  # type: ignore[attr-defined]


def _advance_index(
    index: _StateIndex,
    old_log: _ChunkedLog[_StateItem],
    new_log: _ChunkedLog[_StateItem],
) -> _StateIndex:
    """Index newly completed chunks, preserving prior snapshots by copying.

    The current partial chunk is intentionally not indexed: lookup scans at
    most 63 entries there before consulting this dictionary.  Consequently a
    keyed read has a fixed upper bound while most appends allocate no index.
    """
    old_complete = len(old_log) // old_log._chunk_size
    new_complete = len(new_log) // new_log._chunk_size
    if old_complete == new_complete:
        return index
    updated = index.copy()
    for chunk_number in range(old_complete, new_complete):
        for item in new_log._chunks[chunk_number]:
            _index_item(updated, item)
    return updated


def _build_index(log: _ChunkedLog[_StateItem]) -> _StateIndex:
    return _advance_index({}, _ChunkedLog(chunk_size=log._chunk_size), log)


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


def _refuse_non_integer(value: Any, path: str, *, surface: str) -> None:
    """Rule J4 (ADR-030) at a producing boundary, naming the write.

    `_integer_only` knows the value and the path inside it; it does not know
    which write it was handed, and a message that says "at $.value" without
    naming the fact is a message that sends a node author to grep. The surface
    prefixes the walk's finding: `fact 'temperature': rule J4 ...`.
    """
    try:
        _integer_only(value, path)
    except NonIntegerNumber as exc:
        raise NonIntegerNumber(f"{surface}: {exc}") from None


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
        index: _StateIndex | None = None,
        _check_integers: bool = True,
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
        # The runtime's metadata seeding is a producing boundary: the header's
        # metadata is hashed by T11, and under rule J4 (ADR-030) every number
        # in a 3.2.0 trace is a JSON integer. The refusal therefore happens
        # here, at `State.empty`/`State.new` — before the run starts, before
        # anything is written. `from_snapshot` (a READER) clears the flag:
        # evidence written before J4 existed stays readable without rewriting.
        if _check_integers:
            for key, value in raw_metadata.items():
                _refuse_non_integer(
                    value, f"$.metadata.{key}", surface=f"metadata {key!r}")
        self._metadata = {key: _value(value) for key, value in raw_metadata.items()}
        self._reads = reads
        self._events = _ChunkedLog() if events is None else events
        self._lineage = lineage or object()
        self._index = _build_index(self._log) if index is None else index

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
        index: _StateIndex,
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
    def empty(
        cls, intent: str = "", metadata: dict[str, Any] | None = None,
        *, _check_integers: bool = True,
    ) -> "State":
        return cls(intent=intent, metadata=metadata, _check_integers=_check_integers)

    @classmethod
    def new(
        cls,
        intent: str = "",
        *,
        facts: Iterable[Fact] = (),
        decisions: Iterable[Decision] = (),
        rejections: Iterable[Rejection] = (),
        metadata: dict[str, Any] | None = None,
        _check_integers: bool = True,
    ) -> "State":
        state = cls.empty(intent, metadata, _check_integers=_check_integers)
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
                # `State.new` seeds `run_started.initial_state`, which rule J4
                # (ADR-030) covers — a number in it is a number in the trace.
                # Skip the walk when READING (`from_snapshot`), whose documents
                # were truthful under their own version.
                if _check_integers and collection == "facts":
                    _refuse_non_integer(
                        value.value, "$.value", surface=f"fact {value.key!r}")
                elif _check_integers and collection == "decisions":
                    _refuse_non_integer(
                        value.value, "$.value", surface=f"decision {value.key!r}")
                elif _check_integers:
                    _refuse_non_integer(
                        value.evidence, "$.evidence",
                        surface=f"rejection {value.what!r}")
                initial.append(_StateItem(collection, _isolate_item_value(value), {
                    "kind": "initial", "collection": collection, "index": index,
                }))
        old_log = state._log
        state._log = old_log.extend(initial)
        state._index = _advance_index(state._index, old_log, state._log)
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
            # A reader, not a producer: `from_snapshot` re-seeds state from a
            # document that declares its own version, and evidence written
            # before rule J4 existed stays readable without rewriting
            # (ADR-030 decision 2). The document-level J4 gate lives at
            # `Trace.from_json`/`from_dict`, which know the declared version;
            # a 3.2.0 document carrying a float is refused there, before it
            # can reach a snapshot.
            _check_integers=False,
        )

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return the complete committed state as a portable wire snapshot.

        Called from inside a node this is a read of all three collections, and
        is recorded as three scans — not as the keys that happen to be present.
        A declaration that varied with the data would not be a declaration: the
        same node against a state with no decisions yet would need a different
        one. So the reads describe what the code asked for, which is
        everything, and `reads_declared` naming the three collections is how a
        node that legitimately needs the whole state says so.

        Called from the host `self._reads` is None and nothing is recorded,
        which is correct: serialising state for storage or replay is not a node
        reading it.
        """
        for collection in ("facts", "decisions", "rejections"):
            self._note_scan(collection)
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
        new_log = self._log.extend(additions)
        index = _advance_index(self._index, self._log, new_log)
        return State._from_parts(
            log=new_log, pending=_ChunkedLog(),
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
        new_log = self._log.extend(committed)
        index = _advance_index(self._index, self._log, new_log)
        return State._from_parts(
            log=new_log, pending=_ChunkedLog(),
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

    def _note_scan(self, collection: str) -> None:
        """Record that a whole collection was examined.

        Every bulk read goes through here. It exists as its own method because
        the two ways of reading a whole collection — the scan properties and
        `snapshot()` — once disagreed: `snapshot()` returned the entire
        committed state and recorded nothing, so a node could declare
        `reads_declared: []`, read every fact including ones it had no business
        seeing, and leave a trace saying it read nothing. One caller owning the
        recording makes that particular drift structurally impossible rather
        than a thing to remember.
        """
        if self._reads is not None:
            self._reads.add(collection, {"kind": "scan", "collection": collection})

    def _scan(self, collection: str) -> tuple[Any, ...]:
        self._note_scan(collection)
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
        item = self._latest_item(collection, key)
        if item is not None:
            if self._reads is not None:
                self._reads.add(key, item.origin)
            return copy.deepcopy(item.value.value)  # type: ignore[attr-defined]
        if self._reads is not None:
            self._reads.add(key, {"kind": "absent", "surface": collection})
        return copy.deepcopy(default)

    def _latest_item(self, collection: str, key: str) -> _StateItem | None:
        partial_start = (len(self._log) // self._log._chunk_size) * self._log._chunk_size
        for position in range(len(self._log) - 1, partial_start - 1, -1):
            item = self._log[position]
            if (
                item.collection == collection
                and item.value.key == key  # type: ignore[attr-defined]
            ):
                return item
        return self._index.get((collection, key))

    def fact(self, key: str, default: Any = None) -> Any:
        return self._lookup("facts", key, default)

    def decision(self, key: str, default: Any = None) -> Any:
        return self._lookup("decisions", key, default)

    def with_fact(self, fact: Fact) -> "State":
        if not isinstance(fact, Fact):
            raise TypeError("with_fact requires Fact")
        # The producing boundary for a fact value (ADR-030, rule J4): refuse a
        # number this trace's version cannot carry BEFORE the write is pending.
        # The Fact constructor stays lenient on purpose — `State.from_snapshot`
        # and `_replay_commit` construct through it while READING a document
        # that declares its own (possibly pre-J4) version.
        _refuse_non_integer(fact.value, "$.value", surface=f"fact {fact.key!r}")
        item = _StateItem("facts", _isolate_item_value(fact), {"kind": "pending"})
        return self._spawn(pending=self._pending.append(item))

    def with_decision(self, decision: Decision) -> "State":
        if not isinstance(decision, Decision):
            raise TypeError("with_decision requires Decision")
        _refuse_non_integer(
            decision.value, "$.value", surface=f"decision {decision.key!r}")
        item = _StateItem("decisions", _isolate_item_value(decision), {"kind": "pending"})
        return self._spawn(pending=self._pending.append(item))

    def with_rejection(self, rejection: Rejection) -> "State":
        if not isinstance(rejection, Rejection):
            raise TypeError("with_rejection requires Rejection")
        _refuse_non_integer(
            rejection.evidence, "$.evidence",
            surface=f"rejection {rejection.what!r}")
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
        item = self._latest_item("decisions", key)
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
