"""ADR-043 composite verification facade tests."""
from __future__ import annotations

import base64
import importlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from vitruvyan_motus import execute_verification_query, query_artifacts, verify_artifact
from vitruvyan_motus.contract import validate

verification_query = importlib.import_module("vitruvyan_motus.verification_query")

ROOT = Path(__file__).resolve().parent.parent


def fixture(name: str) -> dict:
    value = json.loads((ROOT / "contract" / "fixtures" / name).read_text("utf-8"))
    return value.get("instance", value)


def typed(kind: str, document: dict, input_id: str) -> dict:
    return {"input_id": input_id, "kind": kind, "media_type": "application/json", "document": document}


def binary(kind: str, data: bytes, input_id: str) -> dict:
    return {
        "input_id": input_id,
        "kind": kind,
        "media_type": "application/zip",
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


def test_invalid_artifact_shells_are_refused_before_outer_schema_expansion(monkeypatch):
    original = validate.validate_verification_query_message
    message_types = []

    def counted(document):
        message_types.append(document.get("message_type"))
        return original(document)

    monkeypatch.setattr(validate, "validate_verification_query_message", counted)
    result = execute_verification_query({
        "interface_version": "1.0.0",
        "message_type": "request",
        "operation": "query",
        "projection": "executions",
        "artifacts": [{} for _ in range(10_000)],
    })

    assert result["outcome"] == "invalid_request"
    assert message_types == ["result"]


def test_trace_without_graphspec_is_not_verified_not_matched():
    result = verify_artifact(typed("trace", fixture("04-trace-happy-path.json"), "trace"))
    assert result["outcome"] == "not_verified"


def test_trace_composes_existing_spec_correlated_rules():
    trace = typed("trace", fixture("04-trace-happy-path.json"), "trace")
    spec = typed("graphspec", fixture("02-graphspec-routed-default.json"), "spec")
    result = verify_artifact(trace, [spec])
    assert result["outcome"] == "matched"


def test_trace_graphspec_binding_failure_is_a_mismatch_not_invalid():
    trace = typed("trace", fixture("04-trace-happy-path.json"), "trace")
    different_spec = typed(
        "graphspec", fixture("01-graphspec-linear.json"), "different-spec",
    )

    result = verify_artifact(trace, [different_spec])

    assert result["outcome"] == "mismatched"
    assert result["violations"] == []
    assert any(item["status"] == "mismatched" for item in result["findings"])


def test_manifest_verification_preserves_an_in_flight_trace_companion():
    trace_document = fixture("04-trace-happy-path.json")
    trace_document["records"] = trace_document["records"][:-1]
    manifest_document = fixture("310-system-manifest-valid.json")
    manifest_document["bindings"]["graphs"] = [
        dict(trace_document["run"]["graph"])
    ]
    manifest = typed("system_manifest", manifest_document, "manifest")
    unrelated = fixture("04-trace-happy-path.json")
    unrelated["run"]["graph"]["name"] = "unrelated-graph"
    result = verify_artifact(manifest, [
        typed("trace", trace_document, "in-flight-trace"),
        typed("trace", unrelated, "unrelated-trace"),
    ])
    assert result["outcome"] != "invalid"
    assert [item["input_id"] for item in result["matches"]] == ["in-flight-trace"]
    assert not any(
        item["path"].startswith("$.companions[in-flight-trace]")
        for item in result["violations"]
    )


def test_manifest_trace_join_budget_fails_closed_before_authority(monkeypatch):
    module = __import__(
        "vitruvyan_motus.system_manifest",
        fromlist=["verify_system_manifest_bindings"],
    )
    monkeypatch.setattr(module, "_TRACE_JOIN_LIMIT", -1)
    manifest = typed(
        "system_manifest", fixture("310-system-manifest-valid.json"), "manifest",
    )
    result = verify_artifact(manifest)
    assert result["outcome"] in {"mismatched", "not_verified"}
    assert any(
        item["status"] == "not_verified"
        and "join budget" in item["reason"]
        for item in result["findings"]
    )


def test_control_application_without_registry_fails_closed():
    result = verify_artifact(typed("control_application", fixture("330-control-application-valid.json"), "application"))
    assert result["outcome"] == "not_verified"


def test_control_application_dispatches_to_existing_binding_verifier():
    application_document = fixture("330-control-application-valid.json")
    registry_document = fixture("320-risk-control-registry-valid.json")
    application_document["registry_fingerprint"] = (
        validate.risk_control_registry_fingerprint(registry_document)
    )
    application = typed("control_application", application_document, "application")
    registry = typed("risk_control_registry", registry_document, "registry")
    result = verify_artifact(application, [registry])
    assert result["outcome"] in {"matched", "mismatched", "not_verified", "conflict"}
    assert result["findings"]
    assert validate.validate_verification_query_message(result) == []


def test_control_application_selects_exact_registry_among_unrelated_revisions():
    application_document = fixture("330-control-application-valid.json")
    exact_document = fixture("320-risk-control-registry-valid.json")
    application_document["registry_fingerprint"] = (
        validate.risk_control_registry_fingerprint(exact_document)
    )
    application = typed("control_application", application_document, "application")
    exact = typed(
        "risk_control_registry", exact_document, "exact-registry",
    )
    unrelated_document = fixture("320-risk-control-registry-valid.json")
    unrelated_document["registry"]["version"] = "2026.09.21-2"
    unrelated = typed("risk_control_registry", unrelated_document, "unrelated")

    result = verify_artifact(application, [unrelated, exact])

    assert result["outcome"] != "conflict"
    assert [item["input_id"] for item in result["matches"]] == ["exact-registry"]


def test_verify_identifies_only_the_companions_actually_used():
    application_document = fixture("330-control-application-valid.json")
    registry_document = fixture("320-risk-control-registry-valid.json")
    application_document["registry_fingerprint"] = (
        validate.risk_control_registry_fingerprint(registry_document)
    )
    application = typed("control_application", application_document, "application")
    registry = typed("risk_control_registry", registry_document, "registry")
    unrelated = typed("graphspec", fixture("01-graphspec-linear.json"), "unrelated")
    result = verify_artifact(application, [unrelated, registry])
    assert [item["input_id"] for item in result["matches"]] == ["registry"]
    assert result["scope"]["input_ids"] == ["application", "unrelated", "registry"]


def test_profile_does_not_claim_companions_for_unrequested_evidence_kinds():
    profile_document = fixture("350-regulatory-evidence-profile-valid.json")
    profile_document["requirements"][0]["evidence"] = [
        {"kind": "risk_control_registry"},
    ]
    profile = typed("regulatory_evidence_profile", profile_document, "profile")
    unrelated = typed("graphspec", fixture("01-graphspec-linear.json"), "unrelated-spec")
    result = verify_artifact(profile, [unrelated])
    assert result["matches"] == []
    assert result["scope"]["input_ids"] == ["profile", "unrelated-spec"]


def test_profile_uses_transitive_control_application_dependencies():
    profile = typed(
        "regulatory_evidence_profile",
        fixture("350-regulatory-evidence-profile-valid.json"),
        "profile",
    )
    application = typed(
        "control_application", fixture("330-control-application-valid.json"),
        "application",
    )
    registry = typed(
        "risk_control_registry", fixture("320-risk-control-registry-valid.json"),
        "registry",
    )
    result = verify_artifact(profile, [application, registry])
    assert {item["input_id"] for item in result["matches"]} == {
        "application", "registry",
    }


def test_invalid_companion_preserves_its_structural_violations():
    application = typed("control_application", fixture("330-control-application-valid.json"), "application")
    invalid = fixture("320-risk-control-registry-valid.json")
    invalid["unexpected"] = True
    result = verify_artifact(application, [typed("risk_control_registry", invalid, "bad-registry")])
    assert result["outcome"] == "invalid"
    assert result["violations"]
    assert result["violations"][0]["path"].startswith("$.companions[bad-registry]")
    assert validate.validate_verification_query_message(result) == []


def test_limited_binary_companion_preserves_transport_findings():
    application = typed("control_application", fixture("330-control-application-valid.json"), "application")
    registry = typed("risk_control_registry", fixture("320-risk-control-registry-valid.json"), "registry")
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("oversize", b"\0" * (28 * 1024 * 1024 + 1))
    limited = binary("execution_evidence_package", target.getvalue(), "limited-package")
    result = verify_artifact(application, [registry, limited])
    assert any(
        item["path"].startswith("$.companions[limited-package]")
        and "expanded-byte limit" in item["reason"]
        for item in result["findings"]
    )
    assert validate.validate_verification_query_message(result) == []


def test_declaration_without_standalone_binding_verifier_is_not_promoted():
    registry = typed("risk_control_registry", fixture("320-risk-control-registry-valid.json"), "registry")
    result = verify_artifact(registry)
    assert result["outcome"] == "not_verified"


def test_request_dispatcher_executes_verify_and_preserves_supplied_scope():
    artifact = typed("control_application", fixture("330-control-application-valid.json"), "application")
    request = {"interface_version": "1.0.0", "message_type": "request", "operation": "verify", "artifact": artifact, "companions": []}
    result = execute_verification_query(request)
    assert result["operation"] == "verify"
    assert result["scope"]["input_ids"] == ["application"]


def test_request_dispatcher_preserves_available_ids_on_interface_errors():
    request = {
        "interface_version": "1.0.0",
        "message_type": "request",
        "operation": "inspect",
        "artifact": {
            "input_id": "named-input",
            "kind": "unsupported-kind",
            "media_type": "application/json",
            "document": {},
        },
    }
    result = execute_verification_query(request)
    assert result["outcome"] == "invalid_request"
    assert result["scope"]["input_ids"] == ["named-input"]


def test_direct_facades_preserve_all_available_ids_on_interface_errors():
    manifest = typed("system_manifest", fixture("310-system-manifest-valid.json"), "manifest")
    query_result = query_artifacts({"kind": "unsupported-projection"}, [manifest])
    assert query_result["outcome"] == "invalid_request"
    assert query_result["scope"]["input_ids"] == ["manifest"]
    assert query_result["scope"]["limitations"][0].startswith("query covers")

    invalid_companion = {
        "input_id": "bad-companion",
        "kind": "unsupported-kind",
        "media_type": "application/json",
        "document": {},
    }
    verify_result = verify_artifact(manifest, [invalid_companion])
    assert verify_result["outcome"] == "invalid_request"
    assert verify_result["scope"]["input_ids"] == ["manifest", "bad-companion"]
    assert verify_result["scope"]["limitations"][0].startswith("verify covers")


def test_invalid_request_scope_ids_are_result_bounded():
    violation = SimpleNamespace(
        rule="VQ1", path="$.artifacts", message="too many artifacts",
    )
    result = verification_query._invalid_request(
        [violation], [f"input-{index}" for index in range(10_001)], "query",
    )
    assert result["outcome"] == "invalid_request"
    assert len(result["scope"]["input_ids"]) == 10_000


def test_duplicate_singular_companions_remain_conflict():
    application_document = fixture("330-control-application-valid.json")
    registry_document = fixture("320-risk-control-registry-valid.json")
    application_document["registry_fingerprint"] = (
        validate.risk_control_registry_fingerprint(registry_document)
    )
    artifact = typed("control_application", application_document, "application")
    registry = typed("risk_control_registry", registry_document, "registry")
    duplicate = dict(registry, input_id="registry-2")
    result = verify_artifact(artifact, [registry, duplicate])
    assert result["outcome"] == "conflict"


def test_verifier_expansion_is_bounded_and_fails_closed(monkeypatch):
    module = __import__("vitruvyan_motus.system_manifest", fromlist=["verify_system_manifest_bindings"])
    findings = tuple(
        SimpleNamespace(
            path=f"$.bindings.graphs[{index}]",
            status="missing",
            expected=None,
            observed=None,
            reason="no supplied graph matched",
        )
        for index in range(10_001)
    )
    monkeypatch.setattr(
        module,
        "verify_system_manifest_bindings",
        lambda *_args, **_kwargs: SimpleNamespace(findings=findings),
    )
    artifact = typed("system_manifest", fixture("310-system-manifest-valid.json"), "manifest")
    result = verify_artifact(artifact)
    assert result["outcome"] == "not_verified"
    assert len(result["findings"]) == 10_000
    assert result["findings"][-1]["status"] == "incomplete"
    assert validate.validate_verification_query_message(result) == []


def test_every_authoritative_receipt_status_maps_to_the_closed_result_vocabulary(monkeypatch):
    statuses = (
        "established", "not established", "not yet", "claimed, unchecked", "refused",
    )
    verdict = SimpleNamespace(
        violations=(),
        findings=tuple(
            SimpleNamespace(level=f"LEVEL-{index}", status=status, reason="receipt verdict")
            for index, status in enumerate(statuses)
        ),
    )
    monkeypatch.setattr(validate, "verify", lambda *_args, **_kwargs: verdict)
    artifact = typed("execution_receipt", fixture("204-receipt-a-run-of-one-segment.json"), "receipt")
    result = verify_artifact(artifact)
    assert [item["status"] for item in result["findings"]] == [
        "matched", "not_verified", "not_verified", "not_verified", "not_verified",
    ]
    assert result["outcome"] == "not_verified"
    assert validate.validate_verification_query_message(result) == []


def test_authoritative_verifier_refusal_is_a_stable_not_verified_result(monkeypatch):
    module = __import__(
        "vitruvyan_motus.risk_control",
        fromlist=["verify_control_application_bindings"],
    )

    def refuse(*_args, **_kwargs):
        raise ValueError("inconsistent execution receipt")

    monkeypatch.setattr(module, "verify_control_application_bindings", refuse)
    application_document = fixture("330-control-application-valid.json")
    registry_document = fixture("320-risk-control-registry-valid.json")
    application_document["registry_fingerprint"] = (
        validate.risk_control_registry_fingerprint(registry_document)
    )
    application = typed("control_application", application_document, "application")
    registry = typed("risk_control_registry", registry_document, "registry")
    result = verify_artifact(application, [registry])
    assert result["outcome"] == "not_verified"
    assert any(
        item["path"] == "$.verification"
        and item["observed"] == "ValueError"
        and "authoritative verifier refused" in item["reason"]
        for item in result["findings"]
    )
    assert validate.validate_verification_query_message(result) == []


def test_invalid_outcome_survives_result_truncation(monkeypatch):
    monkeypatch.setattr(verification_query, "_RESULT_ITEM_LIMIT", 1)
    result = verification_query._bounded_result({
        "interface_version": "1.0.0",
        "message_type": "result",
        "operation": "verify",
        "outcome": "invalid",
        "scope": {
            "scope_kind": "supplied_inputs",
            "input_ids": ["invalid-manifest"],
            "global_complete": False,
            "limitations": ["supplied inputs only", "no global completeness"],
        },
        "subject": None,
        "violations": [
            {"rule": "SCHEMA", "path": "$.first", "message": "first"},
            {"rule": "SCHEMA", "path": "$.second", "message": "second"},
        ],
        "findings": [],
        "matches": [],
        "records": [],
    })
    assert result["outcome"] == "invalid"
    assert len(result["violations"]) == 1
    assert result["findings"][0]["status"] == "incomplete"


def test_nested_diagnostic_collections_are_cardinality_bounded():
    bounded = verification_query._bounded_json_strings(list(range(100)))
    assert bounded[:32] == list(range(32))
    assert bounded[32] == {"truncated_items": 68}
    assert len(bounded) == 33
