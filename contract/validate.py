#!/usr/bin/env python3
"""The executable fence of the Motus contract (contract/README.md §"Executable fence").

JSON Schema says what a single document may look like; this module enforces what
the schemas cannot: the GraphSpec R-rules, the trace T-rules, the JSONL stream
form, and the correlations between a trace and the GraphSpec it ran.

Layering (deliberate, mirrored for both artifacts):

1.  JSON Schema validation first — Draft 2020-12 with ``FormatChecker``
    attached.  NOTE: attaching ``FormatChecker`` does NOT make ``format:
    date-time`` an assertion here — jsonschema ships no date-time checker
    unless extra dependencies (``rfc3339-validator``) are installed, and this
    module deliberately stays stdlib + jsonschema.  The calendar truth of
    timestamps is therefore enforced explicitly by semantic rule T9: the
    schema's regex pins the SHAPE of a timestamp, and T9 checks the date for
    real (``datetime.strptime``), because a pattern alone admits month 13.
    Schema violations carry rule ``SCHEMA``.
2.  Semantic rules run only on a schema-clean document.  Structural graph rules
    (R1, R2, R4, R5, R8) run before flow analyses (R3, R11): reachability and
    cycle analysis over a structurally broken graph would only cascade noise.

Rules this validator deliberately does NOT check, and why:

*   R6 — a route map has at least one entry (``minProperties`` covers that part),
    but DUPLICATE map keys are unverifiable after JSON parsing: ``json.loads``
    keeps the last occurrence and the duplication is gone before any validator
    can see it.  Producers MUST NOT emit duplicate keys; a post-parse validator
    cannot be blamed for not catching them (graphspec description, R6).
*   R7 — strict-route miss behavior (miss routing record, selected END, run
    failure under policy strict) is runtime semantics; the trace side of it is
    checked here as T8/T7 correlations, the spec side is only data.
*   R9 — "only string decision values are routable, no coercion" binds the
    runtime's dispatch, not the spec document.  The trace side (matched/default
    observed values are strings) is schema-enforced.
*   R10 — "most recent recorded value" is an execution-time resolution rule;
    statically there is no observed value to resolve.  The trace records its
    outcome in ``routing.origin`` — a structured edge naming the exact
    Decision observed, not merely the record that held it.
*   T-rules that name runtime duties (clock source, capture-not-declare) are
    likewise out of scope: this module checks recorded evidence, not behavior.

JSONL form (trace schema description, ADR-001 open choice 1): one header line
matching ``#/$defs/TraceHeader`` (rule ``JSONL1``), then one line per record
matching ``#/$defs/Record`` (rule ``JSONL2``).  A FINAL line that fails to
JSON-parse is crash truncation — the stream is incomplete, which is violation
``T3/INCOMPLETE`` when a complete trace was expected and no violation at all
otherwise (guarantees.md invariant II treats truncation as evidence, not as a
malformed stream).  A NON-final unparsable line is a malformed stream: JSONL2.

Strict RFC 8259 (rule J1): Python's ``json`` accepts NaN/Infinity/-Infinity;
the contract does not.  Every parse this module performs refuses those
constants (reported as J1 — for a JSONL line that means J1, not JSONL2, since
the line IS parseable, just not strict), and the API entry points, which
receive already-parsed documents, walk them RECURSIVELY at every depth:
non-finite floats, non-string mapping keys, tuples and any Python object that
is not a JSON value (sets, arbitrary instances, ...) are all refused as J1.

JSONL is LF-only (rule JSONL3): a single CR byte anywhere in the stream —
CRLF endings included — rejects it before any line processing.  Canonical
writers emit LF; readers do not silently normalize.

Round 4 additions (trace schema description, contract/README.md):

*   ``canonical_json`` / ``fingerprint`` — the canonical form fingerprints are
    computed over (README §"Fingerprints"); public, because SB2 and the tests
    recompute them rather than trusting recorded values.
*   E-rules E1–E11 — the execution state machine.  A trace is a GRAPH
    EXECUTION, not merely a coherent record sequence: entry runs first,
    routing drives the next attempt, retry/abort/continue have mandatory
    successors, attempt numbering restarts per activation, and failure causes
    are admissible only where the machine can produce them.  They run after
    the T-rules and ONLY on a lifecycle-clean stream (no T1/T2/T3/T5/T6/T7
    findings): running a state machine over incoherent records would only
    cascade the incoherence already reported.
*   SB-rules SB1–SB4 — spec binding, when a GraphSpec is supplied: header
    identity, RECOMPUTED graph fingerprint, per-transition effect_class
    correlation, and truthfulness of the recorded declaration violations.

Dependencies: stdlib + jsonschema.  Nothing else.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import operator
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match
from referencing import Registry, Resource

_CONTRACT_DIR = Path(__file__).resolve().parent
_GRAPHSPEC_SCHEMA_FILE = "graphspec.v1.schema.json"
_TRACE_SCHEMA_FILE = "trace.v1.schema.json"
_COMMITMENT_SCHEMA_FILE = "commitment.v1.schema.json"
_CHECKPOINT_SCHEMA_FILE = "checkpoint.v1.schema.json"
_RECEIPT_SCHEMA_FILE = "receipt.v1.schema.json"
_SYSTEM_MANIFEST_SCHEMA_FILE = "system-manifest.v1.schema.json"

_TERMINAL_KINDS = frozenset({"run_completed", "run_failed", "run_cancelled"})


@dataclass(frozen=True)
class Violation:
    """One broken rule: which rule, where in the document, and why."""

    rule: str
    path: str
    message: str


# --------------------------------------------------------------------------- #
# Strict RFC 8259 parsing — rule J1                                           #
# --------------------------------------------------------------------------- #


class StrictJSONError(ValueError):
    """Text that Python parses and RFC 8259 strictness refuses (J1)."""


class DuplicateKeyJSONError(StrictJSONError):
    """An object with a repeated member name: a document with two readings.

    RFC 8259 only SHOULD-s unique names, and every mainstream parser silently
    keeps one — Python, jq, node, serde and jsonb all keep the last. That is
    survivable for a log and fatal for evidence: a file can carry two complete
    accounts of a run, this validator hashes the one its parser kept, and the
    root reproduces exactly while a human reading the file, a first-wins reader
    or `git diff` sees the other account. A trace is refused rather than
    silently disambiguated, because which reading is "the" document is not a
    question this program is entitled to answer.
    """


class NonFiniteJSONError(StrictJSONError):
    """A JSON text carried NaN, Infinity or -Infinity (refused per rule J1)."""


def _refuse_non_finite(token: str) -> Any:
    raise NonFiniteJSONError(
        f"non-finite JSON constant {token} is refused; "
        "trace values are strict RFC 8259 JSON (rule J1)"
    )


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise DuplicateKeyJSONError(
                f"object member {key!r} appears more than once; this document "
                "has more than one reading and cannot be evidence of one run"
            )
        seen.add(key)
    return dict(pairs)


class UnpairedSurrogateError(StrictJSONError):
    """A JSON string that does not denote Unicode text (refused per rule J1)."""


def _surrogate_at(text: str) -> int | None:
    """The index of the first surrogate code point in ``text``, or None.

    In a Python `str` every surrogate is unpaired by construction: the JSON
    decoder combines `\\uD83D\\uDE00` into one scalar at scan time, so a
    surrogate that survives into a parsed value is one no pair claimed. Two
    adjacent surrogates built directly in Python are two code points and not a
    character, and are refused on the same grounds.

    `isascii()` is a C-level flag check and answers for the overwhelming
    majority of strings without encoding anything; the encode that follows is
    one C call over the rest, and `UnicodeEncodeError.start` hands back the
    position for free. This is the same test `trace._encodable` has applied on
    the producing side since 0.11.0 — deliberately the same test, because the
    defect it closes is that the two sides disagreed.
    """
    if text.isascii():
        return None
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        return exc.start
    return None


def _surrogate_complaint(text: str, index: int, where: str) -> str:
    return (
        f"the string {where} carries U+{ord(text[index]):04X} at index {index}, "
        "an unpaired surrogate. A JSON string denotes a sequence of Unicode "
        "scalar values; a surrogate code point is not one, has no UTF-8 "
        "encoding, and conforming readers disagree about what it means -- so "
        "this document has more than one reading and cannot be evidence of one "
        "run (rule J1, ADR-026)"
    )


def _refuse_unpaired_surrogates(document: Any) -> None:
    """Rule J1's third half, over the PARSED document rather than its text.

    This is not `J3` returning under another name, and the difference is which
    question is asked. `J3` asked *which escape form was written*, which only
    the lexeme answers -- so it needed `parse_string`, which needed CPython's
    pure-Python scanner (13x slower, a recursion budget that depended on the
    caller's stack, and object KEYS structurally out of reach because
    `JSONObject` calls the module-global `scanstring`). This asks *what value
    did the reader get*, which the parsed document answers: the C scanner keeps
    running, keys are covered because keys are in the document, and
    `"appro\\u0076ed"` stays exactly as valid as `"approved"`.

    Iterative and not recursive on purpose: a genuine trace nested 489 deep
    raised `RecursionError` out of `J3`'s reader, and a refusal a document does
    not deserve is the worst answer a verifier gives.

    **Two passes, and the first one decides.** Serialising the whole document
    once with `ensure_ascii=False` and encoding it puts the question to the C
    encoder: every string's characters, keys included, land in that output
    verbatim, so the encode raises exactly when some string carries a
    surrogate. Measured on a 54 KiB trace it is 1.4 ms against the walk's
    3.2 ms, which is why `_loads_strict` costs +15% and not +63%. The walk then
    runs only on a document already known to be bad, and its job is to say
    WHERE -- a verifier that refuses without naming the place is not much
    better than one that crashes.

    If they ever disagreed, this **fails closed**: the encoder's verdict stands
    and the refusal is raised without a path rather than swallowed.
    `tests/test_surrogate_boundary.py` asserts they agree over the table.
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

    stack: list[tuple[Any, str]] = [(document, "$")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            index = _surrogate_at(value)
            if index is not None:
                raise UnpairedSurrogateError(
                    _surrogate_complaint(value, index, f"at {path}"))
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    index = _surrogate_at(key)
                    if index is not None:
                        raise UnpairedSurrogateError(
                            _surrogate_complaint(key, index, f"used as a member name at {path}"))
                stack.append((item, f"{path}.{key}"))
        elif isinstance(value, list):
            for position, item in enumerate(value):
                stack.append((item, f"{path}[{position}]"))

    if not encoder_refused:
        return  # the encoder never objected; only its recursion budget ran out
    raise UnpairedSurrogateError(
        "this document cannot be encoded as JSON text, and the walk that "
        "locates the reason found nothing -- which should be impossible. "
        "Refusing it anyway: a verifier that cannot say what a document is "
        "must not say it is evidence (rule J1, ADR-026)")


class NonCanonicalNumberError(StrictJSONError):
    """A number written in characters our own serializer would not produce (J2)."""


def _canonical_number(lexeme: str, cast: Any) -> Any:
    """Refuse a numeric lexeme that is not the canonical form of its value.

    ADR-024. T11 digests PARSED values, so a number commits to its IEEE-754
    double and not to the characters in the file: a genuine `5e+18` rewritten
    to `5000000000000000511.0` left the validator green and the root unchanged,
    while `jq`, `git diff` and a human read a different number. A genuine `0.0`
    accepted `1e-400` on the same terms.

    The check is a PRECONDITION ON LOADING and not a change to hashing.
    `_canonical_bytes` already serializes through `json.dumps`, so a document
    we produced already carries canonical lexemes; the defect was that a
    VERIFIER re-serializes what it parses, and re-serialization launders the
    difference. Every genuine root is unchanged by this rule.

    Hooked into the parser rather than matched with a regular expression, and
    that is not a style preference: `parse_float` and `parse_int` receive the
    lexeme and are never called for a number inside a STRING. A regex over the
    raw text flags `{"note": "cost 5.10 eur"}`, which is not a number at all.
    """
    value = cast(lexeme)
    if json.dumps(value) != lexeme:
        raise NonCanonicalNumberError(
            f"the number {lexeme} is not written the way this contract writes "
            f"the value it denotes, which is {json.dumps(value)}. Two documents "
            "that differ here are two documents, and the digest is taken over "
            "parsed values -- so accepting this lexeme would let it share a "
            "root with the genuine one (ADR-024, rule J2)")
    return value


def _loads_strict(text: str, *, governed_as: str | None = None) -> Any:
    """``json.loads`` that refuses NaN/Infinity/-Infinity and repeated member
    names (RFC 8259, J1), strings that are not Unicode text (J1, ADR-026), and
    non-canonical numeric lexemes (ADR-024, J2).

    **Which escape form a string was written in is NOT checked, and that is a
    decision rather than a hole** — ADR-026. `"appro\u0076ed"` and
    `"approved"` are the same JSON string, every conforming reader gets the
    same value from both, and refusing one would be a false accusation against
    a document identical in meaning to one we accept. A rule for escape forms
    (`J3`) was written, accepted and withdrawn; ADR-024's residual and #98 are
    closed by ADR-026, not still open.

    What IS refused is a string that denotes no text at all. That check reads
    the parsed value and never the lexeme, so it costs a walk and not a
    scanner — see `_refuse_unpaired_surrogates`.

    Numbers need no such machinery either: the C scanner honours `parse_float`
    and `parse_int`, so J2 costs a hook.
    """
    try:
        document = json.loads(
            text, parse_constant=_refuse_non_finite,
            object_pairs_hook=_refuse_duplicate_keys,
            parse_float=lambda lexeme: _canonical_number(lexeme, float),
            parse_int=lambda lexeme: _canonical_number(lexeme, int),
        )
    except NonCanonicalNumberError:
        # J2 is scoped by the document's own version, and the version is INSIDE
        # the document — so the obvious order is parse-then-maybe-parse-again,
        # which is what this did and what cost every governed document a second
        # full parse. Inverted: the hooks run on the first pass, and the only
        # documents that pay for a second one are the ones a hook already
        # refused. A round measured the obvious order at +110% on
        # `Trace.from_json`, against a claimed +16%.
        document = json.loads(
            text, parse_constant=_refuse_non_finite,
            object_pairs_hook=_refuse_duplicate_keys,
        )
        if _governing_version(document, governed_as) in _LEXICALLY_GOVERNED:
            raise
    # `governed_as` is for a FRAGMENT of a document that declares its version
    # elsewhere. A JSONL record line is `{"seq":..,"kind":..}` with no
    # `schema_version` in it, so every version-scoped rule silently switched
    # itself off for every record in the stream: the exact tamper ADR-024
    # exists to stop -- a store rewriting `1e-06` as `0.000001`, which is
    # `contract/README.md`'s own worked example -- was refused as JSON and
    # accepted as JSONL, with the reassembled document earning the genuine
    # root. The two encodings are declared equivalent by the same README that
    # says hashes are over the object form and never over the bytes of a
    # particular encoding.
    if _governing_version(document, governed_as) in _SCALAR_GOVERNED:
        _refuse_unpaired_surrogates(document)
    return document


#: The trace schema versions at which a genuine document could not carry an
#: unpaired surrogate, measured against the shipped releases rather than
#: reasoned about (ADR-026 decision 4):
#:
#:     v0.5.0  schema 1.0.0  wrote the trace, and its own validator said valid
#:     v0.6.1  schema 1.1.0  wrote the trace
#:     v0.7.0  schema 1.1.0  wrote the trace
#:     v0.8.1  schema 2.0.0  raised at the seal; no trace exists
#:
#: 2.0.0 introduced the per-record digest, and `_canonical_bytes` has refused a
#: string with no UTF-8 encoding ever since — incidentally at first, and by
#: name since 0.11.0. So from 2.0.0 no genuine document can carry one, and
#: below it some can.
#:
#: **The first draft of ADR-026 asserted the opposite** — that `J2`'s version
#: scoping "does not apply here and must not be copied", because `J1` had meant
#: strict RFC 8259 at every version. That is true of the prose and false of the
#: product: a v0.5.0 trace holding `report\udcff.csv`, the exact string
#: `surrogateescape` produces from filesystem byte 0xFF, is declared VALID by
#: the validator of its own release and by this one on `main`. Refusing it now
#: would be a breaking change to a contract surface without a major version,
#: which `guarantees.md` §7 forbids and which `contract/README.md` calls the
#: worst answer a verifier gives: *a refusal outranks a violation.*
#: What a document is governed by when nothing declares a version: today's
#: rules. Failing closed here is the safe direction — a document with no older
#: TRACE contract to appeal to gets the current one — and it is why this is a
#: sentinel rather than `None` meaning "ungoverned".
_UNDECLARED = "__undeclared__"


_SCALAR_GOVERNED = frozenset({"2.0.0", "3.0.0", "3.1.0", "3.2.0", _UNDECLARED})

#: Trace schema versions at which Transition.violations may be null (ADR-029).
_VIOLATIONS_NULL_ADMITTED = frozenset({"3.1.0", "3.2.0"})


def _looks_like_a_trace(document: Any) -> bool:
    """Is this document's `schema_version` a TRACE schema version?

    **Two version namespaces share one key name.** A GraphSpec declares
    `"schema_version": "1.0.0"` meaning graphspec schema 1.0.0, and a version
    scope written for traces read that as "trace schema 1.0.0, before this rule
    existed" and switched itself off — so a spec carrying an unpaired surrogate
    or a non-canonical lexeme sailed through both rules. Found immediately
    after the scoping landed, by a probe that had been checking something else.

    The discriminator is `run`: a whole trace has it and so does a JSONL
    header line, which is the same document's first line and must be read under
    the same rules. A GraphSpec, a receipt, a commitment and a bare fragment do
    not. Everything that is not a trace is governed — failing closed here costs
    a refusal on a document that has no older TRACE contract to appeal to, and
    `test_escaping_j2s_scope_costs_the_attacker_the_root` is why that is safe
    in the other direction: relabelling a 3.0.0 trace to escape a scoped rule
    also escapes the root, since `derived_root` derives nothing below 3.0.0.
    """
    return isinstance(document, dict) and "run" in document


def _governing_version(document: Any, governed_as: str | None = None) -> str:
    """Which trace schema version's rules apply to this document.

    Three cases, and they were three tangled conditions before a JSONL test
    caught them disagreeing:

    - a trace (or a JSONL header) declares its own version, and that decides;
    - a JSONL RECORD line declares nothing, so the stream's header decides —
      `governed_as` carries it down;
    - anything else — a GraphSpec, a receipt, a bare fragment — is governed by
      today's rules, because it has no older trace contract to appeal to.
    """
    if _looks_like_a_trace(document):
        declared = document.get("schema_version")
        return declared if isinstance(declared, str) else _UNDECLARED
    if governed_as is not None:
        return governed_as
    return _UNDECLARED


def _scalar_governed(document: Any) -> bool:
    """Does the unpaired-surrogate refusal apply to this document?

    Scoped by the document's OWN declared version, exactly as `J2` is, and for
    the same reason stated one function down. The producer is NOT scoped: it
    writes 3.0.0 and refuses always, which is where the format is defined.
    """
    return _governing_version(document) in _SCALAR_GOVERNED


#: The trace schema versions at which the terminal digest became an anchorable
#: commitment to the run (ADR-019). Below them there is no root for a lexical
#: collision to attack, so J2 has nothing to protect and refusing an older
#: document would break `contract/README.md`'s promise that old evidence stays
#: valid without rewriting.
_LEXICALLY_GOVERNED = frozenset({"3.0.0", "3.1.0", "3.2.0", _UNDECLARED})


#: Trace schema versions at which rule J4 (ADR-030) governs: every number in
#: the trace is a JSON integer with |n| <= 2^53 - 1. An allow-list like the
#: ones above; a future version that keeps the rule adds itself here, and one
#: that does not removes itself and says so in its ADR.
#:
#: No `_UNDECLARED` here, unlike the other allow-lists above: `_j4_violations`
#: is only ever reached through `_trace_semantics`, which `validate_trace`/
#: `validate_jsonl` call only AFTER the document has already passed full JSON
#: Schema validation — and `schema_version` is a required enum-constrained
#: string there, so a trace with no declared (or non-string) version never
#: survives to this check. Including it was dead configuration a mutant could
#: flip without any test noticing.
_INTEGER_ONLY_GOVERNED = frozenset({"3.2.0"})


def _lexically_governed(document: Any) -> bool:
    """Does J2 apply to this document?

    Scoped by the document's OWN declared version rather than applied
    unconditionally. A 1.x or 2.x trace, or one a third party's serializer
    formatted differently, was written under rules that did not include J2, and
    refusing it now is a breaking change to a contract surface without a major
    version — which `contract/README.md` §7 forbids.

    Measured before choosing the scope: the whole repository corpus, 189 JSON
    documents including the frozen production goldens, contains **zero**
    non-canonical numeric lexemes. So this scope costs nothing today and is
    about what a rule may do to evidence written before it existed.
    """
    return _governing_version(document) in _LEXICALLY_GOVERNED


def _j1_violations(instance: Any, prefix: str = "$") -> tuple[list[Violation], bool]:
    """J1 for already-parsed documents — the full recursive check.

    An API caller hands this module Python objects, not text, so the parser's
    ``parse_constant`` fence never saw them: a ``float('nan')``, a tuple, a
    set, a ``{1: ...}`` mapping may be sitting where no strict RFC 8259 text
    could have produced one.  Walk the document at every depth and flag (rule
    J1): non-finite floats, non-string mapping keys, tuples, and any object
    that is not dict/list/str/int/float/bool/None.

    Returns ``(violations, structural)``: ``structural`` is True when any
    finding is a non-JSON SHAPE (non-finite floats alone are structurally
    valid numbers).  A structurally non-JSON document cannot be meaningfully
    schema-validated, so callers report J1 and stop.
    """
    out: list[Violation] = []
    structural = False
    # Read once, at the top, from the whole document: the walk below descends
    # into fragments that declare nothing, and a rule scoped by a document's
    # own version must not be re-decided at every depth.
    scalars_governed = _scalar_governed(instance)
    stack: list[tuple[Any, str]] = [(instance, prefix)]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            # A `str` was waved through here, and a lone surrogate IS a `str`:
            # it passed every check and then raised UnicodeEncodeError out of
            # `canonical_json`, mid-fingerprint, naming the codec instead of
            # the document (ADR-026). Structural, because a string with no
            # UTF-8 encoding has no canonical form — so no digest, no root,
            # and nothing downstream of here is computable.
            index = _surrogate_at(value) if scalars_governed else None
            if index is not None:
                structural = True
                out.append(
                    Violation("J1", path, _surrogate_complaint(value, index, "here")))
            continue
        if value is None or isinstance(value, (bool, int)):
            continue  # bool before float/int matters not: bool IS int, both fine
        if isinstance(value, float):
            if not math.isfinite(value):
                out.append(
                    Violation(
                        "J1",
                        path,
                        f"value {value!r} is not a finite number; trace values "
                        "are strict RFC 8259 JSON (NaN and Infinity are refused)",
                    )
                )
        elif isinstance(value, tuple):
            # Flagged even though json.dumps would serialize it as an array:
            # a tuple is not a JSON value and does not round-trip (it comes
            # back a list) — the API boundary refuses what text cannot carry.
            structural = True
            out.append(
                Violation(
                    "J1",
                    path,
                    "tuple is not a JSON value (arrays are lists); "
                    "strict RFC 8259 values only (rule J1)",
                )
            )
            for index, item in enumerate(value):
                stack.append((item, f"{path}[{index}]"))
        elif isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    structural = True
                    out.append(
                        Violation(
                            "J1",
                            path,
                            f"mapping key {key!r} is not a string; "
                            "JSON object keys are strings (rule J1)",
                        )
                    )
                else:
                    index = _surrogate_at(key) if scalars_governed else None
                    if index is not None:
                        structural = True
                        out.append(
                            Violation("J1", path, _surrogate_complaint(
                                key, index, "used as a member name here")))
                        # Do NOT descend. Every path below this point would
                        # embed the member name, and a finding whose `path`
                        # carries an unpaired surrogate cannot be printed,
                        # serialised or logged -- `validate.main` raised
                        # UnicodeEncodeError trying to report it. The document
                        # is structural now, so nothing deeper will be read
                        # anyway; naming the place with a string that cannot be
                        # written down is the crash one step later.
                        continue
                stack.append((item, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                stack.append((item, f"{path}[{index}]"))
        else:
            structural = True
            out.append(
                Violation(
                    "J1",
                    path,
                    f"{type(value).__name__!r} object is not a JSON value; "
                    "strict RFC 8259 values only (rule J1)",
                )
            )
    out.sort(key=lambda violation: violation.path)
    return out, structural


#: The largest integer JavaScript can hold exactly — `Number.MAX_SAFE_INTEGER`.
#: ADR-030's rule J4 admits every integer with |n| <= this, and nothing else.
_MAX_SAFE_INTEGER = 2 ** 53 - 1


def _j4_violations(document: Any, prefix: str = "$") -> list[Violation]:
    """Rule J4 (ADR-030) over the parsed document: integers only, within 2^53.

    From trace schema 3.2.0 every number in a trace is a JSON integer with
    |n| <= 2^53 - 1 — at every position T11 hashes: Fact, Decision and
    Rejection values at any depth, `initial_state`, metadata, routing values
    and `context_draws[].value`. A float is refused even when integral
    (`-14.0`), because `-14` and `-14.0` are the same RFC 8259 number and two
    different canonical texts: a document carrying one has a root only CPython
    can derive, which is the defect ADR-030 exists to close. An integer beyond
    the safe range is refused because a double cannot represent every integer
    there and the implementations stop agreeing.

    A walk of the PARSED value, never a regular expression over the text: a
    pattern would flag `{"note": "cost 5.10 eur"}`, where 5.10 is somebody's
    prose and not a number at all.

    Scoped by the document's OWN declared version — `_trace_semantics` calls
    this only when the version is 3.2.0 or above, so a 0.12.0 trace carrying
    `-14.0` validates exactly as it always did. The rule says nothing below
    3.2.0 because those documents were truthful under their own version.
    """
    out: list[Violation] = []
    stack: list[tuple[Any, str]] = [(document, prefix)]
    while stack:
        value, path = stack.pop()
        if isinstance(value, bool):
            continue  # bool is not a number to JSON; isinstance(True, int) must not accuse it
        if isinstance(value, int):
            # `operator.index`, not `abs`: an `int` SUBCLASS can override
            # `__abs__` to answer whatever it likes, and `json.dumps` would
            # still write its true magnitude — the C encoder reads the
            # interpreter's own PyLong bits, not the Python-level method.
            # `operator.index` reads those same bits, so the bound check
            # and the bytes on disk always agree about what is being written.
            plain = operator.index(value)
            if abs(plain) > _MAX_SAFE_INTEGER:
                out.append(Violation(
                    "J4", path,
                    f"the integer {plain} at {path} is beyond 2^53 - 1 "
                    f"({_MAX_SAFE_INTEGER}), the largest integer every JSON "
                    "implementation in use writes identically. Record it as a "
                    "string if it is an identifier, or carry it as an integer "
                    "at a declared scale (rule J4, ADR-030)."))
            continue
        if isinstance(value, float):
            out.append(Violation(
                "J4", path,
                f"the number {value!r} at {path} is not a JSON integer with "
                f"|n| <= 2^53 - 1 ({_MAX_SAFE_INTEGER}). From trace schema "
                "3.2.0 every number in a trace is an integer; a quantity that "
                "is not an integer is carried at a declared scale (basis "
                "points, milliseconds, whole units) or as a string the "
                "producer owns (rule J4, ADR-030)."))
        elif isinstance(value, dict):
            for key, item in value.items():
                stack.append((item, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                stack.append((item, f"{path}[{index}]"))
    out.sort(key=lambda violation: violation.path)
    return out


# --------------------------------------------------------------------------- #
# Canonical form and fingerprints (contract/README.md §"Fingerprints")        #
# --------------------------------------------------------------------------- #


def canonical_json(obj: Any) -> bytes:
    """The canonical JSON encoding fingerprints are computed over.

    Per contract/README.md: UTF-8, keys sorted lexicographically at every
    depth, no insignificant whitespace, strict RFC 8259.  Hashes are computed
    over this canonical object form, never over the bytes of a particular
    encoding (JSON vs JSONL).
    """
    try:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            # `json.dumps` has exactly two ways to emit something that is not
            # JSON. The surrogate arm is handled below; this is the other one,
            # and it was open: `canonical_json` produced b'{"a":NaN}' -- bytes
            # its OWN strict reader refuses and `trace._canonical_bytes` (which
            # has always passed this keyword) will not produce. A fingerprint
            # over non-JSON is a digest of something that is not a document.
            allow_nan=False,
        ).encode("utf-8")
    # UnicodeEncodeError IS a ValueError, so this clause has to come first --
    # the first version put the non-finite clause above it, re-raised the codec
    # error bare, and undid the surrogate repair in the same function it was
    # made in. Caught by the branch's own frontier test.
    except UnicodeEncodeError as exc:
        # The first version of this comment said "reached only by a caller who
        # computes a fingerprint without validating first — `validate_trace`
        # stops at the structural J1". A round measured it false:
        # `validate_trace(doc, spec)` was that caller, because the SPEC
        # argument was never J1-checked. It is now, so this is again the
        # fingerprint-without-validating path — and the comment is written
        # differently, because "unreachable" is a claim and this one was wrong.
        raise UnpairedSurrogateError(
            f"this object cannot be encoded as JSON text: {exc}. A JSON string "
            "denotes a sequence of Unicode scalar values, so an object holding "
            "an unpaired surrogate has no canonical form and therefore no "
            "fingerprint (rule J1, ADR-026)") from None
    except ValueError as exc:
        raise NonFiniteJSONError(
            f"this object cannot be encoded as JSON text: {exc}. NaN and "
            "Infinity are not RFC 8259 values, so an object holding one has no "
            "canonical form and therefore no fingerprint (rule J1)") from None


def fingerprint(kind: str, obj: Any) -> str:
    """Kind-prefixed SHA-256 over ``canonical_json(obj)``, e.g. ``graph:sha256:...``.

    ``graph_fingerprint`` is this function over the ENTIRE GraphSpec document
    as validated (no materialized defaults); SB2 recomputes it — every fixture
    fingerprint is TRUE, never decorative.
    """
    return f"{kind}:sha256:{hashlib.sha256(canonical_json(obj)).hexdigest()}"
def system_manifest_fingerprint(document: Any) -> str:
    """ADR-035 manifest identity over the complete canonical document.

    The digest is deliberately not embedded in the manifest. A caller validates
    the document, then derives this value from the exact object it is holding.
    """
    return "sha256:" + hashlib.sha256(canonical_json(document)).hexdigest()



# --------------------------------------------------------------------------- #
# Schema loading and JSON Schema validation                                   #
# --------------------------------------------------------------------------- #

_LOADED: dict[str, Any] = {}


def _load(name: str) -> dict:
    if name not in _LOADED:
        with open(_CONTRACT_DIR / name, encoding="utf-8") as fh:
            _LOADED[name] = json.load(fh, parse_constant=_refuse_non_finite)
    return _LOADED[name]


def load_graphspec_schema() -> dict:
    """The GraphSpec v1 schema, loaded relative to this file."""
    return _load(_GRAPHSPEC_SCHEMA_FILE)


def load_trace_schema() -> dict:
    """The trace v1 schema, loaded relative to this file."""
    return _load(_TRACE_SCHEMA_FILE)


_VALIDATORS: dict[str, Draft202012Validator] = {}
_COMMITMENT_REGISTRY: Registry | None = None
_SYSTEM_MANIFEST_REGISTRY: Registry | None = None


def _validator(key: str, schema: dict) -> Draft202012Validator:
    if key not in _VALIDATORS:
        _VALIDATORS[key] = Draft202012Validator(schema, format_checker=FormatChecker())
    return _VALIDATORS[key]


def _graphspec_validator() -> Draft202012Validator:
    return _validator("graphspec", load_graphspec_schema())


def _trace_validator() -> Draft202012Validator:
    return _validator("trace", load_trace_schema())


def load_commitment_schema() -> dict:
    """The commitment envelope schema, loaded relative to this file."""
    return _load(_COMMITMENT_SCHEMA_FILE)


def load_checkpoint_schema() -> dict:
    """The checkpoint schema, loaded relative to this file."""
    return _load(_CHECKPOINT_SCHEMA_FILE)


def load_receipt_schema() -> dict:
    """The receipt schema, loaded relative to this file."""
    return _load(_RECEIPT_SCHEMA_FILE)


def load_system_manifest_schema() -> dict:
    """The System Manifest v1 schema, loaded relative to this file."""
    return _load(_SYSTEM_MANIFEST_SCHEMA_FILE)



def _commitment_registry() -> Registry:
    """The three commitment-side schemas, resolvable by their own `$id`.

    They reference each other by relative filename, which resolves against the
    `$id` base — a receipt embeds a checkpoint, and both use the commitment
    schema's shared definitions. Duplicating those definitions into each file
    would have avoided this registry and created three places for `Digest` to
    drift apart.
    """
    global _COMMITMENT_REGISTRY
    if _COMMITMENT_REGISTRY is None:
        resources = []
        for name in (_COMMITMENT_SCHEMA_FILE, _CHECKPOINT_SCHEMA_FILE,
                     _RECEIPT_SCHEMA_FILE):
            schema = _load(name)
            resources.append((schema["$id"], Resource.from_contents(schema)))
        _COMMITMENT_REGISTRY = Registry().with_resources(resources)
    return _COMMITMENT_REGISTRY


def _registry_validator(key: str, schema: dict) -> Draft202012Validator:
    if key not in _VALIDATORS:
        _VALIDATORS[key] = Draft202012Validator(
            schema, format_checker=FormatChecker(),
            registry=_commitment_registry(),
        )
    return _VALIDATORS[key]


def _commitment_validator() -> Draft202012Validator:
    return _registry_validator("commitment", load_commitment_schema())


def _checkpoint_validator() -> Draft202012Validator:
    return _registry_validator("checkpoint", load_checkpoint_schema())


def _receipt_validator() -> Draft202012Validator:
    return _registry_validator("receipt", load_receipt_schema())


def _system_manifest_registry() -> Registry:
    """Schemas needed to resolve System Manifest references.

    System Manifest reuses the commitment contract's opaque Identifier and
    canonical sha256 Digest definitions instead of creating a second spelling
    for either fact.
    """
    global _SYSTEM_MANIFEST_REGISTRY
    if _SYSTEM_MANIFEST_REGISTRY is None:
        resources = []
        for name in (_COMMITMENT_SCHEMA_FILE, _SYSTEM_MANIFEST_SCHEMA_FILE):
            schema = _load(name)
            resources.append((schema["$id"], Resource.from_contents(schema)))
        _SYSTEM_MANIFEST_REGISTRY = Registry().with_resources(resources)
    return _SYSTEM_MANIFEST_REGISTRY


def _system_manifest_validator() -> Draft202012Validator:
    if "system-manifest" not in _VALIDATORS:
        _VALIDATORS["system-manifest"] = Draft202012Validator(
            load_system_manifest_schema(),
            format_checker=FormatChecker(),
            registry=_system_manifest_registry(),
        )
    return _VALIDATORS["system-manifest"]



def _pointer_validator(key: str, root: dict, pointer: str) -> Draft202012Validator:
    """A validator for one ``$defs`` entry of ``root`` (e.g. ``#/$defs/Record``).

    The wrapper schema shares the root's ``$defs`` so every internal ``#/...``
    reference keeps resolving; no external registry machinery is needed.
    """
    if key not in _VALIDATORS:
        wrapper = {
            "$schema": root.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
            "$defs": root["$defs"],
            "$ref": pointer,
        }
        _VALIDATORS[key] = Draft202012Validator(wrapper, format_checker=FormatChecker())
    return _VALIDATORS[key]


def _deref(root: dict, sub: Any) -> dict | None:
    """Resolve a local ``#/...`` reference against ``root``; pass dicts through."""
    if not isinstance(sub, dict):
        return None
    ref = sub.get("$ref")
    if ref is None:
        return sub
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node: Any = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _branch_for_kind(root: dict, oneof_schema: Any, kind: str) -> int | None:
    """Index of the single ``oneOf`` branch whose ``kind`` const matches, if any."""
    if not isinstance(oneof_schema, dict):
        return None
    branches = oneof_schema.get("oneOf")
    if not isinstance(branches, list):
        return None
    matches = []
    for i, sub in enumerate(branches):
        resolved = _deref(root, sub)
        if not resolved:
            continue
        kind_schema = resolved.get("properties", {}).get("kind")
        if isinstance(kind_schema, dict) and kind_schema.get("const") == kind:
            matches.append(i)
    return matches[0] if len(matches) == 1 else None


def _best_leaf(root: dict, err: ValidationError) -> ValidationError:
    """Descend into the most informative sub-error.

    The trace schema's ``oneOf`` unions (Record, Value) are discriminated by a
    ``kind`` const; when the failing instance names its kind, the branch it
    meant is known and the deepest error inside THAT branch is the real story.
    Everything else falls back to ``jsonschema.exceptions.best_match``.
    """
    while err.context:
        chosen: ValidationError | None = None
        if err.validator == "oneOf" and isinstance(err.instance, dict):
            kind = err.instance.get("kind")
            if isinstance(kind, str):
                idx = _branch_for_kind(root, err.schema, kind)
                if idx is not None:
                    branch = [
                        c
                        for c in err.context
                        if len(c.schema_path) and c.schema_path[0] == idx
                    ]
                    if branch:
                        chosen = max(
                            branch,
                            key=lambda c: (
                                len(c.absolute_path),
                                tuple(map(str, c.absolute_path)),
                            ),
                        )
        if chosen is None:
            fallback = best_match([err])
            return fallback if fallback is not None else err
        err = chosen
    return err


def _schema_violations(
    root: dict,
    validator: Draft202012Validator,
    instance: Any,
    rule: str = "SCHEMA",
    prefix: str = "$",
    msg_prefix: str = "",
) -> list[Violation]:
    out = []
    for err in validator.iter_errors(instance):
        leaf = _best_leaf(root, err)
        path = leaf.json_path
        if prefix != "$":
            path = prefix + path[1:]
        out.append(Violation(rule, path, msg_prefix + leaf.message))
    out.sort(key=lambda v: v.path)
    return out


# --------------------------------------------------------------------------- #
# GraphSpec semantics — the R-rules                                           #
# --------------------------------------------------------------------------- #

# PEP 440 public version, with optional epoch / pre / post / dev / local parts.
_PEP440_VERSION = (
    r"v?"
    r"(?:[0-9]+!)?"                                                # epoch
    r"(?P<release>[0-9]+(?:\.[0-9]+)*)"                            # release
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?"  # pre
    r"(?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?"               # post
    r"(?:[-_.]?dev[-_.]?[0-9]*)?"                                  # dev
    r"(?P<local>\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?"                  # local
)
# One specifier clause: operator, then a PEP 440 version, optionally the
# ``.*`` wildcard suffix (legal only with == and !=).  Alternation order makes
# the three-char and two-char operators win over their prefixes.
_SPECIFIER_CLAUSE = re.compile(
    r"^\s*(?P<op>===|==|!=|<=|>=|~=|<|>)\s*"
    r"(?P<version>" + _PEP440_VERSION + r")"
    r"(?P<wildcard>\.\*)?\s*$",
    re.IGNORECASE,
)
# PEP 440 arbitrary equality: after ``===`` ANY non-empty version string is
# legal — it is compared as an opaque string, so there is nothing further to
# parse (whitespace inside it is still refused: no resolver tokenizes past it).
_ARBITRARY_EQUALITY_CLAUSE = re.compile(r"^\s*===\s*\S+\s*$")


def _r12_violations(value: str) -> list[Violation]:
    """R12: ``requires_motus`` must parse as a PEP 440 specifier set."""
    path = "$.requires_motus"
    out = []
    for clause in value.split(","):
        if not clause.strip():
            # DELIBERATE fail-closed choice (documented per the graphspec R12
            # description): resolvers such as pip/packaging silently drop
            # empty clauses — ``'>=1.0,'`` is accepted there.  This fence
            # rejects them: an empty clause is evidence of a truncated or
            # hand-mangled specifier, and the fence may reject edge forms a
            # resolver would tolerate.
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus {value!r} contains an empty clause; "
                    "clauses are comma-separated PEP 440 specifiers",
                )
            )
            continue
        if _ARBITRARY_EQUALITY_CLAUSE.match(clause):
            continue
        m = _SPECIFIER_CLAUSE.match(clause)
        if m is None:
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus clause '{clause.strip()}' is not a valid "
                    "PEP 440 specifier clause "
                    "(operator ==, !=, <=, >=, <, >, ~= or === followed by a version)",
                )
            )
            continue
        op = m.group("op")
        if m.group("wildcard") and op not in ("==", "!="):
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus clause '{clause.strip()}': the '.*' wildcard "
                    "suffix is only valid with == or != (PEP 440)",
                )
            )
        elif m.group("wildcard") and m.group("local"):
            # PEP 440 (and packaging) reject the wildcard on top of a local
            # version label: '==1.0+abc.*' and '!=1.0+abc.*' are invalid.
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus clause '{clause.strip()}': the '.*' wildcard "
                    "suffix cannot combine with a local version label (PEP 440)",
                )
            )
        if m.group("local") and op not in ("==", "!=", "==="):
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus clause '{clause.strip()}': a local version "
                    "label is only valid with ==, != or === (PEP 440)",
                )
            )
        if op == "~=" and "." not in m.group("release"):
            out.append(
                Violation(
                    "R12",
                    path,
                    f"requires_motus clause '{clause.strip()}': the compatible-"
                    "release operator ~= requires at least two release segments "
                    "(PEP 440)",
                )
            )
    return out


