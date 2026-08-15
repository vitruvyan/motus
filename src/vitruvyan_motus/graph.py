"""GraphSpec: topology as validated, fingerprinted data (ADR-001 §Decision 2).

Everything here answers to ``contract/graphspec.v1.schema.json`` — read its
``description`` field for the normative R1-R12 text this module implements.
Construction validates immediately (``GraphSpec.from_dict``); there is no
construct-and-warn mode, and no path to an instance that skipped validation.

Two things this module deliberately does NOT do, both load-bearing:

1.  It does not import ``jsonschema``. That library is a ``[test]``-only
    extra (pyproject.toml) — using it here would make ``vitruvyan_motus``
    unconditionally require a third-party package, which is exactly the new
    runtime dependency the contract forbids. Every JSON-Schema-shape check
    below (required keys, types, patterns, ``additionalProperties``,
    ``oneOf``) is therefore hand-written against the schema's own text.
2.  It does not import ``contract/validate.py``. That file is the
    differential *test* oracle, never a packaged runtime dependency. R1-R12
    and the R12/PEP-440 parser are independently reimplemented here — not
    copied by reference — matching validate.py's documented behavior
    (including its deliberate fail-closed edge cases) because both
    implementations answer to the same contract text, not because one
    imports the other.

The canonical fingerprint (contract/README.md §"Fingerprints") is computed
over the EXACT validated document — no materialized defaults. A node that
omits ``effect_class`` fingerprints differently from one that spells out
``external_effect`` explicitly, even though the two are behaviorally
identical at every other layer: the fingerprint answers "what was declared",
not "what does it resolve to". ``GraphSpec`` therefore keeps the original
validated mapping verbatim and fingerprints that, never a reconstruction
from its own typed properties.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Union

from vitruvyan_motus.effects import EffectClass
from vitruvyan_motus.errors import GraphSpecValidationError, GraphSpecViolation

__all__ = [
    "TransitionKind",
    "NodeDecl",
    "Transition",
    "CompiledPlan",
    "GraphSpec",
]

# A JSON value, exactly as ``json.load`` would produce it (str keys only,
# int/float/bool/None/str, nested list/dict). GraphSpec's schema DOES admit
# a float: ``max_transitions`` accepts any JSON Schema integer, and a float
# with zero fractional part (``8.0``) satisfies that type per Draft 2020-12
# — see ``_is_schema_integer`` and ``GraphSpec.max_transitions``. This alias
# documents the general intended shape of the raw ``data`` a caller hands
# to ``GraphSpec.from_dict``, not a per-field constraint.
Json = Union[None, bool, int, float, str, list, dict]


# --------------------------------------------------------------------------- #
# Canonical form and fingerprint (contract/README.md §"Fingerprints")         #
# --------------------------------------------------------------------------- #


def _canonical_json_bytes(obj: Any) -> bytes:
    """UTF-8, keys sorted lexicographically at every depth, no insignificant
    whitespace — the encoding GraphSpec's fingerprint is computed over.

    Private and GraphSpec-specific, NOT a generic RFC 8259 canonicalizer:
    plain ``json.dumps`` alone, with no independent J1 gate, would silently
    emit NaN/Infinity, convert tuples to arrays, and stringify non-string
    mapping keys for arbitrary input. None of that is a concern HERE because
    the only caller (``_fingerprint``, in turn only called by
    ``GraphSpec.graph_fingerprint``) is always given a document that already
    passed ``_shape_violations``/``_semantic_violations`` — schema
    validation has already refused floats anywhere but a JSON-Schema-integer
    ``max_transitions``, non-string keys, and non-JSON types. A general-
    purpose strict canonicalizer for arbitrary input is J1's concern, out of
    scope for Milestone B (see contract/validate.py's own J1 machinery).

    **That paragraph was true of floats and non-string keys and false of
    strings.** The schema's `name` pattern is `.+`, so a graph named with an
    unpaired surrogate passes every validation above and arrives here — and
    raised a bare `UnicodeEncodeError` mid-fingerprint, blaming the codec for a
    document the caller could not have known was wrong. Found by an adversarial
    round attacking ADR-026's own sweep, which had missed this frontier and
    `commitlog._stem` while enumerating "every frontier"."""
    try:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        # Unreachable through `GraphSpec.from_dict`, which now refuses this in
        # `_shape_violations` and reports it as a violation with a path. Kept
        # because "unreachable" is what the paragraph above claimed and it was
        # wrong, and because a bare codec error is the worst way to find out.
        raise ValueError(
            "this GraphSpec has no canonical JSON encoding and therefore no "
            f"fingerprint: {exc}. A JSON string denotes a sequence of Unicode "
            "scalar values (rule J1, ADR-026)") from None


def _fingerprint(kind: str, obj: Any) -> str:
    """Kind-prefixed SHA-256 over ``_canonical_json_bytes(obj)``, e.g.
    ``"graph:sha256:<hex>"``. Private and GraphSpec-specific (see
    ``_canonical_json_bytes``'s docstring for why this is not exposed as a
    generic canonicalizer). Independently reimplements
    ``contract/validate.py:fingerprint``'s algorithm for GraphSpec's own
    validated documents — same algorithm, same contract, proven equal by
    test parity against the oracle, never by import."""
    return f"{kind}:sha256:{hashlib.sha256(_canonical_json_bytes(obj)).hexdigest()}"


def _deep_freeze(obj: Any) -> Any:
    """Recursively convert dict -> MappingProxyType and list -> tuple, so the
    stored canonical source can never be mutated out from under a GraphSpec
    after construction. Leaves already-immutable leaves untouched."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


def _thaw(obj: Any) -> Any:
    """The inverse of ``_deep_freeze``: MappingProxyType -> dict, tuple ->
    list, recursively — used by ``GraphSpec.to_dict()`` to hand back an
    ordinary, mutable, JSON-shaped document."""
    if isinstance(obj, MappingProxyType):
        return {k: _thaw(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_thaw(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Hand-written JSON-Schema-shape validation (no jsonschema import — see the  #
# module docstring). Mirrors graphspec.v1.schema.json field by field.        #
# --------------------------------------------------------------------------- #

_NODE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
_TOP_LEVEL_REQUIRED = ("schema_version", "name", "version", "entry", "nodes", "transitions")
_TOP_LEVEL_OPTIONAL = ("requires_motus", "max_transitions")
_TOP_LEVEL_ALLOWED = frozenset(_TOP_LEVEL_REQUIRED) | frozenset(_TOP_LEVEL_OPTIONAL)
_NODE_DECL_ALLOWED = frozenset({"name", "effect_class", "reads_declared", "writes_declared"})
_NEXT_ALLOWED = frozenset({"kind", "to"})
_ROUTE_ALLOWED = frozenset({"kind", "on", "map", "default"})
_TERMINAL_ALLOWED = frozenset({"kind"})


def _is_schema_integer(value: Any) -> bool:
    """True iff ``value`` satisfies JSON Schema Draft 2020-12's "integer"
    type exactly — matching ``contract/validate.py``'s jsonschema-backed
    behavior bit for bit, not a stricter reading of our own:

    * a genuine ``int`` (excluding ``bool`` — a Python bool is an int
      subclass, but jsonschema's own type checker explicitly excludes it);
    * OR a ``float`` with zero fractional part: ``8.0`` satisfies "integer"
      per the JSON Schema spec, because a number with a zero fractional
      part IS an integer regardless of its JSON lexical form.

    Verified against jsonschema 4.26's own ``TYPE_CHECKER`` for
    ``Draft202012Validator``, whose "integer" predicate is exactly
    ``is_integer(checker, instance) or (isinstance(instance, float) and
    instance.is_integer())`` — reproduced here without importing
    ``jsonschema`` itself (a ``[test]``-only extra; see the module
    docstring). ``float.is_integer()`` is already ``False`` for NaN and
    +/-Infinity, so those are rejected with no separate finiteness check —
    confirmed empirically, not merely assumed.

    This function only classifies; it never coerces. Whichever numeric form
    the source document used — ``8`` or ``8.0`` — is preserved verbatim in
    ``GraphSpec``'s stored source and therefore in the canonical
    fingerprint: fingerprints answer "what was declared", never a
    normalized reading of it (see the module docstring's "no materialized
    defaults" paragraph — the same principle applies to numeric form)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def _is_string_array(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _first_unencodable_string(document: Any) -> tuple[str, int, str] | None:
    """The first string in ``document`` that is not Unicode text, with its path.

    One C-level serialise decides and the walk only locates -- the same shape
    `trace._refuse_unpaired_surrogates` takes, and iterative for the same
    reason: a refusal a document does not deserve is the worst answer, and a
    `RecursionError` from a deeply nested spec would be one.
    """
    try:
        json.dumps(document, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        pass
    except (RecursionError, TypeError, ValueError):
        return None  # not this rule's business; the shape checks below say so
    else:
        return None

    def at(text: str) -> int | None:
        if text.isascii():
            return None
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as exc:
            return exc.start
        return None

    stack: list[tuple[Any, str]] = [(document, "$")]
    while stack:
        value, path = stack.pop()
        if isinstance(value, str):
            index = at(value)
            if index is not None:
                return path, index, value
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(key, str):
                    index = at(key)
                    if index is not None:
                        return f"{path}.{key!r}", index, key
                stack.append((item, f"{path}.{key}"))
        elif isinstance(value, list):
            for position, item in enumerate(value):
                stack.append((item, f"{path}[{position}]"))
    return None


def _shape_violations(data: Any) -> list[GraphSpecViolation]:
    """The hand-rolled equivalent of jsonschema.Draft202012Validator against
    graphspec.v1.schema.json. Returns as soon as further checking would
    require assuming a shape that plainly is not there."""
    if not isinstance(data, dict):
        return [GraphSpecViolation("SCHEMA", "$", f"expected an object, got {type(data).__name__}")]

    unencodable = _first_unencodable_string(data)
    if unencodable is not None:
        path, index, text = unencodable
        # Before every other check, because a document with no encoding has no
        # canonical form and therefore no fingerprint -- there is nothing for
        # the rest of this function to be about. The schema's `name` pattern is
        # `.+`, so this got all the way to `_canonical_json_bytes` and raised
        # `UnicodeEncodeError` there, naming the codec instead of the spec.
        return [GraphSpecViolation(
            "SCHEMA", path,
            f"U+{ord(text[index]):04X} at index {index} is an unpaired "
            "surrogate: it denotes no character and has no UTF-8 encoding, so "
            "this spec has no canonical form and no graph_fingerprint "
            "(rule J1, ADR-026)")]

    v: list[GraphSpecViolation] = []

    for key in _TOP_LEVEL_REQUIRED:
        if key not in data:
            v.append(GraphSpecViolation("SCHEMA", "$", f"missing required property {key!r}"))
    extra = set(data) - _TOP_LEVEL_ALLOWED
    for key in sorted(extra):
        v.append(GraphSpecViolation("SCHEMA", "$", f"unexpected property {key!r}"))
    if v:
        return v  # required/extra-key violations make every further check moot

    if data["schema_version"] != "1.0.0":
        v.append(GraphSpecViolation(
            "SCHEMA", "$.schema_version",
            f"schema_version must be the literal string '1.0.0', got {data['schema_version']!r}",
        ))

    name = data["name"]
    if not isinstance(name, str) or not (1 <= len(name) <= 200):
        v.append(GraphSpecViolation(
            "SCHEMA", "$.name", "name must be a string of length 1..200"
        ))

    version = data["version"]
    if not isinstance(version, str) or len(version) < 1:
        v.append(GraphSpecViolation(
            "SCHEMA", "$.version", "version must be a non-empty string"
        ))

    if "requires_motus" in data and not isinstance(data["requires_motus"], str):
        v.append(GraphSpecViolation(
            "SCHEMA", "$.requires_motus", "requires_motus must be a string"
        ))

    entry = data["entry"]
    if not isinstance(entry, str):
        v.append(GraphSpecViolation("SCHEMA", "$.entry", "entry must be a string"))

    if "max_transitions" in data:
        mt = data["max_transitions"]
        if not _is_schema_integer(mt) or mt < 1:
            v.append(GraphSpecViolation(
                "SCHEMA", "$.max_transitions", "max_transitions must be an integer >= 1"
            ))

    nodes = data["nodes"]
    if not isinstance(nodes, list) or len(nodes) < 1:
        v.append(GraphSpecViolation("SCHEMA", "$.nodes", "nodes must be a non-empty array"))
    else:
        for i, node in enumerate(nodes):
            v.extend(_node_decl_shape_violations(node, f"$.nodes[{i}]"))

    transitions = data["transitions"]
    if not isinstance(transitions, dict):
        v.append(GraphSpecViolation("SCHEMA", "$.transitions", "transitions must be an object"))
    else:
        for key, step in transitions.items():
            v.extend(_transition_shape_violations(step, f"$.transitions.{key}"))

    return v


def _node_decl_shape_violations(node: Any, path: str) -> list[GraphSpecViolation]:
    if not isinstance(node, dict):
        return [GraphSpecViolation("SCHEMA", path, f"expected an object, got {type(node).__name__}")]

    v: list[GraphSpecViolation] = []
    if "name" not in node:
        v.append(GraphSpecViolation("SCHEMA", path, "missing required property 'name'"))
    extra = set(node) - _NODE_DECL_ALLOWED
    for key in sorted(extra):
        v.append(GraphSpecViolation("SCHEMA", path, f"unexpected property {key!r}"))
    if v:
        return v

    name = node["name"]
    if not isinstance(name, str) or not _NODE_NAME_PATTERN.match(name):
        v.append(GraphSpecViolation(
            "SCHEMA", f"{path}.name",
            "name must match ^[A-Za-z][A-Za-z0-9_.-]{0,99}$",
        ))

    if "effect_class" in node:
        raw = node["effect_class"]
        if raw not in (EffectClass.PURE.value, EffectClass.RECORDED_EFFECT.value,
                       EffectClass.EXTERNAL_EFFECT.value):
            v.append(GraphSpecViolation(
                "SCHEMA", f"{path}.effect_class",
                "effect_class must be one of 'pure', 'recorded_effect', 'external_effect'",
            ))

    for key in ("reads_declared", "writes_declared"):
        if key in node and not _is_string_array(node[key]):
            v.append(GraphSpecViolation(
                "SCHEMA", f"{path}.{key}", f"{key} must be an array of strings"
            ))

    return v


def _transition_shape_violations(step: Any, path: str) -> list[GraphSpecViolation]:
    if not isinstance(step, dict):
        return [GraphSpecViolation("SCHEMA", path, f"expected an object, got {type(step).__name__}")]

    kind = step.get("kind")
    if kind not in ("next", "route", "terminal"):
        return [GraphSpecViolation(
            "SCHEMA", f"{path}.kind",
            "kind must be one of 'next', 'route', 'terminal'",
        )]

    v: list[GraphSpecViolation] = []
    if kind == "next":
        if "to" not in step:
            v.append(GraphSpecViolation("SCHEMA", path, "'next' requires 'to'"))
        elif not isinstance(step["to"], str):
            v.append(GraphSpecViolation("SCHEMA", f"{path}.to", "to must be a string"))
        extra = set(step) - _NEXT_ALLOWED
        for key in sorted(extra):
            v.append(GraphSpecViolation("SCHEMA", path, f"unexpected property {key!r}"))

    elif kind == "route":
        if "on" not in step:
            v.append(GraphSpecViolation("SCHEMA", path, "'route' requires 'on'"))
        elif not isinstance(step["on"], str):
            v.append(GraphSpecViolation("SCHEMA", f"{path}.on", "on must be a string"))
        if "map" not in step:
            v.append(GraphSpecViolation("SCHEMA", path, "'route' requires 'map'"))
        else:
            m = step["map"]
            if not isinstance(m, dict) or len(m) < 1:
                v.append(GraphSpecViolation(
                    "SCHEMA", f"{path}.map", "map must be a non-empty object"
                ))
            else:
                for mk, mv in m.items():
                    if not isinstance(mv, str):
                        v.append(GraphSpecViolation(
                            "SCHEMA", f"{path}.map.{mk}", "map values must be strings"
                        ))
        if "default" in step and not isinstance(step["default"], str):
            v.append(GraphSpecViolation("SCHEMA", f"{path}.default", "default must be a string"))
        extra = set(step) - _ROUTE_ALLOWED
        for key in sorted(extra):
            v.append(GraphSpecViolation("SCHEMA", path, f"unexpected property {key!r}"))

    else:  # terminal
        extra = set(step) - _TERMINAL_ALLOWED
        for key in sorted(extra):
            v.append(GraphSpecViolation("SCHEMA", path, f"unexpected property {key!r}"))

    return v


# --------------------------------------------------------------------------- #
# R12 — requires_motus as a PEP 440 specifier set                            #
# (independently reimplemented from contract/validate.py's own hand-rolled  #
# parser — that file uses no `packaging` import either, so parity does not  #
# require a new dependency; see the module docstring and the reading report #
# delivered alongside this milestone.)                                      #
# --------------------------------------------------------------------------- #

_PEP440_VERSION = (
    r"v?"
    r"(?:[0-9]+!)?"                                                # epoch
    r"(?P<release>[0-9]+(?:\.[0-9]+)*)"                            # release
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?"  # pre
    r"(?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?"               # post
    r"(?:[-_.]?dev[-_.]?[0-9]*)?"                                  # dev
    r"(?P<local>\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?"                  # local
)
_SPECIFIER_CLAUSE = re.compile(
    r"^\s*(?P<op>===|==|!=|<=|>=|~=|<|>)\s*"
    r"(?P<version>" + _PEP440_VERSION + r")"
    r"(?P<wildcard>\.\*)?\s*$",
    re.IGNORECASE,
)
_ARBITRARY_EQUALITY_CLAUSE = re.compile(r"^\s*===\s*\S+\s*$")


def _r12_violations(value: str) -> list[GraphSpecViolation]:
    path = "$.requires_motus"
    out: list[GraphSpecViolation] = []
    for clause in value.split(","):
        if not clause.strip():
            # Deliberate fail-closed choice, matching the contract: resolvers
            # such as pip/packaging silently drop empty clauses
            # ('>=1.0,' is accepted there); this validator refuses them.
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus {value!r} contains an empty clause; "
                "clauses are comma-separated PEP 440 specifiers",
            ))
            continue
        if _ARBITRARY_EQUALITY_CLAUSE.match(clause):
            continue
        m = _SPECIFIER_CLAUSE.match(clause)
        if m is None:
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus clause '{clause.strip()}' is not a valid "
                "PEP 440 specifier clause "
                "(operator ==, !=, <=, >=, <, >, ~= or === followed by a version)",
            ))
            continue
        op = m.group("op")
        if m.group("wildcard") and op not in ("==", "!="):
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus clause '{clause.strip()}': the '.*' wildcard "
                "suffix is only valid with == or != (PEP 440)",
            ))
        elif m.group("wildcard") and m.group("local"):
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus clause '{clause.strip()}': the '.*' wildcard "
                "suffix cannot combine with a local version label (PEP 440)",
            ))
        if m.group("local") and op not in ("==", "!=", "==="):
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus clause '{clause.strip()}': a local version "
                "label is only valid with ==, != or === (PEP 440)",
            ))
        if op == "~=" and "." not in m.group("release"):
            out.append(GraphSpecViolation(
                "R12", path,
                f"requires_motus clause '{clause.strip()}': the compatible-"
                "release operator ~= requires at least two release segments "
                "(PEP 440)",
            ))
    return out


# --------------------------------------------------------------------------- #
# R1-R5, R8, R11 — graph-shape semantics over an already schema-clean spec   #
# --------------------------------------------------------------------------- #


def _step_targets(name: str, step: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(target, json-path) pairs of one transition step. Terminal has none."""
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


def _has_cycle(nodes: set, edges: Mapping[str, list]) -> bool:
    """Back-edge detection over the transition graph, declared nodes only."""
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
                if nxt not in color:  # END or an undeclared target: R2's business
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


def _semantic_violations(data: Mapping[str, Any]) -> list[GraphSpecViolation]:
    """R1-R5, R8, R11, R12 over an already schema-clean document. Mirrors
    contract/validate.py:validate_graphspec's layering exactly: structural
    rules (R1, R2, R4, R5, R8) gate the flow analyses (R3, R11); R12 is
    orthogonal to both."""
    v: list[GraphSpecViolation] = []
    nodes = data["nodes"]
    names = [n["name"] for n in nodes]
    declared = set(names)
    transitions = data["transitions"]
    entry = data["entry"]

    # R5 — node names are unique (one violation per duplicated name).
    seen_counts: dict[str, int] = {}
    for name in names:
        seen_counts[name] = seen_counts.get(name, 0) + 1
    for name, count in seen_counts.items():
        if count > 1:
            v.append(GraphSpecViolation(
                "R5", "$.nodes",
                f"node name '{name}' is declared {count} times; "
                "node names must be unique",
            ))

    # R8 — 'END' is reserved for the terminal pseudo-target.
    for i, node in enumerate(nodes):
        if node["name"] == "END":
            v.append(GraphSpecViolation(
                "R8", f"$.nodes[{i}].name",
                "'END' is reserved for the terminal pseudo-target and may "
                "not be a node name",
            ))

    # R1 — entry names a declared node.
    if entry not in declared:
        v.append(GraphSpecViolation("R1", "$.entry", f"entry '{entry}' is not a declared node"))

    # R4 — exactly one transition entry per declared node, no extra keys.
    seen: set = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        if name not in transitions:
            v.append(GraphSpecViolation(
                "R4", "$.transitions", f"declared node '{name}' has no transition entry"
            ))
    for key in transitions:
        if key not in declared:
            v.append(GraphSpecViolation(
                "R4", f"$.transitions.{key}",
                f"transition key '{key}' does not name a declared node",
            ))

    # R2 — every transition target names a declared node or END.
    edges: dict[str, list[str]] = {}
    for key, step in transitions.items():
        targets = _step_targets(key, step)
        for target, path in targets:
            if target != "END" and target not in declared:
                v.append(GraphSpecViolation(
                    "R2", path, f"target '{target}' is not a declared node or END"
                ))
        edges[key] = [t for t, _ in targets]

    # R12 — requires_motus parses as a PEP 440 specifier set (shape-independent).
    if "requires_motus" in data:
        v.extend(_r12_violations(data["requires_motus"]))

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
            v.append(GraphSpecViolation(
                "R3", f"$.nodes[{i}]",
                f"node '{node['name']}' is not reachable from entry '{entry}'",
            ))

    # R11(a) — every declared node must be able to reach a terminal step.
    exit_capable = {
        name for name in declared
        if transitions[name].get("kind") == "terminal" or "END" in edges.get(name, ())
    }
    reverse: dict[str, set] = {name: set() for name in declared}
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
            v.append(GraphSpecViolation(
                "R11", f"$.nodes[{i}]",
                f"node '{node['name']}' cannot reach a terminal step — "
                "an inescapable trap region",
            ))

    # R11(b) — a cyclic spec MUST declare max_transitions.
    if "max_transitions" not in data and _has_cycle(declared, edges):
        v.append(GraphSpecViolation(
            "R11", "$.max_transitions",
            "spec contains a cycle but does not declare max_transitions; "
            "R11 requires the safety limit for any cyclic spec",
        ))

    return v


# --------------------------------------------------------------------------- #
# The typed, ergonomic view — built only after validation has succeeded      #
# --------------------------------------------------------------------------- #


class TransitionKind:
    """The three legal transition shapes. Not an Enum on purpose: comparing
    ``transition.kind == "next"`` reads naturally against the schema's own
    vocabulary without importing a class to spell the literal."""

    NEXT = "next"
    ROUTE = "route"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class NodeDecl:
    """One declared node. ``effect_class`` is the RESOLVED value (defaulted
    to ``EXTERNAL_EFFECT`` when the source omitted it) — for the fingerprint-
    faithful raw form, read ``GraphSpec.to_dict()`` instead; this view exists
    for ergonomic runtime access, not for re-deriving the canonical form."""

    name: str
    effect_class: EffectClass
    reads_declared: tuple[str, ...] | None
    writes_declared: tuple[str, ...] | None

    @classmethod
    def _from_validated(cls, data: Mapping[str, Any]) -> "NodeDecl":
        """Assumes ``data`` already passed shape validation — never called
        on unvalidated input."""
        raw_class = data.get("effect_class")
        effect_class = EffectClass(raw_class) if raw_class is not None else EffectClass.EXTERNAL_EFFECT
        reads = data.get("reads_declared")
        writes = data.get("writes_declared")
        return cls(
            name=data["name"],
            effect_class=effect_class,
            reads_declared=tuple(reads) if reads is not None else None,
            writes_declared=tuple(writes) if writes is not None else None,
        )


@dataclass(frozen=True)
class Transition:
    """One node's routing step. Unused fields for a given ``kind`` are
    ``None`` (or an empty mapping for ``map``) rather than absent attributes
    — a uniform shape is simpler to consume than three distinct subtypes for
    a document this small."""

    kind: str
    to: str | None = None
    on: str | None = None
    map: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    default: str | None = None

    @classmethod
    def _from_validated(cls, data: Mapping[str, Any]) -> "Transition":
        kind = data["kind"]
        if kind == TransitionKind.NEXT:
            return cls(kind=kind, to=data["to"])
        if kind == TransitionKind.ROUTE:
            return cls(
                kind=kind,
                on=data["on"],
                map=MappingProxyType(dict(data["map"])),
                default=data.get("default"),
            )
        return cls(kind=kind)  # terminal


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    """Immutable, pre-indexed topology consumed by the single interpreter.

    Compilation changes lookup cost, never execution semantics: the plan
    contains only data already present in the validated GraphSpec.
    """

    entry: str
    declarations: Mapping[str, NodeDecl]
    transitions: Mapping[str, Transition]
    max_transitions: int | float | None
    graph_fingerprint: str

    @classmethod
    def _from_spec(cls, spec: "GraphSpec") -> "CompiledPlan":
        return cls(
            entry=spec.entry,
            declarations=MappingProxyType({node.name: node for node in spec.nodes}),
            transitions=MappingProxyType(dict(spec.transitions)),
            max_transitions=spec.max_transitions,
            graph_fingerprint=spec.graph_fingerprint,
        )


@dataclass(frozen=True)
class GraphSpec:
    """A validated, fingerprintable GraphSpec.

    The sole public constructor is :meth:`from_dict`. Validation happens in
    ``__post_init__`` — reachable from ANY construction path, not merely the
    blessed factory — so there is no way to hold a ``GraphSpec`` instance
    that was never checked. An invalid graph raises
    :class:`~vitruvyan_motus.errors.GraphSpecValidationError` and never
    comes into existence.
    """

    _source: dict[str, Any]

    def __post_init__(self) -> None:
        violations = _shape_violations(self._source)
        if not violations:
            violations = _semantic_violations(self._source)
        if violations:
            raise GraphSpecValidationError(tuple(violations))

        frozen_source = _deep_freeze(self._source)
        object.__setattr__(self, "_source", frozen_source)
        object.__setattr__(
            self, "_nodes", tuple(NodeDecl._from_validated(n) for n in frozen_source["nodes"])
        )
        object.__setattr__(
            self, "_transitions",
            MappingProxyType({
                key: Transition._from_validated(step)
                for key, step in frozen_source["transitions"].items()
            }),
        )
        object.__setattr__(self, "_compiled", CompiledPlan._from_spec(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphSpec":
        """The one public entry point. ``data`` must be an actual ``dict``
        — not merely any ``Mapping`` — matching what ``_shape_violations``
        actually accepts (anything failing ``isinstance(data, dict)`` is
        refused as SCHEMA, mirroring the jsonschema oracle's own object-
        instance model): exactly what a fixture's ``spec`` field already is
        once loaded via ``json.load``."""
        return cls(_source=data)

    # -- properties mirroring the schema's own fields ----------------------

    @property
    def schema_version(self) -> str:
        return self._source["schema_version"]

    @property
    def name(self) -> str:
        return self._source["name"]

    @property
    def version(self) -> str:
        return self._source["version"]

    @property
    def requires_motus(self) -> str | None:
        return self._source.get("requires_motus")

    @property
    def entry(self) -> str:
        return self._source["entry"]

    @property
    def max_transitions(self) -> int | float | None:
        """The declared safety limit, or ``None`` if the spec omits it.

        Typed ``int | float`` — not merely ``int`` — because this property
        is truthful about what a ``py.typed`` distribution actually returns:
        JSON Schema Draft 2020-12's "integer" type accepts any number with a
        zero fractional part, and GraphSpec matches that exactly (MF-B1). A
        document declaring ``8.0`` is legal, and this returns it verbatim as
        the Python ``float`` ``8.0`` — never coerced to the ``int`` ``8``.
        A ``float`` value here is therefore always finite and integral
        (``value.is_integer()`` is ``True``); ``_is_schema_integer`` is what
        enforces that at construction time, this property only ever returns
        a value that already passed that gate."""
        return self._source.get("max_transitions")

    @property
    def nodes(self) -> tuple[NodeDecl, ...]:
        return self._nodes  # type: ignore[attr-defined]

    @property
    def transitions(self) -> Mapping[str, Transition]:
        return self._transitions  # type: ignore[attr-defined]

    @property
    def compiled(self) -> CompiledPlan:
        return self._compiled  # type: ignore[attr-defined]

    # -- fingerprint and serialization --------------------------------------

    @property
    def graph_fingerprint(self) -> str:
        """``"graph:sha256:<hex>"`` over the EXACT validated document — no
        materialized defaults. Equal, for every positive fixture, to
        ``contract/validate.py:fingerprint("graph", spec)`` called on that
        fixture's raw ``spec`` dict (proven by this milestone's parity
        tests, not merely asserted)."""
        return _fingerprint("graph", self.to_dict())

    def to_dict(self) -> dict:
        """The exact canonical source, as an ordinary mutable dict — the
        same shape a fixture's ``spec`` field has, byte-for-byte round-
        trippable through ``json.dumps``."""
        return _thaw(self._source)
