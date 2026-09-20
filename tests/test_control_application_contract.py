"""Standing tests for the ADR-036 ControlApplication contract surface."""

from __future__ import annotations

import importlib.util
import json
import subprocess
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


def test_application_fingerprint_is_derived_and_object_key_order_independent():
    document = _application()
    reordered = {
        "evidence": document["evidence"],
        "observed_at": document["observed_at"],
        "outcome": document["outcome"],
        "enforcement_point": document["enforcement_point"],
        "manifest_fingerprint": document["manifest_fingerprint"],
        "execution_ref": document["execution_ref"],
        "control_id": document["control_id"],
        "registry_fingerprint": document["registry_fingerprint"],
        "schema_version": document["schema_version"],
    }

    expected = validate.control_application_fingerprint(document)
    assert expected.startswith("sha256:")
    assert len(expected) == len("sha256:") + 64
    assert validate.control_application_fingerprint(reordered) == expected


def test_application_fingerprint_changes_when_the_event_changes():
    document = _application()
    changed = json.loads(json.dumps(document))
    changed["outcome"] = "allowed"

    assert (
        validate.control_application_fingerprint(changed)
        != validate.control_application_fingerprint(document)
    )


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


def test_execution_ref_rejects_blank_identity_components():
    document = _application()
    document["execution_ref"] = "   /writer-1/42"

    violations = validate.validate_control_application(document)

    assert {item.rule for item in violations} == {"CA1"}


def test_execution_ref_bounds_identifier_components_and_total_input():
    overlong_identity = _application()
    overlong_identity["execution_ref"] = "x" * 201 + "/writer-1/42"
    huge_locator = _application()
    huge_locator["execution_ref"] = "x" * 1_000_000 + "/writer-1/42"

    identity_violations = validate.validate_control_application(overlong_identity)
    huge_violations = validate.validate_control_application(huge_locator)

    assert {item.rule for item in identity_violations} == {"CA1"}
    assert {item.rule for item in huge_violations} == {"SCHEMA"}


def test_cli_accepts_a_valid_control_application(tmp_path):
    path = tmp_path / "application.json"
    path.write_text(json.dumps(_application()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(CONTRACT_DIR / "validate.py"),
            "control-application",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""
