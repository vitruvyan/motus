"""Standing tests for the ADR-036 ControlApplication contract surface."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"


def _load_validate_module():
    name = "motus_contract_validate_control_application"
    spec = importlib.util.spec_from_file_location(name, CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


validate = _load_validate_module()


def _application() -> dict:
    return {
        "schema_version": "1.0.0",
        "registry_fingerprint": "sha256:" + "1" * 64,
        "control_id": "C-001",
        "execution_ref": "acme/writer-1/42",
        "manifest_fingerprint": "sha256:" + "2" * 64,
        "enforcement_point": "tool_dispatch",
        "outcome": "blocked",
        "observed_at": "2026-09-21T01:15:00Z",
        "evidence": {"kind": "motus_execution"},
    }


def test_valid_application_reports_an_event_not_effectiveness_or_compliance():
    assert validate.validate_control_application(_application()) == []


def test_manifest_binding_is_optional_when_not_available():
    document = _application()
    del document["manifest_fingerprint"]

    assert validate.validate_control_application(document) == []


def test_schema_closes_outcome_vocabulary_and_forbids_verdict_fields():
    schema = json.loads(
        (CONTRACT_DIR / "control-application.v1.schema.json").read_text("utf-8")
    )
    assert schema["properties"]["outcome"]["enum"] == [
        "applied", "blocked", "allowed", "not_applicable", "error"
    ]
    for forbidden in (
        "application_fingerprint", "effective", "passed", "compliant",
        "certified", "assurance_level", "mode",
    ):
        assert forbidden not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_in_process_non_json_application_value_is_j1_not_schema():
    document = _application()
    document["evidence"]["kind"] = ("not", "json")

    violations = validate.validate_control_application(document)

    assert violations
    assert {item.rule for item in violations} == {"J1"}
    assert any("tuple is not a JSON value" in item.message for item in violations)


def test_rfc3339_real_leap_second_is_valid():
    document = _application()
    document["observed_at"] = "2016-12-31T23:59:60Z"

    assert validate.validate_control_application(document) == []


def test_unbounded_execution_sequence_is_ca1_not_validator_crash():
    document = _application()
    document["execution_ref"] = "acme/writer-1/" + "9" * 5000

    violations = validate.validate_control_application(document)

    assert {item.rule for item in violations} == {"CA1"}
