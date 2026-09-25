"""ADR-040's first six structural documents; no binding or scope verdicts."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vitruvyan_motus.contract import validate


ROOT = Path(__file__).resolve().parent.parent
KINDS = (
    "retention-policy-declaration",
    "legal-hold-declaration",
    "retention-scope-snapshot",
    "retention-trigger-occurrence",
    "retention-application",
    "custody-observation",
)
FIXTURES = dict(zip(KINDS, range(370, 376)))
VALIDATORS = {
    kind: validate._RETENTION_VALIDATE_DISPATCH[kind] for kind in KINDS
}
HASH = "sha256:" + "a" * 64
ARTIFACT = {"kind": "receipt", "fingerprint": HASH}


def fixture(kind: str) -> dict:
    path = ROOT / "contract" / "retention-fixtures" / f"{FIXTURES[kind]}-{kind}-valid.json"
    return json.loads(path.read_text(encoding="utf-8"))


def rules(violations) -> set[str]:
    return {violation.rule for violation in violations}


@pytest.mark.parametrize("kind", KINDS)
def test_each_shipped_schema_is_valid_and_accepts_its_neutral_fixture(kind):
    schema = json.loads((ROOT / "contract" / f"{kind}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert VALIDATORS[kind](fixture(kind)) == []


@pytest.mark.parametrize("kind", KINDS)
def test_each_document_refuses_undeclared_verdicts_and_bad_version(kind):
    document = fixture(kind)
    document["cleared_for_disposal"] = True
    assert "SCHEMA" in rules(VALIDATORS[kind](document))
    document = fixture(kind)
    document["schema_version"] = "2.0.0"
    assert "SCHEMA" in rules(VALIDATORS[kind](document))


@pytest.mark.parametrize("kind", KINDS)
def test_each_document_refuses_a_calendar_impossibility(kind):
    document = fixture(kind)
    field = {
        "retention-policy-declaration": "declared_at",
        "legal-hold-declaration": "effective_at",
        "retention-scope-snapshot": "observed_at",
        "retention-trigger-occurrence": "occurred_at",
        "retention-application": "observed_at",
        "custody-observation": "observed_at",
    }[kind]
    document[field] = "2026-02-30T10:00:00Z"
    assert "RET1" in rules(VALIDATORS[kind](document))


def test_duration_is_bounded_and_no_automatic_disposal_has_no_trigger():
    document = fixture("retention-policy-declaration")
    for seconds in (0, 3155760001, True):
        candidate = copy.deepcopy(document)
        candidate["rule"]["duration_seconds"] = seconds
        assert "SCHEMA" in rules(validate.validate_retention_policy_declaration(candidate))
    document["rule"] = {"kind": "no_automatic_disposal"}
    assert validate.validate_retention_policy_declaration(document) == []
    document["rule"]["trigger"] = "creation"
    assert "SCHEMA" in rules(validate.validate_retention_policy_declaration(document))


def test_duration_requires_a_real_integer_in_the_direct_api():
    document = fixture("retention-policy-declaration")
    assert document["rule"]["duration_seconds"] == 86400
    assert validate.validate_retention_policy_declaration(document) == []
    document["rule"]["duration_seconds"] = 86400.0
    violations = validate.validate_retention_policy_declaration(document)
    assert [(item.rule, item.path) for item in violations] == [
        ("RET4", "$.rule.duration_seconds")
    ]


def test_cli_refuses_the_integral_float_json_lexeme(tmp_path):
    document = fixture("retention-policy-declaration")
    document["rule"]["duration_seconds"] = 86400.0
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert '"duration_seconds": 86400.0' in path.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"),
         "retention-policy-declaration", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "RET4 $.rule.duration_seconds:" in result.stdout


@pytest.mark.parametrize("action,required", [
    ("placed", "scope"), ("amended", "supersedes"),
    ("released", "supersedes"), ("cancelled", "rationale"),
])
def test_hold_action_requires_its_own_fields(action, required):
    document = fixture("legal-hold-declaration")
    document["action"] = action
    document["supersedes"] = HASH
    document["rationale"] = "Producer recorded the action."
    if action in ("released", "cancelled"):
        del document["scope"]
    if action == "placed":
        del document["supersedes"]
    assert validate.validate_legal_hold_declaration(document) == []
    del document[required]
    assert "SCHEMA" in rules(validate.validate_legal_hold_declaration(document))


def test_release_cannot_restate_scope_and_placement_cannot_supersede():
    document = fixture("legal-hold-declaration")
    document["supersedes"] = HASH
    assert "SCHEMA" in rules(validate.validate_legal_hold_declaration(document))
    document["action"] = "released"
    document["rationale"] = "Producer recorded release."
    assert "SCHEMA" in rules(validate.validate_legal_hold_declaration(document))


def test_snapshot_may_be_empty_but_source_must_be_typed_and_exact():
    document = fixture("retention-scope-snapshot")
    assert validate.validate_retention_scope_snapshot(document) == []
    document["source"]["kind"] = "receipt"
    assert "SCHEMA" in rules(validate.validate_retention_scope_snapshot(document))


def test_snapshot_requires_a_bounded_stable_id_and_binds_it_in_fingerprint():
    document = fixture("retention-scope-snapshot")
    assert validate.validate_retention_scope_snapshot(document) == []
    original = validate.retention_scope_snapshot_fingerprint(document)
    document["snapshot_id"] = "S-002"
    assert validate.validate_retention_scope_snapshot(document) == []
    assert validate.retention_scope_snapshot_fingerprint(document) != original
    document["snapshot_id"] = " " * 201
    assert "SCHEMA" in rules(validate.validate_retention_scope_snapshot(document))
    del document["snapshot_id"]
    assert "SCHEMA" in rules(validate.validate_retention_scope_snapshot(document))


def test_trigger_occurrence_is_a_separate_identified_claim():
    document = fixture("retention-trigger-occurrence")
    del document["occurrence_id"]
    assert "SCHEMA" in rules(validate.validate_retention_trigger_occurrence(document))
    document = fixture("retention-trigger-occurrence")
    document["trigger"] = "external_event"
    assert "SCHEMA" in rules(validate.validate_retention_trigger_occurrence(document))
    document["external_event_ref"] = "event/opaque-1"
    assert validate.validate_retention_trigger_occurrence(document) == []
    document["trigger"] = "execution_completion"
    assert "SCHEMA" in rules(validate.validate_retention_trigger_occurrence(document))
    del document["external_event_ref"]
    document["source"] = ARTIFACT
    assert validate.validate_retention_trigger_occurrence(document) == []


@pytest.mark.parametrize("kind,path", [
    ("retention-policy-declaration", "scope"),
    ("legal-hold-declaration", "scope"),
    ("retention-scope-snapshot", "artifacts"),
    ("retention-application", "artifacts"),
    ("custody-observation", "evidence"),
])
def test_typed_artifact_identity_cannot_repeat(kind, path):
    document = fixture(kind)
    if path == "scope":
        document["scope"] = {"kind": "exact_artifacts", "artifacts": [ARTIFACT, ARTIFACT]}
    else:
        document[path] = [ARTIFACT, ARTIFACT]
    assert "RET2" in rules(VALIDATORS[kind](document))


def test_execution_selector_requires_canonical_motus_coordinate():
    document = fixture("retention-policy-declaration")
    document["scope"] = {"kind": "execution_refs", "execution_refs": ["tenant/writer/-1"]}
    assert "RET3" in rules(validate.validate_retention_policy_declaration(document))
    document["scope"]["execution_refs"] = ["tenant/writer/1"]
    assert validate.validate_retention_policy_declaration(document) == []


def test_application_bindings_are_exact_and_absence_makes_no_hold_claim():
    document = fixture("retention-application")
    assert "hold_fingerprints" not in document
    assert validate.validate_retention_application(document) == []
    document["hold_fingerprints"] = ["not-a-fingerprint"]
    assert "SCHEMA" in rules(validate.validate_retention_application(document))


@pytest.mark.parametrize("kind", KINDS)
def test_fingerprint_binds_complete_canonical_document_and_cli_reaches_it(tmp_path, kind):
    document = fixture(kind)
    helper = getattr(validate, kind.replace("-", "_") + "_fingerprint")
    expected = "sha256:" + hashlib.sha256(validate.canonical_json(document)).hexdigest()
    assert helper(document) == expected
    changed = copy.deepcopy(document)
    changed["producer_ref"] = "another-producer"
    assert helper(changed) != expected
    path = tmp_path / "document.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), kind, str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", KINDS)
def test_cli_reports_an_invalid_retention_document(tmp_path, kind):
    document = fixture(kind)
    document["schema_version"] = "unsupported"
    path = tmp_path / "document.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), kind, str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "SCHEMA" in result.stdout
