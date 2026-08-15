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
    # Through `_loads_strict`, the scoped entry point, and NOT through
    # `_refuse_unpaired_surrogates` directly. The primitive is unscoped by
    # design; what decides whether a document we shipped is refused is the
    # reader a caller actually holds. This test asserted the primitive, passed,
    # and then failed the day a genuine v0.5.0 trace joined the corpus — which
    # is the measurement it was supposed to be making.
    # Every DOT-DIRECTORY, not a list of names. That is the general form of
    # the mistake this line made: it skipped `.venv`, `.git` and three build
    # outputs, and missed `.claude/worktrees/`, where agent scratch checkouts
    # live. The working tree held 675 documents, **480 of them gitignored
    # scratch**, and the guard below was calibrated on that number — so it
    # asserted `> 400` and could not pass on a clean checkout, which holds 193.
    # A round found it by running the suite from a `git archive` export
    # instead of from the working tree, which is what CI clones.
    #
    # Agent worktrees, editor caches, tox environments and virtualenvs are all
    # dot-directories, and none of them is the repository.
    documents = [
        path for pattern in ("*.json", "*.jsonl") for path in ROOT.rglob(pattern)
        if not any(part.startswith(".") for part in path.relative_to(ROOT).parts)
        and not {"build", "dist", "node_modules"} & set(path.parts)
    ]
    tracked = len(documents)
    assert tracked > 150, (
        f"the corpus is {tracked} documents; a clean checkout has ~193. Either "
        "it shrank, or this walk is picking up something that is not the "
        "repository — re-take the measurement rather than lowering the number")

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
                validate._loads_strict(line)
            except validate.UnpairedSurrogateError:
                refused.append(str(path.relative_to(ROOT)))
            except ValueError:
                pass  # some other rule, or not a Motus document at all
        if raw.strip():
            try:
                validate._loads_strict(raw)
            except validate.UnpairedSurrogateError:
                refused.append(str(path.relative_to(ROOT)))
            except ValueError:
                pass
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


# --------------------------------------------------------------------------- #
# The scope — the half ADR-026's first draft got wrong                        #
#                                                                             #
# The draft said J2's version scoping "does not apply here and must not be    #
# copied", because J1 has meant strict RFC 8259 at every version. True of the #
# prose and false of the product: v0.5.0 ran to completion, wrote a schema     #
# 1.0.0 trace holding a surrogateescape filename, and its own shipped          #
# validator returned []. So did `main` today. An adversarial round produced    #
# that document from the tag and it is frozen beside this file.                #
# --------------------------------------------------------------------------- #

LEGACY = ROOT / "tests/legacy/v0.5.0-trace-with-a-surrogateescape-filename.json"

#: The artefact's own digest, pinned here rather than relied on from its path.
#:
#: It first landed under `tests/compat/`, and `check_frozen_paths.py` refused
#: it — correctly: that is the frozen contract corpus, and adding to it is a
#: contract act the founder approves, not something an implementation PR does
#: on its way past. The founder moved it here instead, and pinning the digest
#: is the stronger half of that decision: a path guard protects a location, and
#: this protects the BYTES. Edit the file anywhere and this test fails.
#:
#: Whether it belongs in the compatibility corpus is a question for 1.0.0,
#: which is when that corpus is actually built. It is a good candidate: a
#: document a prior release produced, that later releases must keep verifying,
#: is exactly what such a corpus is for.
LEGACY_SHA256 = "8e12ff83c4fd5b9959e04a128e006c614857f187287c92b7166bc7a41710e7bb"


