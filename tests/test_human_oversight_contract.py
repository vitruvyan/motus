"""Standing tests for the ADR-037 HumanOversightReceipt contract surface."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"


def _load_validate_module():
    name = "motus_contract_validate_human_oversight"
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


def _receipt() -> dict:
    return {
        "schema_version": "1.0.0",
        "execution_ref": "acme/writer-1/42",
        "subject": {"kind": "decision_point", "decision_ref": "approval-gate"},
        "actor": {
            "actor_ref": "operator-7",
            "role": "reviewer",
            "authority_ref": "policy://acme/human-oversight/3",
        },
        "action": "overridden",
        "prior_disposition": "deny",
        "recorded_disposition": "allow-with-monitoring",
        "observed_at": "2026-09-21T01:15:00Z",
        "recorded_at": "2026-09-21T01:16:00Z",
        "bindings": {
            "manifest_fingerprint": "sha256:" + "1" * 64,
            "registry_fingerprint": "sha256:" + "2" * 64,
        },
        "rationale": "Operator accepted the bounded exception.",
        "references": ["ticket://change/184"],
    }


@pytest.mark.parametrize(
    "subject",
    [
        {"kind": "execution"},
        {"kind": "decision_point", "decision_ref": "approval-gate"},
        {
            "kind": "control_application",
            "control_application_fingerprint": "sha256:" + "3" * 64,
        },
    ],
)
def test_all_subject_kinds_are_execution_scoped_events(subject):
    document = _receipt()
    document["subject"] = subject
    document["action"] = "reviewed"
    del document["prior_disposition"]
    del document["recorded_disposition"]

    assert validate.validate_human_oversight_receipt(document) == []


def test_non_override_actions_forbid_override_dispositions():
    document = _receipt()
    document["action"] = "reviewed"

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"SCHEMA"}


def test_override_requires_both_prior_and_replacement_dispositions():
    document = _receipt()
    del document["recorded_disposition"]

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"SCHEMA"}


def test_override_must_name_a_decision_or_control_application_subject():
    document = _receipt()
    document["subject"] = {"kind": "execution"}

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"SCHEMA"}


def test_duplicate_control_application_bindings_must_agree():
    document = _receipt()
    document["action"] = "reviewed"
    del document["prior_disposition"]
    del document["recorded_disposition"]
    document["subject"] = {
        "kind": "control_application",
        "control_application_fingerprint": "sha256:" + "3" * 64,
    }
    document["bindings"]["control_application_fingerprint"] = (
        "sha256:" + "4" * 64
    )

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"HO4"}


def test_schema_closes_actions_and_forbids_verdict_or_asserted_identity_fields():
    schema = json.loads(
        (CONTRACT_DIR / "human-oversight-receipt.v1.schema.json").read_text("utf-8")
    )
    assert schema["properties"]["action"]["enum"] == [
        "reviewed", "approved", "rejected", "overridden", "escalated", "abstained"
    ]
    for forbidden in (
        "oversight_fingerprint", "human_verified", "identity_verified",
        "authority_verified", "compliant", "certified", "assurance_level", "mode",
    ):
        assert forbidden not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_receipt_fingerprint_is_derived_and_object_key_order_independent():
    document = _receipt()
    reordered = {key: document[key] for key in reversed(document)}

    expected = validate.human_oversight_receipt_fingerprint(document)
    assert expected.startswith("sha256:")
    assert len(expected) == len("sha256:") + 64
    assert validate.human_oversight_receipt_fingerprint(reordered) == expected


def test_receipt_fingerprint_changes_when_the_event_changes():
    document = _receipt()
    changed = json.loads(json.dumps(document))
    changed["actor"]["actor_ref"] = "operator-8"

    assert (
        validate.human_oversight_receipt_fingerprint(changed)
        != validate.human_oversight_receipt_fingerprint(document)
    )


def test_in_process_non_json_value_is_j1_not_schema():
    document = _receipt()
    document["subject"]["decision_ref"] = ("not", "json")

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"J1"}


def test_rfc3339_real_leap_second_is_valid_for_both_timestamps():
    document = _receipt()
    document["observed_at"] = "2016-12-31T23:59:60Z"
    document["recorded_at"] = "2016-12-31T23:59:60Z"

    assert validate.validate_human_oversight_receipt(document) == []


def test_unbounded_execution_sequence_is_ho1_not_validator_crash():
    document = _receipt()
    document["execution_ref"] = "acme/writer-1/" + "9" * 5000

    violations = validate.validate_human_oversight_receipt(document)

    assert {item.rule for item in violations} == {"HO1"}


def test_cli_accepts_a_valid_human_oversight_receipt(tmp_path):
    path = tmp_path / "oversight.json"
    path.write_text(json.dumps(_receipt()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(CONTRACT_DIR / "validate.py"),
            "human-oversight-receipt",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == ""
