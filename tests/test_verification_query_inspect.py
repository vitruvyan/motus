"""ADR-043 public inspection facade tests."""
from __future__ import annotations

import base64
import copy
import io
import json
import zipfile
from pathlib import Path

from vitruvyan_motus import inspect_artifact, query_artifacts
from vitruvyan_motus.contract import validate
import vitruvyan_motus.verification_query as verification_query

ROOT = Path(__file__).resolve().parent.parent


def fixture(name: str) -> dict:
    wrapper = json.loads((ROOT / "contract" / "fixtures" / name).read_text("utf-8"))
    return wrapper.get("instance", wrapper)


def typed(kind: str, document: object, input_id: str = "subject") -> dict:
    return {"input_id": input_id, "kind": kind, "media_type": "application/json", "document": document}


def zip_bytes(entries) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, payload in entries:
            output.writestr(name, payload)
    return target.getvalue()


def binary_artifact(kind: str, data: bytes, input_id: str) -> dict:
    return {
        "input_id": input_id,
        "kind": kind,
        "media_type": "application/zip",
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


def test_valid_manifest_uses_the_existing_validator_and_exact_fingerprint():
    document = fixture("310-system-manifest-valid.json")
    result = inspect_artifact(typed("system_manifest", document))
    assert result["outcome"] == "valid"
    assert result["subject"]["fingerprint"] == validate.system_manifest_fingerprint(document)
    assert result["scope"]["global_complete"] is False
    assert validate.validate_verification_query_message(result) == []


def test_invalid_artifact_has_no_derived_identity_and_is_not_a_usage_error():
    document = fixture("310-system-manifest-valid.json")
    document["compliant"] = True
    result = inspect_artifact(typed("system_manifest", document))
    assert result["outcome"] == "invalid"
    assert result["subject"]["fingerprint"] is None
    assert {item["rule"] for item in result["violations"]} == {"SCHEMA"}


def test_unknown_kind_is_an_invalid_request_not_guessed_from_content():
    result = inspect_artifact(typed("legal_opinion", {}))
    assert result["outcome"] == "invalid_request"
    assert result["subject"] is None
    assert result["scope"]["input_ids"] == ["subject"]
    assert {item["rule"] for item in result["violations"]} == {"SCHEMA"}


def test_graphspec_uses_its_existing_kind_prefixed_identity():
    document = fixture("01-graphspec-linear.json")
    result = inspect_artifact(typed("graphspec", document))
    assert result["outcome"] == "valid"
    assert result["subject"]["fingerprint"] == validate.fingerprint("graph", document)


def test_trace_is_valid_without_inventing_a_new_exact_identity():
    document = fixture("04-trace-happy-path.json")
    result = inspect_artifact(typed("trace", document))
    assert result["outcome"] == "valid"
    assert result["subject"]["fingerprint"] is None


def test_all_contract_json_kinds_have_an_explicit_dispatch_entry():
    schema = validate.load_verification_query_schema()
    json_kinds = set(schema["$defs"]["JsonArtifactKind"]["enum"])
    assert set(verification_query._JSON_DISPATCH) == json_kinds


def test_bad_binary_keeps_exact_transport_identity_and_fails_closed():
    data = b"not a zip"
    artifact = {"input_id": "package", "kind": "execution_evidence_package", "media_type": "application/zip", "content_base64": base64.b64encode(data).decode("ascii")}
    result = inspect_artifact(artifact)
    assert result["outcome"] == "invalid"
    assert result["subject"]["fingerprint"] == "sha256:" + __import__("hashlib").sha256(data).hexdigest()
    assert result["scope"]["global_complete"] is False


def test_evidence_package_expansion_limits_run_before_domain_verification(monkeypatch):
    evidence = __import__("vitruvyan_motus.evidence", fromlist=["verify_package"])
    monkeypatch.setattr(
        evidence, "verify_package",
        lambda _data: (_ for _ in ()).throw(AssertionError("domain verifier must not run")),
    )

    cases = [
        zip_bytes((f"member-{index}", b"") for index in range(1_001)),
        zip_bytes([("x" * 9_000, b"\0" * (28 * 1024 * 1024 + 1))]),
        zip_bytes((f"large-{index}", b"\0" * (27 * 1024 * 1024)) for index in range(5)),
    ]
    expected_reasons = (
        "member-count work limit",
        "member exceeds the expanded-byte limit",
        "cumulative expanded-work limit",
    )
    for index, (data, reason) in enumerate(zip(cases, expected_reasons)):
        artifact = binary_artifact("execution_evidence_package", data, f"package-{index}")
        result = inspect_artifact(artifact)
        assert result["outcome"] == "invalid"
        assert reason in result["findings"][0]["reason"]
        assert validate.validate_verification_query_message(result) == []
        if index == 1:
            assert len(result["findings"][0]["observed"]["member"]) <= 512


def test_zip_member_count_is_checked_before_zipfile_directory_parsing(monkeypatch):
    data = zip_bytes((f"member-{index}", b"") for index in range(1_001))
    monkeypatch.setattr(
        verification_query.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ZipFile must not parse an over-count directory")
        ),
    )
    result = inspect_artifact(binary_artifact(
        "execution_evidence_package", data, "over-count",
    ))
    assert result["outcome"] == "invalid"
    assert "member-count" in result["findings"][0]["reason"]