def test_a_trace_v0_5_0_wrote_and_v0_5_0_called_valid_is_still_valid():
    """The evidence `contract/README.md` promises stays valid without rewriting.

    `report\\udcff.csv` is exactly what `surrogateescape` yields for filesystem
    byte 0xFF. Serialised with `ensure_ascii` the document is pure ASCII, so it
    survives `jsonb`, a text column, HTTP — anything — byte for byte, which is
    what makes "somebody still holds one of these" the default assumption
    rather than a hypothesis.
    """
    import hashlib

    payload = LEGACY.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == LEGACY_SHA256, (
        "this artefact is evidence: v0.5.0 wrote these exact bytes and v0.5.0's "
        "own validator called them valid. Regenerating it from today's code "
        "would make it prove nothing, so it is pinned rather than described")

    raw = payload.decode("utf-8")
    assert raw.isascii(), "the fixture stopped being wire-safe; re-take this"
    assert "udcff" in raw.lower(), "the fixture no longer carries the surrogate"

    document = json.loads(raw)
    assert document["schema_version"] == "1.0.0"

    assert validate.validate_trace(document) == []
    validate._loads_strict(raw)
    motus_trace._loads_canonical(raw)
    assert Trace.from_json(raw).to_dict()["schema_version"] == "1.0.0"


@pytest.mark.parametrize(("version", "verdict"), [
    ("1.0.0", "accept"),   # v0.5.0 wrote these
    ("1.1.0", "accept"),   # v0.6.1 and v0.7.0 wrote these
    ("2.0.0", "refuse"),   # v0.8.1 raised at the seal: no genuine one exists
    ("3.0.0", "refuse"),
])
def test_the_surrogate_refusal_is_scoped_by_the_documents_own_version(version, verdict):
    """Measured against the shipped releases, not reasoned about.

    The boundary is 2.0.0 because that is where the per-record digest arrived
    and `_canonical_bytes` began refusing a string with no UTF-8 encoding — by
    accident at first, by name since 0.11.0.
    """
    # A TRACE-shaped document, and that is load-bearing rather than tidy: a
    # GraphSpec declares `schema_version` too, in its own namespace, so the
    # scope reads `run` and not the version key alone. A bare fragment is not
    # old trace evidence and is governed.
    document = ('{"schema_version":"%s","run":{"run_id":"r"},"records":[],'
                '"v":"x\\ud800"}' % version)
    for name, reader in (("verifier", validate._loads_strict),
                         ("runtime", motus_trace._loads_canonical)):
        try:
            reader(document)
            got = "accept"
        except ValueError:
            got = "refuse"
        assert got == verdict, f"{name} says {got} for schema {version}"


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "2.0.0", "3.0.0"])
def test_the_two_sides_agree_about_J2_at_every_version_too(version):
    """Found by the same round, and it is the same defect one rule over: the
    runtime applied J2 unconditionally while the verifier scoped it to 3.0.0,
    so `Trace.from_json` refused 1.x and 2.x documents `validate.py` accepts.

    A false accusation, which `contract/README.md` ranks as the worst answer a
    verifier gives — and this one came from the library, about evidence.
    """
    document = ('{"schema_version":"%s","run":{"run_id":"r"},"records":[],'
                '"v":5000000000000000511.0}' % version)
    verdicts = []
    for reader in (validate._loads_strict, motus_trace._loads_canonical):
        try:
            reader(document)
            verdicts.append("accept")
        except ValueError:
            verdicts.append("refuse")
    assert verdicts[0] == verdicts[1], f"schema {version}: {verdicts}"
    assert verdicts[0] == ("refuse" if version == "3.0.0" else "accept")


def test_the_producer_is_scoped_by_nothing_because_it_writes_3_0_0():
    """The scope is a property of READING old evidence. Writing is not scoped:
    a run produces 3.0.0 and refuses at the boundary, always."""
    with pytest.raises(ValueError):
        motus_trace._strict_plain_json({"v": HIGH})
    with pytest.raises(ValueError):
        motus_trace._encodable(HIGH)


# --------------------------------------------------------------------------- #
# Every string position that reaches a record, swept rather than listed       #
# --------------------------------------------------------------------------- #

def _fact_key(text):
    return Fact(text, "v", "src", NOW)


def _fact_source(text):
    return Fact("k", "v", text, NOW)


def _fact_value(text):
    return Fact("k", text, "src", NOW)


def _decision_key(text):
    from vitruvyan_motus import Decision
    return Decision(text, "v", NOW)


def _decision_reason(text):
    from vitruvyan_motus import Decision
    return Decision("k", "v", NOW, reason=text)


def _rejection_what(text):
    from vitruvyan_motus import Rejection
    return Rejection(text, "r", NOW)


