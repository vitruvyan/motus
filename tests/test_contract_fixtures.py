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


def test_corpus_minimums_and_wrapper_shape():
    assert len(POSITIVE) >= 8, f"corpus needs >= 8 positives, has {len(POSITIVE)}"
    assert len(NEGATIVE) >= 26, f"corpus needs >= 26 negatives, has {len(NEGATIVE)}"
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
