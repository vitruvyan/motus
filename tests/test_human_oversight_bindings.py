"""ADR-037 public HumanOversightReceipt binding-verifier tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vitruvyan_motus import verify_human_oversight_bindings
from vitruvyan_motus.contract import validate

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


def _fixture(name: str) -> dict:
    wrapper = json.loads((FIXTURES_DIR / name).read_text("utf-8"))
    return wrapper["instance"]


def _execution_receipt() -> dict:
    return _fixture("311-receipt-attestation-rfc3161-claimed.json")


def _manifest() -> dict:
    return _fixture("310-system-manifest-valid.json")


def _registry() -> dict:
    return _fixture("320-risk-control-registry-valid.json")


def _application(registry: dict, manifest: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "registry_fingerprint": validate.risk_control_registry_fingerprint(registry),
        "control_id": "C-001",
        "execution_ref": "acme/w1/0",
        "manifest_fingerprint": validate.system_manifest_fingerprint(manifest),
        "enforcement_point": "tool_dispatch",
        "outcome": "blocked",
        "observed_at": "2026-09-21T01:31:00Z",
        "evidence": {"kind": "motus_execution"},
    }


def _oversight(manifest: dict, registry: dict, application: dict) -> dict:
    application_fingerprint = validate.control_application_fingerprint(application)
    return {
        "schema_version": "1.0.0",
        "execution_ref": "acme/w1/0",
        "subject": {
            "kind": "control_application",
            "control_application_fingerprint": application_fingerprint,
        },
        "actor": {"actor_ref": "operator-7", "role": "reviewer"},
        "action": "reviewed",
        "observed_at": "2026-09-21T01:32:00Z",
        "bindings": {
            "manifest_fingerprint": validate.system_manifest_fingerprint(manifest),
            "registry_fingerprint": validate.risk_control_registry_fingerprint(registry),
            "control_application_fingerprint": application_fingerprint,
        },
    }


def _documents():
    manifest = _manifest()
    registry = _registry()
    application = _application(registry, manifest)
    oversight = _oversight(manifest, registry, application)
    return oversight, _execution_receipt(), manifest, registry, application


def _by_path(verdict):
    return {item.path: item for item in verdict.findings}


def test_exact_supplied_artifacts_make_every_declared_binding_match():
    oversight, execution, manifest, registry, application = _documents()

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        manifest=manifest,
        registry=registry,
        control_application=application,
    )

    assert verdict.oversight_violations == ()
    assert verdict.oversight_fingerprint == (
        validate.human_oversight_receipt_fingerprint(oversight)
    )
    assert verdict.bindings_complete is True
    assert verdict.has_mismatch is False
    assert verdict.has_unverified is False
    assert {item.status for item in verdict.findings} == {"matched"}
    assert _by_path(verdict)["control_application:$.execution_ref"].status == "matched"


def test_missing_source_material_is_not_verified_never_matched():
    oversight, _execution, _manifest_doc, _registry_doc, _application_doc = _documents()

    verdict = verify_human_oversight_bindings(oversight)

    assert verdict.has_unverified is True
    assert verdict.has_mismatch is False
    assert verdict.bindings_complete is False
    assert {item.status for item in verdict.findings} == {"not verified"}


def test_each_fingerprint_is_derived_from_the_supplied_document():
    oversight, execution, manifest, registry, application = _documents()
    oversight["bindings"]["manifest_fingerprint"] = "sha256:" + "a" * 64
    oversight["bindings"]["registry_fingerprint"] = "sha256:" + "b" * 64
    wrong_application = "sha256:" + "c" * 64
    oversight["subject"]["control_application_fingerprint"] = wrong_application
    oversight["bindings"]["control_application_fingerprint"] = wrong_application

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        manifest=manifest,
        registry=registry,
        control_application=application,
    )

    findings = _by_path(verdict)
    assert findings["$.bindings.manifest_fingerprint"].status == "mismatched"
    assert findings["$.bindings.registry_fingerprint"].status == "mismatched"
    assert findings["$.subject.control_application_fingerprint"].status == "mismatched"
    assert findings["$.bindings.control_application_fingerprint"].status == "mismatched"
    assert verdict.has_mismatch is True


def test_execution_receipt_for_another_execution_is_a_mismatch():
    oversight, execution, manifest, registry, application = _documents()
    oversight["execution_ref"] = "acme/other-writer/0"
    application["execution_ref"] = oversight["execution_ref"]
    fingerprint = validate.control_application_fingerprint(application)
    oversight["subject"]["control_application_fingerprint"] = fingerprint
    oversight["bindings"]["control_application_fingerprint"] = fingerprint

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        control_application=application,
    )

    finding = _by_path(verdict)["$.execution_ref"]
    assert finding.status == "mismatched"
    assert "acme/w1/0" in finding.observed


def test_control_application_must_name_the_same_execution():
    oversight, execution, _manifest_doc, _registry_doc, application = _documents()
    application["execution_ref"] = "acme/other-writer/0"
    fingerprint = validate.control_application_fingerprint(application)
    oversight["subject"]["control_application_fingerprint"] = fingerprint
    oversight["bindings"]["control_application_fingerprint"] = fingerprint

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        control_application=application,
    )

    finding = _by_path(verdict)["control_application:$.execution_ref"]
    assert finding.status == "mismatched"
    assert verdict.bindings_complete is False


def test_invalid_oversight_receipt_produces_no_fingerprint_or_findings():
    oversight, execution, manifest, registry, application = _documents()
    oversight["compliant"] = True

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        manifest=manifest,
        registry=registry,
        control_application=application,
    )

    assert verdict.oversight_violations
    assert verdict.oversight_fingerprint is None
    assert verdict.findings == ()


@pytest.mark.parametrize(
    ("argument", "message"),
    [
        ("execution_receipt", "execution_receipt does not satisfy"),
        ("manifest", "manifest does not satisfy"),
        ("registry", "registry does not satisfy"),
        ("control_application", "control_application does not satisfy"),
    ],
)
def test_invalid_supplied_source_is_refused(argument, message):
    oversight, execution, manifest, registry, application = _documents()
    sources = {
        "execution_receipt": execution,
        "manifest": manifest,
        "registry": registry,
        "control_application": application,
    }
    sources[argument]["unexpected"] = True

    with pytest.raises(ValueError, match=message):
        verify_human_oversight_bindings(oversight, **sources)


def test_execution_receipt_derived_identity_must_be_consistent():
    oversight, execution, _manifest_doc, _registry_doc, _application_doc = _documents()
    execution["execution"]["run_id"] = "tampered"

    with pytest.raises(ValueError, match="inconsistent derived execution identity"):
        verify_human_oversight_bindings(
            oversight, execution_receipt=execution
        )


def test_verification_does_not_mutate_any_input():
    documents = _documents()
    before = copy.deepcopy(documents)

    verify_human_oversight_bindings(
        documents[0],
        execution_receipt=documents[1],
        manifest=documents[2],
        registry=documents[3],
        control_application=documents[4],
    )

    assert documents == before


def test_irrelevant_supplied_application_creates_no_false_binding():
    oversight, execution, _manifest_doc, _registry_doc, application = _documents()
    oversight["subject"] = {"kind": "execution"}
    del oversight["bindings"]["control_application_fingerprint"]

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        control_application=application,
    )

    assert "control_application:$.execution_ref" not in _by_path(verdict)
    assert all("control_application_fingerprint" not in item.path for item in verdict.findings)
