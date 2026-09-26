"""ADR-043 composite verification facade tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from vitruvyan_motus import execute_verification_query, verify_artifact
from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent


def fixture(name: str) -> dict:
    value = json.loads((ROOT / "contract" / "fixtures" / name).read_text("utf-8"))
    return value.get("instance", value)


def typed(kind: str, document: dict, input_id: str) -> dict:
    return {"input_id": input_id, "kind": kind, "media_type": "application/json", "document": document}


def test_trace_without_graphspec_is_not_verified_not_matched():
    result = verify_artifact(typed("trace", fixture("04-trace-happy-path.json"), "trace"))
    assert result["outcome"] == "not_verified"


def test_trace_composes_existing_spec_correlated_rules():
    trace = typed("trace", fixture("04-trace-happy-path.json"), "trace")
    spec = typed("graphspec", fixture("02-graphspec-routed-default.json"), "spec")
    result = verify_artifact(trace, [spec])
    assert result["outcome"] == "matched"


def test_control_application_without_registry_fails_closed():
    result = verify_artifact(typed("control_application", fixture("330-control-application-valid.json"), "application"))
    assert result["outcome"] == "not_verified"


def test_control_application_dispatches_to_existing_binding_verifier():
    application = typed("control_application", fixture("330-control-application-valid.json"), "application")
    registry = typed("risk_control_registry", fixture("320-risk-control-registry-valid.json"), "registry")
    result = verify_artifact(application, [registry])
    assert result["outcome"] in {"matched", "mismatched", "not_verified", "conflict"}
    assert result["findings"]
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


def test_duplicate_singular_companions_remain_conflict():
    artifact = typed("control_application", fixture("330-control-application-valid.json"), "application")
    registry = typed("risk_control_registry", fixture("320-risk-control-registry-valid.json"), "registry")
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
