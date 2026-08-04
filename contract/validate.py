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
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match

_CONTRACT_DIR = Path(__file__).resolve().parent
_GRAPHSPEC_SCHEMA_FILE = "graphspec.v1.schema.json"
_TRACE_SCHEMA_FILE = "trace.v1.schema.json"

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


class NonFiniteJSONError(ValueError):
    """A JSON text carried NaN, Infinity or -Infinity (refused per rule J1)."""


def _refuse_non_finite(token: str) -> Any:
    raise NonFiniteJSONError(
        f"non-finite JSON constant {token} is refused; "
        "trace values are strict RFC 8259 JSON (rule J1)"
    )


def _loads_strict(text: str) -> Any:
    """``json.loads`` that refuses NaN/Infinity/-Infinity (RFC 8259, J1)."""
    return json.loads(text, parse_constant=_refuse_non_finite)


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
    stack: list[tuple[Any, str]] = [(instance, prefix)]
    while stack:
        value, path = stack.pop()
        if value is None or isinstance(value, (str, bool, int)):
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
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def fingerprint(kind: str, obj: Any) -> str:
    """Kind-prefixed SHA-256 over ``canonical_json(obj)``, e.g. ``graph:sha256:...``.

    ``graph_fingerprint`` is this function over the ENTIRE GraphSpec document
    as validated (no materialized defaults); SB2 recomputes it — every fixture
    fingerprint is TRUE, never decorative.
    """
    return f"{kind}:sha256:{hashlib.sha256(canonical_json(obj)).hexdigest()}"


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


def _validator(key: str, schema: dict) -> Draft202012Validator:
    if key not in _VALIDATORS:
        _VALIDATORS[key] = Draft202012Validator(schema, format_checker=FormatChecker())
    return _VALIDATORS[key]


def _graphspec_validator() -> Draft202012Validator:
    return _validator("graphspec", load_graphspec_schema())


def _trace_validator() -> Draft202012Validator:
    return _validator("trace", load_trace_schema())


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
    """T9 — is ``value`` a calendar-valid RFC 3339 UTC instant (Z form)?

    The schema's regex pins the shape but admits month 13 and hour 99;
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
                    committed = sum(
                        1
                        for r in records
                        if r.get("kind") == "transition"
                        and r.get("disposition") == "commit"
                    )
                    if committed < spec["max_transitions"]:
                        v.append(
                            Violation(
                                "E11",
                                path,
                                f"cause transition_limit_exceeded but only "
                                f"{committed} committed transition(s) are on "
                                f"record, below max_transitions "
                                f"{spec['max_transitions']}",
                            )
                        )
        # sink_failure / validation_failure / runner_internal are system
        # causes, admissible anywhere.

    return v


def _trace_semantics(
    doc: dict, spec: dict | None, expect_complete: bool
) -> list[Violation]:
    v: list[Violation] = []
    records = doc.get("records") or []
    run = doc.get("run") or {}

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
            recorded_violations = Counter(
                (item.get("kind"), item.get("key"))
                for item in record.get("violations") or []
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
    except NonFiniteJSONError as exc:
        # The line IS parseable JSON in Python's lax reading — the problem is
        # strictness, not brokenness: J1, not JSONL1.
        violations.append(
            Violation(
                "J1",
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

    records: list[dict] = []
    lines_clean = True
    truncated = False
    total = len(record_lines)
    for position, (line_index, line) in enumerate(record_lines):
        is_final = position == total - 1
        try:
            obj = _loads_strict(line)
        except NonFiniteJSONError as exc:
            # Not crash truncation and not malformed JSON: the line parses in
            # Python's lax reading but carries a non-finite constant.  That is
            # a strictness violation — J1, never JSONL2, on any line.
            lines_clean = False
            violations.append(
                Violation(
                    "J1",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description=(
            "Semantic validator for the Motus contract: GraphSpec R-rules, "
            "trace T-rules, JSON document and JSONL stream forms."
        ),
        epilog=(
            "Prints one line per violation ('RULE path: message') and exits 0 "
            "iff there are none. Exit codes: 0 valid, 1 violations, 2 usage or "
            "I/O errors (including an invalid --spec, reported with 'spec:' "
            "path prefixes)."
        ),
    )
    parser.add_argument("artifact", choices=["graphspec", "trace", "jsonl"])
    parser.add_argument("file", help="the document (or JSONL stream) to validate")
    parser.add_argument(
        "--spec",
        help="GraphSpec file enabling the spec-correlated trace rules (T5, T8)",
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

    if args.artifact == "graphspec" and (args.spec or args.allow_incomplete):
        parser.error("--spec and --allow-incomplete apply to trace/jsonl only")

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
        except NonFiniteJSONError as exc:
            # Rule J1 applies to the spec input too; an unusable spec keeps
            # the established exit-2 semantics ("invalid --spec").
            print(f"J1 spec:$: {exc}")
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
    if args.artifact == "jsonl":
        violations, _doc = validate_jsonl(raw, spec=spec, expect_complete=expect_complete)
    else:
        try:
            doc = _loads_strict(raw)
        except NonFiniteJSONError as exc:
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
        else:
            violations = validate_trace(doc, spec=spec, expect_complete=expect_complete)

    for violation in violations:
        print(f"{violation.rule} {violation.path}: {violation.message}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
