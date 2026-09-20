"""Standing tests for the ADR-036 Risk & Control Registry contract surface."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"


def _load_validate_module():
    name = "motus_contract_validate_risk_control_registry"
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


def _registry() -> dict:
    return {
        "schema_version": "1.0.0",
        "registry": {
            "id": "acme/ai-governance",
            "version": "2026.09.21-1",
        },
        "system": {
            "system_id": "acme/credit-ai",
            "manifest_fingerprint": "sha256:" + "1" * 64,
        },
        "risks": [
            {
                "risk_id": "R-001",
                "title": "Untrusted instructions influence a tool request",
                "likelihood": "operator-scale-medium",
            },
            {
                "risk_id": "R-002",
                "title": "Sensitive data reaches an unintended destination",
            },
        ],
        "controls": [
            {
                "control_id": "C-001",
                "title": "Tool capability gate",
                "enforcement_point": "tool_dispatch",
                "risk_refs": ["R-001", "R-002"],
            },
            {
                "control_id": "C-002",
                "title": "Human review queue",
                "risk_refs": ["R-001"],
            },
        ],
        "created_at": "2026-09-21T00:30:00Z",
        "supersedes": None,
    }


def test_valid_registry_is_only_a_well_formed_declaration():
    document = _registry()
    document["risks"][0]["category"] = "operator-defined-category"
    document["controls"][0]["control_type"] = "operator-defined-type"

    assert validate.validate_risk_control_registry(document) == []


def test_empty_registry_and_unaddressed_risk_are_valid_declarations():
    empty = _registry()
    empty["risks"] = []
    empty["controls"] = []

    unaddressed = _registry()
    unaddressed["controls"] = []

    assert validate.validate_risk_control_registry(empty) == []
    assert validate.validate_risk_control_registry(unaddressed) == []


def test_risk_and_control_namespaces_are_independent():
    document = _registry()
    document["controls"][0]["control_id"] = "R-001"

    assert validate.validate_risk_control_registry(document) == []


def test_registry_schema_forbids_self_identity_and_operational_verdicts():
    schema = json.loads(
        (CONTRACT_DIR / "risk-control-registry.v1.schema.json").read_text("utf-8")
    )
    assert "registry_fingerprint" not in schema["properties"]
    assert "compliant" not in schema["properties"]
    assert "effective" not in schema["$defs"]["Control"]["properties"]
    assert "passed" not in schema["$defs"]["Control"]["properties"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Control"]["additionalProperties"] is False


def test_in_process_non_json_registry_value_is_j1_not_schema():
    document = _registry()
    document["risks"][0]["description"] = ("not", "json")

    violations = validate.validate_risk_control_registry(document)

    assert violations
    assert {item.rule for item in violations} == {"J1"}
    assert any("tuple is not a JSON value" in item.message for item in violations)


def test_rfc3339_real_leap_second_is_valid():
    document = _registry()
    document["created_at"] = "2016-12-31T23:59:60Z"

    assert validate.validate_risk_control_registry(document) == []