def _step_targets(name: str, step: dict) -> list[tuple[str, str]]:
    """(target, json-path) pairs of one transition step.  Terminal has none."""
    kind = step.get("kind")
    targets: list[tuple[str, str]] = []
    if kind == "next":
        targets.append((step["to"], f"$.transitions.{name}.to"))
    elif kind == "route":
        for key, tgt in step.get("map", {}).items():
            targets.append((tgt, f"$.transitions.{name}.map.{key}"))
        if "default" in step:
            targets.append((step["default"], f"$.transitions.{name}.default"))
    return targets


def _has_cycle(nodes: set[str], edges: dict[str, list[str]]) -> bool:
    """Back-edge detection over the transition graph restricted to declared nodes."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(nodes, WHITE)
    for start in nodes:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack: list[tuple[str, Iterable[str]]] = [(start, iter(edges.get(start, ())))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:  # END or an undeclared target (R2's business)
                    continue
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(edges.get(nxt, ()))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
    return False


def validate_graphspec(spec: dict) -> list[Violation]:
    """Validate a GraphSpec document: JSON Schema first, then the R-rules.

    Flow analyses (R3 reachability, R11 termination/cycles) run only when the
    structural rules (R1, R2, R4, R5, R8) all hold — analysing flow over a
    structurally broken graph would only restate the structural break.
    """
    schema = load_graphspec_schema()
    schema_violations = _schema_violations(schema, _graphspec_validator(), spec)
    if schema_violations:
        return schema_violations

    # J1 — an already-parsed document may carry non-finite floats that no
    # strict RFC 8259 text could have produced.  (Structurally non-JSON
    # objects never reach this line: the schema pass above rejects them.)
    j1_violations, _structural = _j1_violations(spec)
    v: list[Violation] = list(j1_violations)
    nodes = spec["nodes"]
    names = [n["name"] for n in nodes]
    declared = set(names)
    transitions = spec["transitions"]
    entry = spec["entry"]

    # R5 — node names are unique.
    for name, count in Counter(names).items():
        if count > 1:
            v.append(
                Violation(
                    "R5",
                    "$.nodes",
                    f"node name '{name}' is declared {count} times; "
                    "node names must be unique",
                )
            )

    # R8 — 'END' is the reserved terminal pseudo-target, never a node name.
    for i, node in enumerate(nodes):
        if node["name"] == "END":
            v.append(
                Violation(
                    "R8",
                    f"$.nodes[{i}].name",
                    "'END' is reserved for the terminal pseudo-target "
                    "and may not be a node name",
                )
            )

    # R1 — entry names a declared node.
    if entry not in declared:
        v.append(Violation("R1", "$.entry", f"entry '{entry}' is not a declared node"))

    # R4 — exactly one transition entry per declared node, no extra keys.
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name not in transitions:
            v.append(
                Violation(
                    "R4",
                    "$.transitions",
                    f"declared node '{name}' has no transition entry",
                )
            )
    for key in transitions:
        if key not in declared:
            v.append(
                Violation(
                    "R4",
                    f"$.transitions.{key}",
                    f"transition key '{key}' does not name a declared node",
                )
            )

    # R2 — every transition target names a declared node or END.
    edges: dict[str, list[str]] = {}
    for key, step in transitions.items():
        targets = _step_targets(key, step)
        for target, path in targets:
            if target != "END" and target not in declared:
                v.append(
                    Violation(
                        "R2", path, f"target '{target}' is not a declared node or END"
                    )
                )
        edges[key] = [t for t, _ in targets]

    # R12 — requires_motus parses as a PEP 440 specifier set (shape-independent).
    if "requires_motus" in spec:
        v.extend(_r12_violations(spec["requires_motus"]))

    if any(x.rule in {"R1", "R2", "R4", "R5", "R8"} for x in v):
        return v

    # R3 — every declared node is reachable from entry.
    reachable = {entry}
    stack = [entry]
    while stack:
        current = stack.pop()
        for target in edges.get(current, ()):
            if target in declared and target not in reachable:
                reachable.add(target)
                stack.append(target)
    for i, node in enumerate(nodes):
        if node["name"] not in reachable:
            v.append(
                Violation(
                    "R3",
                    f"$.nodes[{i}]",
                    f"node '{node['name']}' is not reachable from entry '{entry}'",
                )
            )

    # R11(a) — cycles are legal, trap regions are not: every declared node must
    # be able to reach a terminal step (kind terminal, or an edge to END).
    exit_capable = {
        name
        for name in declared
        if transitions[name].get("kind") == "terminal" or "END" in edges.get(name, ())
    }
    reverse: dict[str, set[str]] = {name: set() for name in declared}
    for source, targets in edges.items():
        for target in targets:
            if target in reverse:
                reverse[target].add(source)
    can_exit = set(exit_capable)
    stack = list(exit_capable)
    while stack:
        current = stack.pop()
        for predecessor in reverse[current]:
            if predecessor not in can_exit:
                can_exit.add(predecessor)
                stack.append(predecessor)
    for i, node in enumerate(nodes):
        if node["name"] not in can_exit:
            v.append(
                Violation(
                    "R11",
                    f"$.nodes[{i}]",
                    f"node '{node['name']}' cannot reach a terminal step — "
                    "an inescapable trap region",
                )
            )

    # R11(b) — a cyclic spec MUST declare max_transitions (the safety valve).
    if "max_transitions" not in spec and _has_cycle(declared, edges):
        v.append(
            Violation(
                "R11",
                "$.max_transitions",
                "spec contains a cycle but does not declare max_transitions; "
                "R11 requires the safety limit for any cyclic spec",
            )
        )

    return v


# --------------------------------------------------------------------------- #
# System Manifest semantics — ADR-035 rules SM1-SM3                           #
# --------------------------------------------------------------------------- #


def validate_system_manifest(document: dict) -> list[Violation]:
    """Validate one System Manifest declaration.

    This validates the document only. It does NOT verify that a declared
    runtime, graph fingerprint, code fingerprint, policy or control was
    actually present or effective in a deployment. ADR-035 makes binding
    verification a separate operation so schema validity can never be mistaken
    for operational evidence.
    """
    # J1 must run before JSON Schema for the in-process API path. A tuple,
    # set, non-string mapping key or arbitrary Python object is not a JSON
    # value at all; reporting it as merely SCHEMA would give this surface a
    # weaker strict-RFC-8259 boundary than the other contract entry points.
    violations, structural = _j1_violations(document)
    if structural:
        return violations

    schema = load_system_manifest_schema()
    violations += _schema_violations(
        schema, _system_manifest_validator(), document
    )
    if violations:
        return violations

    # SM1 — identifiers that name one member of a manifest namespace are unique.
    graphs = document["bindings"]["graphs"]
    seen_graphs: set[tuple[str, str]] = set()
    for index, graph in enumerate(graphs):
        key = (graph["name"], graph["version"])
        if key in seen_graphs:
            violations.append(Violation(
                "SM1",
                f"$.bindings.graphs[{index}]",
                f"graph identity {key[0]!r} version {key[1]!r} is declared "
                "more than once; one manifest cannot bind one logical graph "
                "identity to multiple entries",
            ))
        seen_graphs.add(key)

    declarations = document["declarations"]
    namespaces = (
        ("components", "component_id"),
        ("policies", "policy_id"),
        ("controls", "control_id"),
    )
    for collection, field in namespaces:
        seen: set[str] = set()
        for index, item in enumerate(declarations.get(collection) or []):
            identifier = item[field]
            if identifier in seen:
                violations.append(Violation(
                    "SM1",
                    f"$.declarations.{collection}[{index}].{field}",
                    f"{field} {identifier!r} is declared more than once; "
                    "identifiers are unique within one manifest namespace",
                ))
            seen.add(identifier)

    # SM2 — policy_ref is a local machine-checkable edge. External policy
    # documents belong in `reference`; a local id must resolve locally.
    policy_ids = {
        item["policy_id"] for item in declarations.get("policies") or []
    }
    for index, control in enumerate(declarations.get("controls") or []):
        policy_ref = control.get("policy_ref")
        if policy_ref is not None and policy_ref not in policy_ids:
            violations.append(Violation(
                "SM2",
                f"$.declarations.controls[{index}].policy_ref",
                f"control names policy_ref {policy_ref!r}, but no policy with "
                "that policy_id is declared in this manifest",
            ))

    # SM3 — shape is schema work; calendar truth needs date arithmetic.
    if not _calendar_valid_utc(document["created_at"]):
        violations.append(Violation(
            "SM3",
            "$.created_at",
            f"{document['created_at']!r} has the RFC 3339 UTC shape but is not "
            "a calendar-valid UTC instant",
        ))

    return violations


# --------------------------------------------------------------------------- #
# Trace semantics — the T-rules and H-rules                                   #
# --------------------------------------------------------------------------- #


def _json_equal(a, b) -> bool:
    """JSON equality, not Python equality.

    Python says ``True == 1`` and ``1 == 1.0``; JSON does not consider a boolean
    and a number the same value.  A causal edge compared with raw ``==`` would
    accept a decision holding ``true`` as proof of a routing that observed ``1``
    (cross-review v4).  Types are compared first, then structure.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return False
    return a == b


