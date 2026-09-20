"""ADR-036 public query and ControlApplication binding tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vitruvyan_motus import (
    controls_for_risk,
    risks_for_control,
    verify_control_application_bindings,
)
from vitruvyan_motus.contract import validate

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


def _registry(*, manifest: dict | None = None) -> dict:
    document = {
        "schema_version": "1.0.0",
        "registry": {"id": "acme/governance", "version": "2026.09.21-1"},
        "risks": [
            {"risk_id": "R-001", "title": "Untrusted tool request"},
            {"risk_id": "R-002", "title": "Sensitive data disclosure"},
            {"risk_id": "R-003", "title": "Unaddressed declared risk"},
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
                "title": "Review queue",
                "risk_refs": ["R-001"],
            },
        ],
        "created_at": "2026-09-21T01:30:00Z",
    }
    if manifest is not None:
        document["system"] = {
            "system_id": manifest["system"]["id"],
            "manifest_fingerprint": validate.system_manifest_fingerprint(manifest),
        }
    return document


def _receipt() -> dict:
    wrapper = json.loads(
        (FIXTURES_DIR / "311-receipt-attestation-rfc3161-claimed.json").read_text(
            "utf-8"
        )
    )
    return wrapper["instance"]


def _manifest() -> dict:
    wrapper = json.loads(
        (FIXTURES_DIR / "310-system-manifest-valid.json").read_text("utf-8")
    )
    return wrapper["instance"]


def _application(registry: dict, *, manifest: dict | None = None) -> dict:
    document = {
        "schema_version": "1.0.0",
        "registry_fingerprint": validate.risk_control_registry_fingerprint(registry),
        "control_id": "C-001",
        "execution_ref": _receipt()["execution"]["ref"],
        "enforcement_point": "tool_dispatch",
        "outcome": "blocked",
        "observed_at": "2026-09-21T01:31:00Z",
        "evidence": {"kind": "motus_execution"},
    }
    if manifest is not None:
        document["manifest_fingerprint"] = validate.system_manifest_fingerprint(
            manifest
        )
    return document


def _by_path(verdict):
    return {item.path: item for item in verdict.findings}


def test_exact_registry_control_and_receipt_bindings_are_complete():
    registry = _registry()
    application = _application(registry)

    verdict = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )

    assert verdict.application_violations == ()
    assert verdict.registry_violations == ()
    assert verdict.application_fingerprint.startswith("sha256:")
    assert verdict.registry_fingerprint == application["registry_fingerprint"]
    assert verdict.bindings_complete is True
    assert verdict.has_mismatch is False
    assert verdict.has_unverified is False
    assert {item.status for item in verdict.findings} == {"matched"}


def test_registry_fingerprint_mismatch_is_detected():
    registry = _registry()
    application = _application(registry)
    application["registry_fingerprint"] = "sha256:" + "f" * 64

    verdict = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )

    finding = _by_path(verdict)["$.registry_fingerprint"]
    assert finding.status == "mismatched"
    assert finding.observed == validate.risk_control_registry_fingerprint(registry)
    assert verdict.has_mismatch is True


def test_control_must_exist_in_the_exact_registry_revision():
    registry = _registry()
    application = _application(registry)
    application["control_id"] = "C-404"

    verdict = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )

    assert _by_path(verdict)["$.control_id"].status == "mismatched"
    assert verdict.bindings_complete is False


def test_declared_enforcement_point_mismatch_is_detected():
    registry = _registry()
    application = _application(registry)
    application["enforcement_point"] = "after_tool_dispatch"

    verdict = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )

    assert _by_path(verdict)["$.enforcement_point"].status == "mismatched"


def test_missing_receipt_is_explicitly_not_verified():
    registry = _registry()
    application = _application(registry)

    verdict = verify_control_application_bindings(application, registry=registry)

    assert _by_path(verdict)["$.execution_ref"].status == "not verified"
    assert verdict.has_unverified is True
    assert verdict.bindings_complete is False


def test_receipt_for_another_execution_is_a_mismatch():
    registry = _registry()
    application = _application(registry)
    application["execution_ref"] = "acme/other-writer/0"

    verdict = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )

    finding = _by_path(verdict)["$.execution_ref"]
    assert finding.status == "mismatched"
    assert "acme/w1/0" in finding.observed


def test_invalid_documents_produce_no_binding_findings():
    registry = _registry()
    application = _application(registry)
    application["compliant"] = True

    verdict = verify_control_application_bindings(application, registry=registry)

    assert verdict.application_violations
    assert verdict.registry_violations == ()
    assert verdict.findings == ()
    assert verdict.application_fingerprint is None


def test_invalid_supplied_receipt_is_refused_before_binding():
    registry = _registry()
    application = _application(registry)
    receipt = _receipt()
    receipt["unexpected"] = True

    with pytest.raises(ValueError, match="receipt does not satisfy"):
        verify_control_application_bindings(
            application, registry=registry, receipt=receipt
        )


def test_system_manifest_binding_requires_the_actual_manifest_document():
    manifest = _manifest()
    registry = _registry(manifest=manifest)
    application = _application(registry, manifest=manifest)

    without_manifest = verify_control_application_bindings(
        application, registry=registry, receipt=_receipt()
    )
    complete = verify_control_application_bindings(
        application, registry=registry, manifest=manifest, receipt=_receipt()
    )

    assert _by_path(without_manifest)["$.manifest_fingerprint"].status == (
        "not verified"
    )
    assert complete.bindings_complete is True
    assert _by_path(complete)["registry:$.system.system_id"].status == "matched"


def test_supplied_manifest_fingerprint_mismatch_is_detected():
    manifest = _manifest()
    registry = _registry(manifest=manifest)
    application = _application(registry, manifest=manifest)
    application["manifest_fingerprint"] = "sha256:" + "e" * 64
    registry["system"]["manifest_fingerprint"] = application[
        "manifest_fingerprint"
    ]
    application["registry_fingerprint"] = validate.risk_control_registry_fingerprint(
        registry
    )

    verdict = verify_control_application_bindings(
        application, registry=registry, manifest=manifest, receipt=_receipt()
    )

    assert _by_path(verdict)["$.manifest_fingerprint"].status == "mismatched"


def test_queries_preserve_registry_order_and_return_detached_documents():
    registry = _registry()

    controls = controls_for_risk(registry, "R-001")
    risks = risks_for_control(registry, "C-001")

    assert [item["control_id"] for item in controls] == ["C-001", "C-002"]
    assert [item["risk_id"] for item in risks] == ["R-001", "R-002"]
    controls[0]["title"] = "caller mutation"
    risks[0]["title"] = "caller mutation"
    assert registry["controls"][0]["title"] == "Tool capability gate"
    assert registry["risks"][0]["title"] == "Untrusted tool request"


def test_queries_distinguish_unknown_from_declared_but_unlinked():
    registry = _registry()

    assert controls_for_risk(registry, "R-003") == ()
    with pytest.raises(KeyError):
        controls_for_risk(registry, "R-404")
    with pytest.raises(KeyError):
        risks_for_control(registry, "C-404")


def test_verification_does_not_mutate_any_input():
    manifest = _manifest()
    registry = _registry(manifest=manifest)
    application = _application(registry, manifest=manifest)
    receipt = _receipt()
    before = tuple(copy.deepcopy(item) for item in (
        application, registry, manifest, receipt
    ))

    verify_control_application_bindings(
        application, registry=registry, manifest=manifest, receipt=receipt
    )

    assert (application, registry, manifest, receipt) == before
