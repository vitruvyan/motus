"""ADR-042 Regulatory Evidence Dossier manifest contract tests."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from vitruvyan_motus.contract import validate

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "contract" / "regulatory-evidence-dossier.v1.schema.json"
FIXTURE = ROOT / "contract" / "regulatory-evidence-dossier-fixtures" / (
    "410-regulatory-evidence-dossier-valid.json"
)


def dossier() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def rules(violations) -> set[str]:
    return {item.rule for item in violations}


def test_schema_fixture_fingerprint_and_cli(tmp_path):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    document = dossier()
    assert validate.validate_regulatory_evidence_dossier(document) == []
    assert validate.regulatory_evidence_dossier_fingerprint(document) == (
        "sha256:" + hashlib.sha256(validate.canonical_json(document)).hexdigest()
    )
    path = tmp_path / "dossier.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"),
         "regulatory-evidence-dossier", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_document_is_closed_and_has_no_compliance_verdict():
    document = dossier()
    document["compliant"] = True
    assert "SCHEMA" in rules(validate.validate_regulatory_evidence_dossier(document))


def test_observed_at_must_be_a_real_utc_instant():
    document = dossier()
    document["observed_at"] = "2026-02-30T08:00:00Z"
    assert "RED1" in rules(validate.validate_regulatory_evidence_dossier(document))


def test_entry_id_path_and_artifact_identity_are_unique():
    for field in ("entry_id", "path"):
        document = dossier()
        document["entries"][1][field] = document["entries"][0][field]
        assert "RED2" in rules(validate.validate_regulatory_evidence_dossier(document))

    document = dossier()
    document["entries"][1]["artifact_kind"] = document["entries"][0]["artifact_kind"]
    document["entries"][1]["artifact_fingerprint"] = document["entries"][0][
        "artifact_fingerprint"
    ]
    assert "RED2" in rules(validate.validate_regulatory_evidence_dossier(document))


def test_exactly_one_profile_entry_matches_the_manifest_binding():
    missing = dossier()
    missing["entries"] = missing["entries"][1:]
    assert "RED3" in rules(validate.validate_regulatory_evidence_dossier(missing))

    mismatched = dossier()
    mismatched["entries"][0]["artifact_fingerprint"] = "sha256:" + "e" * 64
    assert "RED3" in rules(validate.validate_regulatory_evidence_dossier(mismatched))

    duplicate = dossier()
    extra = copy.deepcopy(duplicate["entries"][0])
    extra["entry_id"] = "second-profile"
    extra["path"] = "profile/second-profile.json"
    duplicate["entries"].append(extra)
    assert "RED3" in rules(validate.validate_regulatory_evidence_dossier(duplicate))


def test_member_paths_are_safe_and_depth_bounded():
    unsafe = (
        "../profile.json",
        "/profile.json",
        "C:/profile.json",
        "profile//document.json",
        "./profile.json",
        "/".join(["a"] * 17) + ".json",
    )
    for path in unsafe:
        document = dossier()
        document["entries"][0]["path"] = path
        assert "RED4" in rules(validate.validate_regulatory_evidence_dossier(document)), path


def test_media_type_is_fixed_by_artifact_kind():
    document = dossier()
    document["entries"][0]["media_type"] = "application/zip"
    assert "SCHEMA" in rules(validate.validate_regulatory_evidence_dossier(document))

    package = copy.deepcopy(document["entries"][1])
    package["artifact_kind"] = "execution_evidence_package"
    package["media_type"] = "application/json"
    document = dossier()
    document["entries"][1] = package
    assert "SCHEMA" in rules(validate.validate_regulatory_evidence_dossier(document))


def test_integral_float_size_is_refused_before_schema_can_launder_it():
    document = dossier()
    document["entries"][0]["size_bytes"] = 512.0
    assert "RED5" in rules(validate.validate_regulatory_evidence_dossier(document))