def _calendar_valid_utc(value: str) -> bool:
    """Is ``value`` a calendar-valid RFC 3339 UTC instant (Z form)?

    Trace T9 and System Manifest SM3 share this calendar arithmetic. The
    schema's regex pins the shape but admits month 13 and hour 99;
    ``datetime.strptime`` supplies the calendar arithmetic without any
    third-party date parser (``%f`` accepts the schema's 1–6 fraction digits).
    """
    body = value[:-1] if value.endswith("Z") else value
    layout = "%Y-%m-%dT%H:%M:%S.%f" if "." in body else "%Y-%m-%dT%H:%M:%S"
    try:
        datetime.strptime(body, layout)
    except ValueError:
        return False
    return True


def _declared_nodes(spec: dict | None) -> set[str] | None:
    if spec is None:
        return None
    return {
        n.get("name")
        for n in (spec.get("nodes") or [])
        if isinstance(n, dict) and isinstance(n.get("name"), str)
    }


def _expected_candidates(step: dict) -> Counter:
    """The complete candidate multiset a routing record must show for a step."""
    kind = step.get("kind")
    if kind == "route":
        expected = Counter(
            ("map", key, target) for key, target in step.get("map", {}).items()
        )
        if "default" in step:
            expected[("default", None, step["default"])] += 1
        return expected
    if kind == "next":
        return Counter({("static", None, step["to"]): 1})
    if kind == "terminal":
        return Counter({("static", None, "END"): 1})
    return Counter()


def _format_candidates(entries: Counter) -> str:
    parts = []
    for (kind, key, target), count in sorted(
        entries.items(), key=lambda item: (str(item[0][0]), str(item[0][1]), str(item[0][2]))
    ):
        if kind == "map":
            label = f"map[{key!r}] -> {target!r}"
        elif kind == "default":
            label = f"default -> {target!r}"
        else:
            label = f"static -> {target!r}"
        parts.append(label if count == 1 else f"{label} (x{count})")
    return ", ".join(parts)


_SYSTEM_FAILURE_CAUSES = frozenset(
    {"sink_failure", "validation_failure", "runner_internal"}
)

# A finding under any of these rules means the record stream is not a coherent
# lifecycle; the E-rules (a state machine over that lifecycle) are skipped
# rather than allowed to cascade on already-reported incoherence.
_E_GATE_RULES = frozenset({"T1", "T2", "T3", "T3/INCOMPLETE", "T5", "T6", "T7"})


def _is_cancel_or_system_failure(record: dict) -> bool:
    """The E-rules' 'cancel/system-failure' escape: a run may be cancelled or
    die of a system cause (sink/validation/runner) at ANY point of the state
    machine — those terminals are legal successors everywhere the schema
    description says 'or cancel/system-failure'.  (run_cancelled's own
    coherence is T6's business, not re-checked here.)"""
    kind = record.get("kind")
    if kind == "run_cancelled":
        return True
    return (
        kind == "run_failed"
        and (record.get("cause") or {}).get("kind") in _SYSTEM_FAILURE_CAUSES
    )


def _execution_violations(
    records: list[dict], run: dict, spec: dict | None
) -> list[Violation]:
    """The E-rules (E1–E11): the trace as a GRAPH EXECUTION.

    Pairwise successor checks over a lifecycle-clean stream.  Each violation
    reports the FIRST offending record's path.  Division of labor kept
    deliberate so every defect fires exactly one rule:

    *   E1/E3/E7 check the successor's KIND and NODE; the attempt NUMBER at
        every activation is E10's single concern (1 per routing-driven
        activation — and at E1 for entry — incremented only by E3 retries).
    *   E6 checks that the successor IS run_cancelled; the active_attempt-null
        half is T6's converse arm, already enforced.
    *   E11 (failure-cause admissibility) skips records already implicated by
        a successor rule, so a misplaced terminal fires the rule that names
        the broken edge, not two rules for one defect.
    """
    v: list[Violation] = []
    implicated: set[int] = set()
    policy = run.get("policy")
    resume = run.get("resume") if isinstance(run.get("resume"), dict) else None
    entry = (
        resume.get("start_node")
        if resume is not None
        else (spec.get("entry") if isinstance(spec, dict) else None)
    )

    def flag(index: int, rule: str, message: str, path: str | None = None) -> None:
        implicated.add(index)
        v.append(Violation(rule, path or f"$.records[{index}]", message))

    for i, record in enumerate(records):
        kind = record.get("kind")
        successor = records[i + 1] if i + 1 < len(records) else None
        if successor is None:
            # EOF: a complete trace already ended in a terminal record (T3);
            # an incomplete one is allowed to stop mid-machine — truncation
            # is evidence, not malformation.
            continue
        skind = successor.get("kind")

        if kind == "run_started":
            # E1 — entry executes first, or the run is cancelled / dies of a
            # system cause before it: a run never completes without entry.
            if skind == "attempt_started":
                if entry is not None and successor.get("node") != entry:
                    flag(
                        i + 1,
                        "E1",
                        f"the first attempt names '{successor.get('node')}', "
                        f"not the entry node '{entry}' — execution starts at "
                        "entry",
                        f"$.records[{i + 1}].node",
                    )
            elif not _is_cancel_or_system_failure(successor):
                flag(
                    i + 1,
                    "E1",
                    f"the record after run_started is '{skind}'; expected "
                    "attempt_started of the entry node, or cancellation/"
                    "system failure — a run never completes without "
                    "executing entry",
                )

        elif kind == "transition":
            node = record.get("node")
            outcome = record.get("outcome")
            disposition = record.get("disposition")
            if outcome == "returned" and disposition == "commit":
                # E2 — a committed transition is followed by its routing.
                if skind == "routing":
                    if successor.get("after") != node:
                        flag(
                            i + 1,
                            "E2",
                            f"routing.after is '{successor.get('after')}' but "
                            f"the committed transition it follows is of "
                            f"'{node}'",
                            f"$.records[{i + 1}].after",
                        )
                elif not _is_cancel_or_system_failure(successor):
                    flag(
                        i + 1,
                        "E2",
                        f"after the committed transition of '{node}' the next "
                        f"record is '{skind}'; expected routing(after "
                        f"'{node}'), or cancellation/system failure",
                    )
            elif outcome == "raised" and disposition == "retry":
                # E3 — a retry disposition is followed by the retry attempt.
                if skind == "attempt_started":
                    if successor.get("node") != node:
                        flag(
                            i + 1,
                            "E3",
                            f"retry of '{node}' is followed by an attempt of "
                            f"'{successor.get('node')}'; a retry re-attempts "
                            "the same node",
                            f"$.records[{i + 1}].node",
                        )
                elif not _is_cancel_or_system_failure(successor):
                    flag(
                        i + 1,
                        "E3",
                        f"after transition(raised, retry) of '{node}' the next "
                        f"record is '{skind}'; expected attempt_started"
                        f"('{node}', {record.get('attempt')} + 1), or "
                        "cancellation/system failure",
                    )
            elif outcome == "raised" and disposition == "abort":
                # E4 — abort ends the run as node_failure attributed to this
                # very transition.  No escape: abort IS the decision to fail.
                cause = (successor.get("cause") or {}) if skind == "run_failed" else {}
                if not (
                    skind == "run_failed"
                    and cause.get("kind") == "node_failure"
                    and successor.get("failed_node") == node
                    and cause.get("record_seq") == record.get("seq")
                ):
                    flag(
                        i + 1,
                        "E4",
                        f"after transition(raised, abort) of '{node}' the run "
                        f"must fail as run_failed(node_failure, failed_node "
                        f"'{node}', record_seq {record.get('seq')}); "
                        f"found '{skind}'"
                        + (
                            f" with cause {cause.get('kind')!r}, failed_node "
                            f"{successor.get('failed_node')!r}, record_seq "
                            f"{cause.get('record_seq')!r}"
                            if skind == "run_failed"
                            else ""
                        ),
                    )
            elif outcome == "raised" and disposition == "continue":
                # E5 — continue is an exploration-policy privilege, and the
                # run then routes past the failed node.
                if policy != "exploration":
                    v.append(
                        Violation(
                            "E5",
                            f"$.records[{i}]",
                            f"disposition 'continue' requires policy "
                            f"exploration; run.policy is {policy!r}",
                        )
                    )
                if skind == "routing":
                    if successor.get("after") != node:
                        flag(
                            i + 1,
                            "E5",
                            f"routing.after is '{successor.get('after')}' but "
                            f"the continued-past node is '{node}'",
                            f"$.records[{i + 1}].after",
                        )
                elif not _is_cancel_or_system_failure(successor):
                    flag(
                        i + 1,
                        "E5",
                        f"after transition(raised, continue) of '{node}' the "
                        f"next record is '{skind}'; expected routing(after "
                        f"'{node}'), or cancellation/system failure",
                    )
            elif outcome == "cancelled":
                # E6 — a cooperative cancellation closes the run.  The
                # active_attempt-null half is T6's converse arm.
                if skind != "run_cancelled":
                    flag(
                        i + 1,
                        "E6",
                        f"after transition(cancelled, abort) of '{node}' the "
                        f"next record is '{skind}'; expected run_cancelled "
                        "(the attempt closed itself cooperatively)",
                    )

        elif kind == "routing":
            outcome = record.get("outcome")
            selected = record.get("selected")
            if outcome in ("matched", "default", "static"):
                if selected == "END":
                    # E8 — selecting END completes the run.
                    if skind != "run_completed" and not _is_cancel_or_system_failure(
                        successor
                    ):
                        flag(
                            i + 1,
                            "E8",
                            f"routing selected END but the next record is "
                            f"'{skind}'; expected run_completed, or "
                            "cancellation/system failure",
                        )
                else:
                    # E7 — the selected node is attempted next (or the
                    # transition safety limit trips on this very routing).
                    if skind == "attempt_started":
                        if successor.get("node") != selected:
                            flag(
                                i + 1,
                                "E7",
                                f"routing selected '{selected}' but the next "
                                f"attempt_started names "
                                f"'{successor.get('node')}' — routing drives "
                                "the next attempt",
                                f"$.records[{i + 1}].node",
                            )
                    elif (
                        skind == "run_failed"
                        and (successor.get("cause") or {}).get("kind")
                        == "transition_limit_exceeded"
                    ):
                        if (successor.get("cause") or {}).get(
                            "record_seq"
                        ) != record.get("seq"):
                            flag(
                                i + 1,
                                "E7",
                                "run_failed(transition_limit_exceeded) must "
                                f"carry record_seq {record.get('seq')} — the "
                                "routing whose selection the limit refused",
                                f"$.records[{i + 1}].cause.record_seq",
                            )
                    elif not _is_cancel_or_system_failure(successor):
                        flag(
                            i + 1,
                            "E7",
                            f"routing selected '{selected}' but the next "
                            f"record is '{skind}'; expected attempt_started"
                            f"('{selected}', 1), run_failed"
                            "(transition_limit_exceeded), or cancellation/"
                            "system failure",
                        )
            elif outcome == "miss":
                # E9 — a miss fails a strict run and completes an exploration
                # run (with the miss on record).
                if policy == "strict":
                    if (
                        skind == "run_failed"
                        and (successor.get("cause") or {}).get("kind")
                        == "route_miss"
                    ):
                        if (successor.get("cause") or {}).get(
                            "record_seq"
                        ) != record.get("seq"):
                            flag(
                                i + 1,
                                "E9",
                                "run_failed(route_miss) must carry record_seq "
                                f"{record.get('seq')} — the miss routing it "
                                "evidences",
                                f"$.records[{i + 1}].cause.record_seq",
                            )
                    elif not _is_cancel_or_system_failure(successor):
                        flag(
                            i + 1,
                            "E9",
                            f"strict route miss is followed by '{skind}'; "
                            "expected run_failed(route_miss, record_seq "
                            f"{record.get('seq')}), or cancellation/system "
                            "failure",
                        )
                else:  # exploration
                    if skind != "run_completed" and not _is_cancel_or_system_failure(
                        successor
                    ):
                        flag(
                            i + 1,
                            "E9",
                            f"exploration route miss is followed by '{skind}'; "
                            "expected run_completed (the miss is on record), "
                            "or cancellation/system failure",
                        )

    # E10 — attempt numbering: 1 at each routing-driven activation (and at E1
    # for entry), incremented only by E3 retries within the activation.
    for i, record in enumerate(records):
        if record.get("kind") != "attempt_started" or i == 0:
            continue
        predecessor = records[i - 1]
        pkind = predecessor.get("kind")
        expected_attempt: int | None = None
        if pkind in ("run_started", "routing"):
            expected_attempt = 1
        elif (
            pkind == "transition"
            and predecessor.get("outcome") == "raised"
            and predecessor.get("disposition") == "retry"
            and predecessor.get("node") == record.get("node")
            and isinstance(predecessor.get("attempt"), int)
        ):
            expected_attempt = predecessor["attempt"] + 1
        if expected_attempt is not None and record.get("attempt") != expected_attempt:
            v.append(
                Violation(
                    "E10",
                    f"$.records[{i}].attempt",
                    f"attempt number {record.get('attempt')} at this "
                    f"activation of '{record.get('node')}'; expected "
                    f"{expected_attempt} — numbering is 1 per activation, "
                    "incremented only by retries",
                )
            )

    # E11 — admissibility of failure causes.  Records already implicated by a
    # successor rule are skipped: one defect, one rule.
    for i, record in enumerate(records):
        if record.get("kind") != "run_failed" or i in implicated:
            continue
        cause = record.get("cause") or {}
        cause_kind = cause.get("kind")
        predecessor = records[i - 1] if i > 0 else None
        path = f"$.records[{i}].cause"
        if cause_kind == "node_failure":
            if not (
                isinstance(predecessor, dict)
                and predecessor.get("kind") == "transition"
                and predecessor.get("outcome") == "raised"
                and predecessor.get("disposition") == "abort"
            ):
                v.append(
                    Violation(
                        "E11",
                        path,
                        "cause node_failure is admissible only immediately "
                        "after a transition(raised, abort) (E4); no node "
                        "aborted here",
                    )
                )
        elif cause_kind == "route_miss":
            if not (
                isinstance(predecessor, dict)
                and predecessor.get("kind") == "routing"
                and predecessor.get("outcome") == "miss"
                and policy == "strict"
            ):
                v.append(
                    Violation(
                        "E11",
                        path,
                        "cause route_miss is admissible only immediately after "
                        "a miss routing under policy strict (E9)",
                    )
                )
        elif cause_kind == "transition_limit_exceeded":
            if not (
                isinstance(predecessor, dict)
                and predecessor.get("kind") == "routing"
                and predecessor.get("outcome") in ("matched", "default", "static")
                and predecessor.get("selected") != "END"
            ):
                v.append(
                    Violation(
                        "E11",
                        path,
                        "cause transition_limit_exceeded is admissible only "
                        "immediately after a routing that selected a node "
                        "(E7); no selection was refused here",
                    )
                )
            elif spec is not None:
                if "max_transitions" not in spec:
                    v.append(
                        Violation(
                            "E11",
                            path,
                            "cause transition_limit_exceeded but the supplied "
                            "spec declares no max_transitions",
                        )
                    )
                else:
                    routed_activations = sum(
                        1
                        for r in records
                        if r.get("kind") == "transition"
                        and r.get("disposition") in ("commit", "continue")
                    )
                    if routed_activations != spec["max_transitions"]:
                        v.append(
                            Violation(
                                "E11",
                                path,
                                f"cause transition_limit_exceeded but only "
                                f"{routed_activations} routing-producing "
                                f"activation(s) are on record; the terminal is "
                                f"valid only exactly at max_transitions "
                                f"{spec['max_transitions']}",
                            )
                        )
        # sink_failure / validation_failure / runner_internal are system
        # causes, admissible anywhere.

    return v


