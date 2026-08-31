"""A number this library writes must be one somebody else can write too.

motus#116. A Motus root is taken over the canonical TEXT of a document, so two
implementations share a root only if they write the same characters for the
same value. On some numbers they do not — and a trace carrying one of those has
a root only CPython can derive, while the validator reports T11 over it. T11
reads as tampering. **Said of a reader who did nothing wrong, it is the worst
answer this format can give**, so the value is refused where it is produced.

The rule is empirical and its evidence is in `_portable_number`: 420 values
written by both `json.dumps` and Node's `JSON.stringify`, 70 divergent, 348
accepted by the rule, none of the accepted ones divergent.
"""

from __future__ import annotations

import json

import pytest

from vitruvyan_motus import Decision, Fact, Rejection, State, Trace
from vitruvyan_motus.trace import NonPortableNumber

NOW = "2026-01-01T00:00:00Z"

#: Written differently by Python and JavaScript. Each entry carries the reason,
#: so a new member forces a decision instead of being added by reflex.
DIVERGES = [
    (-14.0, "integral float: JS drops the fractional part"),
    (0.0, "integral float, and the one people reach for first"),
    (1.0, "integral float"),
    (5e18, "integral float, and beyond the safe-integer range as well"),
    (1e-7, "below 1e-6: Python writes 1e-07, JavaScript 1e-7"),
    (1e-5, "below Python's 1e-4 positional threshold"),
    (1e16, "at Python's exponent threshold"),
    (1e21, "at JavaScript's exponent threshold"),
    (2 ** 53, "beyond Number.MAX_SAFE_INTEGER, as an int"),
    (-(2 ** 53) - 1, "beyond it downwards"),
]

#: Written identically. These are the ones an earlier, blunter draft of the
#: rule refused — and refusing them bought nothing and broke `ctx.rand()`.
AGREES = [
    (0.87, "an ordinary fraction"),
    (0.25, "exactly representable"),
    (0.125, "what ctx.rand() produced in the test that caught the blunt rule"),
    (1 / 3, "a repeating fraction, shortest-repr in both"),
    (0.0001, "at the positional boundary, inclusive"),
    (3538, "a scaled integer — the form the demo scenarios use"),
    (-14, "a plain negative integer"),
    (0, "zero as an int, which is not zero as a float"),
    (2 ** 53 - 1, "Number.MAX_SAFE_INTEGER itself"),
    (True, "a bool is not a number to JSON, and must not be refused as one"),
]


@pytest.mark.parametrize("value,reason", DIVERGES, ids=[str(v) for v, _ in DIVERGES])
def test_a_producing_boundary_refuses_what_diverges(value, reason):
    with pytest.raises(NonPortableNumber) as refusal:
        Fact("k", value, "s", NOW)
    assert "116" in str(refusal.value), reason


@pytest.mark.parametrize("value,reason", AGREES, ids=[str(v) for v, _ in AGREES])
def test_it_accepts_what_both_implementations_write_the_same_way(value, reason):
    assert Fact("k", value, "s", NOW).value == value, reason


def test_every_producing_position_is_covered_and_not_only_facts():
    """A rule that holds on facts and not on decisions is not a rule."""
    for build in (
        lambda v: Fact("k", v, "s", NOW),
        lambda v: Decision("k", v, NOW),
        lambda v: Rejection("what", "why", NOW, evidence={"n": v}),
        lambda v: State.empty("q", metadata={"m": v}),
    ):
        with pytest.raises(NonPortableNumber):
            build(-14.0)


def test_it_reaches_inside_containers():
    """The value positions take arbitrary JSON, so the walk has to descend."""
    with pytest.raises(NonPortableNumber):
        Fact("k", {"a": [1, {"b": -14.0}]}, "s", NOW)


def test_reading_a_document_written_before_the_rule_is_untouched():
    """`contract/README.md` promises old evidence stays valid without
    rewriting, and that promise is the library's too. `from_dict` shares a
    constructor with production, which is why the refusal lives at `_value`
    and not at `Trace.__init__`."""
    document = {
        "schema_version": "3.0.0",
        "run": {"run_id": "r", "graph": {}, "policy": "strict",
                "durability_profile": "in-memory",
                "replay": {"capability": "none", "constraints": []},
                "metadata": {"legacy_score": 0.87, "legacy_total": -14.0},
                "created_ts": NOW},
        "records": [],
    }
    trace = Trace.from_dict(json.loads(json.dumps(document)))
    assert trace.header["run"]["metadata"]["legacy_total"] == -14.0, (
        "an integral float can no longer be WRITTEN; a document that already "
        "carries one must still be readable, or the fix has destroyed the "
        "evidence it exists to protect")