def _rejection_reason(text):
    from vitruvyan_motus import Rejection
    return Rejection("w", text, NOW)


def _redact_policy(text):
    from vitruvyan_motus import redact
    return redact({"a": 1}, text)


def _receipt_id(text):
    from vitruvyan_motus import EffectReceipt
    return EffectReceipt(text)


def _effect_description(text):
    from vitruvyan_motus import EffectClass, EffectDescriptor
    return EffectDescriptor(EffectClass.EXTERNAL_EFFECT, text)


def _effect_idempotency_key(text):
    from vitruvyan_motus import EffectClass, EffectDescriptor
    return EffectDescriptor(EffectClass.EXTERNAL_EFFECT, "d", idempotency_key=text)


POSITIONS = [
    ("Fact.key", _fact_key), ("Fact.source", _fact_source),
    ("Fact.value", _fact_value),
    ("Decision.key", _decision_key), ("Decision.reason", _decision_reason),
    ("Rejection.what", _rejection_what), ("Rejection.reason", _rejection_reason),
    ("RedactedValue.policy_ref", _redact_policy),
    ("EffectReceipt.receipt_id", _receipt_id),
    ("EffectDescriptor.description", _effect_description),
    ("EffectDescriptor.idempotency_key", _effect_idempotency_key),
]


@pytest.mark.parametrize(("name", "build"), POSITIONS, ids=[p[0] for p in POSITIONS])
def test_every_string_position_refuses_at_construction(name, build):
    """`Fact.value` was the only one checked. The other ten were `isinstance`
    and nothing else, so the string travelled to `_canonical_bytes` at seal
    time and raised THERE — out of `Runtime.run()` rather than as `NodeFailed`,
    so the caller got no state and no trace and the sink kept a run with no
    terminal record.

    That is verbatim the outcome `_encodable`'s docstring describes as the
    defect it closed in 0.11.0. It closed it at one position out of eleven, and
    the surrogate-boundary work then wrote a §2.2 sentence promising all of
    them: *the runtime never carries what it cannot write.*

    `EffectReceipt.receipt_id` and `idempotency_key` are the realistic ones:
    guarantees.md section 2 makes them an adapter's strings by definition, so
    they are external input by construction.
    """
    with pytest.raises(ValueError) as caught:
        build("before " + HIGH + " after")
    assert "surrogate" in str(caught.value).lower()

    build("perfectly ordinary — with an em-dash and " + PAIR)


@pytest.mark.parametrize(("name", "build"), POSITIONS, ids=[p[0] for p in POSITIONS])
def test_a_refusal_inside_a_node_is_a_node_failure_and_keeps_the_evidence(name, build):
    """The property §2.2's boundary clause is really about: refusing at
    construction means the run fails as a NODE failure, so the caller still has
    state and trace and the sink still received a terminal record."""
    if name.startswith(("EffectReceipt", "EffectDescriptor", "RedactedValue")):
        pytest.skip("not constructed inside a state write in this fixture")

    def node(state):
        built = build("x" + HIGH)
        return state.with_fact(built) if isinstance(built, Fact) else state

    from vitruvyan_motus import NodeFailed
    with pytest.raises(NodeFailed) as caught:
        Runtime(SPEC, {"a": node}, sink=InMemoryTraceSink()).run(
            State.empty("x"), run_id="r")
    assert caught.value.trace is not None, "no trace: the caller cannot see what ran"
    assert caught.value.state is not None


# --------------------------------------------------------------------------- #
# Two defects the repair itself introduced, found by attacking it             #
# --------------------------------------------------------------------------- #

def test_a_clean_document_deeper_than_the_encoder_goes_is_accepted():
    """The `RecursionError` arm fell through to the unconditional refusal, so a
    document with no surrogate anywhere was refused with a message saying that
    should be impossible.

    The C encoder failing to answer is not the encoder refusing. The walk below
    it is iterative precisely so that it can answer, and it did — 'clean'.
    """
    deep = current = {}
    for _ in range(12000):
        current["n"] = {}
        current = current["n"]
    current["v"] = "no surrogate anywhere in here"

    assert validate._refuse_unpaired_surrogates(deep) is None
    assert motus_trace._refuse_unpaired_surrogates(deep) is None