def document_version_header(doc: dict) -> str:
    """The schema version as the header carries it, for the chain's first link."""
    return doc.get("schema_version")


def _trace_semantics(
    doc: dict, spec: dict | None, expect_complete: bool
) -> list[Violation]:
    v: list[Violation] = []
    records = doc.get("records") or []
    run = doc.get("run") or {}

    # J4 (ADR-030), scoped by the document's OWN declared version as every
    # version-scoped rule in this file is (ADR-019/024/026/029): from trace
    # schema 3.2.0 every number in a trace is a JSON integer with |n| <= 2^53 - 1,
    # and below 3.2.0 the rule says nothing, because those documents were
    # truthful under their own version — `-14.0` from 0.12.0 among them.
    # The walk covers every position T11 hashes. This sits at the semantic
    # layer so the JSON and JSONL encodings of one trace get the same verdict
    # from the one place (the reassembled JSONL document flows through here).
    if _governing_version(doc) in _INTEGER_ONLY_GOVERNED:
        v.extend(_j4_violations(doc))

    # H2 — resume provenance links two distinct immutable trace segments.
    resume = run.get("resume") or {}
    if resume and resume.get("source_run_id") == run.get("run_id"):
        v.append(
            Violation(
                "H2",
                "$.run.resume.source_run_id",
                "a resumed segment must have a new run_id distinct from its source",
            )
        )

    # H1 — the buffered durability profile must disclose its loss window:
    # sink must be present WITH BOTH disclosure keys.  An empty or partial
    # sink object discloses nothing and does not satisfy the rule.
    if run.get("durability_profile") == "buffered":
        if "sink" not in run:
            v.append(
                Violation(
                    "H1",
                    "$.run",
                    "durability_profile 'buffered' requires the run.sink object "
                    "disclosing the loss window (guarantees.md invariant II)",
                )
            )
        else:
            sink = run.get("sink")
            missing = [
                key
                for key in ("flush_interval_ms", "chunk_records")
                if not (isinstance(sink, dict) and key in sink)
            ]
            if missing:
                v.append(
                    Violation(
                        "H1",
                        "$.run.sink",
                        "durability_profile 'buffered' requires sink to "
                        "disclose both flush_interval_ms and chunk_records; "
                        "missing " + ", ".join(missing),
                    )
                )

    # T9 — timestamps must be calendar-valid, not merely well-shaped.
    created_ts = run.get("created_ts")
    if isinstance(created_ts, str) and not _calendar_valid_utc(created_ts):
        v.append(
            Violation(
                "T9",
                "$.run.created_ts",
                f"created_ts '{created_ts}' is not a calendar-valid "
                "RFC 3339 UTC instant",
            )
        )
    for i, record in enumerate(records):
        ts = record.get("ts")
        if isinstance(ts, str) and not _calendar_valid_utc(ts):
            v.append(
                Violation(
                    "T9",
                    f"$.records[{i}].ts",
                    f"ts '{ts}' is not a calendar-valid RFC 3339 UTC instant",
                )
            )

    # T10 — replay capability only ever degrades.  The header declares what the
    # run set out to guarantee; the terminal record reports what survived.  A
    # run may DISCOVER it is less reproducible than it hoped (a node drawing
    # ambient time downgrades it); it can never discover it is more.  Nor may a
    # constraint that justified a downgrade quietly disappear.  Without this the
    # two fields are decorative: cross-review v3 walked a trace from a declared
    # 'none' to a final 'full'.
    _RANK = {"none": 0, "partial": 1, "full": 2}
    declared = run.get("replay") or {}
    declared_cap = declared.get("capability")
    for i, record in enumerate(records):
        if record.get("kind") not in _TERMINAL_KINDS:
            continue
        final = record.get("replay") or {}
        final_cap = final.get("capability")
        if declared_cap in _RANK and final_cap in _RANK:
            if _RANK[final_cap] > _RANK[declared_cap]:
                v.append(
                    Violation(
                        "T10",
                        f"$.records[{i}].replay.capability",
                        f"final replay capability '{final_cap}' is STRONGER "
                        f"than the declared '{declared_cap}' — capability "
                        "degrades over a run, it never improves",
                    )
                )
        lost = [
            c
            for c in (declared.get("constraints") or [])
            if c not in (final.get("constraints") or [])
        ]
        if lost:
            v.append(
                Violation(
                    "T10",
                    f"$.records[{i}].replay.constraints",
                    "declared constraint(s) "
                    + ", ".join(repr(c) for c in lost)
                    + " are absent from the final replay status; a constraint "
                    "that justified a limitation cannot vanish by the end of "
                    "the run",
                )
            )

    # T11 — the integrity chain.  Each record's payload_hash is sha256 over the
    # canonical object form of that record WITHOUT its own integrity block (a
    # hash cannot cover itself), and prev_hash is the preceding record's
    # payload_hash.  The first record has no predecessor and carries null.
    #
    # The SCHEMA VERSION IS THE ACTIVATION INDICATOR, and there is deliberately
    # no second flag: a separate "chained: true" could disagree with the hashes
    # actually present, and a reader would have to decide which to believe.
    # 2.0.0 requires the chain; 1.x forbids it, so an unverified hash cannot
    # ride in an old trace and be mistaken for tamper evidence.
    #
    # Recomputation is the whole point.  A chain that is only checked for
    # SHAPE — non-null, right length — proves nothing at all: an editor who
    # changes a record can recompute the shape trivially.  What they cannot do
    # is recompute the rest of the chain without also holding whatever anchored
    # its root.
    version = doc.get("schema_version")
    if version in ("2.0.0", "3.0.0", "3.1.0", "3.2.0"):
        # The chain starts at the HEADER, so the first record's prev_hash is the
        # header's digest and never null. Without this the header sat outside
        # the root: run_id, policy, metadata and graph.code_fingerprint could all
        # be rewritten, the terminal's payload_hash did not move, and this
        # validator passed the result clean. An anchor over that root proves a
        # sequence of records existed and says nothing about whose run it was.
        expected_prev = "sha256:" + hashlib.sha256(
            canonical_json({
                "schema_version": document_version_header(doc),
                "run": doc.get("run") or {},
            })
        ).hexdigest()
        for i, record in enumerate(records):
            integrity = record.get("integrity") or {}
            actual = integrity.get("payload_hash")
            # The digest covers the record with payload_hash NULLED, not
            # removed: what a hash must not cover is its own value, and a
            # constant null is not one. So the object hashed is a real record.
            #
            # THE RECIPE IS SELECTED BY VERSION (ADR-019). 3.0.0 takes the digest
            # with prev_hash PRESENT, so each one commits to its predecessor and
            # the terminal digest commits to the run. 2.0.0 nulled prev_hash too,
            # which left the links uncovered: an editor could rewrite any record
            # or the header, reseal by this same published recipe, pass this
            # check, and the root did not move. 2.0.0 is not amended — a trace
            # sealed under it is a truthful record and must keep validating —
            # so its recipe survives here, and what it is worth is said below.
            payload = dict(record)
            payload["integrity"] = {
                "payload_hash": None,
                "prev_hash": None if version == "2.0.0" else integrity.get("prev_hash"),
            }
            computed = "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()
            if actual is None:
                v.append(
                    Violation(
                        "T11",
                        f"$.records[{i}].integrity.payload_hash",
                        f"schema {version} requires an integrity chain; this "
                        "record carries null, which is the 1.x shape",
                    )
                )
            elif actual != computed:
                v.append(
                    Violation(
                        "T11",
                        f"$.records[{i}].integrity.payload_hash",
                        f"payload_hash does not match the record: declared "
                        f"{actual}, recomputed {computed}",
                    )
                )
            if integrity.get("prev_hash") != expected_prev:
                v.append(
                    Violation(
                        "T11",
                        f"$.records[{i}].integrity.prev_hash",
                        f"prev_hash is {integrity.get('prev_hash')!r}; the chain "
                        f"requires {expected_prev!r}"
                        + (" (the first record's predecessor is the HEADER; a "
                           "header outside the chain can be rewritten without "
                           "moving the root)" if i == 0 else ""),
                    )
                )
            expected_prev = actual
    elif version in ("1.0.0", "1.1.0"):
        for i, record in enumerate(records):
            integrity = record.get("integrity") or {}
            for field in ("payload_hash", "prev_hash"):
                if integrity.get(field) is not None:
                    v.append(
                        Violation(
                            "T11",
                            f"$.records[{i}].integrity.{field}",
                            f"schema {version} carries no integrity chain, so "
                            f"{field} must be null; a hash here is unverified "
                            "and must not be able to pass for tamper evidence",
                        )
                    )

    # T12 — the interaction fingerprint travels with its salt, or not at all.
    # The digest is meaningless to a verifier without the salt that prefixed its
    # material, and a salt alone describes nothing.  Neither is checkable here:
    # the covered material is deliberately NOT in the trace, which is the whole
    # point of the field — it binds a trace to an archive held elsewhere.  So
    # what a validator CAN enforce is that the pair is complete, and it must
    # say plainly that a clean validation never means the fingerprint was
    # checked.  Enforcing only what is enforceable, and publishing which, is the
    # difference between a narrow guarantee and a misleading one.
    #
    # 1.x carries neither field: `result_fingerprint` is the 1.x shape and is
    # not redefined in place, because a 1.x value means what 1.x said it meant.
    for i, record in enumerate(records):
        for j, effect in enumerate(record.get("effects") or []):
            receipt = effect.get("receipt") or {}
            digest = receipt.get("interaction_fingerprint")
            salt = receipt.get("fingerprint_salt")
            path = f"$.records[{i}].effects[{j}].receipt"
            if version not in ("2.0.0", "3.0.0", "3.1.0", "3.2.0") and (
                digest is not None or salt is not None
            ):
                v.append(
                    Violation(
                        "T12",
                        path,
                        f"interaction_fingerprint and fingerprint_salt are 2.0.0 "
                        f"fields; schema {version} carries result_fingerprint",
                    )
                )
                continue
            if digest is not None and salt is None:
                v.append(
                    Violation(
                        "T12",
                        f"{path}.fingerprint_salt",
                        "interaction_fingerprint is present without its salt; a "
                        "verifier holding the archive cannot recompute the digest "
                        "without the salt that prefixed its material",
                    )
                )
            if salt is not None and digest is None:
                v.append(
                    Violation(
                        "T12",
                        f"{path}.interaction_fingerprint",
                        "fingerprint_salt is present with no fingerprint to salt",
                    )
                )

    # T13 — null violations are admitted only from trace schema 3.1.0.
    if version not in _VIOLATIONS_NULL_ADMITTED:
        for i, record in enumerate(records):
            if record.get("kind") == "transition" and record.get("violations") is None:
                v.append(Violation(
                    "T13", f"$.records[{i}].violations",
                    f"schema {version} does not admit violations: null "
                    "(ADR-029 admits it from 3.1.0)",
                ))

    if not records:
        # Only reachable through the JSONL path (the JSON document form pins
        # minItems 1): a stream that ends right after its header.
        if expect_complete:
            v.append(
                Violation(
                    "T3/INCOMPLETE",
                    "$.records",
                    "stream contains a header but no records; a complete trace "
                    "ends with run_completed, run_failed or run_cancelled",
                )
            )
        return v

    # T1 — seq is monotonic and gapless from 1.
    previous = 0
    for i, record in enumerate(records):
        seq = record.get("seq")
        if seq != previous + 1:
            v.append(
                Violation(
                    "T1",
                    f"$.records[{i}].seq",
                    f"seq {seq} follows {previous} (expected {previous + 1}); "
                    "seq must be gapless from 1",
                )
            )
        if isinstance(seq, int):
            previous = seq

    # T2 — run_started appears exactly once, as the first record.
    if records[0].get("kind") != "run_started":
        v.append(
            Violation(
                "T2",
                "$.records[0].kind",
                f"first record is '{records[0].get('kind')}'; expected run_started",
            )
        )
    for i, record in enumerate(records):
        if i > 0 and record.get("kind") == "run_started":
            v.append(
                Violation(
                    "T2",
                    f"$.records[{i}].kind",
                    f"run_started appears again at records[{i}] "
                    f"(seq {record.get('seq')}); run_started appears exactly "
                    "once, as the first record",
                )
            )

    # T3 — a finished run ends in a terminal record.
    if expect_complete and records[-1].get("kind") not in _TERMINAL_KINDS:
        v.append(
            Violation(
                "T3/INCOMPLETE",
                f"$.records[{len(records) - 1}].kind",
                f"last record is '{records[-1].get('kind')}'; a complete trace "
                "ends with run_completed, run_failed or run_cancelled",
            )
        )

    # T3 — a terminal record ends the run: NOTHING may follow it (regardless
    # of expect_complete; an in-flight trace has no terminal record at all).
    for i, record in enumerate(records[:-1]):
        if record.get("kind") in _TERMINAL_KINDS:
            v.append(
                Violation(
                    "T3",
                    f"$.records[{i}]",
                    f"'{record.get('kind')}' at records[{i}] "
                    f"(seq {record.get('seq')}) is followed by "
                    f"{len(records) - 1 - i} more record(s); nothing follows "
                    "a terminal record",
                )
            )

    by_seq: dict[int, dict] = {}
    for record in records:
        seq = record.get("seq")
        if isinstance(seq, int) and seq not in by_seq:
            by_seq[seq] = record

    run_started = records[0] if records[0].get("kind") == "run_started" else next(
        (r for r in records if r.get("kind") == "run_started"), None
    )

    # T4 — per-value read origins address real, earlier evidence, and the
    # read's key BINDS to what the origin names: a keyed fact/decision read
    # must address an entry with that exact key; a scan's key is the
    # collection name; an intent read's key is the literal 'intent'.
    # Rejections have no key — addressing a rejection by keyed read is a T4
    # violation (scans cover rejections); the `absent` origin's surface is
    # schema-enforced and carries no address to dangle.
    for i, record in enumerate(records):
        if record.get("kind") != "transition":
            continue
        for j, read in enumerate(record.get("reads") or []):
            origin = read.get("origin") or {}
            okind = origin.get("kind")
            key = read.get("key")
            path = f"$.records[{i}].reads[{j}].origin"
            if okind == "transition":
                target_seq = origin.get("seq")
                collection = origin.get("collection")
                index = origin.get("index")
                target = by_seq.get(target_seq)
                if collection == "rejections":
                    v.append(
                        Violation(
                            "T4",
                            path,
                            "keyed read addresses writes[rejections] — "
                            "rejections have no key; a keyed read cannot "
                            "address one (scans cover rejections)",
                        )
                    )
                elif target is None:
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read origin references seq {target_seq}, which "
                            "does not exist in this trace",
                        )
                    )
                elif not (
                    isinstance(target_seq, int)
                    and isinstance(record.get("seq"), int)
                    and target_seq < record["seq"]
                ):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read origin references seq {target_seq}, which is "
                            f"not earlier than the reading record "
                            f"(seq {record.get('seq')})",
                        )
                    )
                elif target.get("kind") != "transition":
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read origin references seq {target_seq}, which is "
                            f"a '{target.get('kind')}' record, not a transition",
                        )
                    )
                else:
                    array = (target.get("writes") or {}).get(collection)
                    size = len(array) if isinstance(array, list) else 0
                    if not (isinstance(index, int) and 0 <= index < size):
                        v.append(
                            Violation(
                                "T4",
                                path,
                                f"read origin addresses writes[{collection}]"
                                f"[{index}] of seq {target_seq}, but that "
                                f"collection has {size} element(s) — "
                                "index out of range",
                            )
                        )
                    elif (
                        isinstance(array[index], dict)
                        and array[index].get("key") != key
                    ):
                        v.append(
                            Violation(
                                "T4",
                                path,
                                f"read of key {key!r} addresses writes"
                                f"[{collection}][{index}] of seq {target_seq}, "
                                f"whose key is {array[index].get('key')!r} — "
                                "the origin must resolve to the exact value "
                                "the read names",
                            )
                        )
            elif okind == "initial":
                if run_started is None:
                    continue  # T2 already reported the missing run_started
                collection = origin.get("collection")
                index = origin.get("index")
                array = (run_started.get("initial_state") or {}).get(collection)
                size = len(array) if isinstance(array, list) else 0
                if collection == "rejections":
                    v.append(
                        Violation(
                            "T4",
                            path,
                            "keyed read addresses initial_state[rejections] — "
                            "rejections have no key; a keyed read cannot "
                            "address one (scans cover rejections)",
                        )
                    )
                elif not (isinstance(index, int) and 0 <= index < size):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read origin addresses initial_state[{collection}]"
                            f"[{index}], but that collection has {size} "
                            "element(s) — index out of range",
                        )
                    )
                elif (
                    isinstance(array[index], dict)
                    and array[index].get("key") != key
                ):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read of key {key!r} addresses initial_state"
                            f"[{collection}][{index}], whose key is "
                            f"{array[index].get('key')!r} — the origin must "
                            "resolve to the exact value the read names",
                        )
                    )
            elif okind == "scan":
                if key != origin.get("collection"):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"scan read's key is {key!r} but the scanned "
                            f"collection is {origin.get('collection')!r}; "
                            "a scan's key is the collection name",
                        )
                    )
            elif okind == "header" and origin.get("field") == "intent":
                if key != "intent":
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"header-intent read's key is {key!r}; "
                            "it must be the literal 'intent'",
                        )
                    )
            elif okind == "header" and origin.get("field") == "metadata":
                metadata = run.get("metadata")
                if not (isinstance(metadata, dict) and key in metadata):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"header-metadata read of key {key!r}, which does "
                            "not exist in run.metadata — a missing key is an "
                            "absent read, not a header read",
                        )
                    )
            # absent origins carry no address to dangle; their surface is
            # schema-required (ReadOrigin).

    # SB1–SB4 — spec binding, BEFORE the spec-correlated T5/T8 checks: a
    # trace cannot claim one graph and be checked against another.
    if spec is not None:
        spec_nodes: dict[str, dict] = {
            n["name"]: n
            for n in (spec.get("nodes") or [])
            if isinstance(n, dict) and isinstance(n.get("name"), str)
        }
        graph = run.get("graph") or {}

        # SB1 — header identity equals the supplied spec's.
        for header_field, spec_field in (
            ("name", "name"),
            ("version", "version"),
            ("spec_schema_version", "schema_version"),
        ):
            if graph.get(header_field) != spec.get(spec_field):
                v.append(
                    Violation(
                        "SB1",
                        f"$.run.graph.{header_field}",
                        f"run.graph.{header_field} is "
                        f"{graph.get(header_field)!r} but the supplied spec's "
                        f"{spec_field} is {spec.get(spec_field)!r}",
                    )
                )

        # SB2 — the graph fingerprint is RECOMPUTED over the supplied spec's
        # canonical form; a recorded fingerprint is never trusted.
        # (code_fingerprint is NOT offline-verifiable without the runtime's
        # node registry — an explicit limitation.)
        expected_fp = fingerprint("graph", spec)
        if graph.get("graph_fingerprint") != expected_fp:
            v.append(
                Violation(
                    "SB2",
                    "$.run.graph.graph_fingerprint",
                    f"graph_fingerprint is {graph.get('graph_fingerprint')!r} "
                    f"but the fingerprint recomputed over the supplied spec's "
                    f"canonical form is '{expected_fp}'",
                )
            )

        for i, record in enumerate(records):
            if record.get("kind") != "transition":
                continue
            node_decl = spec_nodes.get(record.get("node"))
            if node_decl is None:
                continue  # an undeclared node is T5's finding
            # SB3 — effect_class equals the spec node's declared class, or
            # external_effect when undeclared (the conservative default).
            declared_class = node_decl.get("effect_class", "external_effect")
            if record.get("effect_class") != declared_class:
                v.append(
                    Violation(
                        "SB3",
                        f"$.records[{i}].effect_class",
                        f"transition of '{record.get('node')}' records "
                        f"effect_class {record.get('effect_class')!r} but the "
                        f"spec declares {declared_class!r}"
                        + (
                            ""
                            if "effect_class" in node_decl
                            else " (undeclared defaults to external_effect)"
                        ),
                    )
                )
            # SB4 — the recorded violations list is TRUTHFUL: recomputed from
            # the declarations against EVERY captured read and every
            # fact/decision write key.  No origin kind is exempt: an `absent`
            # read is captured precisely because a miss steers control flow as
            # much as a value does, a `scan` read names the collection it swept
            # (so the collection name is the declared key), and a `header` read
            # names `intent` or the metadata key it took.  A node that touched
            # something undeclared owes a violation for it whatever shape the
            # touch had — exempting three quarters of the readable surface was
            # how cross-review v3 read an undeclared key and published an empty
            # violations list.  A node without a declaration side contributes no
            # expected entries for that side.
            expected_violations: Counter = Counter()
            reads_declared = node_decl.get("reads_declared")
            if reads_declared is not None:
                allowed_reads = set(reads_declared)
                for read in record.get("reads") or []:
                    if read.get("key") not in allowed_reads:
                        expected_violations[("undeclared_read", read.get("key"))] += 1
            writes_declared = node_decl.get("writes_declared")
            if writes_declared is not None:
                allowed_writes = set(writes_declared)
                for collection in ("facts", "decisions"):
                    for entry in (record.get("writes") or {}).get(collection) or []:
                        if (
                            isinstance(entry, dict)
                            and entry.get("key") not in allowed_writes
                        ):
                            expected_violations[
                                ("undeclared_write", entry.get("key"))
                            ] += 1
            recorded_raw = record.get("violations")
            declares_something = (
                reads_declared is not None or writes_declared is not None
            )
            if version in _VIOLATIONS_NULL_ADMITTED:
                if declares_something and recorded_raw is None:
                    v.append(Violation(
                        "SB4", f"$.records[{i}].violations",
                        f"transition of '{record.get('node')}' records "
                        "violations: null but the spec declares reads_declared "
                        "and/or writes_declared; null means nothing was declared",
                    ))
                    continue
                if not declares_something and recorded_raw is not None:
                    v.append(Violation(
                        "SB4", f"$.records[{i}].violations",
                        f"transition of '{record.get('node')}' records a "
                        "violations array but the spec declares neither "
                        "reads_declared nor writes_declared; an undeclared node "
                        "must record null",
                    ))
                    continue
            recorded_violations = Counter(
                (item.get("kind"), item.get("key"))
                for item in recorded_raw or []
                if isinstance(item, dict)
            )
            # In schema 1.1 strict declaration enforcement happens before a
            # returned write can commit. Transactional raised attempts keep
            # `writes` structurally empty, so the attempted write key survives
            # only in the DeclarationViolation evidence. Accept those keys
            # exactly when they are in fact outside the declaration; reads
            # remain independently recomputable from their captured records.
            error = record.get("error") or {}
            if (
                record.get("outcome") == "raised"
                and error.get("type") == "DeclarationViolation"
                and writes_declared is not None
            ):
                allowed_writes = set(writes_declared)
                for (kind, key), count in recorded_violations.items():
                    if kind == "undeclared_write" and key not in allowed_writes:
                        expected_violations[(kind, key)] += count
            if expected_violations != recorded_violations:
                missing = expected_violations - recorded_violations
                invented = recorded_violations - expected_violations
                parts = []
                if missing:
                    parts.append(
                        "missing "
                        + ", ".join(
                            f"({kind}, {key!r})" + (f" x{n}" if n > 1 else "")
                            for (kind, key), n in sorted(
                                missing.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))
                            )
                        )
                    )
                if invented:
                    parts.append(
                        "invented "
                        + ", ".join(
                            f"({kind}, {key!r})" + (f" x{n}" if n > 1 else "")
                            for (kind, key), n in sorted(
                                invented.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))
                            )
                        )
                    )
                v.append(
                    Violation(
                        "SB4",
                        f"$.records[{i}].violations",
                        f"recorded violations for '{record.get('node')}' do not "
                        "match the set recomputed from the spec declarations: "
                        + "; ".join(parts),
                    )
                )

    # T5 — recorded names exist in the GraphSpec (END only where permitted).
    declared = _declared_nodes(spec)

    def check_node(name: Any, path: str, allow_end: bool = False) -> None:
        if declared is None:
            return
        if allow_end and name == "END":
            return
        if name not in declared:
            suffix = " or END" if allow_end else ""
            v.append(
                Violation(
                    "T5",
                    path,
                    f"'{name}' is not a declared node{suffix} in the GraphSpec",
                )
            )

    for i, record in enumerate(records):
        kind = record.get("kind")
        if kind in ("attempt_started", "transition"):
            check_node(record.get("node"), f"$.records[{i}].node")
        elif kind == "routing":
            check_node(record.get("after"), f"$.records[{i}].after")
            check_node(record.get("selected"), f"$.records[{i}].selected", allow_end=True)
            for j, candidate in enumerate(record.get("candidates") or []):
                check_node(
                    candidate.get("target"),
                    f"$.records[{i}].candidates[{j}].target",
                    allow_end=True,
                )
        elif kind == "run_cancelled":
            active = record.get("active_attempt")
            if isinstance(active, dict):
                check_node(active.get("node"), f"$.records[{i}].active_attempt.node")
        elif kind == "run_failed":
            # failed_node, when non-null, names a declared node — never END
            # (END is a routing pseudo-target, not something that can fail).
            failed_node = record.get("failed_node")
            if failed_node is not None:
                check_node(failed_node, f"$.records[{i}].failed_node")

    # T6 — every transition is opened by the immediately preceding
    # attempt_started with the same (node, attempt); an unclosed attempt_started
    # may be followed only by a run_cancelled naming it, by a run_failed with a
    # non-node cause, or by EOF when the trace is allowed to be incomplete.
    for i, record in enumerate(records):
        kind = record.get("kind")
        if kind == "transition":
            opener = records[i - 1] if i > 0 else None
            if not (
                isinstance(opener, dict)
                and opener.get("kind") == "attempt_started"
                and opener.get("node") == record.get("node")
                and opener.get("attempt") == record.get("attempt")
            ):
                v.append(
                    Violation(
                        "T6",
                        f"$.records[{i}]",
                        f"transition (node '{record.get('node')}', attempt "
                        f"{record.get('attempt')}) is not immediately preceded "
                        "by a matching attempt_started",
                    )
                )
        elif kind == "attempt_started":
            node, attempt = record.get("node"), record.get("attempt")
            successor = records[i + 1] if i + 1 < len(records) else None
            if successor is None:
                if expect_complete:
                    v.append(
                        Violation(
                            "T6",
                            f"$.records[{i}]",
                            f"attempt_started (node '{node}', attempt {attempt}) "
                            "is unclosed at the end of a trace expected to be "
                            "complete",
                        )
                    )
                continue
            skind = successor.get("kind")
            if (
                skind == "transition"
                and successor.get("node") == node
                and successor.get("attempt") == attempt
            ):
                continue
            if skind == "run_cancelled":
                active = successor.get("active_attempt")
                if not (
                    isinstance(active, dict)
                    and active.get("node") == node
                    and active.get("attempt") == attempt
                ):
                    v.append(
                        Violation(
                            "T6",
                            f"$.records[{i + 1}].active_attempt",
                            "run_cancelled follows an unclosed attempt_started "
                            f"(node '{node}', attempt {attempt}) but "
                            "active_attempt does not name it",
                        )
                    )
                continue
            if skind == "run_failed" and (successor.get("cause") or {}).get(
                "kind"
            ) != "node_failure":
                continue
            v.append(
                Violation(
                    "T6",
                    f"$.records[{i}]",
                    f"attempt_started (node '{node}', attempt {attempt}) is "
                    f"unclosed: followed by '{skind}', which may not follow an "
                    "unclosed attempt",
                )
            )
        elif kind == "run_cancelled":
            # CONVERSE arm: a non-null active_attempt names exactly the
            # immediately preceding UNCLOSED attempt_started.  When the
            # preceding record is anything else, the attempt it names was
            # either closed (its transition intervened) or never started —
            # active_attempt can neither invent an attempt nor resurrect a
            # closed one.  (The attempt_started arm above already checks the
            # naming when the predecessor IS an unclosed attempt_started.)
            active = record.get("active_attempt")
            if isinstance(active, dict):
                predecessor = records[i - 1] if i > 0 else None
                if not (
                    isinstance(predecessor, dict)
                    and predecessor.get("kind") == "attempt_started"
                ):
                    predecessor_kind = (
                        f"'{predecessor.get('kind')}'"
                        if isinstance(predecessor, dict)
                        else "absent"
                    )
                    v.append(
                        Violation(
                            "T6",
                            f"$.records[{i}].active_attempt",
                            f"active_attempt names (node "
                            f"'{active.get('node')}', attempt "
                            f"{active.get('attempt')}) but the immediately "
                            f"preceding record is {predecessor_kind}, not an "
                            "unclosed attempt_started; active_attempt must be "
                            "null when cancellation fell between attempts",
                        )
                    )

    # T7 — failure attribution is coherent.
    for i, record in enumerate(records):
        if record.get("kind") != "run_failed":
            continue
        cause = record.get("cause") or {}
        cause_kind = cause.get("kind")
        failed_node = record.get("failed_node")
        if cause_kind == "node_failure" and failed_node is None:
            v.append(
                Violation(
                    "T7",
                    f"$.records[{i}].failed_node",
                    "cause.kind is node_failure but failed_node is null; "
                    "failed_node is non-null iff the cause is node_failure",
                )
            )
        if cause_kind != "node_failure" and failed_node is not None:
            v.append(
                Violation(
                    "T7",
                    f"$.records[{i}].failed_node",
                    f"failed_node '{failed_node}' is non-null but cause.kind is "
                    f"'{cause_kind}', not node_failure",
                )
            )
        record_seq = cause.get("record_seq")
        if record_seq is not None and record_seq not in by_seq:
            v.append(
                Violation(
                    "T7",
                    f"$.records[{i}].cause.record_seq",
                    f"cause.record_seq {record_seq} references no existing record",
                )
            )

    # T8 — routing correlations, and (with a spec) the complete candidate list.
    transitions = (spec.get("transitions") or {}) if isinstance(spec, dict) else {}
    for i, record in enumerate(records):
        if record.get("kind") != "routing":
            continue
        outcome = record.get("outcome")
        selected = record.get("selected")
        candidates = record.get("candidates") or []
        taken = [c for c in candidates if c.get("taken") is True]
        path = f"$.records[{i}].candidates"
        value = record.get("value")
        if outcome in ("matched", "default"):
            if len(taken) != 1:
                v.append(
                    Violation(
                        "T8",
                        path,
                        f"outcome '{outcome}' requires exactly one taken "
                        f"candidate; found {len(taken)}",
                    )
                )
            else:
                condition = taken[0].get("condition") or {}
                if taken[0].get("target") != selected:
                    v.append(
                        Violation(
                            "T8",
                            path,
                            f"the taken candidate targets "
                            f"'{taken[0].get('target')}' but selected is "
                            f"'{selected}'",
                        )
                    )
                if outcome == "matched":
                    if condition.get("kind") != "map":
                        v.append(
                            Violation(
                                "T8",
                                path,
                                "outcome 'matched' but the taken candidate's "
                                f"condition kind is '{condition.get('kind')}', "
                                "not the map entry matching the routed value",
                            )
                        )
                    elif condition.get("key") != value:
                        v.append(
                            Violation(
                                "T8",
                                path,
                                "outcome 'matched' but the taken candidate "
                                f"answers to map key {condition.get('key')!r} "
                                f"while the routed value is {value!r}",
                            )
                        )
                elif condition.get("kind") != "default":
                    v.append(
                        Violation(
                            "T8",
                            path,
                            "outcome 'default' but the taken candidate's "
                            f"condition kind is '{condition.get('kind')}'; "
                            "default requires the declared default candidate",
                        )
                    )
        elif outcome == "miss":
            if taken:
                v.append(
                    Violation(
                        "T8",
                        path,
                        f"outcome 'miss' requires zero taken candidates; "
                        f"found {len(taken)}",
                    )
                )
        elif outcome == "static":
            # The schema pins exactly one candidate for static; verify it is
            # the static edge, taken, and that it targets the selected step.
            if len(candidates) == 1:
                candidate = candidates[0]
                if (
                    candidate.get("taken") is not True
                    or candidate.get("target") != selected
                    or (candidate.get("condition") or {}).get("kind") != "static"
                ):
                    v.append(
                        Violation(
                            "T8",
                            path,
                            "static routing must have its single candidate of "
                            "condition kind static, taken, with target equal "
                            f"to selected '{selected}'",
                        )
                    )
        # `origin` is the routing record's own causal edge, and every routed
        # outcome — matched, default AND miss — owes it.  It addresses the exact
        # Decision observed, not merely the record that held it: a scalar seq
        # never identified a value (round 4 fixed that for reads and left
        # routing behind), and it could not name a SEEDED decision at all, which
        # resume would have walked straight into.
        if outcome != "static":
            origin = record.get("origin")
            opath = f"$.records[{i}].origin"
            on = record.get("on")
            kind = (origin or {}).get("kind")
            decision = None

            if kind == "transition":
                oseq, idx = origin.get("seq"), origin.get("index")
                target = by_seq.get(oseq)
                if target is None:
                    v.append(Violation("T8", opath,
                        f"origin names seq {oseq}, which does not exist in this trace"))
                elif not (isinstance(oseq, int) and isinstance(record.get("seq"), int)
                          and oseq < record["seq"]):
                    v.append(Violation("T8", opath,
                        f"origin names seq {oseq}, which is not earlier than the "
                        f"routing record (seq {record.get('seq')})"))
                elif target.get("kind") != "transition":
                    v.append(Violation("T8", opath,
                        f"origin names seq {oseq}, which is a "
                        f"'{target.get('kind')}' record, not a transition — only a "
                        "committed transition writes a decision"))
                elif target.get("disposition") != "commit":
                    v.append(Violation("T8", opath,
                        f"origin names seq {oseq}, whose disposition is "
                        f"'{target.get('disposition')}' — an attempt that did not "
                        "commit handed nothing to the run"))
                else:
                    decisions = (target.get("writes") or {}).get("decisions") or []
                    if not isinstance(idx, int) or not 0 <= idx < len(decisions):
                        v.append(Violation("T8", opath,
                            f"origin index {idx} is out of range for the "
                            f"{len(decisions)} decision(s) committed by seq {oseq}"))
                    else:
                        decision = decisions[idx]

            elif kind == "initial":
                idx = origin.get("index")
                seeded = (
                    ((records[0] or {}).get("initial_state") or {}).get("decisions") or []
                    if records and records[0].get("kind") == "run_started" else []
                )
                if not isinstance(idx, int) or not 0 <= idx < len(seeded):
                    v.append(Violation("T8", opath,
                        f"origin index {idx} is out of range for the {len(seeded)} "
                        "seeded decision(s) in run_started.initial_state"))
                else:
                    decision = seeded[idx]

            elif kind == "absent":
                if outcome != "miss":
                    v.append(Violation("T8", opath,
                        f"origin kind 'absent' on outcome '{outcome}' — a value "
                        "that was never decided cannot be matched or defaulted"))
                if record.get("value") is not None:
                    v.append(Violation("T8", f"$.records[{i}].value",
                        f"origin kind 'absent' but value is {record.get('value')!r}; "
                        "nothing was observed, so nothing may be reported — an "
                        "absent origin admits only a null value"))
                anywhere = [
                    r["seq"] for r in records
                    if r.get("kind") == "transition" and r.get("disposition") == "commit"
                    and any(isinstance(e, dict) and e.get("key") == on
                            for e in (r.get("writes") or {}).get("decisions") or [])
                ] + ([0] if any(
                    isinstance(e, dict) and e.get("key") == on
                    for e in (((records[0] or {}).get("initial_state") or {}).get("decisions") or [])
                ) and records and records[0].get("kind") == "run_started" else [])
                if anywhere:
                    where = "the initial state" if anywhere == [0] else f"seq {anywhere[0]}"
                    v.append(Violation("T8", opath,
                        f"origin claims no decision {on!r} exists, but {where} "
                        "carries one — 'absent' is a claim about the whole run"))

            # The addressed decision must be the one this routing says it saw.
            if decision is not None:
                if decision.get("key") != on:
                    v.append(Violation("T8", opath,
                        f"origin addresses a decision keyed "
                        f"{decision.get('key')!r} but this routing dispatches on "
                        f"{on!r}"))
                elif not _json_equal(decision.get("value"), record.get("value")):
                    v.append(Violation("T8", f"$.records[{i}].value",
                        f"routing reports value {record.get('value')!r} but the "
                        f"decision its origin addresses holds "
                        f"{decision.get('value')!r} — the causal edge must lead to "
                        "the value actually routed on"))

                # R10 staleness, in the two directions recency actually runs.
                # ACROSS sources: a later committed transition supersedes the
                # origin — and ANY committed transition supersedes the seed.
                oseq = origin.get("seq") if kind == "transition" else 0
                stale = [
                    r["seq"] for r in records
                    if r.get("kind") == "transition" and r.get("disposition") == "commit"
                    and isinstance(r.get("seq"), int) and isinstance(record.get("seq"), int)
                    and oseq < r["seq"] < record["seq"]
                    and any(isinstance(e, dict) and e.get("key") == on
                            for e in (r.get("writes") or {}).get("decisions") or [])
                ]
                if stale:
                    v.append(Violation("T8", opath,
                        f"origin is STALE: transition seq {stale[0]} committed a "
                        f"later decision {on!r} before this routing "
                        f"(seq {record.get('seq')}) — R10 routes on the most "
                        "recent recorded value"))
                else:
                    # WITHIN the source: R10 says last-in-array, so an earlier
                    # index is stale even inside the very record the origin
                    # names — and even when the later entry carries the same
                    # value, because the origin promises the exact Decision
                    # observed, not an equivalent payload.  Exact
                    # addressability is not the same as being current
                    # (cross-review v5).
                    if kind == "transition":
                        siblings = (by_seq[origin["seq"]].get("writes") or {}).get(
                            "decisions"
                        ) or []
                        where = f"transition seq {origin['seq']}"
                    else:
                        siblings = (
                            (records[0].get("initial_state") or {}).get("decisions") or []
                        )
                        where = "the initial state"
                    later = [
                        k
                        for k in range(origin["index"] + 1, len(siblings))
                        if isinstance(siblings[k], dict)
                        and siblings[k].get("key") == on
                    ]
                    if later:
                        v.append(Violation("T8", opath,
                            f"origin is STALE within its own source: {where} "
                            f"records decision {on!r} again at index {later[-1]}, "
                            f"after the index {origin['index']} this origin names "
                            "— R10 routes on the LAST such entry, whatever value "
                            "it carries"))

        if spec is not None:
            step = transitions.get(record.get("after"))
            if isinstance(step, dict):
                expected = _expected_candidates(step)
                actual = Counter(
                    (
                        (c.get("condition") or {}).get("kind"),
                        (c.get("condition") or {}).get("key"),
                        c.get("target"),
                    )
                    for c in candidates
                )
                if expected != actual:
                    missing = expected - actual
                    extra = actual - expected
                    parts = []
                    if missing:
                        parts.append("missing " + _format_candidates(missing))
                    if extra:
                        parts.append("unexpected " + _format_candidates(extra))
                    v.append(
                        Violation(
                            "T8",
                            path,
                            "candidate list does not match the spec route step "
                            f"for '{record.get('after')}': " + "; ".join(parts),
                        )
                    )
                if (
                    outcome == "default"
                    and step.get("kind") == "route"
                    and isinstance(value, str)
                    and value in (step.get("map") or {})
                ):
                    v.append(
                        Violation(
                            "T8",
                            f"$.records[{i}]",
                            f"outcome 'default' but value {value!r} is a "
                            "declared map key of the route step for "
                            f"'{record.get('after')}'; a mapped value is "
                            "matched, never defaulted",
                        )
                    )
                # A miss must have been POSSIBLE.  GraphSpec R7/R9 decide that,
                # not the producer: a value that is a declared map key was
                # matched, and an unmapped value on a route that declares a
                # default was defaulted.  Recording either as a miss claims the
                # graph had nowhere to go when it did.  (Cross-review v3: the
                # miss branch checked only that no candidate was taken, so both
                # contradictions passed.)
                if outcome == "miss" and step.get("kind") == "route":
                    route_map = step.get("map") or {}
                    if isinstance(value, str) and value in route_map:
                        v.append(
                            Violation(
                                "T8",
                                f"$.records[{i}]",
                                f"outcome 'miss' but value {value!r} is a "
                                "declared map key of the route step for "
                                f"'{record.get('after')}' — a mapped value is "
                                "matched, never missed",
                            )
                        )
                    elif isinstance(value, str) and "default" in step:
                        # ONLY a string can be defaulted.  R9 is normative: a
                        # non-string value is never coerced, so it is a miss
                        # whether or not the route declares a default — and the
                        # schema forbids a non-string 'default' outcome, so
                        # rejecting it here left such a value with no legal
                        # outcome at all (cross-review v4).
                        v.append(
                            Violation(
                                "T8",
                                f"$.records[{i}]",
                                f"outcome 'miss' but the route step for "
                                f"'{record.get('after')}' declares a default "
                                f"target {step.get('default')!r} — an unmapped "
                                "STRING is defaulted there, never missed; a miss "
                                "belongs to a strict route, or to a non-string "
                                "value, which R9 never coerces",
                            )
                        )
            # An undeclared 'after' is T5's finding; nothing to compare against.

    # E1–E11 — the execution state machine, after the T-rules and only over a
    # lifecycle-clean stream (see _execution_violations for the gate's why).
    if not any(x.rule in _E_GATE_RULES for x in v):
        v.extend(_execution_violations(records, run, spec))

    return v


