"""Trace values, records, envelope and the append-only chunked log."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, Iterable, Iterator, TypeVar

from vitruvyan_motus import TRACE_SCHEMA_VERSION
from vitruvyan_motus.effects import EffectDescriptor

__all__ = [
    "RedactedValue", "Fact", "Decision", "Rejection",
    "Trace", "redact",
]

T = TypeVar("T")


class _Missing:
    """Identity sentinel that remains itself across isolation boundaries."""

    __slots__ = ()

    def __copy__(self) -> "_Missing":
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> "_Missing":
        return self

    def __reduce__(self):
        return (_restore_missing, ())


def _restore_missing() -> "_Missing":
    """Return the process-local singleton after a pickle round trip."""
    return _MISSING


_MISSING = _Missing()
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class _FrozenDict(dict):
    def _blocked(self, *args, **kwargs):
        raise TypeError("trace records are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _blocked


class _FrozenList(list):
    def _blocked(self, *args, **kwargs):
        raise TypeError("trace records are immutable")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _blocked


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


def _wire_timestamp(value: str | datetime) -> str:
    if isinstance(value, str):
        if _TIMESTAMP_RE.fullmatch(value) is None:
            raise ValueError(
                "timestamps must use canonical RFC 3339 UTC form "
                "YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
            )
        try:
            datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("timestamp is not calendar-valid RFC 3339") from exc
        return value
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime or RFC 3339 UTC string")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_plain_json(value: Any, *, reserve_redacted: bool = True) -> Any:
    """Validate and isolate one RFC 8259 value without coercion."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not RFC 8259 JSON values")
        return value
    if isinstance(value, list):
        return [_strict_plain_json(item, reserve_redacted=reserve_redacted) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        if reserve_redacted and value.get("kind") == "redacted":
            raise ValueError("redacted values are reserved for redact()")
        return {
            key: _strict_plain_json(item, reserve_redacted=reserve_redacted)
            for key, item in value.items()
        }
    raise TypeError(
        f"{type(value).__name__} is not a strict RFC 8259 JSON value; "
        "convert it explicitly before writing"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RedactedValue:
    """A structural placeholder whose secret content never enters the trace."""

    hash: str
    policy_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.hash, str) or not self.hash.startswith("redacted:sha256:"):
            raise ValueError("redacted hash must be prefixed with 'redacted:sha256:'")
        digest = self.hash[16:]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("redacted hash must contain a lowercase SHA-256 digest")
        if not isinstance(self.policy_ref, str) or not self.policy_ref:
            raise ValueError("redaction policy_ref must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return {"kind": "redacted", "hash": self.hash, "policy_ref": self.policy_ref}


def redact(value: Any, policy_ref: str) -> RedactedValue:
    """Replace a strict JSON value with its content hash and policy reference."""
    plain = _strict_plain_json(value)
    return RedactedValue(
        "redacted:sha256:" + hashlib.sha256(_canonical_bytes(plain)).hexdigest(), policy_ref
    )


def _value(value: Any) -> Any:
    if isinstance(value, RedactedValue):
        return value.to_dict()
    return _strict_plain_json(value)


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    value: Any
    source: str
    ts: str | datetime

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not isinstance(self.source, str):
            raise TypeError("Fact key and source must be strings")
        object.__setattr__(self, "value", _value(self.value))
        object.__setattr__(self, "ts", _wire_timestamp(self.ts))

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": copy.deepcopy(self.value), "source": self.source, "ts": self.ts}


@dataclass(frozen=True, slots=True)
class Decision:
    key: str
    value: Any
    ts: str | datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise TypeError("Decision key must be a string")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("Decision reason must be a string when present")
        object.__setattr__(self, "value", _value(self.value))
        object.__setattr__(self, "ts", _wire_timestamp(self.ts))

    def to_dict(self) -> dict[str, Any]:
        out = {"key": self.key, "value": copy.deepcopy(self.value), "ts": self.ts}
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True, slots=True)
class Rejection:
    what: str
    reason: str
    ts: str | datetime
    evidence: Any = _MISSING

    def __post_init__(self) -> None:
        if not isinstance(self.what, str) or not isinstance(self.reason, str):
            raise TypeError("Rejection what and reason must be strings")
        object.__setattr__(self, "ts", _wire_timestamp(self.ts))
        if self.evidence is not _MISSING:
            object.__setattr__(self, "evidence", _value(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        out = {"what": self.what, "reason": self.reason, "ts": self.ts}
        if self.evidence is not _MISSING:
            out["evidence"] = copy.deepcopy(self.evidence)
        return out


class _ChunkedLog(Generic[T]):
    """Persistent append-only sequence with bounded-copy appends."""

    __slots__ = ("_chunks", "_length", "_chunk_size")

    def __init__(
        self, chunks: tuple[tuple[T, ...], ...] = (), length: int = 0, chunk_size: int = 64
    ) -> None:
        self._chunks = chunks
        self._length = length
        self._chunk_size = chunk_size

    def append(self, item: T) -> "_ChunkedLog[T]":
        if self._chunks and len(self._chunks[-1]) < self._chunk_size:
            chunks = self._chunks[:-1] + (self._chunks[-1] + (item,),)
        else:
            chunks = self._chunks + ((item,),)
        return _ChunkedLog(chunks, self._length + 1, self._chunk_size)

    def extend(self, items: Iterable[T]) -> "_ChunkedLog[T]":
        result = self
        for item in items:
            result = result.append(item)
        return result

    def __len__(self) -> int:
        return self._length

    def __iter__(self) -> Iterator[T]:
        for chunk in self._chunks:
            yield from chunk

    def __getitem__(self, index: int) -> T:
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError(index)
        chunk_index, inner = divmod(index, self._chunk_size)
        return self._chunks[chunk_index][inner]

    def is_prefix_of(self, other: "_ChunkedLog[T]") -> bool:
        if self._length > other._length:
            return False
        # Persistent appends share complete chunks.  At most one partial
        # boundary chunk needs value comparison.
        complete, remainder = divmod(self._length, self._chunk_size)
        if self._chunks[:complete] != other._chunks[:complete]:
            return False
        if remainder:
            return self._chunks[complete] == other._chunks[complete][:remainder]
        return True


class Trace:
    """A trace envelope backed by a persistent record log."""

    __slots__ = ("_schema_version", "_run", "_records", "_view_cache", "_json_cache")

    def __init__(
        self, run: dict[str, Any], records: _ChunkedLog[dict[str, Any]] | None = None,
        *, schema_version: str = TRACE_SCHEMA_VERSION,
    ) -> None:
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError("trace schema_version must be a non-empty string")
        self._schema_version = schema_version
        self._run = _strict_plain_json(run, reserve_redacted=False)
        self._records = _ChunkedLog() if records is None else records
        self._view_cache = None
        self._json_cache = None

    @classmethod
    def _from_parts(
        cls, run: dict[str, Any], records: _ChunkedLog[dict[str, Any]], schema_version: str
    ) -> "Trace":
        instance = object.__new__(cls)
        instance._schema_version = schema_version
        instance._run = run
        instance._records = records
        instance._view_cache = None
        instance._json_cache = None
        return instance

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "Trace":
        """Load an isolated trace document while preserving its wire version."""
        plain = _strict_plain_json(document, reserve_redacted=False)
        if not isinstance(plain, dict):
            raise TypeError("trace document must be an object")
        if set(plain) != {"schema_version", "run", "records"}:
            raise ValueError("trace document requires schema_version, run and records")
        if plain["schema_version"] not in ("1.0.0", "1.1.0", "2.0.0"):
            raise ValueError("unsupported trace schema version")
        if not isinstance(plain["run"], dict) or not isinstance(plain["records"], list):
            raise TypeError("trace run must be an object and records an array")
        if plain["records"] and (
            not isinstance(plain["records"][0], dict)
            or plain["records"][0].get("kind") != "run_started"
        ):
            raise ValueError("trace records must begin with run_started")
        log: _ChunkedLog[dict[str, Any]] = _ChunkedLog()
        terminal_seen = False
        for index, record in enumerate(plain["records"], start=1):
            if not isinstance(record, dict):
                raise TypeError("every trace record must be an object")
            if record.get("seq") != index:
                raise ValueError("trace record sequences must be gapless from one")
            if terminal_seen:
                raise ValueError("trace records cannot follow a terminal record")
            terminal_seen = record.get("kind") in (
                "run_completed", "run_failed", "run_cancelled"
            )
            log = log.append(record)
        return cls(plain["run"], log, schema_version=plain["schema_version"])

    def _view(self) -> dict[str, Any]:
        if self._view_cache is None:
            self._view_cache = _freeze_json({
                "schema_version": self._schema_version,
                "run": self._run,
                "records": list(self._records),
            })
        return self._view_cache

    @property
    def header(self) -> dict[str, Any]:
        """The document's ``TraceHeader``: ``{schema_version, run}``.

        This — not :attr:`run` — is what a durable sink needs. ``run`` alone is
        not a valid ``TraceHeader`` (``schema_version`` is required, and the
        schema forbids additional properties inside ``run``), so a sink handed
        only ``run`` could produce a conforming document only by importing the
        version from the writer. ADR-011.
        """
        return copy.deepcopy({"schema_version": self._schema_version, "run": self._run})

    @property
    def run(self) -> dict[str, Any]:
        # Never expose the cached evidence object.  Even a dict subclass that
        # blocks normal mutation can be changed through ``dict.__setitem__``.
        # Isolation, not convention, protects the trace.
        return copy.deepcopy(self._run)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(record) for record in self._records)

    @property
    def log(self) -> _ChunkedLog[dict[str, Any]]:
        # A compatibility-shaped read view, never the evidence-owned dicts.
        # `_ChunkedLog` is structurally persistent but its generic items need
        # not be immutable; returning the internal log would let a caller
        # mutate records behind the cached JSON/bundle fingerprint.
        return _ChunkedLog().extend(copy.deepcopy(record) for record in self._records)

    @property
    def _runtime_log(self) -> _ChunkedLog[dict[str, Any]]:
        """Trusted package-internal log; never expose across the API boundary."""
        return self._records

    def append(self, record: dict[str, Any]) -> "Trace":
        isolated = _strict_plain_json(record, reserve_redacted=False)
        return Trace._from_parts(
            self._run, self._records.append(isolated), self._schema_version
        )

    def _seal(self, record: dict[str, Any]) -> dict[str, Any]:
        """Return this record sealed into the integrity chain.

        Sealing is separate from appending, and it has to be: the sink is
        handed the record BEFORE the trace appends it, so sealing at append
        time put real hashes in memory and null ones in the artifact. Two
        accounts of one run that disagree is worse than neither having a chain.
        `_store` seals once, at the top, and the same sealed dict goes to the
        sink and to the trace.

        The chain needs no state of its own: the previous hash is already in the
        previous record. A trace carries its own chain and can be re-checked
        from nothing but itself, which is what makes an offline validator
        possible.

        The digest covers the record WITHOUT its own integrity block — a hash
        cannot cover itself — over the canonical object form, never over
        encoding bytes, so a trace re-encoded as JSONL or as a document hashes
        identically. The first record's `prev_hash` is null: it has no
        predecessor, and inventing a genesis value would be a constant that
        looks like evidence.
        """
        previous = self._records[len(self._records) - 1] if len(self._records) else None
        prev_hash = previous["integrity"]["payload_hash"] if previous else None
        # Hashed as it arrives, with its integrity block still {null, null} — the
        # shape `_base` builds. Excluding the block by copying the record without
        # it costs an allocation per record and buys nothing: what the digest
        # must not cover is its own VALUE, and a constant null is not one. The
        # object hashed is therefore a real record rather than a projection, and
        # a validator recomputes it by nulling two fields rather than deleting a
        # key.
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(record)).hexdigest()
        sealed = dict(record)
        sealed["integrity"] = {"payload_hash": digest, "prev_hash": prev_hash}
        return sealed

    def _append_runtime(self, record: dict[str, Any]) -> "Trace":
        """Append a record already built from validated runtime primitives."""
        return Trace._from_parts(
            self._run, self._records.append(record), self._schema_version
        )

    @property
    def root(self) -> str | None:
        """The hash covering this whole trace, or None if it has no terminal.

        Deliberately not a stored field. The terminal record's `payload_hash`
        already covers the terminal, which chains the record before it, and so
        on to the first — so the root is derived, and a second copy of it could
        only ever disagree with the first.

        An unfinished trace has no root, and that is the honest answer rather
        than a partial one: anchoring a prefix would publish a value that a
        later complete trace contradicts.
        """
        if not len(self._records):
            return None
        last = self._records[len(self._records) - 1]
        if last["kind"] not in ("run_completed", "run_failed", "run_cancelled"):
            return None
        return last["integrity"]["payload_hash"]

    def to_dict(self) -> dict[str, Any]:
        # A document materialization is deliberately cold: callers receive an
        # isolated object and benchmarks measure the work named by to_dict(),
        # never a parse of to_json()'s memoized encoding.
        return json.loads(json.dumps(
            self._view(), ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ))

    def to_json(self) -> str:
        if self._json_cache is None:
            self._json_cache = json.dumps(
                self._view(),
                ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
        return self._json_cache

    def to_jsonl(self) -> str:
        header = json.dumps(
            {"schema_version": self._schema_version, "run": self._run},
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
        lines = [header]
        lines.extend(
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            for record in self._records
        )
        return "\n".join(lines) + "\n"