def test_a_finding_about_a_member_name_can_actually_be_printed():
    """`_j1_violations` reported the bad member name and kept descending with
    `f"{path}.{key}"`, so every deeper finding's `path` embedded the surrogate
    and `validate.main` raised `UnicodeEncodeError` trying to print its own
    report. Naming the place with a string that cannot be written down is the
    crash one step later."""
    document = {"schema_version": "3.0.0", "run": {"run_id": "r"},
                "records": [{"seq": 1, "kind": "run_started",
                             "payload": {"a" + HIGH: {"b": "x" + HIGH}}}]}
    findings = validate.validate_trace(document)
    assert findings
    for finding in findings:
        rendered = f"{finding.rule} {finding.path}: {finding.message}"
        rendered.encode("utf-8")          # this is what printing does
        json.dumps(rendered)              # and this is what logging does


@pytest.mark.parametrize(("name", "build"), [
    ("GraphSpec.from_dict, the graph name", lambda text: GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": text, "version": "1.0.0", "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}}})),
])
def test_two_frontiers_the_first_sweep_missed(name, build):
    """The sweep presented itself as complete and was not. Both of these
    refused — with a bare `UnicodeEncodeError`, which says the codec is unhappy
    rather than that the document is not a Motus document."""
    with pytest.raises(Exception) as caught:
        build("g" + HIGH)
    assert not isinstance(caught.value, UnicodeEncodeError), name
    assert "surrogate" in str(caught.value).lower()

    build("an ordinary — name " + PAIR)


def test_a_commitment_log_refuses_a_tenant_it_cannot_digest():
    """The same frontier on the commitment side, and the one with teeth: this
    string reached `_stem` (a directory name) and `_canonical_bytes` (a leaf
    digest), and `CommitmentLog.begin` had already fsynced a window line by the
    time the second one raised."""
    import tempfile

    from vitruvyan_motus import commitlog

    for field in ("tenant", "writer_id"):
        arguments = {"tenant": "acme", "writer_id": "w1", field: "x" + HIGH}
        with pytest.raises(ValueError) as caught:
            commitlog.CommitmentLog(
                Path(tempfile.mkdtemp()) / "store", **arguments)
        assert "surrogate" in str(caught.value).lower(), field


# --------------------------------------------------------------------------- #
# Four mutation probes survived these, and none was an equivalent mutant.     #
# Two of them passed because `UnicodeEncodeError`'s own message contains the  #
# word "surrogates" — the test asserted the right sentence about the wrong    #
# thing, which is the failure this project names most often.                  #
# --------------------------------------------------------------------------- #

def test_a_graphspec_refusal_is_a_violation_with_a_path_not_an_exception():
    """`_canonical_json_bytes` keeps a defensive `ValueError` whose message
    also says "surrogate", so asserting only that was indistinguishable from
    the shape check being absent. What the caller is owed is a *violation*:
    which rule, and where."""
    from vitruvyan_motus import GraphSpecValidationError

    with pytest.raises(GraphSpecValidationError) as caught:
        GraphSpec.from_dict({
            "schema_version": "1.0.0", "name": "g" + HIGH, "version": "1.0.0",
            "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
            "transitions": {"a": {"kind": "terminal"}}})

    paths = [violation.path for violation in caught.value.violations]
    assert "$.name" in paths, (
        f"refused without saying where: {caught.value.violations}")
    assert any("surrogate" in v.message for v in caught.value.violations)


def test_a_commitment_log_refusal_does_not_come_from_the_codec():
    """`UnicodeEncodeError` says "surrogates not allowed", so a test looking
    only for that word could not tell the guard from its absence — and behind
    the guard is `_stem`, which hashes the value into a directory name."""
    import tempfile

    from vitruvyan_motus import commitlog

    with pytest.raises(ValueError) as caught:
        commitlog.CommitmentLog(
            Path(tempfile.mkdtemp()) / "store", tenant="acme" + HIGH, writer_id="w")
    assert not isinstance(caught.value, UnicodeEncodeError), (
        "the codec is doing the accusing; the guard is gone")
    assert "ADR-026" in str(caught.value)


