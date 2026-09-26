"""ADR-043 CLI adapter tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def manifest() -> dict:
    wrapper = json.loads((ROOT / "contract" / "fixtures" / "310-system-manifest-valid.json").read_text("utf-8"))
    return wrapper["instance"]


def request(document: dict) -> dict:
    return {"interface_version": "1.0.0", "message_type": "request", "operation": "inspect", "artifact": {"input_id": "manifest", "kind": "system_manifest", "media_type": "application/json", "document": document}}


def run(path: Path, *args: str):
    return subprocess.run([sys.executable, "-m", "vitruvyan_motus.verification_query_cli", str(path), *args], cwd=ROOT, capture_output=True, text=True, timeout=30)


def test_json_output_is_stable_contract_result(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request(manifest())), encoding="utf-8")
    completed = run(path, "--json")
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["outcome"] == "valid"
    assert result["scope"]["global_complete"] is False


def test_human_output_names_scope_and_does_not_claim_compliance(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request(manifest())), encoding="utf-8")
    completed = run(path)
    assert completed.returncode == 0
    assert "outcome: valid" in completed.stdout
    assert "global_complete: false" in completed.stdout
    assert "compliant" not in completed.stdout.lower()


def test_invalid_artifact_exits_one_and_invalid_request_exits_two(tmp_path):
    invalid = manifest()
    invalid["compliant"] = True
    artifact_path = tmp_path / "invalid-artifact.json"
    artifact_path.write_text(json.dumps(request(invalid)), encoding="utf-8")
    assert run(artifact_path, "--json").returncode == 1

    bad_request = request(manifest())
    bad_request["operation"] = "search"
    request_path = tmp_path / "invalid-request.json"
    request_path.write_text(json.dumps(bad_request), encoding="utf-8")
    assert run(request_path, "--json").returncode == 2


def test_duplicate_keys_and_non_utf8_are_usage_errors(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"operation":"inspect","operation":"query"}', encoding="utf-8")
    assert run(duplicate).returncode == 2
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff")
    assert run(binary).returncode == 2


def test_parser_stack_exhaustion_is_a_usage_error_not_a_traceback(tmp_path):
    nested = tmp_path / "nested.json"
    nested.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")
    completed = run(nested)
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
