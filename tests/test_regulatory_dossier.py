"""ADR-042 deterministic dossier export, verification, and lineage tests."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import pytest

from vitruvyan_motus import (
    pack_regulatory_dossier,
    regulatory_dossier_export_fingerprint,
    verify_regulatory_dossier,
    verify_regulatory_dossier_lineage,
)
from vitruvyan_motus.contract import validate
import vitruvyan_motus.regulatory_dossier as dossier_module

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

JSON_FIXTURES = {
    "execution_receipt": "contract/fixtures/311-receipt-attestation-rfc3161-claimed.json",
    "system_manifest": "contract/fixtures/310-system-manifest-valid.json",
    "risk_control_registry": "contract/fixtures/320-risk-control-registry-valid.json",
    "control_application": "contract/fixtures/330-control-application-valid.json",
    "human_oversight_receipt": "contract/fixtures/340-human-oversight-receipt-valid.json",
    "incident_declaration": "contract/fixtures/360-incident-declaration-valid.json",
    "capa_action": "contract/fixtures/361-capa-action-valid.json",
    "incident_capa_ledger": "contract/fixtures/362-incident-capa-ledger-valid.json",
    "retention_policy_declaration": "contract/retention-fixtures/370-retention-policy-declaration-valid.json",
    "legal_hold_declaration": "contract/retention-fixtures/371-legal-hold-declaration-valid.json",
    "retention_scope_snapshot": "contract/retention-fixtures/372-retention-scope-snapshot-valid.json",
    "retention_trigger_occurrence": "contract/retention-fixtures/373-retention-trigger-occurrence-valid.json",
    "retention_application": "contract/retention-fixtures/374-retention-application-valid.json",
    "custody_observation": "contract/retention-fixtures/375-custody-observation-valid.json",
    "ai_system_registration": "contract/ai-system-registry-fixtures/400-ai-system-registration-valid.json",
    "ai_system_registry_event": "contract/ai-system-registry-fixtures/401-ai-system-registry-event-valid.json",
    "ai_system_registry_snapshot": "contract/ai-system-registry-fixtures/402-ai-system-registry-snapshot-valid.json",
}


def profile(*kinds: str) -> dict:
    kinds = kinds or ("execution_receipt",)
    return {
        "schema_version": "1.0.0",
        "profile": {"id": "example/framework", "version": "2026-01"},
        "requirements": [{
            "requirement_ref": "REQ-001",
            "evidence": [{"kind": kind} for kind in kinds],
        }],
    }


def manifest_for(items: list[tuple[str, str, bytes, str]], *, dossier_id="review-001") -> dict:
    entries = []
    profile_fp = None
    for index, (kind, path, payload, artifact_fp) in enumerate(items):
        entries.append({
            "entry_id": f"entry-{index}",
            "artifact_kind": kind,
            "path": path,
            "media_type": "application/zip" if kind == "execution_evidence_package" else "application/json",
            "size_bytes": len(payload),
            "content_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "artifact_fingerprint": artifact_fp,
        })
        if kind == "regulatory_evidence_profile":
            profile_fp = artifact_fp
    return {
        "schema_version": "1.0.0",
        "producer_namespace": "example/governance",
        "producer_ref": "example/exporter",
        "dossier_id": dossier_id,
        "subject": {"kind": "ai-system", "id": "example/assistant"},
        "profile_fingerprint": profile_fp,
        "entries": entries,
        "observed_at": "2026-09-26T10:00:00Z",
    }


def dossier(*requested: str):
    document = profile(*requested)
    payload = validate.canonical_json(document)
    manifest = manifest_for([(
        "regulatory_evidence_profile", "profile/profile.json", payload,
        validate.regulatory_evidence_profile_fingerprint(document),
    )])
    return manifest, {"profile/profile.json": payload}


def rewrite_member(blob: bytes, name: str, payload: bytes, *, extra=None) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(blob))
    out = io.BytesIO()
    with source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, payload if info.filename == name else source.read(info))
        if extra is not None:
            target.writestr(*extra)
    return out.getvalue()


def test_pack_is_deterministic_and_preserves_exact_member_bytes():
    manifest, members = dossier("risk_control_registry")
    first = pack_regulatory_dossier(manifest, members)
    second = pack_regulatory_dossier(copy.deepcopy(manifest), dict(members))
    assert first == second
    assert regulatory_dossier_export_fingerprint(first).startswith("sha256:")
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["dossier.json", "profile/profile.json"]
        assert {info.date_time for info in archive.infolist()} == {(2026, 1, 1, 0, 0, 0)}
        assert archive.read("profile/profile.json") == members["profile/profile.json"]


def test_verify_recomputes_dossier_and_artifact_identity_then_assesses_profile():
    manifest, members = dossier("risk_control_registry")
    verdict = verify_regulatory_dossier(pack_regulatory_dossier(manifest, members))
    assert verdict.transport_ok is True
    assert verdict.dossier_fingerprint == validate.regulatory_evidence_dossier_fingerprint(manifest)
    assert verdict.export_fingerprint.startswith("sha256:")
    assert verdict.entries[0].status == "matched"
    assert verdict.profile_assessment is not None
    assert verdict.profile_assessment.findings[0].status == "missing"


def test_pack_refuses_missing_extra_non_bytes_size_and_digest():
    manifest, members = dossier()
    with pytest.raises(ValueError, match="missing"):
        pack_regulatory_dossier(manifest, {})
    with pytest.raises(ValueError, match="extra"):
        pack_regulatory_dossier(manifest, {**members, "extra.json": b"{}"})
    with pytest.raises(TypeError, match="bytes"):
        pack_regulatory_dossier(manifest, {"profile/profile.json": "no"})
    with pytest.raises(TypeError, match="paths"):
        pack_regulatory_dossier(manifest, {1: b"no"})
    changed = copy.deepcopy(manifest)
    changed["entries"][0]["size_bytes"] += 1
    with pytest.raises(ValueError, match="size"):
        pack_regulatory_dossier(changed, members)
    changed = copy.deepcopy(manifest)
    changed["entries"][0]["content_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="digest"):
        pack_regulatory_dossier(changed, members)


def test_verify_reports_exact_byte_damage_and_undeclared_members():
    manifest, members = dossier()
    blob = pack_regulatory_dossier(manifest, members)
    damaged = rewrite_member(blob, "profile/profile.json", b"{}")
    verdict = verify_regulatory_dossier(damaged)
    assert verdict.transport_ok is False
    assert verdict.entries[0].status == "mismatched"
    assert any("size_bytes" in item for item in verdict.entries[0].violations)
    original = members["profile/profile.json"]
    same_size_damage = bytes([original[0] ^ 1]) + original[1:]
    verdict = verify_regulatory_dossier(
        rewrite_member(blob, "profile/profile.json", same_size_damage)
    )
    assert any(
        "content_sha256" in item for item in verdict.entries[0].violations
    )
    extra = rewrite_member(blob, "missing", b"", extra=("undeclared.json", b"{}"))
    verdict = verify_regulatory_dossier(extra)
    assert any(item.reason == "undeclared member is present" for item in verdict.findings)


def test_verify_refuses_non_zip_duplicate_unsafe_and_symlink_members():
    assert verify_regulatory_dossier(b"not zip").transport_ok is False
    for name, configure in [
        ("../escape.json", None),
        ("linked.json", "symlink"),
    ]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("dossier.json", b"{}")
            info = zipfile.ZipInfo(name)
            if configure == "symlink":
                info.create_system = 3
                info.external_attr = (stat_mode := 0o120777) << 16
                assert stat_mode
            archive.writestr(info, b"x")
        assert verify_regulatory_dossier(out.getvalue()).transport_ok is False
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("dossier.json", b"{}")
        archive.writestr("dossier.json", b"{}")
    assert verify_regulatory_dossier(out.getvalue()).findings[0].status == "conflict"


def test_semantic_fingerprint_mismatch_is_separate_from_raw_digest():
    manifest, members = dossier()
    manifest["entries"][0]["artifact_fingerprint"] = "sha256:" + "f" * 64
    manifest["profile_fingerprint"] = "sha256:" + "f" * 64
    blob = pack_regulatory_dossier(manifest, members)
    verdict = verify_regulatory_dossier(blob)
    assert verdict.entries[0].status == "mismatched"
    assert any("artifact_fingerprint" in item for item in verdict.entries[0].violations)


def test_multiple_candidates_are_visible_profile_assessment_conflict():
    prof = profile("risk_control_registry")
    profile_bytes = validate.canonical_json(prof)
    registry = json.loads((
        ROOT
        / "contract/fixtures/320-risk-control-registry-valid.json"
    ).read_text("utf-8"))["instance"]
    items = [(
        "regulatory_evidence_profile", "profile.json", profile_bytes,
        validate.regulatory_evidence_profile_fingerprint(prof),
    )]
    for number in (1, 2):
        candidate = copy.deepcopy(registry)
        candidate["registry"]["version"] = f"2026.09.21-{number}"
        registry_bytes = validate.canonical_json(candidate)
        items.append((
            "risk_control_registry", f"registry-{number}.json", registry_bytes,
            validate.risk_control_registry_fingerprint(candidate),
        ))
    manifest = manifest_for(items)
    members = {path: payload for _, path, payload, _ in items}
    verdict = verify_regulatory_dossier(pack_regulatory_dossier(manifest, members))
    assert verdict.profile_assessment is None
    assert verdict.findings[0].status == "conflict"


def test_unrequested_multiple_candidates_do_not_block_exact_profile_assessment():
    prof = profile("execution_receipt")
    profile_bytes = validate.canonical_json(prof)
    registry = json.loads((ROOT / JSON_FIXTURES["risk_control_registry"]).read_text("utf-8"))["instance"]
    items = [(
        "regulatory_evidence_profile", "z-profile.json", profile_bytes,
        validate.regulatory_evidence_profile_fingerprint(prof),
    )]
    for number in (1, 2):
        candidate = copy.deepcopy(registry)
        candidate["registry"]["version"] = f"2026.09.21-{number}"
        payload = validate.canonical_json(candidate)
        items.append((
            "risk_control_registry", f"a-registry-{number}.json", payload,
            validate.risk_control_registry_fingerprint(candidate),
        ))
    manifest = manifest_for(items)
    members = {path: payload for _, path, payload, _ in items}
    verdict = verify_regulatory_dossier(pack_regulatory_dossier(manifest, members))
    assert [item.entry_id for item in verdict.entries] == [
        entry["entry_id"] for entry in manifest["entries"]
    ]
    assert verdict.profile_assessment is not None
    assert verdict.profile_assessment.findings[0].status == "missing"
    assert not any(item.path == "$.profile_assessment" for item in verdict.findings)


@pytest.mark.parametrize("kind", JSON_FIXTURES)
def test_every_declared_json_kind_dispatches_to_its_existing_validator(kind):
    raw = json.loads((ROOT / JSON_FIXTURES[kind]).read_text("utf-8"))
    document = raw.get("instance", raw)
    stem = "receipt" if kind == "execution_receipt" else kind
    payload = validate.canonical_json(document)
    artifact_fp = getattr(validate, stem + "_fingerprint")(document)
    prof = profile()
    profile_payload = validate.canonical_json(prof)
    items = [
        ("regulatory_evidence_profile", "profile.json", profile_payload,
         validate.regulatory_evidence_profile_fingerprint(prof)),
        (kind, f"evidence/{kind}.json", payload, artifact_fp),
    ]
    manifest = manifest_for(items)
    members = {path: member for _, path, member, _ in items}
    verdict = verify_regulatory_dossier(pack_regulatory_dossier(manifest, members))
    entry = next(item for item in verdict.entries if item.artifact_kind == kind)
    assert entry.status == "matched", entry.violations


def test_existing_binding_verifiers_are_composed_without_changing_transport_status():
    prof = profile()
    profile_payload = validate.canonical_json(prof)
    system_manifest = json.loads(
        (ROOT / JSON_FIXTURES["system_manifest"]).read_text("utf-8")
    )["instance"]
    registration = json.loads((ROOT / JSON_FIXTURES["ai_system_registration"]).read_text("utf-8"))
    registration["system_id"] = system_manifest["system"]["id"]
    registration["system_manifest_fingerprint"] = validate.system_manifest_fingerprint(system_manifest)
    registration_payload = validate.canonical_json(registration)
    manifest_payload = validate.canonical_json(system_manifest)
    items = [
        ("regulatory_evidence_profile", "profile.json", profile_payload,
         validate.regulatory_evidence_profile_fingerprint(prof)),
        ("ai_system_registration", "registration.json", registration_payload,
         validate.ai_system_registration_fingerprint(registration)),
        ("system_manifest", "system-manifest.json", manifest_payload,
         validate.system_manifest_fingerprint(system_manifest)),
    ]
    manifest = manifest_for(items)
    members = {path: member for _, path, member, _ in items}
    verdict = verify_regulatory_dossier(pack_regulatory_dossier(manifest, members))
    assert verdict.transport_ok is True
    assert any(
        item.path.startswith("binding:ai_system_registration:")
        and item.status == "matched"
        for item in verdict.binding_findings
    )


def test_lineage_orders_corrections_and_exposes_forks_missing_and_duplicate_roots():
    first, _ = dossier()
    first_fp = validate.regulatory_evidence_dossier_fingerprint(first)
    second = copy.deepcopy(first)
    second["supersedes"] = first_fp
    second["observed_at"] = "2026-09-26T11:00:00Z"
    third = copy.deepcopy(second)
    third["observed_at"] = "2026-09-26T12:00:00Z"
    verdict = verify_regulatory_dossier_lineage([third, first, second])
    assert verdict.ordered_fingerprints[0] == first_fp
    assert any(item.reason == "correction lineage forks" for item in verdict.findings)
    missing = copy.deepcopy(first)
    missing["supersedes"] = "sha256:" + "d" * 64
    verdict = verify_regulatory_dossier_lineage([missing])
    assert verdict.findings[0].status == "missing"
    competing_root = copy.deepcopy(first)
    competing_root["observed_at"] = "2026-09-26T14:00:00Z"
    verdict = verify_regulatory_dossier_lineage([first, competing_root])
    assert any(item.reason == "multiple roots claim one stable identity" for item in verdict.findings)
    with pytest.raises(TypeError, match="iterable"):
        verify_regulatory_dossier_lineage(first)


def test_manifest_and_export_fingerprints_are_independent():
    manifest, members = dossier()
    first = pack_regulatory_dossier(manifest, members)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(first)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
    second = out.getvalue()
    assert first != second
    assert verify_regulatory_dossier(first).dossier_fingerprint == verify_regulatory_dossier(second).dossier_fingerprint
    assert regulatory_dossier_export_fingerprint(first) != regulatory_dossier_export_fingerprint(second)


def test_nested_execution_package_uses_existing_verifier_and_transport_bound(monkeypatch):
    prof = profile()
    profile_payload = validate.canonical_json(prof)
    package_payload = b"not-an-evidence-package"
    items = [
        ("regulatory_evidence_profile", "profile.json", profile_payload,
         validate.regulatory_evidence_profile_fingerprint(prof)),
        ("execution_evidence_package", "evidence/package.zip", package_payload,
         "sha256:" + hashlib.sha256(package_payload).hexdigest()),
    ]
    manifest = manifest_for(items)
    members = {path: member for _, path, member, _ in items}
    blob = pack_regulatory_dossier(manifest, members)
    verdict = verify_regulatory_dossier(blob)
    package_entry = next(
        item for item in verdict.entries
        if item.artifact_kind == "execution_evidence_package"
    )
    assert package_entry.status == "not_verified"
    monkeypatch.setattr(dossier_module, "_ARCHIVE_MAX_BYTES", len(blob) - 1)
    bounded = verify_regulatory_dossier(blob)
    assert bounded.findings[0].reason == "archive exceeds the compressed transport size limit"