def validate_trace(
    doc: dict, spec: dict | None = None, expect_complete: bool = True
) -> list[Violation]:
    """Validate a trace document: JSON Schema first, then the T-rules.

    ``spec`` enables the spec-correlated rules (SB1–SB4, T5 names, T8
    candidate lists).  ``expect_complete=False`` accepts a trace still in
    flight or recovered from a crash: T3 and the unclosed-attempt-at-EOF arm
    of T6 are then waived — truncation is evidence, not malformation
    (guarantees.md invariant II).
    """
    # J1 first, recursively: an already-parsed document may carry what no
    # strict RFC 8259 text could have produced — non-finite floats, tuples,
    # sets, non-string mapping keys.  A structurally non-JSON document cannot
    # be meaningfully schema-validated (jsonschema would report its shape as
    # type noise), so structural J1 findings are reported alone.
    j1_violations, structural = _j1_violations(doc)
    if structural:
        return j1_violations
    if spec is not None:
        # The spec argument went straight to `fingerprint("graph", spec)` in
        # `_trace_semantics` with nothing between. A caller handing this
        # function a Python object -- which is the whole point of this entry
        # point -- got `TypeError` from `json.dumps` or `UnicodeEncodeError`
        # from the codec, RAISED out of a function whose contract is to RETURN
        # findings. And for a NaN or a non-string key it did worse: it reported
        # SB2, "the recomputed graph fingerprint does not match", when the truth
        # is "your spec is not a JSON document".
        spec_j1, _ = _j1_violations(spec, prefix="spec:$")
        if spec_j1:
            # ANY J1 finding on the spec is terminal, not only a structural
            # one. The spec's only use here is to be fingerprinted, and a
            # non-finite float is a structurally valid NUMBER that
            # `canonical_json` still cannot write -- so "report it and carry
            # on" reaches the fingerprint anyway and raises there. That is the
            # first repair of this defect, and it was not enough.
            return spec_j1
    schema = load_trace_schema()
    schema_violations = _schema_violations(schema, _trace_validator(), doc)
    if schema_violations:
        return schema_violations
    # Non-finite floats are schema-invisible (they are numbers); report them
    # with the semantic findings.
    violations = list(j1_violations)
    violations.extend(_trace_semantics(doc, spec, expect_complete))
    return violations


