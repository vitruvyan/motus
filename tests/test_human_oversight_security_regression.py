"""Standing regression for the PR #182 contradictory-artifact attack."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from vitruvyan_motus import verify_human_oversight_bindings
from vitruvyan_motus.contract import validate

FIXTURES = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))["instance"]


def test_exact_application_hash_cannot_hide_a_substituted_registry():
    """`bindings_complete` must include the application's outbound bindings."""
    manifest = _fixture("310-system-manifest-valid.json")
    registry = _fixture("320-risk-control-registry-valid.json")
    execution = _fixture("311-receipt-attestation-rfc3161-claimed.json")

    manifest_fp = validate.system_manifest_fingerprint(manifest)
    registry["system"]["manifest_fingerprint"] = manifest_fp
    application = {
        "schema_version": "1.0.0",
        "registry_fingerprint": validate.risk_control_registry_fingerprint(registry),
        "control_id": "C-001",
        "execution_ref": "acme/w1/0",
        "manifest_fingerprint": manifest_fp,
        "enforcement_point": "tool_dispatch",
        "outcome": "blocked",
        "observed_at": "2026-09-21T01:31:00Z",
        "evidence": {"kind": "motus_execution"},
    }
    application_fp = validate.control_application_fingerprint(application)

    substituted_registry = copy.deepcopy(registry)
    substituted_registry["controls"][0]["title"] = "Substituted control declaration"
    substituted_registry_fp = validate.risk_control_registry_fingerprint(
        substituted_registry
    )

    oversight = {
        "schema_version": "1.0.0",
        "execution_ref": "acme/w1/0",
        "subject": {
            "kind": "control_application",
            "control_application_fingerprint": application_fp,
        },
        "actor": {"actor_ref": "operator-7", "role": "reviewer"},
        "action": "reviewed",
        "observed_at": "2026-09-21T01:32:00Z",
        "bindings": {
            "manifest_fingerprint": manifest_fp,
            "registry_fingerprint": substituted_registry_fp,
            "control_application_fingerprint": application_fp,
        },
    }

    verdict = verify_human_oversight_bindings(
        oversight,
        execution_receipt=execution,
        manifest=manifest,
        registry=substituted_registry,
        control_application=application,
    )
    findings = {item.path: item for item in verdict.findings}

    # The receipt itself genuinely names the substituted registry and exact
    # application. The contradiction lives in the application's outbound
    # registry binding and must still prevent a complete verdict.
    assert findings["$.bindings.registry_fingerprint"].status == "matched"
    assert findings["$.bindings.control_application_fingerprint"].status == "matched"
    assert (
        findings["control_application_binding:$.registry_fingerprint"].status
        == "mismatched"
    )
    assert verdict.has_mismatch is True
    assert verdict.bindings_complete is False
