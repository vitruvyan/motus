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
    text = json.dumps({"v": value})
    assert validate._loads_strict(text) == {"v": value}


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
        validate._loads_strict('{"v":%s}' % lexeme)
    assert lexeme in str(caught.value) and canonical in str(caught.value)


def test_a_number_inside_a_string_is_not_a_number(run):
    """The false positive a regular expression over the raw text produces, and
    the reason the check is hooked into the parser instead.

    `5.10` here is somebody's prose. It is not a value the digest commits to as
    a number, and refusing the document for it would be refusing a document that
    is entirely well-formed."""
    for text in ('{"note":"cost 5.10 eur"}',
                 '{"note":"5000000000000000511.0"}',
                 '{"note":"1e-400 and 0.10 and 1E5"}'):
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
