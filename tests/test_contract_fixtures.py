"""Contract fixture suite — the executable fence of the Motus contract (MF-13).

Runs metaschema, schema and semantic validation over every fixture in
``contract/fixtures/``.  Each fixture is a wrapper object declaring what it is
(``artifact``), what should happen (``expect``), and — for negatives — which
single rule it violates (``rule``) and a substring of the expected message
(``reason_contains``): the corpus asserts that every negative fails for its
declared reason and no other.  A contract change that does not update fixtures
alongside it is incomplete by definition (contract/README.md, "Executable
fence").
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"
FIXTURES_DIR = CONTRACT_DIR / "fixtures"


def _load_validate_module():
    """Import contract/validate.py by path (contract/ is not a package)."""
    module_name = "motus_contract_validate"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, CONTRACT_DIR / "validate.py"
    )
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


FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))
FIXTURES = [(path, _read(path)) for path in FIXTURE_PATHS]
POSITIVE = [(p, w) for p, w in FIXTURES if w["expect"] == "valid"]
NEGATIVE = [(p, w) for p, w in FIXTURES if w["expect"] == "invalid"]
JSONL_EQUIVALENT = [
    (p, w) for p, w in POSITIVE if w["artifact"] == "jsonl" and w.get("equivalent_to")
]


def _ids(pairs):
    return [path.name for path, _ in pairs]


def _format(violations):
    return "\n".join(f"{v.rule} {v.path}: {v.message}" for v in violations) or "<none>"


def _run_fixture(wrapper: dict):
    """Route a wrapper through the validator the way its artifact demands.

    ``spec`` is passed when present so the spec-correlated rules (T5, T8) run;
    the optional ``expect_complete`` flag is honored for traces recorded as
    legitimately incomplete (a cancelled run is NOT one of those —
    run_cancelled is a terminal record and those fixtures validate complete).
    """
    artifact = wrapper["artifact"]
    if artifact == "graphspec":
        return validate.validate_graphspec(wrapper["instance"])
    if artifact == "trace":
        return validate.validate_trace(
            wrapper["instance"],
            spec=wrapper.get("spec"),
            expect_complete=wrapper.get("expect_complete", True),
        )
    if artifact == "jsonl":
        text = "\n".join(wrapper["lines"]) + "\n"
        violations, _doc = validate.validate_jsonl(
            text,
            spec=wrapper.get("spec"),
            expect_complete=wrapper.get("expect_complete", True),
        )
        return violations
    raise AssertionError(f"unknown artifact {artifact!r}")


# --------------------------------------------------------------------------- #
# (a) the schemas themselves are valid Draft 2020-12 schemas                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "schema_file", ["graphspec.v1.schema.json", "trace.v1.schema.json"]
)
def test_schema_passes_metaschema(schema_file):
    schema = _read(CONTRACT_DIR / schema_file)
    jsonschema.Draft202012Validator.check_schema(schema)


# --------------------------------------------------------------------------- #
# (b) every positive fixture is clean                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("path", "wrapper"), POSITIVE, ids=_ids(POSITIVE))
def test_positive_fixture_yields_zero_violations(path, wrapper):
    violations = _run_fixture(wrapper)
    assert violations == [], (
        f"{path.name} declared valid but produced:\n{_format(violations)}"
    )


# --------------------------------------------------------------------------- #
# (c) every negative fixture fails for its declared reason and no other       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("path", "wrapper"), NEGATIVE, ids=_ids(NEGATIVE))
def test_negative_fixture_fails_for_declared_reason(path, wrapper):
    violations = _run_fixture(wrapper)
    assert violations, f"{path.name} declared invalid but produced no violations"

    rule = wrapper["rule"]
    reason = wrapper["reason_contains"]
    assert any(v.rule == rule and reason in v.message for v in violations), (
        f"{path.name} expected a violation of rule {rule!r} whose message "
        f"contains {reason!r}; got:\n{_format(violations)}"
    )
    # Rule purity: every negative fixture violates exactly ONE rule (schema
    # layer fixtures declare rule "SCHEMA").  A fixture that trips extra rules
    # no longer isolates its declared defect — fix the fixture, not this line.
    assert {v.rule for v in violations} == {rule}, (
        f"{path.name} must violate rule {rule!r} and no other; "
        f"got:\n{_format(violations)}"
    )


# --------------------------------------------------------------------------- #
# (d) JSONL and JSON document are two encodings of ONE logical model          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "wrapper"), JSONL_EQUIVALENT, ids=_ids(JSONL_EQUIVALENT)
)
def test_jsonl_reassembles_to_equivalent_document(path, wrapper):
    text = "\n".join(wrapper["lines"]) + "\n"
    violations, doc = validate.validate_jsonl(
        text,
        spec=wrapper.get("spec"),
        expect_complete=wrapper.get("expect_complete", True),
    )
    assert violations == [], _format(violations)
    reference = _read(FIXTURES_DIR / wrapper["equivalent_to"])["instance"]
    assert doc == reference, (
        f"{path.name} reassembled into a document structurally different from "
        f"{wrapper['equivalent_to']}"
    )


# --------------------------------------------------------------------------- #
# corpus shape — the fence's own minimums stay pinned                         #
# --------------------------------------------------------------------------- #


# Every rule the contract advertises carries at least one negative fixture.
# A rule with no fixture is a rule nothing proves — the exact failure mode the
# README forbids ("a contract is binding exactly where a gate checks it").
# Adding a rule to the contract means adding it here AND writing its fixture.
ADVERTISED_RULES = {
    # GraphSpec structure (R6/R7/R9/R10 are runtime semantics, not static)
    "R1", "R2", "R3", "R4", "R5", "R8", "R11", "R12",
    # Trace record coherence
    "T1", "T2", "T3", "T3/INCOMPLETE", "T4", "T5", "T6", "T7", "T8", "T9", "T10",
    # Execution state machine
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11",
    # Spec binding
    "SB1", "SB2", "SB3", "SB4",
    # Header, JSON strictness, JSONL encoding, schema layer
    "H1", "J1", "JSONL1", "JSONL2", "JSONL3", "SCHEMA",
}


def test_every_advertised_rule_has_a_negative_fixture():
    covered = {wrapper["rule"] for _, wrapper in NEGATIVE}
    missing = ADVERTISED_RULES - covered
    assert not missing, f"rules with no negative fixture: {sorted(missing)}"
    unexpected = covered - ADVERTISED_RULES
    assert not unexpected, (
        f"fixtures declare rules the contract does not advertise: {sorted(unexpected)}"
    )


def test_corpus_minimums_and_wrapper_shape():
    assert len(POSITIVE) >= 13, f"corpus needs >= 13 positives, has {len(POSITIVE)}"
    assert len(NEGATIVE) >= 82, f"corpus needs >= 82 negatives, has {len(NEGATIVE)}"
    for path, wrapper in FIXTURES:
        assert wrapper["artifact"] in {"graphspec", "trace", "jsonl"}, path.name
        if wrapper["artifact"] == "jsonl":
            assert isinstance(wrapper["lines"], list) and wrapper["lines"], path.name
        else:
            assert "instance" in wrapper, path.name
        assert "spec" in wrapper, path.name
        if wrapper["expect"] == "invalid":
            assert wrapper["layer"] in {"schema", "semantic"}, path.name
            assert wrapper["rule"], path.name
            assert wrapper["reason_contains"], path.name


# --------------------------------------------------------------------------- #
# (f) J1 cases no fixture FILE can carry                                      #
#                                                                             #
# J1 is recursive strict RFC 8259 on the API path.  Two of its cases cannot   #
# live in the fixture corpus at all: a JSON file cannot contain a Python      #
# tuple, a set, or a non-string mapping key — those values only exist for an  #
# in-process caller handing the validator an already-built document.  So the  #
# corpus pins what a file can express (NaN/Infinity literals, fixtures 55/56) #
# and these tests pin the rest.                                               #
# --------------------------------------------------------------------------- #


def _happy_doc() -> dict:
    """A clean, spec-bound trace to mutate — proven valid by the corpus."""
    wrapper = _read(FIXTURES_DIR / "04-trace-happy-path.json")
    return wrapper["instance"], wrapper["spec"]


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("tuple", {"pair": (1, 2)}),
        ("set", {"members": {1, 2}}),
        ("non-string key", {7: "seven"}),
        ("nested non-finite", [1, [2, [float("nan")]]]),
    ],
)
def test_j1_rejects_non_json_values_at_any_depth(label, value):
    doc, spec = _happy_doc()
    transition = next(r for r in doc["records"] if r["kind"] == "transition")
    transition["writes"]["facts"].append(
        {
            "key": "smuggled",
            "value": value,
            "source": "test",
            "ts": transition["ts"],
        }
    )
    violations = validate.validate_trace(doc, spec=spec)
    assert {v.rule for v in violations} == {"J1"}, (
        f"a {label} value must be refused as J1; got:\n{_format(violations)}"
    )


# --------------------------------------------------------------------------- #
# (g) fingerprints are true, not decorative (SB2)                             #
# --------------------------------------------------------------------------- #


def test_positive_trace_fingerprints_are_recomputable():
    """Every spec-bound positive trace declares the fingerprint of its own spec.

    The round-3 corpus shipped a happy path whose declared graph fingerprint was
    not its spec's — decorative, and invisible until SB2 recomputed it.  This
    pins that it can never happen again.
    """
    checked = 0
    for path, wrapper in POSITIVE:
        if wrapper["artifact"] != "trace" or not wrapper.get("spec"):
            continue
        declared = wrapper["instance"]["run"]["graph"]["graph_fingerprint"]
        recomputed = validate.fingerprint("graph", wrapper["spec"])
        assert declared == recomputed, (
            f"{path.name} declares {declared} but its spec fingerprints to "
            f"{recomputed}"
        )
        checked += 1
    assert checked >= 5, f"expected several spec-bound positives, checked {checked}"


def test_canonical_json_is_key_order_independent():
    """The canonical form is what hashes agree on — key order must not matter."""
    a = {"b": 1, "a": {"d": 2, "c": [3, {"f": 4, "e": 5}]}}
    b = {"a": {"c": [3, {"e": 5, "f": 4}], "d": 2}, "b": 1}
    assert validate.canonical_json(a) == validate.canonical_json(b)
    assert validate.fingerprint("graph", a) == validate.fingerprint("graph", b)
    assert validate.fingerprint("graph", a).startswith("graph:sha256:")


# --------------------------------------------------------------------------- #
# (e) CLI smoke — the fence is reachable from a shell                         #
# --------------------------------------------------------------------------- #


def test_cli_smoke(tmp_path):
    positive = next(w for _, w in POSITIVE if w["artifact"] == "graphspec")
    negative = next(w for _, w in NEGATIVE if w["rule"] == "R1")

    good = tmp_path / "good-graphspec.json"
    good.write_text(json.dumps(positive["instance"]), encoding="utf-8")
    bad = tmp_path / "bad-graphspec.json"
    bad.write_text(json.dumps(negative["instance"]), encoding="utf-8")

    cli = [sys.executable, str(CONTRACT_DIR / "validate.py")]

    ok = subprocess.run(
        [*cli, "graphspec", str(good)], capture_output=True, text=True
    )
    assert ok.returncode == 0, f"stdout={ok.stdout!r} stderr={ok.stderr!r}"
    assert ok.stdout == ""  # violations only; a clean document prints nothing

    ko = subprocess.run([*cli, "graphspec", str(bad)], capture_output=True, text=True)
    assert ko.returncode == 1, f"stdout={ko.stdout!r} stderr={ko.stderr!r}"
    assert "R1" in ko.stdout


def test_cli_rejects_a_physical_crlf_jsonl_file(tmp_path):
    """JSONL3 must survive the trip through a real file.

    validate_jsonl() rejected CR bytes from the start, but the CLI read its
    input with universal newlines, so a CRLF file was silently converted to LF
    before the rule could see it: the API refused what the command line
    accepted.  This test exercises the bytes on disk, which is what the
    contract actually judges.
    """
    wrapper = next(
        w for _, w in POSITIVE
        if w["artifact"] == "jsonl" and w.get("equivalent_to")
    )
    stream = tmp_path / "trace.jsonl"
    stream.write_bytes(("\r\n".join(wrapper["lines"]) + "\r\n").encode("utf-8"))
    lf_stream = tmp_path / "trace-lf.jsonl"
    lf_stream.write_bytes(("\n".join(wrapper["lines"]) + "\n").encode("utf-8"))

    cli = [sys.executable, str(CONTRACT_DIR / "validate.py"), "jsonl"]
    crlf = subprocess.run([*cli, str(stream)], capture_output=True, text=True)
    assert crlf.returncode == 1, f"stdout={crlf.stdout!r} stderr={crlf.stderr!r}"
    assert "JSONL3" in crlf.stdout

    # ...and the same content with LF endings still passes, so the rule is
    # rejecting the encoding, not the document.
    lf = subprocess.run([*cli, str(lf_stream)], capture_output=True, text=True)
    assert lf.returncode == 0, f"stdout={lf.stdout!r} stderr={lf.stderr!r}"