def test_zip_expansion_budget_is_cumulative_across_query_inputs(monkeypatch):
    evidence = __import__("vitruvyan_motus.evidence", fromlist=["verify_package"])
    monkeypatch.setattr(
        evidence,
        "verify_package",
        lambda _data: type("Verdict", (), {
            "transport_ok": True, "damaged": (), "trace_violations": (), "verdict": None,
        })(),
    )
    data = zip_bytes([("large", b"\0" * (27 * 1024 * 1024))])
    artifacts = [
        binary_artifact("execution_evidence_package", data, f"package-{index}")
        for index in range(5)
    ]
    result = query_artifacts({
        "kind": "artifact_identity",
        "artifact_kind": "execution_evidence_package",
        "fingerprint": "sha256:" + "0" * 64,
    }, artifacts)
    assert result["outcome"] == "invalid"
    assert any("cumulative expanded-work" in item["reason"] for item in result["findings"])


def test_nested_zip_work_is_preflighted_before_dossier_verification(monkeypatch):
    dossier = __import__("vitruvyan_motus.regulatory_dossier", fromlist=["verify_regulatory_dossier"])
    monkeypatch.setattr(
        dossier,
        "verify_regulatory_dossier",
        lambda _data: (_ for _ in ()).throw(AssertionError("dossier verifier must not run")),
    )
    inner = zip_bytes([("large", b"\0" * (27 * 1024 * 1024))])
    paths = [f"nested-{index}.zip" for index in range(5)]
    manifest = json.dumps({
        "entries": [
            {"path": path, "artifact_kind": "execution_evidence_package"}
            for path in paths
        ],
    }).encode("utf-8")
    outer = zip_bytes([
        ("dossier.json", manifest),
        *((path, inner) for path in paths),
    ])
    result = inspect_artifact(binary_artifact(
        "regulatory_evidence_dossier_export", outer, "dossier",
    ))
    assert result["outcome"] == "invalid"
    assert "cumulative expanded-work" in result["findings"][0]["reason"]


def test_declared_self_extracting_evidence_package_is_preflighted(monkeypatch):
    dossier = __import__("vitruvyan_motus.regulatory_dossier", fromlist=["verify_regulatory_dossier"])
    monkeypatch.setattr(
        dossier,
        "verify_regulatory_dossier",
        lambda _data: (_ for _ in ()).throw(AssertionError("dossier verifier must not run")),
    )
    inner = b"self-extracting-prefix" + zip_bytes([
        ("oversize", b"\0" * (28 * 1024 * 1024 + 1)),
    ])
    manifest = json.dumps({
        "entries": [{
            "path": "nested.pkg",
            "artifact_kind": "execution_evidence_package",
        }],
    }).encode("utf-8")
    outer = zip_bytes([
        ("dossier.json", manifest),
        ("nested.pkg", inner),
    ])
    result = inspect_artifact(binary_artifact(
        "regulatory_evidence_dossier_export", outer, "dossier",
    ))
    assert result["outcome"] == "invalid"
    assert "member exceeds the expanded-byte limit" in result["findings"][0]["reason"]


def test_dossier_metadata_does_not_consume_the_thousand_entry_work_limit():
    data = zip_bytes([
        ("dossier.json", b"{}"),
        *((f"member-{index}", b"") for index in range(1_000)),
    ])
    result = inspect_artifact(binary_artifact(
        "regulatory_evidence_dossier_export", data, "dossier",
    ))
    assert result["outcome"] == "invalid"
    assert not any("member-count work limit" in item["reason"] for item in result["findings"])
    assert result["violations"]


def test_inspection_takes_no_ownership_of_the_callers_document():
    artifact = typed("system_manifest", fixture("310-system-manifest-valid.json"))
    original = copy.deepcopy(artifact)
    inspect_artifact(artifact)
    assert artifact == original


def test_non_json_python_values_are_invalid_request_not_exceptions():
    result = inspect_artifact(typed("system_manifest", {"bad": object()}))
    assert result["outcome"] == "invalid_request"
    assert {item["rule"] for item in result["violations"]} == {"J1"}


def test_deep_already_parsed_input_is_a_bounded_invalid_request():
    nested: object = None
    for _ in range(129):
        nested = [nested]
    result = inspect_artifact(typed("system_manifest", nested))
    assert result["outcome"] == "invalid_request"
    assert {item["rule"] for item in result["violations"]} == {"VQ2"}
