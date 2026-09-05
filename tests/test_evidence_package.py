from __future__ import annotations

import hashlib
import io
import json
import subprocess
import zipfile
from datetime import datetime, timezone

from vitruvyan_motus import Fact, GraphSpec, Runtime, State, TraceBundle
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork
from vitruvyan_motus.evidence import pack, verify_package


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bundle():
    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0", "name": "evidence", "version": "1.0.0",
        "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}},
    })
    result = Runtime(spec, {"a": lambda state: state.with_fact(
        Fact("answer", 42, "test", NOW))}).run(State.empty("seed"), run_id="run-1")
    return TraceBundle(spec, result.trace)


def members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def rebuilt(values):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, value in sorted(values.items()):
            zf.writestr(name, value)
    return out.getvalue()


def test_package_round_trip_is_byte_only_and_has_no_receipt():
    verdict = verify_package(pack(bundle(), proofs={"anchor": b"proof"}))
    assert verdict.transport_ok is True
    assert verdict.damaged == ()
    assert verdict.verdict is None


def test_tampered_trace_is_reported_even_when_manifest_is_forged(tmp_path):
    original = bundle()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="writer", fsync=False)
    # The runtime supplies matching BEGIN/END commitments; sealing makes the
    # receipt producible, and the verifier then has an independent root claim.
    spec = original.spec
    result = Runtime(spec, {"a": lambda state: state.with_fact(
        Fact("answer", 42, "test", NOW))}, commitments=log).run(
        State.empty("seed"), run_id="run-1")
    logged = TraceBundle(spec, result.trace)
    log.seal("2026-01-01T00:00:01Z")
    values = members(pack(logged, log=log))
    trace = json.loads(values["core/trace.json"])
    trace["records"][2]["writes"]["facts"][0]["value"] = 43
    values["core/trace.json"] = json.dumps(trace, separators=(",", ":")).encode()
    manifest = json.loads(values["manifest.json"])
    for entry in manifest["files"]:
        if entry["name"] == "core/trace.json":
            import hashlib
            entry["sha256"] = "sha256:" + hashlib.sha256(values[entry["name"]]).hexdigest()
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    verdict = verify_package(rebuilt(values))
    assert verdict.transport_ok is True
    assert verdict.damaged == ()
    assert any(item.startswith("T11 ") for item in verdict.trace_violations)
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") != "ESTABLISHED"
    log.close()


