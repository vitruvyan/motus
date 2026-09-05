"""ADR-028 migration tests for the commitment envelope key."""

import json
import shutil
import subprocess
import sys

import pytest

from contract.validate import validate_commitment
from vitruvyan_motus.commitlog import CommitmentLog

AT = "2026-01-01T00:00:00Z"
ROOT = "sha256:" + "a" * 64


def _window(root):
    return next(root.rglob("window-000000.jsonl"))


def _make_log(path, count=1):
    log = CommitmentLog(path, tenant="t", writer_id="w")
    for index in range(count):
        run_id = f"r{index}"
        log.begin(run_id, at=AT, nonce=f"b{index}")
        log.end(run_id, root=ROOT, outcome="completed", at=AT, nonce=f"e{index}")
    checkpoint = log.seal(AT)
    log.close()
    return checkpoint


def _rewrite_key(path, spelling):
    source = _window(path)
    lines = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        body = json.loads(raw)
        body[spelling] = body.pop("commitment" if spelling == "c" else "c")
        lines.append(json.dumps(body, sort_keys=True, separators=(",", ":")))
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_h1_reemitting_c_as_commitment_preserves_window_and_checkpoint(tmp_path):
    source = tmp_path / "source"
    _make_log(source)
    old = tmp_path / "old"
    new = tmp_path / "new"
    shutil.copytree(source, old)
    shutil.copytree(source, new)
    _rewrite_key(old, "c")
    # source is the new writer spelling; old is the re-emitted legacy spelling.
    old_log = CommitmentLog(old, tenant="t", writer_id="w")
    new_log = CommitmentLog(new, tenant="t", writer_id="w")
    try:
        assert [c.leaf for c in old_log._sealed_window(0)[1]] == [
            c.leaf for c in new_log._sealed_window(0)[1]
        ]
        assert old_log._sealed_window(0)[0].window_root == new_log._sealed_window(0)[0].window_root
        assert next(old.rglob("checkpoint-000000.json")).read_bytes() == (
            next(new.rglob("checkpoint-000000.json")).read_bytes()
        )
    finally:
        old_log.close()
        new_log.close()


def test_new_lines_validate_strictly_line_by_line(tmp_path):
    root = tmp_path / "new"
    _make_log(root, count=9)
    lines = _window(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 18
    for index, raw in enumerate(lines):
        document = json.loads(raw)
        violations = validate_commitment(document)
        assert violations == [], violations
        line_file = tmp_path / f"line-{index}.json"
        line_file.write_text(raw + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "contract.validate", "commitment", str(line_file)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_c_in_open_window_reports_diagnostic_during_recovery(tmp_path):
    root = tmp_path / "open-legacy"
    log = CommitmentLog(root, tenant="t", writer_id="w")
    log.begin("r0", at=AT, nonce="b0")
    log.end("r0", root=ROOT, outcome="completed", at=AT, nonce="e0")
    log.close()

    # There are no checkpoints, so this fresh open can only discover `c` via
    # _recover -> _replay, never by scanning a sealed window.
    _rewrite_key(root, "c")
    reopened = CommitmentLog(root, tenant="t", writer_id="w")
    try:
        assert reopened.diagnostics == ("written by a Motus before 0.14.0",)
    finally:
        reopened.close()


def test_old_log_chain_receipt_and_diagnostic_are_compatible(tmp_path):
    root = tmp_path / "old"
    _make_log(root)
    _rewrite_key(root, "c")
    log = CommitmentLog(root, tenant="t", writer_id="w")
    try:
        assert log.diagnostics == ("written by a Motus before 0.14.0",)
        assert log.verify_chain() == 1
        receipt = log.receipt_for("t/w/0")
        assert receipt["execution"]["run_id"] == "r0"
        assert log.diagnostics == ("written by a Motus before 0.14.0",)
        log._sealed_window(0)
        log.verify_chain()
        assert log.diagnostics == ("written by a Motus before 0.14.0",)
    finally:
        log.close()


def test_all_commitment_log_is_diagnostic_free_on_fresh_open(tmp_path):
    root = tmp_path / "new"
    _make_log(root)
    log = CommitmentLog(root, tenant="t", writer_id="w")
    try:
        assert log.diagnostics == ()
    finally:
        log.close()


def test_mixed_spellings_in_one_window_verify(tmp_path):
    root = tmp_path / "mixed"
    _make_log(root, count=3)
    path = _window(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = []
    for index, raw in enumerate(lines):
        body = json.loads(raw)
        if index % 2 == 0:
            body["c"] = body.pop("commitment")
        rewritten.append(json.dumps(body, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    log = CommitmentLog(root, tenant="t", writer_id="w")
    try:
        assert log.verify_chain() == 1
        assert log._sealed_window(0)[0].window_root
        assert log.diagnostics == ("written by a Motus before 0.14.0",)
    finally:
        log.close()


def test_both_envelope_spellings_are_rejected_as_ambiguous(tmp_path):
    root = tmp_path / "ambiguous"
    _make_log(root)
    path = _window(root)
    body = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    body["c"] = body["commitment"]
    path.write_text(
        json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    log = CommitmentLog(root, tenant="t", writer_id="w")
    try:
        with pytest.raises(ValueError, match="both commitment and legacy c"):
            log._sealed_window(0)
    finally:
        log.close()


def test_contract_freeze_removes_legacy_window_marker():
    import vitruvyan_motus
    import vitruvyan_motus.commitlog as commitlog_module

    # The freeze ADR removes both the legacy fallback and its explicit marker.
    if vitruvyan_motus.__version__.startswith("1."):
        assert not hasattr(commitlog_module, "LEGACY_WINDOW_KEY")
