"""ADR-044 adapter profile contract tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from vitruvyan_motus.contract import validate
from vitruvyan_motus.verification_query import execute_verification_query

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "contract" / "adapter-profile.v1.schema.json"
CORPUS = ROOT / "contract" / "adapter-profile-conformance.v1.json"


def request(operation: str = "receipt.retrieve") -> dict:
    document = {
        "profile_version": "1.0.0",
        "message_type": "request",
        "operation": operation,
    }
    if operation == "evidence.execute":
        document["evidence_request"] = {
            "interface_version": "1.0.0",
            "message_type": "request",
            "operation": "inspect",
            "artifact": {
                "input_id": "manifest",
                "kind": "system_manifest",
                "media_type": "application/json",
                "document": {"schema_version": "1.0.0"},
            },
        }
    else:
        document["execution_ref"] = "tenant/writer/0"
    if operation == "package.verify":
        document["package_base64"] = "bm90LWEtemlw"
    return document


def rules(document: dict) -> set[str]:
    return {
        issue.rule for issue in validate.validate_adapter_profile_message(document)
    }


def test_schema_is_valid_and_every_corpus_message_satisfies_the_profile():
    Draft202012Validator.check_schema(json.loads(SCHEMA.read_text(encoding="utf-8")))
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["corpus_version"] == "1.0.0"
    assert corpus["profile_version"] == "1.0.0"
    assert len(corpus["cases"]) >= 15
    assert {
        "unknown-string-operation-is-refused",
        "unknown-member-is-refused",
        "over-nested-request-is-resource-exhausted",
        "unrelated-evidence-result-remains-an-operational-error",
    } <= {case["case_id"] for case in corpus["cases"]}
    intentionally_invalid = {
        "unsupported-profile-version-fails-before-dispatch",
        "unsupported-evidence-interface-fails-before-dispatch",
        "non-string-operation-is-invalid-not-an-exception",
        "unknown-string-operation-is-refused",
        "unknown-member-is-refused",
        "noncanonical-execution-ref-is-invalid",
        "over-nested-request-is-resource-exhausted",
    }
    for case in corpus["cases"]:
        request_issues = validate.validate_adapter_profile_message(case["request"])
        assert bool(request_issues) == (case["case_id"] in intentionally_invalid), case["case_id"]
        if "expected_result" in case:
            assert validate.validate_adapter_profile_message(case["expected_result"]) == [], case["case_id"]
        else:
            assert case["expected_hook_error"] == "operational_exception"


def test_evidence_fixture_is_the_exact_result_of_the_real_adr043_executor():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    case = next(
        item for item in corpus["cases"]
        if item["case_id"] == "evidence-inspect-result-is-preserved"
    )
    actual = execute_verification_query(case["request"]["evidence_request"])
    assert actual == case["setup"]["evidence_result"]
    assert actual == case["expected_result"]["evidence_result"]


def test_operations_versions_members_and_shortcut_verdicts_are_closed():
    unknown = request()
    unknown["operation"] = "evidence.search"
    assert rules(unknown) == {"SCHEMA"}

    future = request()
    future["profile_version"] = "2.0.0"
    assert rules(future) == {"SCHEMA"}

    extra = request()
    extra["run_id"] = "convenient-but-not-an-identity"
    assert rules(extra) == {"SCHEMA"}

    shortcut = {
        "profile_version": "1.0.0",
        "message_type": "result",
        "operation": "receipt.retrieve",
        "outcome": "completed",
        "receipt": {},
        "verified": True,
    }
    assert rules(shortcut) == {"SCHEMA"}


def test_execution_reference_is_canonical_and_never_a_run_id_or_end_label():
    malformed = request()
    malformed["execution_ref"] = "run-123"
    assert rules(malformed) == {"AP1"}

    leading_zero = request()
    leading_zero["execution_ref"] = "tenant/writer/01"
    assert rules(leading_zero) == {"AP1"}

    # Shape validation cannot decide whether sequence 7 is BEGIN or END. The
    # retrieval source must bind it, and the profile names that duty explicitly.
    shaped = request()
    shaped["execution_ref"] = "tenant/writer/7"
    assert validate.validate_adapter_profile_message(shaped) == []

    long_but_publicly_valid = request()
    long_but_publicly_valid["execution_ref"] = (
        f"{'t' * 100}/{'w' * 100}/1{'0' * 300}"
    )
    assert len(long_but_publicly_valid["execution_ref"]) > 423
    assert validate.validate_adapter_profile_message(long_but_publicly_valid) == []


def test_package_base64_is_strict_and_resource_bounded(monkeypatch):
    malformed = request("package.verify")
    malformed["package_base64"] = "bm90LWEtemlw\n"
    assert "AP2" in rules(malformed)

    noncanonical_pad_bits = request("package.verify")
    noncanonical_pad_bits["package_base64"] = "Zh=="
    assert rules(noncanonical_pad_bits) == {"AP2"}

    canonical = request("package.verify")
    canonical["package_base64"] = "Zg=="
    assert validate.validate_adapter_profile_message(canonical) == []

    bounded = request("package.verify")
    monkeypatch.setattr(validate, "_VQ_MAX_BINARY_TOTAL_BYTES", 4)
    assert rules(bounded) == {"AP2"}


def test_adapter_json_bound_leaves_room_for_complete_base64_expansion(monkeypatch):
    assert validate._AP_MAX_JSON_TOTAL_BYTES > (
        (validate._VQ_MAX_BINARY_TOTAL_BYTES + 2) // 3 * 4 + 8192
    )
    monkeypatch.setattr(validate, "_AP_MAX_JSON_TOTAL_BYTES", 1024)
    monkeypatch.setattr(validate, "_VQ_MAX_JSON_TOTAL_BYTES", 10)
    assert validate.validate_adapter_profile_message(request()) == []


def test_embedded_adr043_direction_and_contract_are_enforced():
    wrong_direction = request("evidence.execute")
    wrong_direction["evidence_request"]["message_type"] = "result"
    assert "AP3" in rules(wrong_direction)

    invalid = request("evidence.execute")
    invalid["evidence_request"]["artifact"]["kind"] = "legal_opinion"
    assert rules(invalid) == {"AP3"}


def test_completed_receipt_result_must_carry_a_valid_motus_receipt():
    result = {
        "profile_version": "1.0.0",
        "message_type": "result",
        "operation": "receipt.retrieve",
        "outcome": "completed",
        "receipt": {"looks": "receipt-shaped"},
    }
    assert rules(result) == {"AP4"}


def test_failure_is_operational_and_cannot_carry_evidence_payloads():
    failure = {
        "profile_version": "1.0.0",
        "message_type": "result",
        "operation": "package.verify",
        "outcome": "failed",
        "failure": {"kind": "not_found", "detail": "package missing"},
    }
    assert validate.validate_adapter_profile_message(failure) == []
    failure["package_verdict"] = {
        "verdict": None,
        "transport_ok": False,
        "damaged": [],
        "trace_violations": [],
    }
    assert rules(failure) == {"SCHEMA"}


def test_validation_does_not_mutate_messages():
    document = request("evidence.execute")
    original = copy.deepcopy(document)
    assert validate.validate_adapter_profile_message(document) == []
    assert document == original