def test_exact_one_byte_tamper_of_each_member_is_reported(tmp_path):
    bundle_value, log = logged_bundle(tmp_path)
    original = members(pack(bundle_value, log=log, proofs={"p": b"proof"}))
    for name in ("core/trace.json", "core/graphspec.json", "core/receipt.json",
                 "core/proofs/p"):
        changed = dict(original)
        payload = bytearray(changed[name])
        payload[len(payload) // 2] ^= 1
        changed[name] = bytes(payload)
        verdict = verify_package(rebuilt(changed))
        assert name in verdict.damaged
        if name in ("core/trace.json", "core/receipt.json"):
            assert verdict.verdict is not None
            assert verdict.verdict.status_of("INTEGRITY") != "established"
    changed = dict(original)
    manifest = json.loads(changed["manifest.json"])
    entry = next(item for item in manifest["files"] if item["name"] == "core/trace.json")
    digest = entry["sha256"]
    entry["sha256"] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    changed["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    assert "core/trace.json" in verify_package(rebuilt(changed)).damaged
    log.close()


def test_bad_zip_manifest_and_zip_slip_are_clean_refusals(tmp_path):
    assert verify_package(b"not a zip").transport_ok is False
    for unsafe_name in ("../evil", "/etc/passwd", "C:\\\\evil.json"):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            zf.writestr(unsafe_name, b"x")
        verdict = verify_package(out.getvalue())
        assert verdict.verdict is None and verdict.transport_ok is False
        assert any(unsafe_name in item for item in verdict.damaged)
    assert not (tmp_path / "evil").exists()

    missing = rebuilt({"core/trace.json": b"{}"})
    assert "manifest.json is missing" in verify_package(missing).damaged[0]
    malformed = rebuilt({"manifest.json": b"[]"})
    assert verify_package(malformed).transport_ok is False


def logged_bundle(tmp_path, run_id="run-1"):
    spec = bundle().spec
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="writer", fsync=False)
    result = Runtime(spec, {"a": lambda state: state.with_fact(
        Fact("answer", 42, "test", NOW))}, commitments=log).run(
        State.empty("seed"), run_id=run_id)
    log.seal("2026-01-01T00:00:01Z")
    return TraceBundle(spec, result.trace), log


def test_manifest_accounting_detects_omitted_and_extra_physical_members(tmp_path):
    source, log = logged_bundle(tmp_path)
    values = members(pack(source, log=log))
    manifest = json.loads(values["manifest.json"])
    manifest["files"] = [entry for entry in manifest["files"]
                         if entry["name"] != "core/trace.json"]
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    values["core/trace.json"] = b"{broken"
    result = verify_package(rebuilt(values))
    assert "core/trace.json" in result.damaged

    values = members(pack(source, log=log))
    values["core/injected.bin"] = b"injected"
    result = verify_package(rebuilt(values))
    assert "core/injected.bin" in result.damaged
    log.close()


def test_non_dict_trace_is_a_refusal_result_not_an_exception(tmp_path):
    source, log = logged_bundle(tmp_path)
    values = members(pack(source, log=log))
    values["core/trace.json"] = b"[]"
    result = verify_package(rebuilt(values))
    assert result.verdict is not None
    assert result.transport_ok is False
    log.close()


def test_logged_completed_package_establishes_integrity_and_has_fixed_layout(tmp_path):
    bundle_value, log = logged_bundle(tmp_path)
    data = pack(bundle_value, log=log, anchors=(),
                proofs={"checkpoint.ots": b"ots"},
                attachments={"later.txt": b"later"})
    values = members(data)
    assert set(values) == {"manifest.json", "core/trace.json", "core/graphspec.json",
                           "core/receipt.json", "core/proofs/checkpoint.ots",
                           "supplementary/attachments/later.txt"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(values)
        assert all(info.date_time == (2026, 1, 1, 0, 0, 0) and
                   info.create_system == 0 and info.external_attr == 0o644 << 16
                   for info in infos)
    verdict = verify_package(data)
    assert verdict.transport_ok and verdict.damaged == ()
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") == "established"
    log.close()


def test_no_log_manifest_uses_derived_trace_root_and_tamper_files_independently(tmp_path):
    source_bundle = bundle()
    data = pack(source_bundle, proofs={"proof": b"one"}, attachments={"a": b"two"})
    values = members(data)
    manifest = json.loads(values["manifest.json"])
    assert manifest["execution"]["fingerprint"] == source_bundle.trace.root
    cases = {
        "core/trace.json": lambda value: value.replace(b'"run_id":"run-1"', b'"run_id":"run-2"'),
        "core/graphspec.json": lambda value: value.replace(b'"name":"evidence"', b'"name":"evidencf"'),
        "core/proofs/proof": lambda value: value[:-1] + b"x",
    }
    for name, mutate in cases.items():
        changed = dict(values)
        changed[name] = mutate(changed[name])
        result = verify_package(rebuilt(changed))
        assert name in result.damaged
    # An edit to a digest in the manifest identifies the described member;
    # manifest.json itself is intentionally not a fifth identity.
    forged = dict(values)
    manifest = json.loads(forged["manifest.json"])
    entry = next(item for item in manifest["files"] if item["name"] == "core/trace.json")
    entry["sha256"] = "sha256:" + "0" * 64
    forged["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    result = verify_package(rebuilt(forged))
    assert "core/trace.json" in result.damaged and "manifest.json" not in result.damaged


def test_no_receipt_unresealed_trace_is_t11_even_with_forged_manifest(tmp_path):
    values = members(pack(bundle()))
    trace = json.loads(values["core/trace.json"])
    trace["records"][2]["writes"]["facts"][0]["value"] = 43
    values["core/trace.json"] = json.dumps(
        trace, separators=(",", ":")
    ).encode()
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"]
                 if item["name"] == "core/trace.json")
    entry["sha256"] = "sha256:" + hashlib.sha256(
        values["core/trace.json"]).hexdigest()
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")
    ).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok is True
    assert any(item.startswith("T11 ") for item in result.trace_violations)
    path = tmp_path / "unsealed.zip"
    path.write_bytes(rebuilt(values))
    cli = subprocess.run(
        [".venv/bin/motus-validate", "package", str(path)],
        capture_output=True, text=True,
    )
    assert cli.returncode == 1
    assert "INTEGRITY" in cli.stdout and "T11 " in cli.stdout


def test_resealed_no_receipt_trace_is_clean_but_existence_is_not_established(tmp_path):
    from tests.test_motus_integrity_chain import _reseal_with_kernel

    values = members(pack(bundle()))
    trace = json.loads(values["core/trace.json"])
    trace["records"][2]["writes"]["facts"][0]["value"] = 43
    values["core/trace.json"] = json.dumps(
        _reseal_with_kernel(trace), separators=(",", ":")
    ).encode()
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"]
                 if item["name"] == "core/trace.json")
    entry["sha256"] = "sha256:" + hashlib.sha256(
        values["core/trace.json"]).hexdigest()
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")
    ).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok is True
    assert result.trace_violations == ()
    assert result.verdict is None
    path = tmp_path / "resealed.zip"
    path.write_bytes(rebuilt(values))
    cli = subprocess.run(
        [".venv/bin/motus-validate", "package", str(path)],
        capture_output=True, text=True,
    )
    assert cli.returncode == 0
    assert "EXISTENCE NOT ESTABLISHED" in cli.stdout
    assert "edited trace can be resealed" in cli.stdout


