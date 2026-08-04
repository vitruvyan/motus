"""GraphSpec fixture-driven gate (Milestone B).

Two distinct evidence sources, deliberately not conflated:

1.  The FROZEN fixture corpus under ``contract/fixtures/`` — every positive
    GraphSpec fixture must construct; every negative must raise with the
    exact declared rule. These fixtures are never modified to fit this
    implementation (if one looks wrong, that's an amendment proposal, not
    an edit) and this file does not touch them.
2.  This implementation's OWN tests — schema-shape malformation (no such
    fixture exists in the frozen corpus at all: zero graphspec fixtures are
    schema-layer) and R12/PEP-440 boundary cases beyond the two the corpus
    exercises. These are clearly separated below and owned by this file,
    not derived from the contract.

Fingerprint parity is checked against ``contract/validate.py:fingerprint``,
loaded dynamically exactly as ``tests/test_contract_fixtures.py`` already
does (``contract/`` is not an importable package) — this is the test-only
differential oracle the runtime itself never imports.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import typing
from pathlib import Path

import pytest

from vitruvyan_motus.effects import EffectClass
from vitruvyan_motus.errors import GraphSpecValidationError, NodeFailed
from vitruvyan_motus.graph import GraphSpec

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"
FIXTURES_DIR = CONTRACT_DIR / "fixtures"


def _load_validate_module():
    """Import contract/validate.py by path — the differential oracle. Same
    loader tests/test_contract_fixtures.py already uses; sharing the module
    name lets both files reuse one cached import within a session."""
    module_name = "motus_contract_validate"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


validate = _load_validate_module()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


_ALL_FIXTURES = [(p, _read(p)) for p in sorted(FIXTURES_DIR.glob("*.json"))]
GRAPHSPEC_FIXTURES = [(p, w) for p, w in _ALL_FIXTURES if w["artifact"] == "graphspec"]
POSITIVE = [(p, w) for p, w in GRAPHSPEC_FIXTURES if w["expect"] == "valid"]
NEGATIVE = [(p, w) for p, w in GRAPHSPEC_FIXTURES if w["expect"] == "invalid"]


def _ids(pairs):
    return [path.name for path, _ in pairs]


# --------------------------------------------------------------------------- #
# Corpus sanity — these numbers are the ground truth this file answers to    #
# --------------------------------------------------------------------------- #


def test_graphspec_fixture_corpus_shape():
    assert len(GRAPHSPEC_FIXTURES) == 13
    assert len(POSITIVE) == 3
    assert len(NEGATIVE) == 10
    assert not any(w.get("layer") == "schema" for _, w in NEGATIVE), (
        "no schema-layer graphspec fixture exists in the frozen corpus — "
        "confirmed by this assertion, not merely claimed in the reading report"
    )


# --------------------------------------------------------------------------- #
# 1. Every positive fixture constructs, and its fingerprint matches the      #
#    differential oracle exactly.                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("path", "wrapper"), POSITIVE, ids=_ids(POSITIVE))
def test_positive_fixture_constructs(path, wrapper):
    spec = GraphSpec.from_dict(wrapper["instance"])
    assert spec.name == wrapper["instance"]["name"]


@pytest.mark.parametrize(("path", "wrapper"), POSITIVE, ids=_ids(POSITIVE))
def test_positive_fixture_fingerprint_matches_the_oracle(path, wrapper):
    spec = GraphSpec.from_dict(wrapper["instance"])
    oracle = validate.fingerprint("graph", wrapper["instance"])
    assert spec.graph_fingerprint == oracle, (
        f"{path.name}: {spec.graph_fingerprint!r} != oracle {oracle!r}"
    )


@pytest.mark.parametrize(("path", "wrapper"), POSITIVE, ids=_ids(POSITIVE))
def test_positive_fixture_to_dict_round_trips_through_json(path, wrapper):
    spec = GraphSpec.from_dict(wrapper["instance"])
    # The round trip must be lossless enough that a FRESH GraphSpec built
    # from the JSON-round-tripped form still fingerprints identically —
    # proves to_dict() carries nothing that only survives in memory (no
    # stray tuples, no proxies). Checked entirely through the public
    # GraphSpec API and the oracle, never through graph.py's private
    # fingerprinting helper (MF-B4).
    round_tripped = json.loads(json.dumps(spec.to_dict()))
    rebuilt = GraphSpec.from_dict(round_tripped)
    assert rebuilt.graph_fingerprint == spec.graph_fingerprint
    assert rebuilt.graph_fingerprint == validate.fingerprint("graph", round_tripped)


# --------------------------------------------------------------------------- #
# 2. Every negative fixture is rejected with exactly its declared rule.      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("path", "wrapper"), NEGATIVE, ids=_ids(NEGATIVE))
def test_negative_fixture_rejected_with_declared_rule(path, wrapper):
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(wrapper["instance"])
    declared_rule = wrapper["rule"]
    assert excinfo.value.rules == {declared_rule}, (
        f"{path.name}: expected rules == {{{declared_rule!r}}}, "
        f"got {excinfo.value.rules!r} from violations {excinfo.value.violations!r}"
    )
    # .violations always agrees with .rules — no violation carries a rule
    # outside what .rules reports, and every one is a well-formed 3-tuple.
    for violation in excinfo.value.violations:
        assert violation.rule == declared_rule
        assert violation.path.startswith("$")
        assert violation.message


# --------------------------------------------------------------------------- #
# 3. Fingerprint properties beyond fixture parity                            #
# --------------------------------------------------------------------------- #


def test_fingerprint_stable_under_equivalent_key_insertion_order():
    base = dict(_read(FIXTURES_DIR / "02-graphspec-routed-default.json")["instance"])

    # Rebuild the SAME logical document by inserting keys in reverse order —
    # canonical_json's sort_keys=True must make this a non-event.
    reordered = {}
    for key in reversed(list(base.keys())):
        reordered[key] = base[key]
    reordered["nodes"] = [dict(reversed(list(n.items()))) for n in base["nodes"]]

    a = GraphSpec.from_dict(base)
    b = GraphSpec.from_dict(reordered)
    assert a.graph_fingerprint == b.graph_fingerprint


def test_max_transitions_changes_the_fingerprint():
    base = _read(FIXTURES_DIR / "03-graphspec-cyclic-max-transitions.json")["instance"]
    changed = dict(base, max_transitions=base["max_transitions"] + 1)

    a = GraphSpec.from_dict(base)
    b = GraphSpec.from_dict(changed)
    assert a.graph_fingerprint != b.graph_fingerprint
    # And each still agrees with the oracle individually.
    assert a.graph_fingerprint == validate.fingerprint("graph", base)
    assert b.graph_fingerprint == validate.fingerprint("graph", changed)


@pytest.mark.parametrize(
    ("value",),
    [(8,), (8.0,)],
    ids=["int-8", "float-8.0"],
)
def test_max_transitions_accepted_edge_values_match_the_oracle(value):
    """JSON Schema Draft 2020-12 accepts a number with zero fractional part
    as type "integer" — 8.0 satisfies it exactly as 8 does — and
    contract/validate.py (jsonschema-backed) accepts it too (MF-B1). Proven
    here against the real oracle: the accept decision, the exact numeric
    form preserved verbatim (8.0 must never normalize to 8), and byte-for-
    byte fingerprint parity on the identical document."""
    doc = dict(_MINIMAL, max_transitions=value)
    oracle_violations = validate.validate_graphspec(doc)
    assert oracle_violations == [], f"oracle unexpectedly rejects {value!r}: {oracle_violations}"

    spec = GraphSpec.from_dict(doc)  # must not raise
    assert spec.max_transitions == value
    assert type(spec.max_transitions) is type(value), (
        f"{value!r} must be preserved as a {type(value).__name__}, never normalized"
    )
    assert spec.to_dict()["max_transitions"] == value
    assert spec.graph_fingerprint == validate.fingerprint("graph", doc), (
        f"max_transitions={value!r}: fingerprint diverges from the oracle"
    )


@pytest.mark.parametrize(
    ("value",),
    [(8.5,), (True,), (float("nan"),), (float("inf"),)],
    ids=["float-8.5", "bool-True", "nan", "inf"],
)
def test_max_transitions_rejected_edge_values_match_the_oracle(value):
    """8.5 has a nonzero fractional part; bool is explicitly excluded from
    "integer" by jsonschema's own type checker even though Python's bool is
    an int subclass; NaN and Infinity satisfy neither "integer" nor any
    finite number. The real oracle refuses all four as SCHEMA on
    $.max_transitions, and GraphSpec must match it exactly (MF-B1) — never
    inventing a semantic rule for what is a schema-shape defect."""
    doc = dict(_MINIMAL, max_transitions=value)
    oracle_violations = validate.validate_graphspec(doc)
    assert any(
        v.rule == "SCHEMA" and v.path == "$.max_transitions" for v in oracle_violations
    ), f"oracle unexpectedly accepts {value!r}: {oracle_violations}"

    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(doc)
    assert excinfo.value.rules == {"SCHEMA"}


def test_max_transitions_property_type_hint_includes_float():
    """MF-B3: a py.typed distribution's exported annotation must be
    truthful about what the property actually returns. GraphSpec accepts
    and preserves max_transitions=8.0 (a float — see the accepted-edge-
    values test above), so ``int | None`` alone would be a false promise
    to a type checker; the resolved annotation must include ``float`` too.

    Resolved via ``typing.get_type_hints`` rather than inspecting
    ``__annotations__`` directly, because graph.py uses ``from __future__
    import annotations`` — every annotation is a deferred string at
    runtime, and get_type_hints is the stable way to evaluate it back into
    real type objects."""
    hints = typing.get_type_hints(GraphSpec.max_transitions.fget)
    args = typing.get_args(hints["return"])
    assert int in args and float in args and type(None) in args, (
        f"max_transitions return annotation resolved to {hints['return']!r}; "
        "expected int | float | None"
    )


def test_omitting_effect_class_fingerprints_differently_from_declaring_the_default():
    """The load-bearing "no materialized defaults" property (contract/
    README.md §Fingerprints): a node that omits effect_class is NOT the
    same document as one that spells out external_effect, even though both
    resolve to the identical runtime EffectClass.

    Every graphspec fixture in the frozen corpus happens to declare
    effect_class explicitly on every node, so this uses a synthetic minimal
    pair rather than fixture content — clearly not a corpus-derived case."""
    omitted = json.loads(json.dumps(_MINIMAL))
    assert "effect_class" not in omitted["nodes"][0]

    explicit = json.loads(json.dumps(omitted))
    explicit["nodes"][0]["effect_class"] = "external_effect"

    spec_omitted = GraphSpec.from_dict(omitted)
    spec_explicit = GraphSpec.from_dict(explicit)

    assert spec_omitted.graph_fingerprint != spec_explicit.graph_fingerprint
    assert spec_omitted.graph_fingerprint == validate.fingerprint("graph", omitted)
    assert spec_explicit.graph_fingerprint == validate.fingerprint("graph", explicit)
    # Yet the RESOLVED runtime view agrees — the divergence is fingerprint-only.
    assert spec_omitted.nodes[0].effect_class == spec_explicit.nodes[0].effect_class == (
        EffectClass.EXTERNAL_EFFECT
    )


# --------------------------------------------------------------------------- #
# 4. Named required-behavior checks, anchored to specific fixtures           #
# --------------------------------------------------------------------------- #


def test_cyclic_graph_without_max_transitions_fails():
    wrapper = next(w for p, w in NEGATIVE if p.name == "41-graphspec-r11-cycle-no-max.json")
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(wrapper["instance"])
    assert excinfo.value.rules == {"R11"}


def test_terminal_reachable_cycle_passes():
    wrapper = next(w for p, w in POSITIVE if p.name == "03-graphspec-cyclic-max-transitions.json")
    spec = GraphSpec.from_dict(wrapper["instance"])
    assert spec.max_transitions == 25


def test_trap_scc_fails():
    wrapper = next(w for p, w in NEGATIVE if p.name == "40-graphspec-r11-trap-region.json")
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(wrapper["instance"])
    assert excinfo.value.rules == {"R11"}
    # Both trapped nodes ('b' and 'c') are individually reported.
    trapped = {v.message for v in excinfo.value.violations}
    assert any("'b'" in m for m in trapped)
    assert any("'c'" in m for m in trapped)


def test_end_as_declared_node_fails():
    wrapper = next(w for p, w in NEGATIVE if p.name == "39-graphspec-r8-node-named-end.json")
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(wrapper["instance"])
    assert excinfo.value.rules == {"R8"}


@pytest.mark.parametrize(
    ("path", "wrapper"), [(p, w) for p, w in NEGATIVE if (w.get("rule") == "R12")],
    ids=_ids([(p, w) for p, w in NEGATIVE if w.get("rule") == "R12"]),
)
def test_requires_motus_negative_cases_match_the_validator(path, wrapper):
    spec = wrapper["instance"]
    my_violations = [v for v in _graphspec_violations_for_test(spec) if v.rule == "R12"]
    oracle_violations = [v for v in validate.validate_graphspec(spec) if v.rule == "R12"]
    assert my_violations and oracle_violations


def _graphspec_violations_for_test(data: dict):
    """Small helper: run GraphSpec construction and collect violations
    without depending on graph.py's private functions directly — keeps this
    test file honest about only using the public API."""
    try:
        GraphSpec.from_dict(data)
    except GraphSpecValidationError as exc:
        return list(exc.violations)
    return []


@pytest.mark.parametrize(
    "requires_motus",
    [
        ">=0.5,<1.0",       # plain range — legal
        "==0.5.0",          # exact — legal
        "~=0.5",            # compatible release, two segments — legal
        "===custom-build",  # arbitrary equality — legal
        "!=0.4.*",          # wildcard with != — legal
    ],
)
def test_requires_motus_legal_forms_match_the_validator(requires_motus):
    base = _read(FIXTURES_DIR / "01-graphspec-linear.json")["instance"]
    spec_dict = dict(base, requires_motus=requires_motus)

    oracle_violations = [v for v in validate.validate_graphspec(spec_dict) if v.rule == "R12"]
    assert oracle_violations == [], f"oracle itself rejects {requires_motus!r}: {oracle_violations}"

    spec = GraphSpec.from_dict(spec_dict)  # must not raise
    assert spec.requires_motus == requires_motus


@pytest.mark.parametrize(
    "requires_motus",
    [
        "~=1",              # ~= needs >= 2 release segments
        ">=1.0,",           # trailing empty clause
        ">1.0.*",           # wildcard on an operator other than ==/!=
    ],
)
def test_requires_motus_illegal_forms_match_the_validator(requires_motus):
    base = _read(FIXTURES_DIR / "01-graphspec-linear.json")["instance"]
    spec_dict = dict(base, requires_motus=requires_motus)

    oracle_violations = [v for v in validate.validate_graphspec(spec_dict) if v.rule == "R12"]
    assert oracle_violations, f"oracle unexpectedly accepts {requires_motus!r}"

    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(spec_dict)
    assert excinfo.value.rules == {"R12"}


# --------------------------------------------------------------------------- #
# 5. Effect classes                                                          #
# --------------------------------------------------------------------------- #


def test_effect_classes_round_trip():
    base = _read(FIXTURES_DIR / "02-graphspec-routed-default.json")["instance"]
    spec = GraphSpec.from_dict(base)
    by_name = {n.name: n.effect_class for n in spec.nodes}
    assert by_name == {
        "fetch": EffectClass.RECORDED_EFFECT,
        "classify": EffectClass.PURE,
        "store": EffectClass.EXTERNAL_EFFECT,
        "escalate": EffectClass.EXTERNAL_EFFECT,
    }


def test_undeclared_effect_class_means_external_effect():
    data = {
        "schema_version": "1.0.0", "name": "n", "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a"}],  # no effect_class at all
        "transitions": {"a": {"kind": "terminal"}},
    }
    spec = GraphSpec.from_dict(data)
    assert spec.nodes[0].effect_class == EffectClass.EXTERNAL_EFFECT
    assert "effect_class" not in spec.to_dict()["nodes"][0]  # the raw form stays silent


# --------------------------------------------------------------------------- #
# 6. NodeFailed                                                              #
# --------------------------------------------------------------------------- #


def test_node_failed_preserves_object_identity_of_its_state():
    sentinel = object()
    err = NodeFailed(node="fetch", state=sentinel)
    assert err.state is sentinel
    assert err.node == "fetch"


def test_node_failed_accepts_none_state():
    err = NodeFailed(node="fetch", state=None)
    assert err.state is None


# --------------------------------------------------------------------------- #
# 7. This implementation's OWN schema-shape tests — no frozen fixture        #
#    exercises this ground (test_graphspec_fixture_corpus_shape proves it),  #
#    so these are mine, clearly labeled, not derived from contract/fixtures. #
# --------------------------------------------------------------------------- #


_MINIMAL = {
    "schema_version": "1.0.0", "name": "n", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a"}],
    "transitions": {"a": {"kind": "terminal"}},
}


def _mutate(**overrides):
    data = json.loads(json.dumps(_MINIMAL))
    data.update(overrides)
    return data


@pytest.mark.parametrize("missing_key", [
    "schema_version", "name", "version", "entry", "nodes", "transitions",
])
def test_own_missing_required_key_is_rejected(missing_key):
    data = json.loads(json.dumps(_MINIMAL))
    del data[missing_key]
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_unexpected_top_level_property_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(unexpected="surprise"))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_wrong_schema_version_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(schema_version="2.0.0"))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_non_string_entry_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(entry=7))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_empty_name_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(name=""))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_bad_node_name_pattern_is_rejected():
    data = _mutate()
    data["nodes"] = [{"name": "1-bad-start"}]
    data["transitions"] = {"1-bad-start": {"kind": "terminal"}}
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_bad_effect_class_enum_is_rejected():
    data = _mutate()
    data["nodes"] = [{"name": "a", "effect_class": "not-a-real-class"}]
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_empty_nodes_array_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(nodes=[]))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_non_object_max_transitions_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(max_transitions="25"))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_zero_max_transitions_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(max_transitions=0))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_max_transitions_as_bool_is_rejected():
    """A Python bool IS an int subclass; max_transitions=True must not slip
    through as 1 — this is exactly the kind of thing a naive isinstance(x,
    int) check gets wrong."""
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(_mutate(max_transitions=True))
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_transition_missing_kind_is_rejected():
    data = _mutate()
    data["transitions"] = {"a": {}}
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_next_transition_missing_to_is_rejected():
    data = _mutate()
    data["nodes"] = [{"name": "a"}, {"name": "b"}]
    data["transitions"] = {"a": {"kind": "next"}, "b": {"kind": "terminal"}}
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_route_transition_with_empty_map_is_rejected():
    """R6's first half — an empty route map — IS schema-detectable
    (graphspec.v1.schema.json pins ``map: {minProperties: 1}``) and is
    reported as SCHEMA, exactly like contract/validate.py's own jsonschema-
    backed pass reports it: never as a fabricated 'R6' violation. Only R6's
    other half — duplicate JSON object keys inside one map — is genuinely
    unenforceable post-parse (json.loads keeps the last occurrence; the
    duplication is gone before any validator, jsonschema included, can see
    it), which is why no test attempts to construct that case at all."""
    data = _mutate()
    data["transitions"] = {"a": {"kind": "route", "on": "x", "map": {}}}

    oracle_violations = validate.validate_graphspec(data)
    assert any(
        v.rule == "SCHEMA" and v.path == "$.transitions.a.map" for v in oracle_violations
    ), f"oracle unexpectedly accepts an empty route map: {oracle_violations}"
    assert not any(v.rule == "R6" for v in oracle_violations), (
        "the oracle itself never reports rule R6 — an empty map is SCHEMA, "
        "not a distinct semantic rule"
    )

    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_terminal_transition_rejects_extra_keys():
    data = _mutate()
    data["transitions"] = {"a": {"kind": "terminal", "to": "nowhere"}}
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_route_map_value_must_be_string():
    data = _mutate()
    data["nodes"] = [{"name": "a"}, {"name": "b"}]
    data["transitions"] = {
        "a": {"kind": "route", "on": "x", "map": {"yes": 7}},
        "b": {"kind": "terminal"},
    }
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_top_level_non_object_is_rejected():
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict([])  # type: ignore[arg-type]
    assert excinfo.value.rules == {"SCHEMA"}


def test_own_schema_shape_violations_short_circuit_before_semantic_rules():
    """A document broken enough to fail SCHEMA must not also report R-rules
    — running flow analysis over structurally broken input would cascade
    noise, exactly as contract/validate.py documents its own layering."""
    data = _mutate(entry="ghost")  # would ALSO be R1 if shape passed
    del data["name"]  # but shape is broken first
    with pytest.raises(GraphSpecValidationError) as excinfo:
        GraphSpec.from_dict(data)
    assert excinfo.value.rules == {"SCHEMA"}


# --------------------------------------------------------------------------- #
# 8. Immutability                                                            #
# --------------------------------------------------------------------------- #


def test_graphspec_source_cannot_be_mutated_after_construction():
    original = json.loads(json.dumps(_MINIMAL))
    spec = GraphSpec.from_dict(original)
    original["name"] = "mutated-after-the-fact"
    assert spec.name == "n", "GraphSpec must not observe changes to the caller's dict"


def test_graphspec_to_dict_is_a_copy_not_a_view():
    spec = GraphSpec.from_dict(json.loads(json.dumps(_MINIMAL)))
    out = spec.to_dict()
    out["name"] = "mutated"
    assert spec.name == "n"