def test_validate_trace_reports_a_bad_spec_and_does_not_raise():
    """The `spec` argument reached `fingerprint("graph", spec)` with nothing
    between, so a Python-object caller got an exception out of a function whose
    contract is to RETURN findings. Nothing tested it; a probe removing the
    check survived."""
    run_result = Runtime(SPEC, {"a": lambda state: state},
                         sink=InMemoryTraceSink()).run(State.empty("x"), run_id="r")
    document = json.loads(run_result.trace.to_json())
    spec = json.loads(json.dumps({
        "schema_version": "1.0.0", "name": "boundary", "version": "1.0.0",
        "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}}}))

    for label, broken in (("a lone surrogate", {**spec, "name": "b" + HIGH}),
                          ("a NaN", {**spec, "extra": float("nan")}),
                          ("a set", {**spec, "extra": {1, 2}})):
        findings = validate.validate_trace(document, broken)
        assert findings, label
        assert all(f.rule == "J1" for f in findings), (
            f"{label}: reported as {[f.rule for f in findings]} — SB2 says the "
            "fingerprint does not match, when the truth is that the spec is "
            "not a JSON document")
        assert any(f.path.startswith("spec:$") for f in findings), (
            f"{label}: the finding does not say it is about the SPEC")


def test_a_legacy_jsonl_stream_is_read_under_the_version_its_header_declares():
    """The direction the first test of this pair missed.

    Governing record lines by the header protects old evidence as much as it
    closes the tamper: without `governed_as` a record line declares nothing, so
    it falls to the fail-closed default and gets TODAY's rules — and a 1.0.0
    stream written before J2 existed would be refused line by line.
    """
    stream = (
        '{"schema_version":"1.0.0","run":{"run_id":"r"}}\n'
        '{"seq":1,"kind":"run_started","v":5000000000000000511.0}\n'
        '{"seq":2,"kind":"run_completed","v":0.10}\n'
    )
    violations, _ = validate.validate_jsonl(stream)
    assert not [v for v in violations if v.rule in ("J1", "J2")], (
        f"a 1.0.0 stream was refused by rules written after it: {violations}")

    modern = stream.replace('"schema_version":"1.0.0"', '"schema_version":"3.0.0"')
    violations, _ = validate.validate_jsonl(modern)
    assert [v.rule for v in violations if v.rule in ("J1", "J2")] == ["J2", "J2"], (
        f"the same lines under 3.0.0 must be refused: {violations}")


# --------------------------------------------------------------------------- #
# The package's own text, held to the rule the package enforces                #
# --------------------------------------------------------------------------- #

def test_no_docstring_in_this_package_carries_a_lone_surrogate():
    """`_loads_canonical`'s docstring contained a real one, in the function
    whose subject is refusing them.

    The cause is ordinary and will recur: a docstring is a string LITERAL, so
    writing the six characters of an escape inside a non-raw one builds the
    code point rather than the text about it. A document about escapes is the
    one place where losing that distinction is guaranteed.

    The consequence is not cosmetic. `__doc__` is data this package hands out —
    `motus_explain` returns exception docstrings and `motus_where` returns
    module docstrings, verbatim — so a docstring that cannot be UTF-8 encoded
    is an MCP answer that cannot be rendered, logged or serialised. Found by an
    automated reviewer on the pull request, not by any test here, which is why
    there is now a test here.
    """
    import importlib
    import pkgutil

    import vitruvyan_motus

    offenders = []
    for module_info in pkgutil.walk_packages(
            vitruvyan_motus.__path__, vitruvyan_motus.__name__ + "."):
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue  # an optional extra that is not installed here
        for name in ["__doc__"] + dir(module):
            holder = module if name == "__doc__" else getattr(module, name, None)
            text = holder if name == "__doc__" else getattr(holder, "__doc__", None)
            if isinstance(text, str) and motus_trace._surrogate_at(text) is not None:
                offenders.append(f"{module_info.name}.{name}")

    assert offenders == [], (
        "these docstrings cannot be encoded as UTF-8, in a package that refuses "
        f"exactly that in a trace: {offenders}. Write the escape as a literal "
        "backslash, or make the docstring raw")
