"""Standing tests for the ADR-035 System Manifest contract surface."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"


def _load_validate_module():
    name = "motus_contract_validate_system_manifest"
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


def _manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "system": {
            "id": "acme/credit-ai",
            "manifest_version": "2026.09.20-1",
            "name": "Acme Credit AI",
        },
        "bindings": {
            "motus": {
                "runtime_version": "0.14.0",
                "trace_schema_version": "3.2.0",
            },
            "graphs": [
                {
                    "name": "credit_review",
                    "version": "1.0.0",
                    "spec_schema_version": "1.0.0",
                    "graph_fingerprint": "graph:sha256:" + "1" * 64,
                    "code_fingerprint": "code:sha256:" + "2" * 64,
                }
            ],
        },
        "declarations": {
            "operator": {"id": "acme-bank"},
            "policies": [{"policy_id": "oversight-policy"}],
            "controls": [
                {
                    "control_id": "HO-001",
                    "policy_ref": "oversight-policy",
                    "enforcement_point": "final-decision-gate",
                }
            ],
        },
        "created_at": "2026-09-20T17:00:00Z",
        "supersedes": None,
    }


def test_document_validation_does_not_turn_shape_valid_bindings_into_proof():
    """Fake-but-well-shaped binding values are still a valid declaration.

    Binding verification is deliberately a later, separate operation. If this
    test ever starts failing because validate_system_manifest recomputes runtime
    or graph identity, the presence/verification boundary has collapsed.
    """
    document = _manifest()
    document["bindings"]["motus"]["runtime_version"] = "9999.operator-claim"
    document["bindings"]["graphs"][0]["graph_fingerprint"] = (
        "graph:sha256:" + "a" * 64
    )

    assert validate.validate_system_manifest(document) == []


def test_manifest_fingerprint_is_derived_and_object_key_order_independent():
    document = _manifest()
    reordered = {
        "supersedes": document["supersedes"],
        "created_at": document["created_at"],
        "declarations": document["declarations"],
        "bindings": document["bindings"],
        "system": document["system"],
        "schema_version": document["schema_version"],
    }

    expected = validate.system_manifest_fingerprint(document)
    assert expected.startswith("sha256:")
    assert len(expected) == len("sha256:") + 64
    assert validate.system_manifest_fingerprint(reordered) == expected


def test_manifest_schema_has_no_self_declared_fingerprint_or_compliance_flag():
    schema = json.loads(
        (CONTRACT_DIR / "system-manifest.v1.schema.json").read_text("utf-8")
    )
    assert "manifest_fingerprint" not in schema["properties"]
    assert "compliant" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_rfc3339_nanosecond_created_at_is_valid():
    document = _manifest()
    document["created_at"] = "2026-09-20T17:00:00.123456789Z"

    assert validate.validate_system_manifest(document) == []


def test_cli_accepts_a_valid_system_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(CONTRACT_DIR / "validate.py"),
            "system-manifest",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""


def test_in_process_non_json_manifest_value_is_j1_not_schema():
    document = _manifest()
    document["declarations"]["components"] = [
        {"component_id": "bad", "kind": "other", "name": ("not", "json")}
    ]

    violations = validate.validate_system_manifest(document)

    assert violations
    assert {item.rule for item in violations} == {"J1"}
    assert any("tuple is not a JSON value" in item.message for item in violations)


def test_rfc3339_real_leap_second_is_valid():
    document = _manifest()
    document["created_at"] = "2016-12-31T23:59:60Z"

    assert validate.validate_system_manifest(document) == []


def test_rfc3339_spurious_leap_second_is_sm3():
    document = _manifest()
    document["created_at"] = "2016-12-30T23:59:60Z"

    violations = validate.validate_system_manifest(document)

    assert {item.rule for item in violations} == {"SM3"}
