"""A number commits to the characters it was written as (ADR-024, rule J2).

The defect these pin: T11 digests PARSED values, so a genuine `5e+18` and a
rewritten `5000000000000000511.0` are the same IEEE-754 double, produce the same
digest, and share a root — while `jq`, `git diff` and a human read a different
number.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State
from vitruvyan_motus.trace import NonCanonicalNumber, Trace

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


def _validate_module():
    name = "motus_contract_validate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "contract" / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = _validate_module()

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "numbers", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


@pytest.fixture()
def run():
    """A real run carrying the values that make this defect reachable."""
    def node(state):
        return (state
                .with_fact(Fact("importo", 5e18, "test", NOW))
                .with_fact(Fact("score", 0.87, "test", NOW))
                .with_fact(Fact("zero", 0.0, "test", NOW))
                .with_fact(Fact("count", 42, "test", NOW)))
    result = Runtime(SPEC, {"a": node}, sink=InMemoryTraceSink()).run(
        State.empty("x"), run_id="r1")
    return result.trace


# -- the property that makes J2 affordable ----------------------------------

@pytest.mark.parametrize("value", [
    5e18, 0.0, 0.1, 1 / 3, 1e-300, float(2 ** 53), -0.5, 1.0, 42, -7, 0,
])
def test_everything_this_contract_writes_is_already_canonical(value):
    """Zero false positives on our own output, and this is the whole reason the
    repair costs nothing: `_canonical_bytes` already serializes through
    `json.dumps`, so a document we produced carries canonical lexemes. **The
    defect was never in what we write** — it was that a verifier re-serializes
    what it parses, and re-serialization launders the difference."""
    text = json.dumps({"schema_version": "3.0.0", "v": value})
    assert validate._loads_strict(text) == {"schema_version": "3.0.0", "v": value}


def test_a_real_trace_survives_the_rule_unchanged(run):
    """Including one huge float, one ordinary one, a zero and an integer."""
    text = run.to_json()
    assert validate._loads_strict(text) == json.loads(text)
    assert Trace.from_json(text).root == run.root


# -- what it catches --------------------------------------------------------

@pytest.mark.parametrize(("lexeme", "canonical"), [
    ("5000000000000000511.0", "5e+18"),     # the tampering from #74
    ("4999999999999999489.0", "5e+18"),     # and its other end
    ("5e18", "5e+18"),                      # hand-written exponent
    ("0.10", "0.1"),                        # a trailing zero
    ("1e-400", "0.0"),                      # underflows: "zero" vs "nonzero"
    ("1E5", "100000.0"),                    # a capital E
    ("-0", "0"),                            # negative zero
])
def test_a_lexeme_the_contract_would_not_write_is_refused(lexeme, canonical):
    with pytest.raises(validate.NonCanonicalNumberError) as caught:
        validate._loads_strict(
            '{"schema_version":"3.0.0","v":%s}' % lexeme)
    assert lexeme in str(caught.value) and canonical in str(caught.value)


def test_a_number_inside_a_string_is_not_a_number(run):
    """The false positive a regular expression over the raw text produces, and
    the reason the check is hooked into the parser instead.

    `5.10` here is somebody's prose. It is not a value the digest commits to as
    a number, and refusing the document for it would be refusing a document that
    is entirely well-formed."""
    for text in ('{"schema_version":"3.0.0","note":"cost 5.10 eur"}',
                 '{"schema_version":"3.0.0","note":"5000000000000000511.0"}',
                 '{"schema_version":"3.0.0","note":"1e-400 and 0.10 and 1E5"}'):
        assert validate._loads_strict(text) == json.loads(text)


# -- where the guarantee is, and where it provably is not -------------------

def test_the_tampering_is_refused_when_the_text_is_in_reach(run):
    genuine = run.to_json()
    tampered = genuine.replace("5e+18", "5000000000000000511.0")
    assert tampered != genuine, "the fixture no longer contains the value under test"

    assert json.loads(tampered)["records"] != [], "the tampered document still parses"
    with pytest.raises(NonCanonicalNumber):
        Trace.from_json(tampered)


def test_from_dict_cannot_see_it_and_no_implementation_could(run):
    """ADR-024 decision 3, asserted rather than asserted-about.

    By the time `from_dict` is called the parser has already turned both
    documents into the same float. This test exists so that the day somebody
    "fixes" it, they find out here that there is nothing to fix — and so the
    residual stays visible instead of being quietly assumed away."""
    genuine = run.to_json()
    tampered = genuine.replace("5e+18", "5000000000000000511.0")

    from_genuine = Trace.from_dict(json.loads(genuine))
    from_tampered = Trace.from_dict(json.loads(tampered))

    assert from_genuine.root == from_tampered.root, (
        "the two parsed documents became distinguishable, which would mean the "
        "parser is no longer discarding the lexeme — re-read ADR-024"
    )
    assert from_genuine.root is not None


def test_the_cli_names_j2_and_not_j1(tmp_path, run):
    """A non-canonical number IS strict RFC 8259, so reporting it as J1 would
    send a reader looking for a duplicate key or a NaN."""
    import subprocess

    document = tmp_path / "trace.json"
    document.write_text(run.to_json().replace("5e+18", "5000000000000000511.0"))
    done = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), "trace",
         str(document)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert done.returncode == 1
    assert done.stdout.startswith("J2 $:"), done.stdout


# --------------------------------------------------------------------------- #
# The class, enumerated — not the instance                                     #
#                                                                              #
# #74 reported one member: a number rewritten to another lexeme with the same  #
# double. Fixing that alone would have left the class open, and the founder    #
# said so before the second member was found.                                  #
#                                                                              #
# The line between the two columns is the whole content of ADR-024:            #
# **a text difference may be absorbed only if a reader reads the same thing.** #
# --------------------------------------------------------------------------- #

def _reformat_number(text):
    return text.replace('"n":1.5', '"n":1.50')


def _rewrite_number_to_the_same_double(text):
    return text.replace('"big":5e+18', '"big":5000000000000000511.0')


def _escape_an_ascii_letter(text):
    return text.replace('"decision":"approved"', '"decision":"appro\\u0076ed"')


def _escape_a_non_ascii_letter(text):
    return text.replace('"note":"café"', '"note":"caf\\u00e9"')


def _escape_the_solidus(text):
    return text.replace('"path":"a/b"', '"path":"a\\/b"')


def _insert_whitespace(text):
    return text.replace(",", " , ").replace(":", " : ")


def _reorder_members(text):
    return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


#: Every value-preserving text transformation we know of, and what must happen
#: to it. A new member of the class goes in this table, and the table forces a
#: decision rather than allowing a quiet default.
CLASS_MEMBERS = [
    (_rewrite_number_to_the_same_double, "REFUSED", "J2",
     "a reader reads a different number"),
    (_reformat_number, "REFUSED", "J2",
     "a reader reads different characters where a value is"),
    # OPEN, and named rather than absent. A rule for these was written,
    # reviewed and WITHDRAWN — see #98 and ADR-024's residual. Two reasons, both
    # measured: object KEYS are structurally unreachable through CPython's
    # decoder hooks, so it covered half its own surface; and its canonical form
    # refused a FROZEN production artifact of ours for a `\u2014`.
    (_escape_an_ascii_letter, "OPEN", "#98",
     "renders as 'approved' and `grep approved` does not find it"),
    (_escape_a_non_ascii_letter, "OPEN", "#98",
     "same characters, different bytes, and the file no longer greps"),
    (_escape_the_solidus, "OPEN", "#98",
     "an escape this contract never writes"),
    (_insert_whitespace, "ABSORBED", None,
     "nobody reads whitespace, and the JSON<->JSONL equivalence needs it"),
    (_reorder_members, "ABSORBED", None,
     "nobody reads member order, and canonical JSON sorts it"),
]

SUBJECT = json.dumps(
    {"schema_version": "3.0.0", "decision": "approved", "note": "café",
     "path": "a/b", "big": 5e18, "n": 1.5, "z": 0},
    ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize(
    ("transform", "outcome", "rule", "reason"), CLASS_MEMBERS,
    ids=[f.__name__ for f, _, _, _ in CLASS_MEMBERS])
def test_every_known_member_of_the_class_is_decided(transform, outcome, rule, reason):
    """Three assertions per row, and the middle one is what makes the table
    honest: each transformation must genuinely be a member of the class —
    meaning it would otherwise have produced the same root. A row that changes
    the parsed value is not evidence of anything."""
    mutated = transform(SUBJECT)
    assert mutated != SUBJECT, "this transformation changed nothing"
    assert json.loads(mutated) == json.loads(SUBJECT), (
        "this transformation changed the parsed value, so it is not a member of "
        "the class and proves nothing about laundering"
    )

    if outcome in ("ABSORBED", "OPEN"):
        assert validate._loads_strict(mutated) == json.loads(SUBJECT), reason
    else:
        with pytest.raises(validate.StrictJSONError) as caught:
            validate._loads_strict(mutated)
        expected = (validate.NonCanonicalNumberError if rule == "J2"
                    else validate.NonCanonicalStringError)
        assert isinstance(caught.value, expected), (
            f"{rule} was expected and the wrong rule fired: {caught.value}")


def test_every_row_carries_a_verdict_and_a_reason():
    """The table's purpose is that a member of the class forces a decision.
    `ABSORBED` is a decision; `OPEN` with an issue number is a decision; a row
    with neither is somebody having noticed and moved on."""
    assert {o for _, o, _, _ in CLASS_MEMBERS} <= {"REFUSED", "ABSORBED", "OPEN"}
    for name, outcome, rule, reason in [(f.__name__, o, r, why)
                                        for f, o, r, why in CLASS_MEMBERS]:
        assert reason, name
        if outcome == "OPEN":
            assert rule and rule.startswith("#"), (
                f"{name} is open and names no issue, so nobody is going to "
                "come back to it")
    absorbed = [f.__name__ for f, o, _, _ in CLASS_MEMBERS if o == "ABSORBED"]
    assert absorbed == ["_insert_whitespace", "_reorder_members"], absorbed


@pytest.mark.parametrize("hidden", ["approved", "rejected"])
def test_an_escape_can_still_hide_a_word_from_grep_and_this_is_open(hidden):
    """**A defect, kept visible rather than kept quiet.** #98.

    A rule for this was written and withdrawn, for two measured reasons: object
    KEYS are structurally unreachable through CPython's decoder hooks, so it
    covered half its own surface; and its canonical form refused a frozen
    production artifact of ours for a `\\u2014`.

    This test asserts the hole so that closing it breaks the test and forces
    somebody to come back here and read why the first attempt failed."""
    escaped = "\\u%04x" % ord(hidden[0]) + hidden[1:]
    document = '{"schema_version":"3.0.0","v":"%s"}' % escaped
    assert json.loads(document)["v"] == hidden
    assert validate._loads_strict(document)["v"] == hidden, (
        "string escapes are refused now — good. Close #98, delete this test, "
        "and read ADR-024's residual before choosing the canonical form"
    )




# --------------------------------------------------------------------------- #
# The canonical number form, frozen against the interpreter                    #
#                                                                              #
# ADR-024 names an implementation, and naming one is not enough for a format    #
# meant to verify in ten years: `pyproject` supports >=3.10 open-endedly, and a #
# formatter change in a future interpreter would make a newer verifier refuse   #
# genuine historical evidence. These vectors are the contract; the interpreter  #
# is the thing being checked against them.                                     #
# --------------------------------------------------------------------------- #

CANONICAL_VECTORS = [
    (0.0, "0.0"),
    (-0.0, "-0.0"),
    (1.0, "1.0"),
    (0.1, "0.1"),
    (1 / 3, "0.3333333333333333"),
    (5e18, "5e+18"),
    (1e16, "1e+16"),
    (1e15, "1000000000000000.0"),
    (1e-4, "0.0001"),
    (1e-5, "1e-05"),
    (float(2 ** 53), "9007199254740992.0"),
    (2 ** 53, "9007199254740992"),
    (-0, "0"),
    (42, "42"),
    (-7, "-7"),
]


@pytest.mark.parametrize(("value", "canonical"), CANONICAL_VECTORS,
                         ids=[c for _, c in CANONICAL_VECTORS])
def test_the_canonical_number_form_is_what_the_adr_froze(value, canonical):
    """If this fails, the interpreter's formatter has moved and the contract has
    not. Do not update the table to match: a changed formatter means documents
    written before it are no longer canonical by the new rule, which is a
    schema-version question and not a test fixture."""
    assert json.dumps(value) == canonical


@pytest.mark.parametrize(("value", "canonical"), CANONICAL_VECTORS,
                         ids=[c for _, c in CANONICAL_VECTORS])
def test_every_frozen_vector_is_accepted_by_j2(value, canonical):
    assert validate._loads_strict(
        '{"schema_version":"3.0.0","v":%s}' % canonical) == {
            "schema_version": "3.0.0", "v": value}


# -- J2 is scoped by the document's own version -----------------------------

def test_j2_does_not_reach_evidence_written_before_it_existed():
    """`contract/README.md` §7: old evidence remains valid without rewriting,
    and a breaking change to a contract surface is a major version. A 1.x or
    2.x trace — or one another serializer formatted differently — was written
    under rules that did not include J2.

    Below 3.0.0 the terminal digest covers one record rather than the run
    (ADR-019), so there is no anchorable root for a lexical collision to
    attack: the rule would refuse without protecting anything."""
    tampered = '{"schema_version":"%s","v":5000000000000000511.0}'
    for older in ("1.0.0", "1.1.0", "2.0.0"):
        assert validate._loads_strict(tampered % older)["v"] == 5e18

    with pytest.raises(validate.NonCanonicalNumberError):
        validate._loads_strict(tampered % "3.0.0")


def test_escaping_j2s_scope_costs_the_attacker_the_root(run):
    """The obvious attack on a version-scoped rule: the attacker declares a
    version the rule does not govern.

    It fails, and the reason is structural rather than lucky — but it is not
    the reason a first reading suggests, and a mutation probe is what corrected
    it. `derived_root` refuses to derive anything below 3.0.0 at all (ADR-019:
    the digests do not cover `prev_hash` there, so the terminal hash covers one
    record rather than the run). So the version J2 is scoped on is the same
    version the root is scoped on, and **escaping one escapes the other**: the
    relabelled document derives no root, and there is nothing for an anchor or
    a receipt to bind to. That is exactly the state J2 exists to protect.

    Not the mechanism to state here: that `schema_version` sits inside the
    header digest. It does, but the gate returns before that line is reached,
    which is why a mutant removing it survives the entire suite as an
    equivalent mutant — recorded at the line in `contract/validate.py`.

    Pinned because the two halves of the argument live in different modules and
    a change to either would separate them silently."""
    genuine = run.to_json()
    assert run.root is not None

    key = json.dumps("schema_version")
    before, after = genuine.split(key, 1)
    relabelled = before + key + after.replace("3.0.0", "2.0.0", 1)
    assert relabelled != genuine, "the fixture no longer declares 3.0.0 as written"

    document = json.loads(relabelled)
    assert validate._lexically_governed(document) is False, (
        "the relabelling did escape J2 — that half is expected")
    assert validate.derived_root(document) is None, (
        "escaping the scope must cost the root; if this ever holds a value, "
        "the version is no longer inside the digest and J2's scoping is unsafe")

    tampered = relabelled.replace("5e+18", "5000000000000000511.0")
    assert validate._loads_strict(tampered)["records"] != [], (
        "J2 does not fire outside its scope, by design")
    assert validate.derived_root(json.loads(tampered)) is None


@pytest.mark.parametrize(("stored", "written"), [
    ("0.000001", "1e-06"),      # a store that expands the exponent
    ("1E-06", "1e-06"),         # and one that capitalises it
    ("1e+15", "1000000000000000.0"),   # and one that contracts it
])
def test_a_store_that_renumbers_produces_a_document_j2_refuses(stored, written):
    """`contract/README.md`'s storage guidance, pinned to the code.

    A store may reorder object keys freely and may not renumber, and the two
    halves point opposite ways. The first was measured by an integrator against
    0.10.0 — before `J2` existed — and carried forward into guidance that would
    have been wrong by the time anybody followed it.

    A column type that parses numbers and re-renders them writes the same
    VALUE and a different DOCUMENT. That is the whole point of `J2`, so the
    refusal is correct and the guidance has to say it.
    """
    assert json.dumps(json.loads(stored)) == written, "the fixture drifted"

    validate._loads_strict('{"schema_version":"3.0.0","v":%s}' % written)
    with pytest.raises(validate.NonCanonicalNumberError):
        validate._loads_strict('{"schema_version":"3.0.0","v":%s}' % stored)


# -- ADR-026: the root commits to the JSON value, not to the characters -----

def test_the_same_string_written_two_ways_shares_a_root_and_that_is_correct():
    """ADR-026 decision 3, and the reason #98 is closed rather than open.

    `"approved"` and `"appro\\u0076ed"` are the same JSON string — not two
    values a reader conflates, two spellings RFC 8259 defines as denoting the
    same code points. Refusing one would be accusing a document of tampering
    that is identical in meaning to one we accept, which is the worst thing a
    verifier does.

    This test exists so that the day somebody sets out to close #98, they find
    out it was decided rather than forgotten.
    """
    plain = '{"schema_version":"3.0.0","decision":"approved"}'
    escaped = '{"schema_version":"3.0.0","decision":"appro\\u0076ed"}'

    assert validate._loads_strict(plain) == validate._loads_strict(escaped)
    assert validate.canonical_json(validate._loads_strict(plain)) == \
           validate.canonical_json(validate._loads_strict(escaped))

    # And the fact that made it look like a defect is still true, and is a
    # fact about `grep` rather than about the evidence.
    assert "approved" in plain and "approved" not in escaped


def test_the_absorptions_do_not_hide_a_value_and_the_escape_does():
    """The leg of the argument for ADR-026 that does NOT work, pinned so it is
    not reused.

    The proposal reasoned that refusing escapes would force Motus to constrain
    whitespace and member order too. Measured, it would not: those do not hide
    a value from a raw-text search. The decision stands on its other leg, and
    this test is here because a true constraint reused one question past where
    it applies is more dangerous than an ordinary mistake.
    """
    value = {"decision": "approved"}
    for document in (json.dumps(value), json.dumps(value, indent=2),
                     json.dumps(value, separators=(",", ":")),
                     json.dumps({"z": 1, "decision": "approved"}, sort_keys=True)):
        assert "approved" in document

    assert "approved" not in '{"decision":"appro\\u0076ed"}'


def test_a_lone_surrogate_is_refused_and_that_is_a_decision_not_a_drift():
    """ADR-026 decision 4, as the founder settled it.

    This test asserted the opposite for one day. It was written when decision 4
    named the lone surrogate as an open residual and pinned the then-current
    answer so 1.0.0 would decide deliberately — and the founder then decided,
    against leaving it open: **a Motus string must represent a valid sequence
    of Unicode scalar values.** The old assertion is not deleted quietly, it is
    inverted in place, because the record of what changed is the point of
    having pinned it.

    The boundary itself lives in `tests/test_surrogate_boundary.py`, including
    the four cases that distinguish it from the withdrawn `J3`.
    """
    document = '{"schema_version":"3.0.0","v":"\\ud800"}'
    with pytest.raises(validate.UnpairedSurrogateError):
        validate._loads_strict(document)

    # And the reason, in one line: the value it denotes cannot be written down.
    with pytest.raises(UnicodeEncodeError):
        "\ud800".encode("utf-8")
