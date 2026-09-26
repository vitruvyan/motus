"""ADR-043 verification/query interface contract tests."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "contract" / "verification-query.v1.schema.json"


def artifact(input_id: str = "manifest") -> dict:
    return {"input_id": input_id, "kind": "system_manifest", "media_type": "application/json", "document": {"schema_version": "1.0.0"}}


def inspect_request() -> dict:
    return {"interface_version": "1.0.0", "message_type": "request", "operation": "inspect", "artifact": artifact()}


def result() -> dict:
    return {
        "interface_version": "1.0.0", "message_type": "result", "operation": "inspect", "outcome": "invalid",
        "scope": {"scope_kind": "supplied_inputs", "input_ids": ["manifest"], "global_complete": False, "limitations": ["result covers only explicitly supplied inputs"]},
        "subject": {"input_id": "manifest", "kind": "system_manifest", "fingerprint": None},
        "violations": [{"rule": "SCHEMA", "path": "$", "message": "invalid manifest"}],
        "findings": [], "matches": [], "records": [],
    }


def rules(document: dict) -> set[str]:
    return {item.rule for item in validate.validate_verification_query_message(document)}


def test_schema_is_valid_and_accepts_closed_request_and_result_envelopes():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert validate.validate_verification_query_message(inspect_request()) == []
    assert validate.validate_verification_query_message(result()) == []


def test_operations_kinds_media_and_unknown_members_are_closed():
    unknown_operation = inspect_request()
    unknown_operation["operation"] = "search"
    assert rules(unknown_operation) == {"SCHEMA"}
    unknown_kind = inspect_request()
    unknown_kind["artifact"]["kind"] = "legal_opinion"
    assert rules(unknown_kind) == {"SCHEMA"}
    guessed_binary = inspect_request()
    guessed_binary["artifact"] = {"input_id": "package", "kind": "execution_evidence_package", "media_type": "application/json", "document": {}}
    assert rules(guessed_binary) == {"SCHEMA"}
    verdict = result()
    verdict["compliant"] = True
    assert rules(verdict) == {"SCHEMA"}


def test_verify_and_query_input_ids_must_be_unique():
    verify = {"interface_version": "1.0.0", "message_type": "request", "operation": "verify", "artifact": artifact("same"), "companions": [artifact("same")]}
    assert rules(verify) == {"VQ1"}
    query = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": {"kind": "artifact_identity", "artifact_kind": "system_manifest", "fingerprint": "sha256:" + "a" * 64}, "artifacts": [artifact("same"), artifact("same")]}
    assert rules(query) == {"VQ1"}


def test_binary_base64_and_resource_bounds_fail_closed(monkeypatch):
    binary = {"interface_version": "1.0.0", "message_type": "request", "operation": "inspect", "artifact": {"input_id": "package", "kind": "execution_evidence_package", "media_type": "application/zip", "content_base64": base64.b64encode(b"12345").decode("ascii")}}
    assert validate.validate_verification_query_message(binary) == []
    monkeypatch.setattr(validate, "_VQ_MAX_BINARY_TOTAL_BYTES", 4)
    assert rules(binary) == {"VQ2"}
    document = inspect_request()
    monkeypatch.setattr(validate, "_VQ_MAX_JSON_DOCUMENT_BYTES", 4)
    assert rules(document) == {"VQ2"}


def test_execution_ref_projection_requires_the_canonical_coordinate():
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": {"kind": "execution_ref", "execution_ref": "not/a/ref/0"}, "artifacts": [artifact()]}
    assert rules(request) == {"VQ3"}


def test_result_references_only_its_declared_supplied_input_scope():
    document = result()
    document["matches"].append({"input_id": "not-supplied", "kind": "system_manifest", "fingerprint": "sha256:" + "b" * 64})
    assert rules(document) == {"VQ4"}
    record = result()
    record["records"].append({"source_input_id": "not-supplied", "record_kind": "artifact", "record": {}})
    assert rules(record) == {"VQ4"}


def test_scope_cannot_be_promoted_to_global_completeness():
    document = result()
    document["scope"]["global_complete"] = True
    assert rules(document) == {"SCHEMA"}


def test_result_outcome_and_subject_are_constrained_by_operation():
    nonsense = result()
    nonsense["outcome"] = "completed"
    assert rules(nonsense) == {"SCHEMA"}

    query = result()
    query["operation"] = "query"
    query["outcome"] = "completed"
    assert rules(query) == {"SCHEMA"}
    query["subject"] = None
    assert validate.validate_verification_query_message(query) == []


def test_kind_prefixed_existing_fingerprints_are_supported():
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": {"kind": "artifact_identity", "artifact_kind": "graphspec", "fingerprint": "graph:sha256:" + "d" * 64}, "artifacts": [artifact()]}
    assert validate.validate_verification_query_message(request) == []


def test_projection_source_must_name_a_supplied_artifact_of_the_required_kind():
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": {"kind": "controls_for_risk", "registry_input_id": "manifest", "risk_id": "risk-1"}, "artifacts": [artifact()]}
    assert rules(request) == {"VQ4"}


def test_closed_projection_vocabulary_has_no_predicate_or_script_escape():
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "query", "projection": {"kind": "artifact_identity", "artifact_kind": "system_manifest", "fingerprint": "sha256:" + "c" * 64, "predicate": "$.compliant == true"}, "artifacts": [artifact()]}
    assert rules(request) == {"SCHEMA"}


def test_validation_does_not_mutate_the_message():
    document = inspect_request()
    original = copy.deepcopy(document)
    assert validate.validate_verification_query_message(document) == []
    assert document == original
