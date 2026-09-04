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
from vitruvyan_motus._text import encodable as _encodable, surrogate_at as _surrogate_at

__all__ = [
    "RedactedValue", "Fact", "Decision", "Rejection",
    "Trace", "redact",
]

T = TypeVar("T")

# Schema versions whose digest recipe DOES cover `prev_hash` (ADR-019), named
# as an allow-list on purpose. The deny-list this replaced failed OPEN: any
# version not in it — a typo, a future 4.0.0, a string an editor put in the
# header — was treated as chained and got a root. A guard about what may be
# anchored has to fail closed.
_CHAIN_BINDS_PREV = frozenset({"3.0.0"})


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


def _strict_plain_json(value: Any, *, reserve_redacted: bool = True,
                      scalars_required: bool = True) -> Any:
    """Validate and isolate one RFC 8259 value without coercion.

    `scalars_required=False` is for READING a document that declares a schema
    version written before the unpaired-surrogate refusal existed. This
    function is the producing boundary and also the isolation step `from_dict`
    takes, and those two are not the same question: 0.11.0 gave `_encodable`
    to both, so from that release the library could no longer READ a v0.5.0
    trace that its own validator still calls valid. Nothing said so and no test
    covered it (ADR-026 decision 4).
    """
    if isinstance(value, str):
        return _encodable(value) if scalars_required else value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not RFC 8259 JSON values")
        return value
    if isinstance(value, list):
        return [_strict_plain_json(item, reserve_redacted=reserve_redacted,
                                   scalars_required=scalars_required)
                for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        if scalars_required:
            for key in value:
                _encodable(key)
        if reserve_redacted and value.get("kind") == "redacted":
            raise ValueError("redacted values are reserved for redact()")
        return {
            key: _strict_plain_json(item, reserve_redacted=reserve_redacted,
                                    scalars_required=scalars_required)
            for key, item in value.items()
        }
    raise TypeError(
        f"{type(value).__name__} is not a strict RFC 8259 JSON value; "
        "convert it explicitly before writing"
    )


class NonCanonicalNumber(ValueError):
    """A lexeme written in characters this contract would not produce.

    Numbers (J2) and string escapes (J3) are one defect class and share one
    exception, because a caller catching it wants the same thing in both cases:
    the document's characters are not the ones its values denote, so its root is
    not the root of what a reader reads. ADR-024.
    """


def _canonical_number(lexeme: str, cast: Any) -> Any:
    """ADR-024 rule J2, hooked into the parser rather than matched by pattern.

    `parse_float` and `parse_int` are called only for real JSON numbers and
    receive the exact characters. A regular expression over the raw text would
    flag `{"note": "cost 5.10 eur"}`, where 5.10 is somebody's prose.
    """
    value = cast(lexeme)
    if json.dumps(value) != lexeme:
        raise NonCanonicalNumber(
            f"the number {lexeme} is not written the way this contract writes "
            f"the value it denotes, which is {json.dumps(value)}. The digest is "
            "taken over parsed values, so accepting this lexeme would let it "
            "share a root with the genuine document (ADR-024, rule J2)")
    return value


class _RepeatedMember(NonCanonicalNumber):
    """A repeated member name, which is J1 and is governed by no version.

    It has its own type only so the loader can tell it apart: J2 is version
    scoped and this is not, and both were raised as `NonCanonicalNumber`.
    """


def _refuse_repeated_members(pairs: list) -> dict:
    """RFC 8259 J1's duplicate-member half, at the loader that holds the text."""
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _RepeatedMember(
                f"the member {key!r} appears more than once. A reader and every "
                "first-wins parser take the first; Python takes the last, so "
                "this document says two different things and would earn the "
                "root of one of them")
        seen[key] = value
    return seen


def _loads_canonical(text: str) -> Any:
    """`json.loads` with the text rules a loader holding bytes can apply.

    J2 (numeric lexemes) and J1's duplicate-member half, which this loader did
    not have: a document repeating a member reads as the FIRST value to a human
    and to every first-wins parser, parses to the genuine object in Python, and
    was earning the genuine root here while `contract/validate.py` refused the
    same bytes. Two loaders disagreeing about what a Motus document is, at the
    one place ADR-024 introduces as where the guarantee lives.

    **Which escape form a string was written in is not checked, and ADR-026
    settles that as intended** — `"appro\u0076ed"` and `"approved"` are the
    same JSON string and share a root, correctly. What IS checked is that every
    string denotes text at all: this loader accepted `"\\ud800"`, which
    `_encodable` had refused to write since 0.11.0, so the producer and the
    reader in one package disagreed about what a Motus document is. That is the
    same defect this docstring already records against duplicate members, found
    a second time in the same function.
    """
    # Parse once with no lexical hooks to learn what the document DECLARES
    # ITSELF to be, then apply the rules that version was written under. Both
    # of these were unscoped here while `contract/validate.py` scoped them, so
    # the runtime refused 1.x and 2.x documents its own verifier accepts — a
    # false accusation, which `contract/README.md` ranks as the worst answer,
    # and the third instance of this docstring's own defect in one function.
    try:
        document = json.loads(
            text,
            object_pairs_hook=_refuse_repeated_members,
            parse_float=lambda lexeme: _canonical_number(lexeme, float),
            parse_int=lambda lexeme: _canonical_number(lexeme, int),
        )
    except NonCanonicalNumber as refusal:
        if isinstance(refusal, _RepeatedMember):
            raise
        # The version that decides whether J2 applies is inside the document,
        # so the naive order parses twice for every governed document. Only a
        # document a hook already refused pays for the second parse here.
        document = json.loads(text, object_pairs_hook=_refuse_repeated_members)
        if _governed(document, _LEXICALLY_GOVERNED):
            raise
    if _governed(document, _SCALAR_GOVERNED):
        # **Redundant, and kept deliberately.** Both callers of this loader
        # (`Trace.from_json` and `ReplayResult.from_json`) hand the result
        # straight to `from_dict`, which checks every string again through
        # `_strict_plain_json`. Dropping this would return ~30% of the read
        # path, measured -- and it would make the loader's answer depend on
        # what a caller does two frames later, which is precisely the coupling
        # that produced the defect ADR-026 repairs: a producer refused and a
        # reader did not, and neither could see the other. A loader holding
        # bytes decides what a Motus document is; that is what this function is
        # for. Anyone reading this to optimise it: measure `_loads_canonical`
        # BY ITSELF first, because that is the surface the frontier table names.
        _refuse_unpaired_surrogates(document)
    return document


#: Where each text rule starts applying, by the document's own declared trace
#: schema version. The two constants differ and the difference is measured:
#:
#: `J2` (ADR-024) protects the anchorable terminal digest, which arrives at
#: 3.0.0 — below it there is no root for a lexical collision to attack.
#:
#: The unpaired-surrogate refusal (ADR-026) starts at 2.0.0, where the
#: per-record digest made `_canonical_bytes` refuse such a string as a side
#: effect. v0.5.0 (schema 1.0.0) and v0.7.0 (schema 1.1.0) both WROTE traces
#: holding `report\udcff.csv`, and the validators of those releases called them
#: valid. Refusing them now would break `contract/README.md`'s promise that old
#: evidence stays valid without rewriting.
#:
#: The producing side is scoped by neither: it writes 3.0.0 and refuses always.
_LEXICALLY_GOVERNED = frozenset({"3.0.0"})
_SCALAR_GOVERNED = frozenset({"2.0.0", "3.0.0"})


def _governed(document: Any, versions: frozenset[str]) -> bool:
    """A TRACE schema version scopes these rules, and only a trace has one.

    A GraphSpec also declares `schema_version`, in its own namespace, so
    reading that key without checking the shape switches a trace rule off for
    a spec. Anything that is not a trace is governed — fail closed.
    """
    if not (isinstance(document, dict) and "run" in document):
        return True
    return document.get("schema_version") in versions


def _refuse_unpaired_surrogates(document: Any) -> None:
    """Rule J1 over the PARSED document — the value, never the lexeme.

    Iterative, because a genuine trace nested 489 deep raised `RecursionError`
    out of the withdrawn `J3` reader; and over the parsed document, because
    that is what makes object KEYS reachable at all -- CPython's `JSONObject`
    calls the module-global `scanstring`, so no decoder hook ever sees a
    member name.

    One C-level serialise decides (every string's characters, keys included,
    reach that output verbatim, so the encode raises exactly when one carries a
    surrogate); the walk runs only on a document already known to be bad, and
    exists to name the string. Measured on a 54 KiB trace: +24% on
    `Trace.from_json` became +16%.
    """
    encoder_refused = False
    try:
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        encoder_refused = True
    except RecursionError:
        # Deeper than the C encoder goes. It did not refuse the document, it
        # failed to answer -- and the walk below is iterative precisely so it
        # can. The first version of this fell through to the unconditional
        # refusal at the end, so a clean document nested past the encoder's
        # budget was refused with a message saying that should be impossible.
        pass
    else:
        return

    stack: list[Any] = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            _encodable(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                _encodable(key)
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)

    if not encoder_refused:
        return  # the encoder never objected; only its recursion budget ran out
    raise ValueError(
        "this document has no JSON encoding and the walk that locates the "
        "reason found nothing, which should be impossible. Refusing it anyway "
        "(rule J1, ADR-026)")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        # try/except costs nothing on the path that does not raise, which is
        # every genuine one -- this is the hot digest path and a walk here
        # would be paid for by every record. Reached by a caller who reaches
        # a digest without passing `_strict_plain_json`, e.g. a commitment
        # tenant; the bare codec error named neither the value nor the rule.
        raise ValueError(
            f"this value has no canonical JSON encoding and therefore no "
            f"digest: {exc}. A JSON string denotes a sequence of Unicode "
            "scalar values (rule J1, ADR-026)") from None


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
        _encodable(self.policy_ref)

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
        # Only `value` went through `_strict_plain_json`. `key` and `source`
        # were checked for being `str` and nothing else, so a string with no
        # UTF-8 encoding reached `_canonical_bytes` at seal time and raised
        # there -- out of `Runtime.run()`, not as NodeFailed, so the caller got
        # no state and no trace and the sink kept a run with no terminal
        # record. That is verbatim the outcome `_encodable`'s docstring
        # describes as the defect it closed in 0.11.0; it closed it at one
        # position out of several (ADR-026 decision 4).
        _encodable(self.key)
        _encodable(self.source)
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
        _encodable(self.key)
        if self.reason is not None:
            _encodable(self.reason)
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
        _encodable(self.what)
        _encodable(self.reason)
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

    __slots__ = ("_schema_version", "_run", "_records", "_view_cache", "_json_cache",
                 "_root_cache")

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
        self._root_cache = _MISSING

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
        instance._root_cache = _MISSING
        return instance

    @classmethod
    def from_json(cls, text: str) -> "Trace":
        """Load a trace from the TEXT of a document, which is the only place the
        whole guarantee is available.

        ADR-024. A number's digest is taken over its parsed value, so a genuine
        `5e+18` and a rewritten `5000000000000000511.0` are the same double and
        would share a root — while `jq`, `git diff` and a human read different
        numbers. The characters are the evidence, and a parser destroys them.

        So this refuses a numeric lexeme that is not what serializing its value
        produces (rule J2), before the document becomes objects.
        `from_dict` cannot do this and no implementation can: by the time it is
        called, the two documents are indistinguishable. A caller holding bytes
        should come through here.
        """
        return cls.from_dict(_loads_canonical(text))

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "Trace":
        """Load an isolated trace document while preserving its wire version.

        **The caller has already discarded the numeric lexemes**, so the J2
        guarantee of ADR-024 is not available here. Use :meth:`from_json` when
        the document's text is in reach.
        """
        # The declared version decides which rules this document was written
        # under, so it is read before the document is isolated rather than
        # after — `contract/README.md` promises old evidence stays valid
        # without rewriting, and that promise is the library's too, not only
        # the validator's.
        plain = _strict_plain_json(
            document, reserve_redacted=False,
            scalars_required=_governed(document, _SCALAR_GOVERNED))
        if not isinstance(plain, dict):
            raise TypeError("trace document must be an object")
        if set(plain) != {"schema_version", "run", "records"}:
            raise ValueError("trace document requires schema_version, run and records")
        if plain["schema_version"] not in ("1.0.0", "1.1.0", "2.0.0", "3.0.0"):
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

        The digest covers the record with its own `payload_hash` nulled and,
        under 3.0.0, its `prev_hash` present — a hash cannot cover its own
        value, and `prev_hash` is not it. Over the canonical object form, never
        over encoding bytes, so a trace re-encoded as JSONL or as a document
        hashes identically. The first record's `prev_hash` is the HEADER's
        digest, not null: a null first link would leave the header outside the
        chain, which is the hole ADR-017 found and ADR-019 finished closing.
        """
        # The header is the first record's predecessor, not nothing. Chaining
        # records alone left run_id, policy, metadata and graph.code_fingerprint
        # outside the root entirely: they could all be rewritten and the root —
        # the value an anchor publishes — did not move, and the validator passed
        # clean. Anchoring that root would have proved a sequence of records
        # existed while saying nothing about WHOSE run they were, under WHICH
        # policy, of WHICH graph.
        #
        # So the chain starts at the header. `prev_hash` is never null: a null
        # first link would be the same hole with a name.
        if len(self._records):
            prev_hash = self._records[len(self._records) - 1]["integrity"]["payload_hash"]
        else:
            header = {"schema_version": self._schema_version, "run": self._run}
            prev_hash = "sha256:" + hashlib.sha256(_canonical_bytes(header)).hexdigest()
        # Hashed as it arrives, with its integrity block still {null, null} — the
        # shape `_base` builds. Excluding the block by copying the record without
        # it costs an allocation per record and buys nothing: what the digest
        # must not cover is its own VALUE, and a constant null is not one. The
        # object hashed is therefore a real record rather than a projection, and
        # a validator recomputes it by nulling two fields rather than deleting a
        # key.
        # ADR-019. Under 3.0.0 the digest is taken with `prev_hash` PRESENT, so
        # each digest commits to its predecessor and the terminal one commits to
        # the whole run. Under 2.0.0 the whole block was nulled, which left every
        # digest independent of the link beside it: an editor could rewrite any
        # record, reseal by the published recipe, and the root did not move.
        # What a digest must not cover is its own value; `prev_hash` is not it.
        payload = dict(record)
        payload["integrity"] = (
            {"payload_hash": None, "prev_hash": None}
            if self._schema_version not in _CHAIN_BINDS_PREV
            else {"payload_hash": None, "prev_hash": prev_hash}
        )
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        sealed = dict(record)
        sealed["integrity"] = {"payload_hash": digest, "prev_hash": prev_hash}
        return sealed

    def _append_runtime(self, record: dict[str, Any]) -> "Trace":
        """Append a record already built from validated runtime primitives."""
        return Trace._from_parts(
            self._run, self._records.append(record), self._schema_version
        )

    def _header_digest(self) -> str:
        return "sha256:" + hashlib.sha256(_canonical_bytes(
            {"schema_version": self._schema_version, "run": self._run}
        )).hexdigest()

    def _derived_root(self) -> str | None:
        """Recompute the chain and return the root it DERIVES, or None.

        Reading `records[-1].integrity.payload_hash` is not deriving it. Three
        attacks live in that difference, and an adversarial round found all
        three within a day of the chain being fixed:

        - a document whose header was rewritten while `records[0].prev_hash`
          was left stale. Every digest recomputes, the chain is internally
          perfect, and the root does not move — because a digest covers the
          STRING that names its predecessor, not the predecessor. Deriving the
          root means hashing the header ourselves and refusing the document
          when the first link does not match what we computed;
        - a 2.0.0 document relabelled 3.0.0. One edited string used to turn an
          unanchorable value into an anchorable-looking one;
        - a document carrying any invented hash at all, which `from_dict`
          accepts because loading is not verifying.

        So this walks the chain, recomputing every digest under THIS version's
        recipe, and hands back a value only if what the document declares is
        what the document's own contents produce. Anything else returns None:
        the caller anchors nothing rather than a decoration.

        This is not a substitute for `contract/validate.py`, which checks the
        forty other things a trace must be. It is the narrow question the
        anchor asks — is this root really this document's? — answered by the
        object that is about to hand the value over.
        """
        if not len(self._records):
            return None
        last = self._records[len(self._records) - 1]
        if last["kind"] not in ("run_completed", "run_failed", "run_cancelled"):
            return None

        binds_prev = self._schema_version in _CHAIN_BINDS_PREV
        expected_prev = self._header_digest()
        for record in self._records:
            # `record.get("integrity") or {}` reaches `.get` on whatever is
            # there, and `from_dict` accepts an unvalidated document -- so a
            # record whose integrity is a string raised AttributeError out of a
            # property whose entire contract is to answer None when the
            # document does not earn a root. An anchor ingesting a malformed
            # file must be told "do not anchor this", not handed a crash.
            integrity = record.get("integrity")
            if not isinstance(integrity, dict):
                return None
            if integrity.get("prev_hash") != expected_prev:
                return None
            payload = dict(record)
            payload["integrity"] = {
                "payload_hash": None,
                "prev_hash": expected_prev if binds_prev else None,
            }
            digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            if integrity.get("payload_hash") != digest:
                return None
            expected_prev = digest
        return expected_prev

    @property
    def root(self) -> str | None:
        """The hash covering this whole trace, or None if there is not one.

        DERIVED, never read back: the value returned is recomputed from this
        document's own contents, and disagreement with what the document
        declares yields None. See `_derived_root` for the three attacks that
        distinction stops.

        Under 3.0.0 the terminal record's digest covers the terminal INCLUDING
        its `prev_hash`, which commits to the record before it, and so on to
        the first and — because the first link is recomputed here rather than
        trusted — to the header, and so to `run_id`, `policy`, `metadata` and
        `graph.code_fingerprint`.

        None means "do not anchor anything", and it has four causes, each of
        them the honest answer:

        - the trace is unfinished. Anchoring a prefix publishes a value a later
          complete trace contradicts;
        - the schema is below 3.0.0. Its digests do not cover `prev_hash`, so
          the terminal's hash covers one record rather than the run (ADR-019);
        - the chain does not verify, so this document is not what it says;
        - there are no records at all.
        """
        if self._root_cache is not _MISSING:
            return self._root_cache
        # Safe to memoize on the instance: a Trace is immutable and every
        # append returns a new one, so no cached root can outlive its records.
        if self._schema_version not in _CHAIN_BINDS_PREV:
            root = None
        else:
            root = self._derived_root()
        self._root_cache = root
        return root

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