def test_valid_but_wrong_graphspec_is_checked_by_trace_validation():
    values = members(pack(bundle()))
    graph = json.loads(values["core/graphspec.json"])
    graph["name"] = "another-graph"
    values["core/graphspec.json"] = json.dumps(graph, separators=(",", ":")).encode()
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"]
                 if item["name"] == "core/graphspec.json")
    entry["sha256"] = "sha256:" + hashlib.sha256(
        values["core/graphspec.json"]).hexdigest()
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok is True
    assert any(item.startswith("SB") for item in result.trace_violations)


def test_invalid_or_missing_graphspec_is_reported_not_silently_skipped():
    values = members(pack(bundle()))
    graph = json.loads(values["core/graphspec.json"])
    del graph["entry"]
    values["core/graphspec.json"] = json.dumps(
        graph, separators=(",", ":")
    ).encode()
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"]
                 if item["name"] == "core/graphspec.json")
    entry["sha256"] = "sha256:" + hashlib.sha256(
        values["core/graphspec.json"]).hexdigest()
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")
    ).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok is True
    assert any(item.startswith("SCHEMA core/graphspec")
               for item in result.trace_violations)
    assert any(item.startswith("SB0 core/graphspec")
               for item in result.trace_violations)

    values = members(pack(bundle()))
    del values["core/graphspec.json"]
    manifest = json.loads(values["manifest.json"])
    manifest["files"] = [entry for entry in manifest["files"]
                          if entry["name"] != "core/graphspec.json"]
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")
    ).encode()
    result = verify_package(rebuilt(values))
    assert any(item.startswith("SB0 core/graphspec")
               for item in result.trace_violations)


def test_forging_manifest_cannot_make_logged_trace_or_receipt_valid(tmp_path):
    bundle_value, log = logged_bundle(tmp_path)
    values = members(pack(bundle_value, log=log))
    trace = json.loads(values["core/trace.json"])
    trace["records"][1]["payload"] = "forged"
    values["core/trace.json"] = json.dumps(trace, separators=(",", ":")).encode()
    manifest = json.loads(values["manifest.json"])
    next(item for item in manifest["files"] if item["name"] == "core/trace.json")["sha256"] = (
        "sha256:" + hashlib.sha256(values["core/trace.json"]).hexdigest())
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok and result.verdict is not None
    assert result.verdict.status_of("INTEGRITY") != "established"
    log.close()