# --------------------------------------------------------------------------- #
# JSONL — the streaming encoding of the same logical model                    #
# --------------------------------------------------------------------------- #


def validate_jsonl(
    text: str, spec: dict | None = None, expect_complete: bool = True
) -> tuple[list[Violation], dict | None]:
    """Validate a JSONL trace stream; return (violations, reassembled doc).

    Line 1 (first non-empty) must be a ``TraceHeader`` (rule JSONL1); every
    further line must be a ``Record`` (rule JSONL2).  A FINAL line that fails
    to JSON-parse is crash truncation: ``T3/INCOMPLETE`` when a complete trace
    was expected, no violation otherwise — never JSONL2.  A non-final
    unparsable line is JSONL2.  When every line is accounted for, the stream is
    reassembled into ``{schema_version, run, records}`` and the T-rules run on
    it; the doc is returned so callers can pin JSON <-> JSONL equivalence.
    """
    # JSONL3 — the stream is LF-only: a single CR byte anywhere (CRLF endings
    # included) rejects it before any line processing.  Canonical writers emit
    # LF; readers do not silently normalize.
    if "\r" in text:
        cr_line = text.count("\n", 0, text.index("\r"))
        return (
            [
                Violation(
                    "JSONL3",
                    f"$.lines[{cr_line}]",
                    "stream contains a CR byte (first on this line); JSONL "
                    "traces are LF-only — canonical writers emit LF and "
                    "readers do not silently normalize (rule JSONL3)",
                )
            ],
            None,
        )

    trace_schema = load_trace_schema()
    header_validator = _pointer_validator(
        "trace-header", trace_schema, "#/$defs/TraceHeader"
    )
    record_validator = _pointer_validator("trace-record", trace_schema, "#/$defs/Record")

    violations: list[Violation] = []
    content = [
        (index, line) for index, line in enumerate(text.split("\n")) if line.strip()
    ]
    if not content:
        return (
            [Violation("JSONL1", "$", "stream is empty; expected a TraceHeader line")],
            None,
        )

    (header_index, header_line), record_lines = content[0], content[1:]
    try:
        header = _loads_strict(header_line)
    except StrictJSONError as exc:
        # The line IS parseable JSON in Python's lax reading — the problem is
        # strictness, not brokenness: J1 or J2, never JSONL1.
        violations.append(
            Violation(
                "J2" if isinstance(exc, NonCanonicalNumberError) else "J1",
                f"$.lines[{header_index}]",
                f"header line is not strict RFC 8259 JSON: {exc}",
            )
        )
        return violations, None
    except ValueError as exc:
        violations.append(
            Violation(
                "JSONL1",
                f"$.lines[{header_index}]",
                f"header line is not valid JSON: {exc}",
            )
        )
        return violations, None
    header_violations = _schema_violations(
        trace_schema,
        header_validator,
        header,
        rule="JSONL1",
        prefix=f"$.lines[{header_index}]",
        msg_prefix="header line is not a valid TraceHeader: ",
    )
    violations.extend(header_violations)

    # Every record line is read under the version the HEADER declares. Without
    # this the stream is strictly more permissive than the same trace as JSON.
    stream_version = header.get("schema_version") if isinstance(header, dict) else None

    records: list[dict] = []
    lines_clean = True
    truncated = False
    total = len(record_lines)
    for position, (line_index, line) in enumerate(record_lines):
        is_final = position == total - 1
        try:
            obj = _loads_strict(line, governed_as=stream_version)
        except StrictJSONError as exc:
            # Not crash truncation and not malformed JSON: the line parses in
            # Python's lax reading but carries a non-finite constant.  That is
            # a strictness violation — never JSONL2, on any line.
            #
            # And the RULE has to be the right one. `main` already makes this
            # distinction for `--spec` and the JSON path, with the reason
            # written in a test: a non-canonical number IS strict RFC 8259, so
            # reporting it as J1 sends a reader looking for a duplicate key or
            # a NaN. This arm called everything J1, which mattered from the
            # moment record lines became version-governed and J2 could fire
            # here at all.
            lines_clean = False
            rule = "J2" if isinstance(exc, NonCanonicalNumberError) else "J1"
            violations.append(
                Violation(
                    rule,
                    f"$.lines[{line_index}]",
                    f"line is not strict RFC 8259 JSON: {exc}",
                )
            )
            continue
        except ValueError as exc:
            if is_final:
                # Crash truncation: the stream ends mid-write.  Incomplete,
                # not malformed — JSONL2 is never raised for the final line.
                truncated = True
                if expect_complete:
                    violations.append(
                        Violation(
                            "T3/INCOMPLETE",
                            f"$.lines[{line_index}]",
                            "final line is not parseable JSON — the stream is "
                            "crash-truncated and the trace is incomplete",
                        )
                    )
            else:
                lines_clean = False
                violations.append(
                    Violation(
                        "JSONL2",
                        f"$.lines[{line_index}]",
                        f"line is not valid JSON: {exc}",
                    )
                )
            continue
        record_violations = _schema_violations(
            trace_schema,
            record_validator,
            obj,
            rule="JSONL2",
            prefix=f"$.lines[{line_index}]",
            msg_prefix="line is not a valid Record: ",
        )
        if record_violations:
            lines_clean = False
            violations.extend(record_violations)
        else:
            records.append(obj)

    if header_violations:
        return violations, None

    doc = {
        "schema_version": header.get("schema_version"),
        "run": header.get("run"),
        "records": records,
    }
    if not lines_clean:
        # A malformed stream cannot be reassembled faithfully; running the
        # T-rules over the surviving lines would only cascade (T1 gaps, T6
        # adjacency breaks) off the lines already reported.
        return violations, None

    # Per-line validation cannot express version-family constraints that bind
    # a 1.0 header to 1.1-only record fields such as effect receipts.
    root_violations = _schema_violations(
        trace_schema, _trace_validator(), doc,
    )
    if root_violations:
        violations.extend(root_violations)
        return violations, doc

    # When truncation was detected the incompleteness is already on record;
    # run the T-rules in incomplete mode so it is not reported twice.
    violations.extend(
        _trace_semantics(doc, spec, expect_complete and not truncated)
    )
    return violations, doc


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #



# --------------------------------------------------------------------------- #
# Commitments, checkpoints and receipts — rules C, K and P                    #
# --------------------------------------------------------------------------- #
#
# ADR-021 gives the accumulator, ADR-020 gives what a receipt may claim, and
# ADR-023 gives what a resumed segment says about the one it continued. The
# digests below are RE-IMPLEMENTED here rather than imported from
# `vitruvyan_motus.commitments`, and that is the point: the contract is the
# authority (ADR-001), so a validator that called the implementation would
# check the implementation against itself and agree with any drift.
#
# RFC 6962 domain separation, byte for byte:
_LEAF_DOMAIN = b"\x00"
_NODE_DOMAIN = b"\x01"
_CHECKPOINT_DOMAIN = b"\x02"

# Networks this validator can evaluate. ADR-020 decision 8: a `VERIFIED` on a
# chain the verifier cannot evaluate is the worst lie this system can tell, so
# an unknown network is refused rather than passed through.
KNOWN_ANCHOR_NETWORKS = frozenset({"tron:nile", "opentimestamps:bitcoin"})

# A reference that is not in the network's own form is not a reference. The
# schema's `Identifier` pattern (`\S`) is an unanchored SEARCH, not a shape
# check, so a reference carrying a newline still validates — and `verify`
# below interpolates it raw into an explorer URL. This is the reachable
# injection the ADR-031 review found (d09_verdict_injection.py): the fix is
# not quoting the string (that corrupts the clickable URL a legitimate
# reference produces) but refusing one that never had the network's shape.
#
# `tron:nile`'s real transaction id is 64 hex nibbles (a SHA-256 hash,
# optionally `0x`-prefixed) — `demo/out/anchor_receipt.json`'s
# `6010ded80e15b8005a14c5f13ef44aa36a11d9f0cde75c961ef5e11a49ea17b0` is one.
# This repository's own tests predate this check and use shorter hex
# mnemonics for readability (`"6010ded8"`, `"0xdeadbeef"`) — legitimate hex,
# just not a full id — so 64 is enforced as a CEILING against an attacker
# padding the field, not a floor a real id happens to clear.
_TRON_REFERENCE_RE = re.compile(r"(0x)?[0-9a-fA-F]{1,64}")
# `plugs/motus-anchor-opentimestamps` writes exactly this: the literal
# `bitcoin-block:` prefix followed by `str(int)` of a block height — never a
# leading zero, never negative.
_OPENTIMESTAMPS_REFERENCE_RE = re.compile(r"bitcoin-block:(0|[1-9][0-9]*)")

ANCHOR_REFERENCE_SHAPES: dict[str, tuple[re.Pattern[str], str]] = {
    "tron:nile": (_TRON_REFERENCE_RE,
                  "a hexadecimal transaction id, optionally 0x-prefixed"),
    "opentimestamps:bitcoin": (_OPENTIMESTAMPS_REFERENCE_RE,
                               "bitcoin-block:<the block height, a decimal integer>"),
}


def _anchor_reference_issue(network: str, reference: Any) -> str | None:
    """None if `reference` is in the form `network` writes; else the form it
    was supposed to be in.

    Only meaningful for a network already in `KNOWN_ANCHOR_NETWORKS` — an
    unknown network is its own P5 violation and has no known form to hold a
    reference to. `reference` is `None` for a `pending` anchor (the schema
    allows it), which is not malformed, only not yet claimed.
    """
    shape = ANCHOR_REFERENCE_SHAPES.get(network)
    if shape is None or not isinstance(reference, str):
        return None
    pattern, description = shape
    return None if pattern.fullmatch(reference) else description

# Attestation types this validator can evaluate (ADR-031 decision 4). A closed
# set with one member is the same ceremony as KNOWN_ANCHOR_NETWORKS had with
# one member, and it is what made adding OpenTimestamps a row instead of a
# rewrite: an unknown type is refused (P10), never passed through as a
# violation that happens to exit 0.
KNOWN_ATTESTATION_TYPES = frozenset({"rfc3161_timestamp"})

_TOKEN_DER_MIN_BYTES = 64
_TOKEN_DER_MAX_BYTES = 2 * 1024 * 1024


def _rfc3161_material_issue(subject: Any, proof: Any) -> str | None:
    """Why `proof` cannot be trusted as material to check, or ``None``.

    Two separate questions, both about the BYTES and not merely about which
    keys are present (the schema already refuses a missing key as SCHEMA):
    is `token_der` a real TimeStampResp-shaped blob (decodable, and neither
    an empty stub nor an unbounded one), and is it bound to THIS subject —
    `message_imprint` must equal sha256 of the subject's digest bytes, the
    same double hash RFC 3161 requires the TSA to have signed. Without the
    second check a token issued over any digest could be pasted onto any
    other attestation's subject and still read as material.
    """
    if not isinstance(proof, dict):
        return None  # SCHEMA's territory: proof is not even the right shape
    token_der = proof.get("token_der")
    if token_der is None:
        return None  # SCHEMA already refuses a missing required key
    if not isinstance(token_der, str):
        return "its token_der is not a string"
    try:
        decoded = base64.b64decode(token_der, validate=True)
    except (binascii.Error, ValueError):
        return "its token_der does not decode as base64"
    if not (_TOKEN_DER_MIN_BYTES <= len(decoded) <= _TOKEN_DER_MAX_BYTES):
        return (f"its token_der decodes to {len(decoded)} byte(s), and a "
                f"TimeStampResp is never that short or longer than "
                f"{_TOKEN_DER_MAX_BYTES} bytes")
    imprint = proof.get("message_imprint")
    if imprint is None:
        return None  # SCHEMA already refuses a missing required key
    if not isinstance(subject, str) or ":" not in subject:
        return "its subject is not a digest this validator can hash"
    try:
        subject_bytes = bytes.fromhex(subject.split(":", 1)[1])
    except ValueError:
        return "its subject is not valid hex"
    expected = hashlib.sha256(subject_bytes).hexdigest()
    if imprint != expected:
        return ("its message_imprint is not sha256(subject) — the token is "
                "not shown to be about what this attestation asserts")
    return None


#: What each known row fixes beyond the envelope: the digest algorithms it
#: admits, the `proof` keys it requires, and how to tell real material from a
#: stub (ADR-031 decisions 4 and 6, and the 2026-09-07 review correction).
#: "Each known row also fixes the algorithms it admits and the `proof` keys
#: it requires; `rfc3161_timestamp` admits `sha256` and requires
#: `proof.token_der`, `proof.tsa_url` and `proof.message_imprint`."
_ATTESTATION_TYPE_RULES: dict[str, dict[str, Any]] = {
    "rfc3161_timestamp": {
        "algorithms": frozenset({"sha256"}),
        "proof_keys": ("token_der", "tsa_url", "message_imprint"),
        "material": _rfc3161_material_issue,
    },
}



def _sha256_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def commitment_leaf(body: dict) -> str:
    """The leaf digest of a commitment, recomputed from its own fields.

    Only the fields the writer digests enter here, in the order canonical JSON
    imposes — so a document carrying an extra key produces a different leaf and
    the proof stops verifying, which is the behaviour wanted.
    """
    return _sha256_digest(_LEAF_DOMAIN + canonical_json(body))


def _pair(left: str, right: str) -> str:
    return _sha256_digest(_NODE_DOMAIN + left.encode() + b"\x00" + right.encode())


def checkpoint_digest(body: dict) -> str:
    """CP(n) = H(root(n) ‖ CP(n-1) ‖ meta(n)) — the link inside the digest.

    A digest that does not cover its own link leaves the link free to be
    restated, which is the correction ADR-019 had to make to the trace chain.
    """
    return _sha256_digest(_CHECKPOINT_DOMAIN + canonical_json(body))


def _fold_path(leaf: str, path: list) -> str:
    """Walk a proof from leaf to root, honouring the side each sibling sits on.

    A path element whose side is neither `left` nor `right` is refused rather
    than skipped: skipping one yields a shorter walk that can still land on a
    real root, so junk appended to a valid path would verify.
    """
    current = leaf
    for element in path:
        side = element.get("side")
        digest = element.get("digest")
        if side == "left":
            current = _pair(digest, current)
        elif side == "right":
            current = _pair(current, digest)
        else:
            raise ValueError(f"a path element with side {side!r} cannot be walked")
    return current


def validate_commitment(document: dict) -> list[Violation]:
    """A stored commitment envelope: schema, then what a schema cannot say."""
    violations, structural = _j1_violations(document)
    if structural:
        return violations
    violations += _schema_violations(
        load_commitment_schema(), _commitment_validator(), document, "SCHEMA")
    if violations:
        return violations

    body = document["commitment"]
    ack = document.get("witness")
    if ack:
        leaf = commitment_leaf(body)
        if ack["commitment"] != leaf:
            violations.append(Violation(
                "C1", "$.witness.commitment",
                f"this acknowledgement names {ack['commitment']}, and the "
                f"commitment beside it digests to {leaf}. An acknowledgement "
                "that does not name what it acknowledges is decoration, and it "
                "would carry a witnessed receipt's authority"))
    return violations


def validate_checkpoint(document: dict) -> list[Violation]:
    """One sealed window, checked for what its own numbers must agree about."""
    violations, structural = _j1_violations(document)
    if structural:
        return violations
    violations += _schema_violations(
        load_checkpoint_schema(), _checkpoint_validator(), document, "SCHEMA")
    if violations:
        return violations
    return _checkpoint_semantics(document)


def _checkpoint_semantics(document: dict) -> list[Violation]:
    """K-rules on a checkpoint, wherever it is standing.

    Split out so an embedded checkpoint in a receipt is held to exactly what a
    standalone one is. It was not, and a receipt could therefore legitimize a
    checkpoint the checkpoint entry point rejects.
    """
    violations: list[Violation] = []
    span = document["last_sequence"] - document["first_sequence"] + 1
    if span != document["count"]:
        violations.append(Violation(
            "K1", "$.count",
            f"this checkpoint says it sealed {document['count']} commitments "
            f"over sequences {document['first_sequence']}.."
            f"{document['last_sequence']}, which spans {span}. A window whose "
            "count and range disagree has had something added or removed, and "
            "the count is the half a reader trusts"))

    if document["index"] == 0 and document["previous"] is not None:
        violations.append(Violation(
            "K2", "$.previous",
            "checkpoint 0 links to a predecessor, and there is nothing before "
            "the first window. A chain that begins by pointing backwards is "
            "either not the beginning or not this chain"))
    if document["index"] > 0 and document["previous"] is None:
        violations.append(Violation(
            "K2", "$.previous",
            f"checkpoint {document['index']} states no predecessor. Chaining "
            "is what makes a whole window impossible to drop after the fact, "
            "and a null link at a non-zero index drops every window before it"))
    return violations


