"""A Motus string denotes a sequence of Unicode scalar values (J1, ADR-026).

The founder's boundary, and the four cases that place it:

    "approved"  ==  "appro\\u0076ed"    both accepted, one root -- the same
                                        JSON string written two ways
    "\\ud83d\\ude00"                      accepted -- a surrogate PAIR is a
                                        character, U+1F600
    "\\ud800"  /  "\\udc00"               refused -- a lone surrogate denotes
                                        nothing at all

**This is not the withdrawn `J3` returning.** `J3` asked *which escape form was
written*, a question only the lexeme answers, and paid for it: CPython's
pure-Python scanner (13x slower), a recursion budget that depended on the
caller's stack, object KEYS structurally out of reach, and a frozen production
golden refused for an em-dash. This asks *what value did the reader get* --
which the parsed document answers, so the C scanner keeps running, keys are
covered because keys are in the document, and every escape form of a real
character stays exactly as valid as every other.

The defect it closes is a producer/verifier asymmetry, not a change of meaning:
`trace._encodable` has refused to WRITE a lone surrogate since 0.11.0, while
both readers accepted one and the verifier then raised `UnicodeEncodeError`
mid-fingerprint -- naming the codec instead of the document.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State, Trace
from vitruvyan_motus import trace as motus_trace

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)

HIGH = "\ud800"      # a high surrogate with no low one
LOW = "\udc00"       # a low surrogate with no high one
LAST = "\udfff"      # the top of the surrogate block
PAIR = "\U0001f600"  # what the escape pair decodes TO: one character


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
    "schema_version": "1.0.0", "name": "boundary", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


@pytest.fixture()
def run():
    def node(state):
        return state.with_fact(Fact("outcome", "approved", "policy", NOW))
    return Runtime(SPEC, {"a": node}, sink=InMemoryTraceSink()).run(
        State.empty("boundary"), run_id="r1").trace


# --------------------------------------------------------------------------- #
# The four cases, as the founder stated them                                  #
# --------------------------------------------------------------------------- #

def test_an_equivalent_escape_is_accepted_and_shares_the_root(run):
    """The half of ADR-026 that is NOT changing, pinned beside the half that is.

    `"appro\\u0076ed"` and `"approved"` are the same JSON string. Refusing one
    would be a false accusation against a document identical in meaning to one
    we accept -- the worst answer a verifier gives.
    """
    genuine = run.to_json()
    assert '"approved"' in genuine, "the fixture no longer carries the value under test"
    escaped = genuine.replace('"approved"', '"appro\\u0076ed"')
    assert escaped != genuine

    assert validate._loads_strict(escaped) == validate._loads_strict(genuine)
    assert Trace.from_json(escaped).root == run.root
    assert (validate.canonical_json(json.loads(escaped))
            == validate.canonical_json(json.loads(genuine)))


def test_a_surrogate_pair_is_a_character_and_is_accepted():
    """The escape pair is U+1F600. The decoder joins it before anyone sees it,
    so what reaches a Motus document is one scalar value and not two halves."""
    document = '{"schema_version":"3.0.0","v":"\\ud83d\\ude00"}'
    parsed = validate._loads_strict(document)
    assert parsed["v"] == PAIR
    assert len(parsed["v"]) == 1
    assert motus_trace._loads_canonical(document) == parsed
    assert parsed["v"].encode("utf-8") == b"\xf0\x9f\x98\x80"


@pytest.mark.parametrize(("label", "escape"), [
    ("high surrogate, unpaired", "\\ud800"),
    ("low surrogate, unpaired", "\\udc00"),
    ("the top of the block", "\\udfff"),
    ("a high one followed by a non-low", "\\ud800x"),
    ("a low one before a high one", "\\udc00\\ud800"),
])
def test_a_lone_surrogate_is_refused(label, escape):
    document = '{"schema_version":"3.0.0","v":"%s"}' % escape
    with pytest.raises(validate.UnpairedSurrogateError) as caught:
        validate._loads_strict(document)
    assert "unpaired surrogate" in str(caught.value), label
    assert "$.v" in str(caught.value), "a refusal that does not say where"
    with pytest.raises(ValueError):
        motus_trace._loads_canonical(document)


def test_a_lone_surrogate_in_a_member_NAME_is_refused():
    """The half `J3` could never reach. `JSONObject` calls the module-global
    `scanstring`, so no decoder hook ever sees a member name -- which is why
    this check reads the parsed document, where a key is just a key."""
    with pytest.raises(validate.UnpairedSurrogateError) as caught:
        validate._loads_strict('{"schema_version":"3.0.0","\\ud800":1}')
    assert "member name" in str(caught.value)


# --------------------------------------------------------------------------- #
# The class, enumerated: every frontier, each with its verdict and its reason  #
#                                                                             #
# A frontier added to Motus later fails here by default instead of passing by  #
# default, which is the only reason this table is a table.                     #
# --------------------------------------------------------------------------- #

def _read_via_validate(text):
    validate._loads_strict(json.dumps({"v": text}))


def _read_via_runtime(text):
    motus_trace._loads_canonical(json.dumps({"v": text}))


def _write_a_fact_value(text):
    Runtime(SPEC, {"a": lambda s: s.with_fact(Fact("o", text, "p", NOW))},
            sink=InMemoryTraceSink()).run(State.empty("x"), run_id="r")


def _write_a_fact_name(text):
    Runtime(SPEC, {"a": lambda s: s.with_fact(Fact(text, "v", "p", NOW))},
            sink=InMemoryTraceSink()).run(State.empty("x"), run_id="r")


def _write_a_run_id(text):
    Runtime(SPEC, {"a": lambda s: s}, sink=InMemoryTraceSink()).run(
        State.empty("x"), run_id=text)


def _write_state_metadata(text):
    Runtime(SPEC, {"a": lambda s: s}, sink=InMemoryTraceSink()).run(
        State.empty("x", metadata={"k": text}), run_id="r")


def _fingerprint_it(text):
    validate.fingerprint("graph", {"v": text})


def _digest_it(text):
    motus_trace._canonical_bytes({"v": text})


FRONTIERS = [
    # (name, what it does with a string, why it is in this table)
    ("verifier reader (_loads_strict)", _read_via_validate,
     "accepted one, then the verifier crashed mid-fingerprint (the defect)"),
    ("runtime reader (_loads_canonical)", _read_via_runtime,
     "accepted what this same package refused to write -- one package, two answers"),
    ("producer: a fact value", _write_a_fact_value,
     "_strict_plain_json, the path that was already correct"),
    ("producer: a fact name", _write_a_fact_name,
     "reached a digest without _strict_plain_json and raised a bare codec error"),
    ("producer: the run_id", _write_a_run_id,
     "_encodable, already correct"),
    ("producer: state metadata", _write_state_metadata,
     "_encodable, already correct"),
    ("verifier fingerprint (canonical_json)", _fingerprint_it,
     "no encoding means no canonical form means no fingerprint"),
    ("runtime digest (_canonical_bytes)", _digest_it,
     "the hot digest path: try/except, never a walk"),
]


@pytest.mark.parametrize(("name", "frontier", "why"), FRONTIERS,
                         ids=[f[0] for f in FRONTIERS])
def test_every_frontier_refuses_a_lone_surrogate(name, frontier, why):
    """Refuses -- and refuses by NAMING it, never by UnicodeEncodeError.

    A bare codec error is what several of these did before ADR-026. It is not a
    refusal: it says the codec is unhappy, not that the document is not a Motus
    document, and a caller cannot tell which of the two it is holding.
    """
    with pytest.raises(Exception) as caught:
        frontier("before " + HIGH + " after")

    # The whole chain, not just the top: a producer wraps the refusal in
    # NodeFailed, which is right -- what must not appear anywhere in it is the
    # codec's own complaint standing in for a verdict about the document.
    # Walked the way Python DISPLAYS it: `raise ... from None` sets
    # __suppress_context__, so the codec error stays on the object for a
    # debugger and is absent from every traceback a human or a log will see.
    # That distinction is the whole point -- the object may remember, the
    # verdict may not.
    chain, error = [], caught.value
    while error is not None and error not in chain:
        chain.append(error)
        error = error.__cause__ or (
            None if error.__suppress_context__ else error.__context__)
    assert not any(isinstance(x, UnicodeEncodeError) for x in chain), (
        f"{name} still lets the codec do the accusing ({why}): "
        f"{[type(x).__name__ for x in chain]}")
    assert any("surrogate" in str(x).lower() for x in chain), (
        f"{name} refuses without saying why ({why})")


@pytest.mark.parametrize(("name", "frontier", "why"), FRONTIERS,
                         ids=[f[0] for f in FRONTIERS])
def test_every_frontier_accepts_text_that_is_merely_awkward(name, frontier, why):
    """The other column, and the one that matters more.

    Three correct generalisations of real defects were shipped and withdrawn
    this month for refusing legitimate documents. An em-dash is in a frozen
    production golden of the first external integrator; an astral character is
    an emoji in somebody's payload. Both are Unicode text and both stay.
    """
    for payload in ("plain ascii", "un em-dash — e accenti perché",
                    "emoji " + PAIR, "replacement �", "tab\tand newline\n"):
        frontier(payload)


# --------------------------------------------------------------------------- #
# The two places this repair could silently rot                               #
# --------------------------------------------------------------------------- #

STRINGS = [
    "", "ascii", "un accento perché", "—", PAIR, "�",
    HIGH, LOW, LAST, "x" + HIGH, HIGH + LOW, "\ud83d" + "y", PAIR + HIGH,
    "\U0010ffff", "￿", "surrogate-looking but not: \\ud800",
]
IDS = [f"s{index}" for index, _ in enumerate(STRINGS)]


@pytest.mark.parametrize("text", STRINGS, ids=IDS)
def test_the_producer_and_the_verifier_agree_about_every_string(text):
    """The predicate is duplicated, on purpose, and duplication is how two
    sides drift -- which is the exact defect ADR-026 closes.

    It cannot be shared: this package is stdlib-only (zero runtime
    dependencies, checked against a built wheel in test_motus_packaging.py) and
    `contract/validate.py` imports `jsonschema`. So rather than sharing the
    code, this asserts the answer.
    """
    assert validate._surrogate_at(text) == motus_trace._surrogate_at(text)


@pytest.mark.parametrize("text", STRINGS, ids=IDS)
def test_the_fast_path_and_the_walk_agree(text):
    """The C serialise decides and the walk says where. If the fast path ever
    passed something the walk would catch, the document would be accepted --
    so the two are held against each other here rather than by argument."""
    document = {"k": text, text: "as a member name", "n": [{"deep": text}]}

    def by_walk():
        stack = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, str):
                if motus_trace._surrogate_at(value) is not None:
                    return True
            elif isinstance(value, dict):
                for key, item in value.items():
                    if motus_trace._surrogate_at(key) is not None:
                        return True
                    stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
        return False

    def by_fast_path():
        try:
            json.dumps(document, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False

    assert by_walk() == by_fast_path()


# --------------------------------------------------------------------------- #
# What this must NOT have changed                                             #
# --------------------------------------------------------------------------- #

def test_no_document_in_this_repository_is_refused_by_the_new_rule():
    """The measurement that decided the schema version does not move.

    Every JSON and JSONL document in the tree, including the frozen production
    goldens of the first external integrator and every anchored trace, contains
    zero unpaired surrogates -- so no root moves and no existing evidence
    becomes invalid. A genuine document could never have carried one: the
    producer has refused since 0.11.0, and before that the run raised at the
    seal rather than writing a trace.
    """
    skip = {".venv", "build", "node_modules", ".git", "dist"}
    documents = [p for pattern in ("*.json", "*.jsonl")
                 for p in ROOT.rglob(pattern) if not skip & set(p.parts)]
    assert len(documents) > 400, "the corpus shrank; re-take this measurement"

    refused = []
    for path in documents:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in raw.splitlines() or [raw]:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            try:
                validate._refuse_unpaired_surrogates(parsed)
            except validate.UnpairedSurrogateError:
                refused.append(str(path.relative_to(ROOT)))
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            try:
                validate._refuse_unpaired_surrogates(parsed)
            except validate.UnpairedSurrogateError:
                refused.append(str(path.relative_to(ROOT)))
    assert refused == [], f"the new rule refuses documents we already shipped: {refused}"


def test_the_frozen_production_golden_still_validates():
    """The artefact `J3` refused, for an em-dash. It is frozen and CI forbids
    modifying it, so it is the sharpest available test that this rule is not
    `J3` under another name."""
    golden = ROOT / "tests/compat/terraveler/golden/production-ingestion-trace.json"
    if not golden.exists():
        pytest.skip("the frozen golden is not in this checkout")
    raw = golden.read_text(encoding="utf-8")
    # The ESCAPE, not the character -- and that distinction is the whole of
    # what `J3` got wrong. The golden writes `\u2014`; `J3`'s canonical form
    # was `ensure_ascii=False`, which writes the character, so `J3` refused an
    # artefact lifted from production into a corpus CI forbids editing.
    assert "\\u2014" in raw, "the golden no longer carries the escape that broke J3"
    assert "—" not in raw, "the golden was rewritten to the character; re-take this"

    validate._loads_strict(raw)           # the reader accepts it
    motus_trace._loads_canonical(raw)     # so does the runtime's
    assert validate._refuse_unpaired_surrogates(json.loads(raw)) is None


def test_the_digest_recipe_did_not_change(run):
    """Same bytes in, same root out -- this ADR changes what the project says
    it commits to, and nothing about what it computes."""
    text = run.to_json()
    assert Trace.from_json(text).root == run.root
    assert (validate.canonical_json(json.loads(text))
            == json.dumps(json.loads(text), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# --------------------------------------------------------------------------- #
# The path an API caller takes, which has no text in it at all                 #
#                                                                             #
# Both of these were written because a mutation probe survived. The frontier   #
# table above covers every path that HOLDS TEXT; `validate_trace(doc)` holds   #
# Python objects, and a lone surrogate is a `str` — so it passed every check   #
# and blew up later, in `canonical_json`, where the message is about a codec.  #
# --------------------------------------------------------------------------- #

def _with_a_surrogate_in_it(run, where):
    document = json.loads(run.to_json())
    stack = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if item == "approved":
                    if where == "value":
                        value[key] = "approved" + HIGH
                    else:
                        del value[key]
                        value["name" + HIGH] = item
                    return document
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    raise AssertionError("the fixture no longer carries the value under test")


@pytest.mark.parametrize("where", ["value", "member name"])
def test_a_caller_who_never_serialises_is_told_which_rule_and_where(run, where):
    """It must REPORT, not raise — a validator that crashes has not validated."""
    findings = validate.validate_trace(_with_a_surrogate_in_it(run, where))

    assert [f.rule for f in findings] == ["J1"], (
        "the Python-object path lets a lone surrogate through; it will surface "
        "as UnicodeEncodeError from canonical_json, blaming the codec")
    assert "unpaired surrogate" in findings[0].message
    assert findings[0].path != "$", "reported without saying where"


@pytest.mark.parametrize("where", ["value", "member name"])
def test_the_finding_is_structural_so_nothing_downstream_is_attempted(run, where):
    """`structural` is what makes `validate_trace` report and stop.

    Without it the run continues into schema validation and the T-rules, which
    compute digests — and a string with no UTF-8 encoding has no canonical
    form, so there is nothing there to compute. The finding would arrive buried
    under schema noise about a document whose real problem is that it cannot be
    written down at all.
    """
    document = _with_a_surrogate_in_it(run, where)
    _, structural = validate._j1_violations(document)
    assert structural is True

    findings = validate.validate_trace(document)
    assert len(findings) == 1, (
        f"reported alongside {[f.rule for f in findings]} — the document was "
        "carried past the point where it stopped being computable")