def test_resealed_trace_with_receipt_is_caught_by_receipt_root(tmp_path):
    from tests.test_motus_integrity_chain import _reseal_with_kernel

    bundle_value, log = logged_bundle(tmp_path)
    values = members(pack(bundle_value, log=log))
    trace = json.loads(values["core/trace.json"])
    trace["records"][2]["writes"]["facts"][0]["value"] = 43
    values["core/trace.json"] = json.dumps(
        _reseal_with_kernel(trace), separators=(",", ":")
    ).encode()
    manifest = json.loads(values["manifest.json"])
    next(item for item in manifest["files"]
         if item["name"] == "core/trace.json")["sha256"] = (
        "sha256:" + hashlib.sha256(values["core/trace.json"]).hexdigest())
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")
    ).encode()
    result = verify_package(rebuilt(values))
    assert result.transport_ok and result.trace_violations == ()
    assert result.verdict is not None
    assert result.verdict.status_of("INTEGRITY") != "established"
    assert any(violation.rule == "P8"
               for violation in result.verdict.violations)
    log.close()


def test_receipt_tamper_and_unfinished_receipt_notes(tmp_path):
    bundle_value, log = logged_bundle(tmp_path)
    values = members(pack(bundle_value, log=log))
    receipt = json.loads(values["core/receipt.json"])
    receipt["execution"]["run_id"] = "other"
    values["core/receipt.json"] = json.dumps(receipt, separators=(",", ":")).encode()
    result = verify_package(rebuilt(values))
    assert "core/receipt.json" in result.damaged
    assert result.verdict is not None
    log.close()

    unfinished_log = CommitmentLog(tmp_path / "unfinished", tenant="acme", writer_id="writer", fsync=False)
    unfinished_log.begin("run-1", at="2026-01-01T00:00:00Z", nonce="n")
    unfinished_log.seal("2026-01-01T00:00:01Z")
    result = verify_package(pack(bundle(), log=unfinished_log))
    assert result.verdict is not None
    assert any("AN EXECUTION THAT LEFT NO COMPLETION" in note
               for note in result.verdict.notes)
    unfinished_log.close()


def test_find_execution_ref_rejects_missing_and_ambiguous_runs(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="writer", fsync=False)
    assert log.open_window._commitments == []
    import pytest
    with pytest.raises(ValueError):
        log.find_execution_ref("missing")
    for sequence in ("n1", "n2"):
        log.begin("retry", at="2026-01-01T00:00:00Z", nonce=sequence)
        log.end("retry", root="sha256:" + "a" * 64, outcome="completed",
                at="2026-01-01T00:00:00Z", nonce=sequence + "e")
        log.seal("2026-01-01T00:00:01Z")
    with pytest.raises(CommitmentLogFork, match="sequences"):
        log.find_execution_ref("retry")
    assert log.find_execution_ref("retry", sequence=0) == "acme/writer/0"
    log.close()


def test_resumed_package_reports_continuation_and_incomplete_notes(tmp_path):
    from vitruvyan_motus import InMemoryTraceSink, ReplayEngine
    from tests.test_commit_lifecycle import SPEC_CHAIN, _chain_nodes
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    runtime = Runtime(SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(), commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="seg-1")
    next(driver)
    next(driver)
    first = driver.trace
    resumed = ReplayEngine(TraceBundle(SPEC_CHAIN, first)).resume(
        Runtime(SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(), commitments=log),
        run_id="seg-2")
    log.seal("2026-01-01T00:00:01Z")
    verdict = verify_package(pack(TraceBundle(SPEC_CHAIN, resumed.trace), log=log))
    assert verdict.verdict is not None
    assert any("2 segments" in note for note in verdict.verdict.notes)
    assert any("AN EXECUTION THAT LEFT NO COMPLETION" in note
               for note in verdict.verdict.notes)
    log.close()


def test_package_cli_accepts_good_damaged_and_no_receipt_packages(tmp_path):
    path = tmp_path / "evidence.zip"
    path.write_bytes(pack(bundle()))
    result = subprocess.run(
        [".venv/bin/motus-validate", "package", str(path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "transport_ok: True",
        "INTEGRITY CLEAN",
        "EXISTENCE NOT ESTABLISHED",
        "no receipt, checkpoint, or anchor is present; this package cannot "
        "establish existence. An edited trace can be resealed with a new "
        "root, and without an external anchor that edit cannot be detected.",
    ]
    damaged = tmp_path / "damaged.zip"
    damaged.write_bytes(b"not a zip")
    result = subprocess.run(
        [".venv/bin/motus-validate", "package", str(damaged)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "transport_ok: False" in result.stdout