def _entry_violations(entry: dict, where: str, expect_kind: str) -> list[Violation]:
    """One commitment, its path and its checkpoint, checked by recomputation.

    The checkpoint embedded here is put through the SAME semantics as a
    standalone one. Without that, a receipt could legitimize a checkpoint the
    checkpoint entry point rejects — count 99 over a range of three, or index 1
    linking to nothing — because the P-rules only ever looked at the fields
    they happened to need.
    """
    out: list[Violation] = []
    body = entry["commitment"]
    checkpoint = entry["checkpoint"]
    leaf = commitment_leaf(body)

    out += [Violation(v.rule, f"{where}.checkpoint{v.path[1:]}", v.message)
            for v in _checkpoint_semantics(checkpoint)]

    if body["kind"] != expect_kind:
        out.append(Violation(
            "P6", f"{where}.commitment.kind",
            f"this position holds the run's {expect_kind.upper()} and carries a "
            f"{body['kind'].upper()}. A receipt that pairs the wrong ends "
            "describes a run nobody executed"))

    try:
        reached = _fold_path(leaf, entry["proof"])
    except ValueError as exc:
        reached = None
        out.append(Violation("P1", f"{where}.proof", str(exc)))
    if reached is not None and reached != checkpoint["window_root"]:
        out.append(Violation(
            "P1", f"{where}.proof",
            f"this path takes the commitment to {reached}, and the checkpoint "
            f"sealed {checkpoint['window_root']}. A proof that lands anywhere "
            "else proves a different window"))

    if not (checkpoint["first_sequence"] <= body["sequence"]
            <= checkpoint["last_sequence"]):
        out.append(Violation(
            "P2", f"{where}.commitment.sequence",
            f"sequence {body['sequence']} is outside the range this checkpoint "
            f"sealed ({checkpoint['first_sequence']}.."
            f"{checkpoint['last_sequence']}). A commitment proved against a "
            "window it was never in is a proof about somebody else"))
    if (body["tenant"], body["writer_id"]) != (checkpoint["tenant"],
                                               checkpoint["writer_id"]):
        out.append(Violation(
            "P2", f"{where}.commitment.writer_id",
            f"this commitment belongs to "
            f"{body['tenant']}/{body['writer_id']} and the checkpoint to "
            f"{checkpoint['tenant']}/{checkpoint['writer_id']}. One chain per "
            "writer (ADR-021 decision 5), so these are two chains"))

    ack = entry.get("witness")
    if ack and ack["commitment"] != leaf:
        out.append(Violation(
            "C1", f"{where}.witness.commitment",
            f"this acknowledgement names {ack['commitment']}, and the "
            f"commitment beside it digests to {leaf}. An acknowledgement that "
            "does not name what it acknowledges is decoration, and it would "
            "carry a witnessed receipt's authority"))
    return out


def validate_receipt(document: dict) -> list[Violation]:
    """A run, as the chain of segments it actually was.

    ADR-021 decision 7: a receipt carrying only the checkpoint and the
    transaction proves a checkpoint and not a run. ADR-023 decision 1: the last
    segment's root already binds every predecessor transitively, so a receipt
    that can hold only one segment cannot present what it is claiming.

    Every rule here answers a question a verifier must not answer from the
    document's own say-so: does this path reach that root, is this commitment
    inside that range, does this segment continue the one before it, does this
    anchor name a checkpoint that is actually here, and is the mode it claims
    supported by what it carries.
    """
    violations, structural = _j1_violations(document)
    if structural:
        return violations
    violations += _schema_violations(
        load_receipt_schema(), _receipt_validator(), document, "SCHEMA")
    if violations:
        return violations

    segments = document["segments"]
    execution = document.get("execution")
    if execution is not None:
        ref = execution["ref"]
        parts = ref.split("/") if isinstance(ref, str) else []
        malformed = (len(parts) != 3 or any(not part for part in parts)
                     or not parts[2].isdigit())
        sequence = None
        if not malformed:
            try:
                sequence = int(parts[2])
            except ValueError:
                # Python bounds the number of decimal digits accepted by int;
                # an execution ref is untrusted contract input and must become
                # P7, never an exception escaping the validator.
                malformed = True
            else:
                malformed = str(sequence) != parts[2]
        first_commitment = segments[0]["begin"]["commitment"]
        if malformed or (parts[0], parts[1], sequence) != (
                first_commitment["tenant"], first_commitment["writer_id"],
                first_commitment["sequence"]):
            violations.append(Violation(
                "P7", "$.execution.ref",
                "execution.ref must be a canonical tenant/writer/sequence "
                "coordinate naming the receipt's original BEGIN"))
        if "end" not in segments[-1] and execution["fingerprint"] is not None:
            violations.append(Violation(
                "P8", "$.execution.fingerprint",
                "an unfinished receipt has no END and must carry a null "
                "execution.fingerprint"))

    for index, segment in enumerate(segments):
        where = f"$.segments[{index}]"
        violations += _entry_violations(segment["begin"], f"{where}.begin", "begin")
        if "end" in segment:
            violations += _entry_violations(segment["end"], f"{where}.end", "end")
            if index != len(segments) - 1:
                violations.append(Violation(
                    "P6", f"{where}.end",
                    "a segment with an END has a successor in this receipt, and "
                    "a trace that reached a terminal record cannot be resumed. "
                    "Either this is not the run's order, or these segments "
                    "belong to different runs"))
            if segment["end"]["commitment"]["run_id"] != \
                    segment["begin"]["commitment"]["run_id"]:
                violations.append(Violation(
                    "P6", f"{where}.end.commitment.run_id",
                    f"this segment begins as "
                    f"{segment['begin']['commitment']['run_id']!r} and ends as "
                    f"{segment['end']['commitment']['run_id']!r}. Two runs, not "
                    "one segment"))

    for index in range(1, len(segments)):
        this = segments[index]["begin"]["commitment"]
        before = segments[index - 1]["begin"]["commitment"]
        link = this.get("continues")
        if link is None:
            violations.append(Violation(
                "P6", f"$.segments[{index}].begin.commitment.continues",
                "this segment follows another in the receipt and states that it "
                "continues nothing. A chain asserted by position and by nothing "
                "else is a chain the holder arranged"))
            continue
        if link["run_id"] != before["run_id"]:
            violations.append(Violation(
                "P6", f"$.segments[{index}].begin.commitment.continues.run_id",
                f"this segment continues {link['run_id']!r}, and the segment "
                f"before it in this receipt is {before['run_id']!r}"))
        elif link.get("writer_id") is not None and (
                link["writer_id"] != before["writer_id"]
                or link["sequence"] != before["sequence"]):
            violations.append(Violation(
                "P6", f"$.segments[{index}].begin.commitment.continues.sequence",
                f"this segment names {link['writer_id']}/{link['sequence']} as "
                f"its predecessor, and the segment before it in this receipt is "
                f"{before['writer_id']}/{before['sequence']}"))

    mode = document["mode"]
    if mode in ("witnessed", "qualified"):
        for index, segment in enumerate(segments):
            if "witness" not in segment["begin"]:
                violations.append(Violation(
                    "P3", f"$.segments[{index}].begin",
                    f"this receipt claims {mode} but segment {index}'s BEGIN "
                    "carries no acknowledgement. Every included segment must "
                    "support the claimed assurance mode"))
    if mode == "qualified":
        violations.append(Violation(
            "P3", "$.mode",
            "QUALIFIED needs an identity attestation and a qualified timestamp "
            "(ADR-020 levels 6 and 7), and this distribution produces neither. "
            "A mode nothing here can establish must not be claimed here"))

    present = {checkpoint_digest(entry["checkpoint"])
               for segment in segments
               for entry in (segment["begin"], segment.get("end"))
               if entry is not None}
    for index, anchor in enumerate(document.get("anchors", [])):
        if anchor["checkpoint"] not in present:
            violations.append(Violation(
                "P4", f"$.anchors[{index}].checkpoint",
                f"this anchor published {anchor['checkpoint']}, and no "
                "checkpoint in this receipt digests to it. An anchor for a "
                "checkpoint that is not here says nothing about this run"))
        if anchor["network"] not in KNOWN_ANCHOR_NETWORKS:
            violations.append(Violation(
                "P5", f"$.anchors[{index}].network",
                f"this validator cannot evaluate the network "
                f"{anchor['network']!r}, so it will not report what an anchor "
                "there establishes. Known: "
                f"{', '.join(sorted(KNOWN_ANCHOR_NETWORKS))}"))
        else:
            issue = _anchor_reference_issue(anchor["network"], anchor.get("reference"))
            if issue is not None:
                violations.append(Violation(
                    "P5", f"$.anchors[{index}].reference",
                    f"{anchor['reference']!r} is not in the form "
                    f"{anchor['network']!r} uses ({issue}). A reference that "
                    "is not in the network's own form is not a reference"))

    # Attestations — the second container (ADR-031), and a second binding rule
    # and a second known-set, mirroring P4/P5 so a reader who knows anchors
    # knows attestations. What the END commits to is the run root: a subject
    # that is neither a checkpoint of this receipt nor that root asserts about
    # a digest that is not here, and says nothing about this run.
    last_segment_end = segments[-1].get("end")
    run_root = (last_segment_end["commitment"]["root"]
                if last_segment_end is not None else None)
    seen_attestation_ids: set[str] = set()
    for index, attestation in enumerate(document.get("attestations", [])):
        attestation_id = attestation["attestation_id"]
        if attestation_id in seen_attestation_ids:
            violations.append(Violation(
                "P10", f"$.attestations[{index}].attestation_id",
                f"attestation_id {attestation_id!r} used twice in this "
                "receipt. Two attestations sharing an identifier are two "
                "records a reader cannot tell apart, and a re-used id can "
                "shadow one attestation's proof under another's name"))
        seen_attestation_ids.add(attestation_id)
        if (attestation["subject"] not in present
                and attestation["subject"] != run_root):
            violations.append(Violation(
                "P9", f"$.attestations[{index}].subject",
                f"this attestation asserts {attestation['subject']}, and no "
                "checkpoint in this receipt digests to it and it is not the "
                "run root this receipt's END commits to. An assertion about a "
                "digest that is not here says nothing about this run"))
        kind = attestation["type"]
        rules = _ATTESTATION_TYPE_RULES.get(kind)
        if kind not in KNOWN_ATTESTATION_TYPES:
            violations.append(Violation(
                "P10", f"$.attestations[{index}].type",
                f"this validator cannot evaluate the attestation type "
                f"{kind!r}, so it will not report what an attestation of that "
                "type establishes. Known: "
                f"{', '.join(sorted(KNOWN_ATTESTATION_TYPES))}"))
        else:
            if attestation["algorithm"] not in rules["algorithms"]:
                violations.append(Violation(
                    "P10", f"$.attestations[{index}].algorithm",
                    f"attestation type {kind!r} admits "
                    f"{', '.join(sorted(rules['algorithms']))} and this one "
                    f"carries {attestation['algorithm']!r}, which this "
                    "validator cannot read. Unreadable cryptography must not "
                    "sit on the same footing as checked cryptography"))
            material_issue = rules["material"](
                attestation["subject"], attestation["proof"])
            if material_issue:
                violations.append(Violation(
                    "P10", f"$.attestations[{index}].proof",
                    f"an attestation of type {kind!r} carries proof material "
                    f"this validator will not trust: {material_issue}. A "
                    "token nobody can verify is not an attestation"))
    return violations



# --------------------------------------------------------------------------- #
# The verifier: what a receipt and its trace let somebody establish            #
# --------------------------------------------------------------------------- #
#
# ADR-021 decision 8 and ADR-020's seven levels. Two rules govern everything
# below and they pull in the same direction:
#
#   * it REFUSES rather than guesses. An unknown hash algorithm, anchor network
#     or attestation type ends the answer — "a VERIFIED on a chain the verifier
#     cannot evaluate is the worst lie this system can tell";
#   * it says what it could NOT establish, and why, in the same breath as what
#     it could. A report that lists only successes is read as a clean bill.
#
# It needs no network for INTEGRITY. For EXISTENCE it needs the chain, and not
# us.

ESTABLISHED = "established"
NOT_ESTABLISHED = "not established"
NOT_YET = "not yet"
UNCHECKED = "claimed, unchecked"
REFUSED = "refused"

#: The levels of ADR-020, in order. Every report answers all seven, because a
#: level omitted reads as a level passed.
LEVELS = (
    "INTEGRITY", "EXISTENCE", "RETENTION", "EXECUTION_CONTINUITY",
    "PROVENANCE", "IDENTITY", "LEGAL_TIME",
)

KNOWN_DIGEST_PREFIX = "sha256:"

#: Signature algorithms this verifier is competent to name. ADR-021 decision 8
#: refuses an unknown ATTESTATION TYPE, and an acknowledgement signed with
#: something we cannot name is one: reporting it as "present but unchecked"
#: would put an unreadable object on the same footing as a readable one.
KNOWN_WITNESS_ALGORITHMS = frozenset({"ed25519"})

#: Where a reader can look a published anchor up for themselves. This verifier
#: does not contact any network — see `verify` — so what it can offer instead
#: is the address of the thing it declined to check.
#: How a reader settles for themselves what this verifier declined to check.
#: A URL for a chain with a block explorer; an instruction for one where the
#: answer is not a web page. Writing both as URLs would have been tidier and
#: would have sent an OpenTimestamps holder looking for a transaction that does
#: not exist — an `.ots` attestation says the commitment sits under a block's
#: merkle root, and settling it means running the proof against a Bitcoin node.
ANCHOR_LOOKUPS = {
    "tron:nile": lambda ref: f"https://nile.tronscan.org/#/transaction/{ref}",
    "opentimestamps:bitcoin": lambda ref: (
        f"{ref} — verify the .ots proof in `anchor.proof.serialized` against a "
        "Bitcoin node (`ots verify`); there is no transaction to open, the "
        "attestation is to a block's merkle root"
    ),
}


@dataclass(frozen=True)
class Finding:
    """One level, what came of it, and the sentence a reader is owed."""

    level: str
    status: str
    reason: str


@dataclass(frozen=True)
class Verdict:
    """The whole answer, including the parts that are refusals.

    `refused` is not a level that failed — it is the verifier declining to
    answer at all, and it makes every level below it unreportable.
    """

    findings: tuple[Finding, ...]
    violations: tuple[Violation, ...]
    notes: tuple[str, ...]

    @property
    def refused(self) -> bool:
        return any(f.status == REFUSED for f in self.findings)

    def status_of(self, level: str) -> str:
        for finding in self.findings:
            if finding.level == level:
                return finding.status
        raise KeyError(level)


def derived_root(doc: dict) -> str | None:
    """The root a trace DERIVES, recomputed here, or None.

    ADR-019. Reading `records[-1].integrity.payload_hash` is not deriving it,
    and the difference is three attacks: a rewritten header with a stale first
    link, a 2.0.0 document relabelled 3.0.0, and any invented hash at all.

    Re-implemented rather than imported for the reason every digest in this
    file is: the contract is the authority, and a validator that called the
    implementation would agree with any drift.
    """
    version = doc.get("schema_version")
    if version not in ("3.0.0", "3.1.0", "3.2.0"):
        # Below 3.0.0 the digests do not cover prev_hash, so the terminal's
        # hash covers one record rather than the run. There is no root to have.
        return None
    records = doc.get("records")
    if not isinstance(records, list) or not records:
        return None
    last = records[-1]
    if not isinstance(last, dict) or last.get("kind") not in (
            "run_completed", "run_failed", "run_cancelled"):
        return None

    # `document_version_header(doc)` is provably "3.0.0" here — the gate above
    # returned for every other value — so a mutant hard-coding it survives the
    # whole suite. That is an EQUIVALENT mutant and not a coverage gap: do not
    # invent a test for it. The call stays because the header digest's inputs
    # should read as the header's fields rather than as constants, and because
    # a later version admitted to the gate must change this line with it.
    expected_prev = "sha256:" + hashlib.sha256(canonical_json({
        "schema_version": document_version_header(doc),
        "run": doc.get("run") or {},
    })).hexdigest()
    for record in records:
        if not isinstance(record, dict):
            return None
        integrity = record.get("integrity")
        if not isinstance(integrity, dict):
            return None
        if integrity.get("prev_hash") != expected_prev:
            return None
        payload = dict(record)
        payload["integrity"] = {"payload_hash": None, "prev_hash": expected_prev}
        digest = "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()
        if integrity.get("payload_hash") != digest:
            return None
        expected_prev = digest
    # `expected_prev` is the last record's digest, which the loop has just
    # asserted equals its stored payload_hash — so returning that field instead
    # is provably the same value here, and a mutation probe swapping them
    # survives. Recorded rather than papered over with a contrived test: it is
    # an equivalent mutant, not a gap. What makes reading-back wrong is doing
    # it WITHOUT this walk, which is the version ADR-019 was written against.
    return expected_prev


def _unknown_digests(receipt: dict) -> list[str]:
    """Every digest in the receipt whose algorithm this verifier does not know.

    Collected before anything is checked, because the fail-closed rule is about
    what the verifier is competent to evaluate, not about whether the values
    happen to line up.
    """
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str) and path.endswith(
                ("digest", "window_root", "previous", "root", "checkpoint")):
            if node and not node.startswith(KNOWN_DIGEST_PREFIX):
                found.append(f"{path} = {node}")

    walk(receipt, "$")
    return found


