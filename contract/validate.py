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
    outcome in ``routing.written_at``.
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
receive already-parsed documents, walk them for non-finite floats.

Dependencies: stdlib + jsonschema.  Nothing else.
"""

from __future__ import annotations

import argparse
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


def _non_finite_violations(instance: Any, prefix: str = "$") -> list[Violation]:
    """J1 for already-parsed documents.

    An API caller hands this module Python objects, not text, so the parser's
    ``parse_constant`` fence never saw them: a ``float('nan')`` or
    ``float('inf')`` may be sitting where no strict RFC 8259 text could have
    produced one.  Walk the document and flag every non-finite float.
    """
    out: list[Violation] = []
    stack: list[tuple[Any, str]] = [(instance, prefix)]
    while stack:
        value, path = stack.pop()
        if isinstance(value, float) and not math.isfinite(value):
            out.append(
                Violation(
                    "J1",
                    path,
                    f"value {value!r} is not a finite number; trace values are "
                    "strict RFC 8259 JSON (NaN and Infinity are refused)",
                )
            )
        elif isinstance(value, dict):
            for key, item in value.items():
                stack.append((item, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                stack.append((item, f"{path}[{index}]"))
    out.sort(key=lambda violation: violation.path)
    return out


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
    # strict RFC 8259 text could have produced.
    v: list[Violation] = list(_non_finite_violations(spec))
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


def _trace_semantics(
    doc: dict, spec: dict | None, expect_complete: bool
) -> list[Violation]:
    v: list[Violation] = []
    records = doc.get("records") or []
    run = doc.get("run") or {}

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

    # T4 — per-value read origins address real, earlier evidence.
    for i, record in enumerate(records):
        if record.get("kind") != "transition":
            continue
        for j, read in enumerate(record.get("reads") or []):
            origin = read.get("origin") or {}
            okind = origin.get("kind")
            path = f"$.records[{i}].reads[{j}].origin"
            if okind == "transition":
                target_seq = origin.get("seq")
                collection = origin.get("collection")
                index = origin.get("index")
                target = by_seq.get(target_seq)
                if target is None:
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
            elif okind == "initial":
                if run_started is None:
                    continue  # T2 already reported the missing run_started
                collection = origin.get("collection")
                index = origin.get("index")
                array = (run_started.get("initial_state") or {}).get(collection)
                size = len(array) if isinstance(array, list) else 0
                if not (isinstance(index, int) and 0 <= index < size):
                    v.append(
                        Violation(
                            "T4",
                            path,
                            f"read origin addresses initial_state[{collection}]"
                            f"[{index}], but that collection has {size} "
                            "element(s) — index out of range",
                        )
                    )
            elif okind == "header" and origin.get("field") == "metadata":
                metadata = run.get("metadata")
                key = read.get("key")
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
            # intent-header / scan / absent origins carry no address to dangle.

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
            # written_at is the routing record's own causal edge: it must
            # reference an existing EARLIER transition whose committed
            # writes.decisions contain the routed key, and the R10-most-recent
            # such value in that transition must equal the routed value.
            written_at = record.get("written_at")
            on = record.get("on")
            wpath = f"$.records[{i}].written_at"
            target = by_seq.get(written_at)
            if target is None:
                v.append(
                    Violation(
                        "T8",
                        wpath,
                        f"written_at references seq {written_at}, which does "
                        "not exist in this trace",
                    )
                )
            elif not (
                isinstance(written_at, int)
                and isinstance(record.get("seq"), int)
                and written_at < record["seq"]
            ):
                v.append(
                    Violation(
                        "T8",
                        wpath,
                        f"written_at references seq {written_at}, which is "
                        "not earlier than the routing record "
                        f"(seq {record.get('seq')})",
                    )
                )
            elif target.get("kind") != "transition":
                v.append(
                    Violation(
                        "T8",
                        wpath,
                        f"written_at references seq {written_at}, which is a "
                        f"'{target.get('kind')}' record, not a transition "
                        "with committed writes",
                    )
                )
            else:
                decided = [
                    entry
                    for entry in (target.get("writes") or {}).get("decisions") or []
                    if isinstance(entry, dict) and entry.get("key") == on
                ]
                if not decided:
                    v.append(
                        Violation(
                            "T8",
                            wpath,
                            f"written_at references seq {written_at}, whose "
                            "committed writes.decisions contain no entry with "
                            f"key {on!r}",
                        )
                    )
                elif isinstance(value, str) and decided[-1].get("value") != value:
                    # R10: last-in-array is the most recent value within a
                    # transition; matched/default values are strings (schema).
                    v.append(
                        Violation(
                            "T8",
                            wpath,
                            f"the R10-most-recent decision {on!r} in seq "
                            f"{written_at} has value "
                            f"{decided[-1].get('value')!r}, not the routed "
                            f"value {value!r}",
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
            # An undeclared 'after' is T5's finding; nothing to compare against.

    return v


def validate_trace(
    doc: dict, spec: dict | None = None, expect_complete: bool = True
) -> list[Violation]:
    """Validate a trace document: JSON Schema first, then the T-rules.

    ``spec`` enables the spec-correlated rules (T5 names, T8 candidate lists).
    ``expect_complete=False`` accepts a trace still in flight or recovered from
    a crash: T3 and the unclosed-attempt-at-EOF arm of T6 are then waived —
    truncation is evidence, not malformation (guarantees.md invariant II).
    """
    schema = load_trace_schema()
    schema_violations = _schema_violations(schema, _trace_validator(), doc)
    if schema_violations:
        return schema_violations
    # J1 — an already-parsed document may carry non-finite floats (NaN,
    # Infinity) that no strict RFC 8259 text could have produced; the schema
    # cannot see them (they are numbers), so the semantic pass flags them.
    violations = _non_finite_violations(doc)
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
        raw = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
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