def verify(receipt: dict, trace: dict | None = None) -> Verdict:
    """What this receipt — with this trace, if one is supplied — establishes.

    The trace is optional and its absence is reported rather than assumed away:
    without it, INTEGRITY is about the receipt's own arithmetic and says
    nothing about any run.
    """
    violations = validate_receipt(receipt)
    notes: list[str] = []
    findings: list[Finding] = []

    def add(level: str, status: str, reason: str) -> None:
        findings.append(Finding(level, status, reason))

    if not isinstance(receipt, dict) or (
            violations and not isinstance(receipt.get("segments"), list)):
        # The refusal checks below read fields, and a document that failed the
        # schema may not have them. `verify([])` used to raise AttributeError
        # where the plain validator printed the violation and exited 1.
        for level in LEVELS:
            add(level, NOT_ESTABLISHED,
                "this document is not a receipt this validator can read; "
                "nothing is established over a shape that failed the schema")
        return Verdict(tuple(findings), tuple(violations), tuple(notes))

    unknown = _unknown_digests(receipt)
    if unknown:
        for level in LEVELS:
            add(level, REFUSED,
                "this receipt carries a digest in an algorithm this verifier "
                f"cannot recompute ({unknown[0]}). Reporting anything about it "
                "would be reporting about arithmetic nobody here performed")
        return Verdict(tuple(findings), tuple(violations), tuple(notes))

    # A REFUSAL OUTRANKS A VIOLATION, and the first version had it the other
    # way round. An unknown network is also a P5 violation, so the report said
    # "not established, the document is wrong" for a document that may be
    # perfectly correct on a chain we simply cannot read. Those are different
    # answers, and the wrong one is the one that sounds like a finding.
    for anchor in receipt.get("anchors") or []:
        if isinstance(anchor, dict) and \
                anchor.get("network") not in KNOWN_ANCHOR_NETWORKS:
            for level in LEVELS:
                add(level, REFUSED,
                    f"this receipt is anchored on {anchor.get('network')!r}, "
                    "which this verifier cannot evaluate. Known: "
                    f"{', '.join(sorted(KNOWN_ANCHOR_NETWORKS))}")
            return Verdict(tuple(findings), tuple(violations), tuple(notes))

    segments_field = receipt.get("segments")
    for index, segment in enumerate(
            segments_field if isinstance(segments_field, list) else []):
        for position in ("begin", "end"):
            if not isinstance(segment, dict):
                continue
            entry = segment.get(position)
            ack = entry.get("witness") if isinstance(entry, dict) else None
            if isinstance(ack, dict) and \
                    ack.get("algorithm") not in KNOWN_WITNESS_ALGORITHMS:
                for level in LEVELS:
                    add(level, REFUSED,
                        f"segment {index}'s {position.upper()} carries an "
                        f"acknowledgement signed with "
                        f"{ack.get('algorithm')!r}, which this verifier cannot "
                        "name. An attestation type we cannot read must not be "
                        "put on the same footing as one we can. Known: "
                        f"{', '.join(sorted(KNOWN_WITNESS_ALGORITHMS))}")
                return Verdict(tuple(findings), tuple(violations), tuple(notes))

    # P10 as a refusal, exactly the P5 pattern: the violation above says the
    # document broke the rule; this says the verifier cannot evaluate it at
    # all, and a refusal outranks a violation — a document may be perfectly
    # correct under a type or algorithm we cannot read, and unreadable
    # cryptography must never sit on the same footing as checked cryptography.
    for index, attestation in enumerate(receipt.get("attestations") or []):
        if not isinstance(attestation, dict):
            continue
        kind = attestation.get("type")
        rules = _ATTESTATION_TYPE_RULES.get(kind)
        if kind not in KNOWN_ATTESTATION_TYPES:
            reason = (f"this receipt carries an attestation of type {kind!r}, "
                      "which this verifier cannot evaluate. Known: "
                      f"{', '.join(sorted(KNOWN_ATTESTATION_TYPES))}")
        else:
            algorithm = attestation.get("algorithm")
            if algorithm not in rules["algorithms"]:
                reason = (f"this receipt carries an attestation of type "
                          f"{kind!r} imprinted with {algorithm!r}, which this "
                          "verifier cannot name. That type admits: "
                          f"{', '.join(sorted(rules['algorithms']))}")
            else:
                material_issue = rules["material"](
                    attestation.get("subject"), attestation.get("proof"))
                if material_issue is None:
                    continue
                reason = (f"this receipt carries an attestation of type "
                          f"{kind!r} whose proof this verifier will not "
                          f"trust: {material_issue}. A token nobody can "
                          "verify is not an attestation this verifier will "
                          "report on")
        for level in LEVELS:
            add(level, REFUSED, reason)
        return Verdict(tuple(findings), tuple(violations), tuple(notes))

    if violations:
        for level in LEVELS:
            add(level, NOT_ESTABLISHED,
                f"the receipt does not satisfy the contract "
                f"({len(violations)} violation(s)); nothing is established over "
                "a document that is not what it says")
        return Verdict(tuple(findings), tuple(violations), tuple(notes))

    segments = receipt["segments"]
    last = segments[-1]
    anchors = receipt.get("anchors", [])

    # -- INTEGRITY ---------------------------------------------------------
    derived = derived_root(trace) if trace is not None else None
    if trace is None:
        add("INTEGRITY", NOT_ESTABLISHED,
            "no trace was supplied. The receipt's own arithmetic checks out, "
            "which says the commitments are internally consistent and nothing "
            "at all about a run")
    elif "end" not in last:
        add("INTEGRITY", NOT_ESTABLISHED,
            "the run's last segment has no END, so there is no root in the "
            "receipt to compare this trace against")
    else:
        computed = derived
        claimed = last["end"]["commitment"]["root"]
        trace_run = (trace.get("run") or {}).get("run_id")
        receipt_run = last["end"]["commitment"]["run_id"]
        if receipt_run != trace_run:
            # Comparing roots alone is not enough: a holder can pair a receipt
            # about run A with a valid trace for run B, and the roots would be
            # compared without either document ever claiming to be about the
            # other. The root says WHAT was executed; the run_id says WHOSE.
            add("INTEGRITY", NOT_ESTABLISHED,
                f"this receipt is about run {receipt_run!r} and this trace is "
                f"run {trace_run!r}. They are documents about two different "
                "executions, whatever their roots do")
        elif computed is None:
            add("INTEGRITY", NOT_ESTABLISHED,
                "this trace derives no root: it is unfinished, below schema "
                "3.0.0, or its own chain does not recompute. The receipt is "
                "not the thing at fault here")
        elif computed != claimed:
            add("INTEGRITY", NOT_ESTABLISHED,
                f"this trace derives {computed} and the receipt's END commits "
                f"to {claimed}. They are two different documents")
        else:
            add("INTEGRITY", ESTABLISHED,
                "the trace's chain recomputes to the root the END committed "
                "to, and the END is inside a sealed window whose root the "
                "proof reaches")

    # P8 is separate from the END comparison: execution is a public locator
    # and must agree with the paired trace even when the receipt's END field is
    # also edited.  A missing trace supplies no root to compare.
    if (trace is not None and isinstance(receipt.get("execution"), dict)
            and "end" in last):
        claimed_execution = receipt["execution"].get("fingerprint")
        if claimed_execution != derived:
            violations.append(Violation(
                "P8", "$.execution.fingerprint",
                f"execution.fingerprint is {claimed_execution!r}, but the "
                f"paired trace derives {derived!r}"))
            findings = [Finding(f.level,
                                NOT_ESTABLISHED if f.level == "INTEGRITY" else f.status,
                                f.reason) for f in findings]

    # -- EXISTENCE and RETENTION ------------------------------------------
    covered = {checkpoint_digest(entry["checkpoint"])
               for segment in segments
               for entry in (segment["begin"], segment.get("end"))
               if entry is not None}
    published = [a for a in anchors
                 if a["state"] == "anchored" and a["checkpoint"] in covered]
    pending = [a for a in anchors if a["state"] == "pending"]
    attestations = receipt.get("attestations")

    # **A claim is a claim about what its subject covers.** ADR-031 decision 7
    # (review correction 1) binds the verdict to the subject: EXISTENCE *of the
    # execution* is reported only when the subject is the run root or a
    # checkpoint whose sealed range includes the END. A checkpoint that seals
    # only the BEGIN, on a *completed* receipt, can support "existence of the
    # BEGIN no later than T" and nothing further — for an anchor and for an
    # attestation alike, as the ADR says it for both.
    last_end_entry = segments[-1].get("end")
    end_checkpoint = (checkpoint_digest(last_end_entry["checkpoint"])
                      if last_end_entry is not None else None)
    run_root = (last_end_entry["commitment"]["root"]
                if last_end_entry is not None else None)

    def _covers_execution(subject: Any) -> bool:
        """Does this subject name the execution rather than only its BEGIN?"""
        return (last_end_entry is not None and subject is not None
                and (subject == run_root or subject == end_checkpoint))

    def _anchor_covers_execution(checkpoint: Any) -> bool:
        """Same question, for an anchor, with one exemption ADR-031 correction
        1 makes explicit ("for a completed receipt"): a run still in flight
        has no END for a checkpoint to fail to cover, so an anchor over its
        only checkpoint (the BEGIN) is the whole claim it can make — exactly
        the pre-ADR-031 anchor path, unamended. Without this exemption an
        anchor recorded before the run finished would be downgraded to a
        sentence about coverage that does not apply to it yet.
        """
        return last_end_entry is None or _covers_execution(checkpoint)

    anchors_covering = [a for a in published
                        if _anchor_covers_execution(a["checkpoint"])]
    anchors_begin_only = [a for a in published
                          if not _anchor_covers_execution(a["checkpoint"])]

    execution_claims: list[str] = []
    begin_claims: list[str] = []

    if anchors_covering:
        # **This verifier contacts no network, and an anchor is a CLAIM until
        # somebody does.** ADR-021 decision 8 is explicit that for EXISTENCE it
        # needs the chain and not us — and the first version of this function
        # read `state: "anchored"` out of the receipt and reported EXISTENCE
        # established, which let a holder mint the property by typing it. An
        # allow-listed network says we could evaluate that chain, never that we
        # did.
        #
        # So the status is CLAIMED, and what this verifier can offer instead
        # of a verdict is the address of the thing it declined to check.
        # Contacting the chain belongs in the anchor plugs (phase 3), not in a
        # contract validator that must run offline and stdlib-only.
        # A reference that fails `_anchor_reference_issue` never reaches this
        # loop at all: it is a P5 violation, and `verify` already returns
        # "does not satisfy the contract" for every level the moment
        # `violations` is non-empty (above). So the URL below is always built
        # from a reference that PASSED the shape check — the property the
        # ADR-031 review asked for — without a second guard here that no
        # input could ever exercise.
        lookups = []
        for anchor in anchors_covering:
            resolve = ANCHOR_LOOKUPS.get(anchor["network"])
            detail = (resolve(anchor["reference"])
                      if resolve else repr(anchor["reference"]))
            lookups.append(f"{anchor['network']} {detail}")
        where = "; ".join(lookups)
        execution_claims.append(
            f"this receipt CLAIMS publication at {where}. This verifier "
            "contacts no network, so it has not confirmed that transaction "
            "exists or that it commits to this checkpoint. Look it up and the "
            "answer is yours, not ours — which is the point")
    if anchors_begin_only:
        begin_claims.append(
            "the anchored checkpoint does not cover this receipt's END, so the "
            "publication can only support existence of the BEGIN no later than "
            "T, and says nothing about the completed execution")
    for attestation in attestations or []:
        subject = attestation.get("subject")
        if _covers_execution(subject):
            # **An attestation is CLAIMED, never verified.** ADR-031 decision 5:
            # this verifier holds no TSA key, checks no CMS signature and
            # contacts no network — same posture as an anchor, same reason.
            # What it CAN hand the reader is the issuer and the incantation
            # that would settle it without us.
            execution_claims.append(
                f"this attestation by {attestation['issuer']!r} CLAIMS the "
                f"execution existed no later than "
                f"{attestation['issued_at']}, from "
                f"{attestation['proof'].get('tsa_url')}: its "
                "`proof.token_der` is the complete TimeStampResp and its "
                "`proof.message_imprint` is sha256 of this subject's digest "
                "bytes — the double hash RFC 3161 itself requires, not this "
                "validator's choice — so a reader checks it with `openssl ts "
                "-reply -in response.tsr -token_out -out token.p7`, then "
                "`openssl pkcs7 -inform DER -in token.p7 -print_certs -out "
                "certs.pem`, then `openssl ts -verify -in response.tsr "
                f"-digest {attestation['proof'].get('message_imprint')} "
                "-CAfile certs.pem` — claimed, never checked here")
        else:
            if last_end_entry is None:
                scope = ("the run reached no END, so the claim is about the "
                         "run's beginning, not about a completion")
            else:
                scope = ("its subject is a checkpoint that does not seal this "
                         "receipt's END, so the execution's existence is not "
                         "established by it")
            begin_claims.append(
                f"this attestation of {attestation['issuer']!r} asserts "
                "existence of the BEGIN no later than "
                f"{attestation['issued_at']}: {scope}")

    if execution_claims or begin_claims:
        if execution_claims or last_end_entry is None:
            add("EXISTENCE", UNCHECKED, " ".join(execution_claims + begin_claims))
        else:
            add("EXISTENCE", NOT_ESTABLISHED,
                " ".join(execution_claims + begin_claims))
    elif pending:
        add("EXISTENCE", NOT_YET,
            "an anchor is recorded as `pending`. An intention to publish is "
            "not a publication, and until it confirms this receipt supports "
            "INTEGRITY and nothing more")
    else:
        add("EXISTENCE", NOT_ESTABLISHED,
            "no anchor is present. `LOCAL` means written, never published — "
            "ADR-020: \"checkpointed\" means externally anchored, and a local "
            "chain proves nothing to a third party")

    # RETENTION is an anchor's and only an anchor's (ADR-031 decision 4: the
    # rfc3161_timestamp row supports level 2 EXISTENCE alone). An anchored
    # checkpoint that does not seal the END has published nothing that resists
    # rewriting the run's completion.
    if anchors_covering:
        add("RETENTION", UNCHECKED,
            "it rests entirely on the anchor above. If that transaction is "
            "real and carries this checkpoint, removing or reordering the "
            "commitment would mean rewriting a chain that is already "
            "published; if it is not, nothing here has left its author")
    elif published:
        add("RETENTION", NOT_ESTABLISHED,
            "the published anchor does not cover this receipt's END, so the "
            "completion could still be removed or reordered without breaking "
            "a chain that is already published. RETENTION for this run is "
            "not established")
    elif pending:
        add("RETENTION", NOT_YET,
            "the checkpoint has not left the operator's control yet, and a "
            "checkpoint that has not left is one more file its author can "
            "rewrite")
    else:
        add("RETENTION", NOT_ESTABLISHED,
            "nothing here has left the operator's machine, so nothing here "
            "resists its author")

    # -- EXECUTION_CONTINUITY ---------------------------------------------
    first_begin = segments[0]["begin"]
    ack = first_begin.get("witness")
    if ack is None:
        add("EXECUTION_CONTINUITY", NOT_ESTABLISHED,
            "the run's first BEGIN carries no acknowledgement. The property "
            "holds when the BEGIN left the operator's unilateral control "
            "before the outcome was known, and a local write does not do that")
    else:
        add("EXECUTION_CONTINUITY", NOT_ESTABLISHED,
            f"an acknowledgement from {ack['witness_id']!r} is present and "
            "bound to this commitment, and **its signature was not checked**: "
            "this verifier holds no key for that witness. Binding is what "
            "makes the signature checkable by somebody who does; it is not a "
            "substitute for checking it")

    # -- the levels this distribution cannot reach ------------------------
    add("PROVENANCE", NOT_ESTABLISHED,
        "no signature over this evidence was verified. This distribution "
        "produces no keys and this verifier was given none")
    add("IDENTITY", NOT_ESTABLISHED,
        "binding a key to a legal entity is ADR-020 level 6, which is sold "
        "rather than shipped, and nothing here attempts it")
    add("LEGAL_TIME", NOT_ESTABLISHED,
        "a qualified timestamp is ADR-020 level 7 and needs a QTSP. The times "
        "in this receipt are the writer's own clock")

    # -- what the reader must not be allowed to misread --------------------
    unfinished = [i for i, s in enumerate(segments) if "end" not in s]
    if unfinished:
        notes.append(
            "segment(s) " + ", ".join(str(i) for i in unfinished) +
            " have a BEGIN and no END. That is AN EXECUTION THAT LEFT NO "
            "COMPLETION, and it is never a finding of suppression: a process "
            "can die, a stream driver can be abandoned mid-iteration, and "
            "ADR-020 declares that class rather than discovering it. It is a "
            "question, not a verdict.")
    first_continuation = segments[0]["begin"]["commitment"].get("continues")
    if len(segments) > 1 or first_continuation is not None:
        if first_continuation is not None and len(segments) == 1:
            notes.append(
                "the first included segment continues a predecessor whose "
                "BEGIN is not present in this receipt or available from this "
                "log; that predecessor is omitted and the continuation claim "
                "is UNVERIFIABLE rather than false")
        notes.append(
            f"this run has {len(segments)} segments. Each one's claim to "
            "continue its predecessor is checkable only against that "
            "predecessor's whole BUNDLE — bundle_version, the complete "
            "graph_spec and the trace. Whoever holds only this receipt cannot "
            "check it, and its absence makes the claim UNVERIFIABLE rather "
            "than false.")
    if trace is not None and trace.get("run", {}).get("resume"):
        stated = trace["run"]["resume"]["bundle_fingerprint"]
        link = segments[-1]["begin"]["commitment"].get("continues") or {}
        if link.get("bundle_fingerprint") != stated:
            violations = violations + [Violation(
                "V1", "$.segments[-1].begin.commitment.continues",
                f"this trace resumed from {stated} and the receipt's last "
                f"segment says it continued {link.get('bundle_fingerprint')}. "
                "The receipt is not this trace's")]
            findings = [Finding(f.level, NOT_ESTABLISHED, f.reason)
                        if f.level == "INTEGRITY" else f for f in findings]

    return Verdict(tuple(findings), tuple(violations), tuple(notes))


def format_verdict(verdict: Verdict) -> str:
    """The report, written so the refusals are as loud as the successes."""
    lines: list[str] = []
    for violation in verdict.violations:
        lines.append(f"{violation.rule} {violation.path}: {violation.message}")
    if verdict.violations:
        lines.append("")
    width = max(len(level) for level in LEVELS)
    for finding in verdict.findings:
        lines.append(f"  {finding.level.ljust(width)}  {finding.status.upper()}")
        lines.append(f"  {' ' * width}  {finding.reason}")
    for note in verdict.notes:
        lines.append("")
        lines.append(f"  NOTE  {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        # Left to argparse rather than hardcoded: this program is now reached
        # three ways — `python contract/validate.py` from a checkout,
        # `python -m vitruvyan_motus.contract.validate` from an install, and
        # the `motus-validate` command. Usage output that names only the first
        # is wrong for the two a consumer actually has.
        prog=None,
        description=(
            "Semantic validator for the Motus contract: GraphSpec R-rules, "
            "trace T-rules, commitment C-rules, checkpoint K-rules, receipt "
            "P-rules, System Manifest SM-rules, JSON document and JSONL "
            "stream forms."
        ),
        epilog=(
            "Prints one line per violation ('RULE path: message') and exits 0 "
            "iff there are none. Exit codes: 0 valid, 1 violations, 2 usage or "
            "I/O errors (including an invalid --spec, reported with 'spec:' "
            "path prefixes)."
        ),
    )
    parser.add_argument(
        "artifact",
        choices=["graphspec", "trace", "jsonl", "commitment", "checkpoint",
                 "receipt", "system-manifest", "package"],
    )
    parser.add_argument("file", help="the document (or JSONL stream) to validate")
    parser.add_argument(
        "--spec",
        help="GraphSpec file enabling the spec-correlated trace rules (T5, T8)",
    )
    parser.add_argument(
        "--trace",
        help=(
            "the trace a receipt is claimed to be about; turns `receipt` into "
            "the verifier of ADR-021 decision 8, which reports what it could "
            "establish per ADR-020's levels and refuses rather than guesses"
        ),
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help=(
            "accept a trace that does not end in a terminal record "
            "(in flight, or recovered after a crash)"
        ),
    )
    args = parser.parse_args(argv)

    if args.artifact not in ("trace", "jsonl") and (args.spec or args.allow_incomplete):
        parser.error("--spec and --allow-incomplete apply to trace/jsonl only")
    if args.trace and args.artifact != "receipt":
        parser.error("--trace applies to receipt only")

    if args.artifact == "package":
        import importlib
        verify_package = importlib.import_module(
            "vitruvyan_motus.evidence").verify_package
        try:
            data = Path(args.file).read_bytes()
        except OSError as exc:
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 2
        package = verify_package(data)
        lines = [f"transport_ok: {package.transport_ok}"]
        if package.damaged:
            lines.append("damaged: " + ", ".join(package.damaged))
        if package.trace_violations:
            lines.append("INTEGRITY VIOLATION")
            lines.extend(package.trace_violations)
        else:
            lines.append("INTEGRITY CLEAN")
        if package.verdict is None:
            lines.extend([
                "EXISTENCE NOT ESTABLISHED",
                "no receipt, checkpoint, or anchor is present; this package "
                "cannot establish existence. An edited trace can be resealed "
                "with a new root, and without an external anchor that edit "
                "cannot be detected.",
            ])
        else:
            lines.append(format_verdict(package.verdict))
        print("\n".join(lines))
        bad = bool(package.damaged) or bool(package.trace_violations) or (
            package.verdict is not None and
            (package.verdict.violations or package.verdict.refused)
        )
        return 1 if bad else 0

    try:
        # Bytes, then an explicit decode — NEVER read_text().  Python's
        # universal-newline handling turns CRLF into LF before the validator
        # can see it, so a physical CRLF file would sail past JSONL3 while the
        # same bytes handed to the API were refused (cross-review v3, MF3-05).
        # The file is what the contract judges; the reader must not launder it.
        raw = Path(args.file).read_bytes().decode("utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as exc:
        print(f"error: {args.file} is not valid UTF-8: {exc}", file=sys.stderr)
        return 2

    spec = None
    if args.spec:
        try:
            spec = _loads_strict(Path(args.spec).read_text(encoding="utf-8"))
        except StrictJSONError as exc:
            # Rule J1 applies to the spec input too; an unusable spec keeps
            # the established exit-2 semantics ("invalid --spec").
            rule = "J2" if isinstance(exc, NonCanonicalNumberError) else "J1"
            print(f"{rule} spec:$: {exc}")
            print(f"error: cannot load --spec {args.spec}: {exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError) as exc:
            print(f"error: cannot load --spec {args.spec}: {exc}", file=sys.stderr)
            return 2
        spec_violations = validate_graphspec(spec)
        if spec_violations:
            for violation in spec_violations:
                print(f"{violation.rule} spec:{violation.path}: {violation.message}")
            print(
                f"error: --spec {args.spec} is not a valid GraphSpec; "
                "refusing to correlate against it",
                file=sys.stderr,
            )
            return 2

    expect_complete = not args.allow_incomplete
    trace_doc: dict[str, Any] | None = None
    if args.artifact == "jsonl":
        violations, _doc = validate_jsonl(raw, spec=spec, expect_complete=expect_complete)
        trace_doc = _doc
    else:
        try:
            doc = _loads_strict(raw)
        except NonCanonicalNumberError as exc:
            # J2 before J1: a non-canonical number IS strict RFC 8259, so
            # reporting it as J1 would name the wrong rule and send a reader to
            # look for a duplicate key or a NaN.
            print(f"J2 $: {exc}")
            return 1
        except StrictJSONError as exc:
            # A parseable-but-non-strict document is a CONTRACT violation
            # (J1), not an I/O problem: report it like any other violation
            # and exit 1.
            print(f"J1 $: {exc}")
            return 1
        except ValueError as exc:
            print(f"error: {args.file} is not valid JSON: {exc}", file=sys.stderr)
            return 2
        if args.artifact == "graphspec":
            violations = validate_graphspec(doc)
        elif args.artifact == "commitment":
            violations = validate_commitment(doc)
        elif args.artifact == "checkpoint":
            violations = validate_checkpoint(doc)
        elif args.artifact == "system-manifest":
            violations = validate_system_manifest(doc)
        elif args.artifact == "receipt":
            trace_side = None
            if args.trace:
                try:
                    trace_side = _loads_strict(
                        Path(args.trace).read_bytes().decode("utf-8"))
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    print(f"error: cannot load --trace {args.trace}: {exc}",
                          file=sys.stderr)
                    return 2
            verdict = verify(doc, trace_side)
            print(format_verdict(verdict))
            # Exit 1 on a receipt that breaks the contract, and ALSO on a
            # refusal: a verifier that could not evaluate what it was given has
            # not said yes, and a caller reading only the exit code must not be
            # able to mistake "I cannot tell" for "verified".
            return 1 if (verdict.violations or verdict.refused) else 0
        else:
            violations = validate_trace(doc, spec=spec, expect_complete=expect_complete)
            trace_doc = doc if isinstance(doc, dict) else None

    for violation in violations:
        print(f"{violation.rule} {violation.path}: {violation.message}")

    # A 2.0.0 trace is not malformed and this is not a violation: it is a
    # truthful record of a real run and exits 0 like any other. What it does not
    # have is an anchorable root — its digests do not cover prev_hash, so the
    # terminal one covers the terminal record and not the run (ADR-019). The
    # only place that fact is any use is beside the value, at the moment someone
    # is deciding what to publish, so it is said here and on stderr, where it
    # cannot be mistaken for a finding about the document.
    if isinstance(trace_doc, dict) and trace_doc.get("schema_version") == "2.0.0":
        print(
            "note: schema 2.0.0 — this trace's root covers only its terminal "
            "record.\n      It is not an anchorable commitment to the run "
            "(ADR-019); re-run under\n      3.0.0 to obtain one.",
            file=sys.stderr,
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
