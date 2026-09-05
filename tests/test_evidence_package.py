from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone

import pytest

from vitruvyan_motus import Fact, GraphSpec, Runtime, State, TraceBundle
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork
import vitruvyan_motus.evidence as evidence_module
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


@pytest.mark.parametrize("trace_json", [b"[]", b'"trace"'])
def test_non_dict_trace_reports_exact_refusal_for_each_json_shape(tmp_path, trace_json):
    source, log = logged_bundle(tmp_path)
    values = members(pack(source, log=log))
    values["core/trace.json"] = trace_json
    result = verify_package(rebuilt(values))
    assert result.trace_violations == (
        "TRACE core/trace.json: not valid JSON object",
    )
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
        [sys.executable, "-m", "vitruvyan_motus.contract.validate", "package", str(path)],
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
        [sys.executable, "-m", "vitruvyan_motus.contract.validate", "package", str(path)],
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


def _corrupt_compressed_member(data, name):
    raw = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = next(item for item in archive.infolist() if item.filename == name)
        start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        # This byte/bit mutation is deliberately known to make zlib reject
        # the raw DEFLATE stream, rather than merely failing its ZIP CRC.
        raw[start] ^= 0x02
    return bytes(raw)


def test_64mib_astral_manifest_has_no_copy_amplification_or_traceback(tmp_path):
    # 8M astral scalars are well over 28 MiB of UTF-8 once JSON framing is
    # included. The child limit makes whole-document amplification observable.
    payload = b'{"files":[],"padding":"' + ("😀" * 8_000_000).encode() + b'"}'
    path = tmp_path / "astral.zip"
    path.write_bytes(rebuilt({"manifest.json": payload}))

    def limit_memory():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (700 * 1024 * 1024,
                                                700 * 1024 * 1024))

    result = subprocess.run(
        [sys.executable, "-m", "vitruvyan_motus.contract.validate",
         "package", str(path)], capture_output=True, text=True,
        preexec_fn=limit_memory,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stdout + result.stderr
    assert "MemoryError" not in result.stdout + result.stderr


def test_fixed_member_size_bound_is_named_and_mutation_worthy(monkeypatch):
    monkeypatch.setattr(evidence_module, "_FIXED_MEMBER_MAX_BYTES", 32)

    oversized_manifest = rebuilt({
        "manifest.json": b'{"files":[],"padding":"' + b"x" * 100 + b'"}',
    })
    verdict = verify_package(oversized_manifest)
    assert any("manifest.json: damaged/refused" in item
               for item in verdict.damaged)
    assert "Traceback" not in "\\n".join(verdict.damaged)

    oversized_trace = rebuilt({
        "manifest.json": b'{"files":[]}',
        "core/trace.json": b'{"padding":"' + b"x" * 100 + b'"}',
    })
    verdict = verify_package(oversized_trace)
    assert any("core/trace.json: damaged/refused" in item
               for item in verdict.trace_violations)
    assert "Traceback" not in "\\n".join(verdict.damaged + verdict.trace_violations)


def test_manifest_hashes_fixed_members_with_the_same_bound(monkeypatch):
    payload = b'{"padding":"' + b"x" * 1000 + b'"}'
    manifest = json.dumps({"files": [{
        "name": "core/trace.json",
        "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }]} , separators=(",", ":")).encode()
    monkeypatch.setattr(evidence_module, "_FIXED_MEMBER_MAX_BYTES", 128)
    original_digest = evidence_module._digest_member

    def digest_only_arbitrary(archive, name):
        assert name not in evidence_module._FIXED_JSON_MEMBERS
        return original_digest(archive, name)

    monkeypatch.setattr(evidence_module, "_digest_member", digest_only_arbitrary)
    verdict = verify_package(rebuilt({
        "manifest.json": manifest,
        "core/trace.json": payload,
    }))
    assert any("core/trace.json: damaged/refused" in item
               for item in verdict.damaged)


def test_real_corrupted_deflate_is_named_for_manifest_and_graphspec():
    data = pack(bundle())
    for name in ("manifest.json", "core/graphspec.json"):
        corrupted = _corrupt_compressed_member(data, name)
        with pytest.raises(zlib.error):
            with zipfile.ZipFile(io.BytesIO(corrupted)) as archive:
                archive.read(name)
        verdict = verify_package(corrupted)
        assert verdict.transport_ok is False
        assert any(name in item for item in verdict.damaged)
        assert "Traceback" not in "\\n".join(verdict.damaged)


@pytest.mark.parametrize("failure", [
    NotImplementedError(), RuntimeError("encrypted"), zipfile.LargeZipFile(),
    zipfile.BadZipFile("truncated"), EOFError("truncated"), zlib.error("bad"),
])
def test_archive_read_refusals_are_named_and_traceback_free(monkeypatch, failure):
    original = zipfile.ZipFile.open

    def refuse(self, name, *args, **kwargs):
        if name == "manifest.json":
            raise failure
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", refuse)
    verdict = verify_package(pack(bundle()))
    assert verdict.transport_ok is False
    assert "manifest.json" in verdict.damaged[0]
    assert "traceback" not in " ".join(verdict.damaged).lower()


def test_duplicate_physical_member_names_are_refused_before_lookup():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    verdict = verify_package(out.getvalue())
    assert verdict.transport_ok is False
    assert verdict.damaged == ("<duplicate member>: manifest.json",)


def test_fixed_json_members_use_strict_loader_for_duplicate_keys_and_j2(tmp_path):
    source, log = logged_bundle(tmp_path)
    values = members(pack(source, log=log))
    # A duplicate in the manifest is a package refusal, not a last-value parse.
    values["manifest.json"] = b'{"files": [], "files": []}'
    result = verify_package(rebuilt(values))
    assert any("manifest.json: J1" in item for item in result.damaged)

    # The other fixed-path artifacts must not regress to the permissive loader.
    for name, marker in (("core/trace.json", "J1 core/trace.json"),
                         ("core/graphspec.json", "J1 core/graphspec.json"),
                         ("core/receipt.json", "RECEIPT J1 core/receipt.json")):
        values = members(pack(source, log=log))
        values[name] = b'{"x": 1, "x": 2}'
        manifest = json.loads(values["manifest.json"])
        next(item for item in manifest["files"] if item["name"] == name)["sha256"] = (
            "sha256:" + hashlib.sha256(values[name]).hexdigest())
        values["manifest.json"] = json.dumps(
            manifest, separators=(",", ":")).encode()
        result = verify_package(rebuilt(values))
        assert any(marker in item for item in result.trace_violations)

    values = members(pack(source, log=log))
    values["core/trace.json"] = values["core/trace.json"].replace(
        b'"value":42', b'"value":1e+00', 1)
    manifest = json.loads(values["manifest.json"])
    next(item for item in manifest["files"]
         if item["name"] == "core/trace.json")["sha256"] = (
        "sha256:" + hashlib.sha256(values["core/trace.json"]).hexdigest())
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()
    result = verify_package(rebuilt(values))
    assert any(item.startswith("J2 core/trace.json:")
               for item in result.trace_violations)
    log.close()


def test_package_nesting_refusal_and_ordinary_manifest_errors_are_not_j1():
    deep = b"[" * 10000 + b"]" * 10000
    verdict = verify_package(rebuilt({"manifest.json": deep}))
    assert verdict.damaged == (
        "manifest.json: nesting beyond what this verifier can parse",)
    assert all("J1" not in item for item in verdict.damaged + verdict.trace_violations)

    malformed = verify_package(rebuilt({"manifest.json": b"{"}))
    assert malformed.damaged[0].startswith("manifest.json: not valid JSON: ")
    assert "J1" not in malformed.damaged[0]
    invalid_utf8 = verify_package(rebuilt({"manifest.json": b"\xff"}))
    assert invalid_utf8.damaged[0].startswith("manifest.json: not valid UTF-8:")
    assert "J1" not in invalid_utf8.damaged[0]
    trace_utf8 = verify_package(rebuilt({
        "manifest.json": b'{}', "core/trace.json": b"\xff",
    }))
    assert trace_utf8.trace_violations[0].startswith(
        "core/trace.json: not valid UTF-8: ")
    assert "J1" not in trace_utf8.trace_violations[0]


def test_duplicate_manifest_entries_are_reported_without_rehashing(monkeypatch):
    payload = b"attachment"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    manifest = json.dumps({"files": [
        {"name": "supplementary/attachments/a", "sha256": digest},
        {"name": "supplementary/attachments/a", "sha256": digest},
    ]}, separators=(",", ":")).encode()
    calls = []
    original_digest = evidence_module._digest_member

    def count_digest(archive, name):
        calls.append(name)
        return original_digest(archive, name)

    monkeypatch.setattr(evidence_module, "_digest_member", count_digest)
    verdict = verify_package(rebuilt({
        "manifest.json": manifest,
        "supplementary/attachments/a": payload,
    }))
    assert verdict.transport_ok is False
    assert verdict.damaged.count("<duplicate manifest entry>: supplementary/attachments/a") == 1
    assert calls == ["supplementary/attachments/a"]


def test_deep_nested_fixed_members_are_package_refusals_and_cli_has_no_traceback(tmp_path):
    deep = b"[" * 10000 + b"]" * 10000

    # Manifest is the first fixed member and must fail closed at the package boundary.
    manifest_zip = rebuilt({"manifest.json": deep})
    verdict = verify_package(manifest_zip)
    assert verdict.damaged == (
        "manifest.json: nesting beyond what this verifier can parse",)
    assert all("J1" not in item for item in verdict.damaged + verdict.trace_violations)

    # Exercise a different fixed member after a valid manifest has been read.
    values = members(pack(bundle()))
    values["core/trace.json"] = deep
    manifest = json.loads(values["manifest.json"])
    next(item for item in manifest["files"]
         if item["name"] == "core/trace.json")["sha256"] = (
        "sha256:" + hashlib.sha256(deep).hexdigest())
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")).encode()
    trace_zip = tmp_path / "deep-trace.zip"
    trace_zip.write_bytes(rebuilt(values))
    verdict = verify_package(trace_zip.read_bytes())
    assert (
        "core/trace.json: nesting beyond what this verifier can parse"
        in verdict.trace_violations
    )
    assert all("J1" not in item for item in verdict.trace_violations)

    cli = subprocess.run(
        [sys.executable, "-m", "vitruvyan_motus.contract.validate",
         "package", str(trace_zip)], capture_output=True, text=True)
    assert cli.returncode == 1
    assert "core/trace.json: nesting beyond what this verifier can parse" in cli.stdout
    assert "Traceback" not in cli.stdout + cli.stderr


@pytest.mark.parametrize(
    ("name", "marker"),
    [("core/graphspec.json", "SB0 core/graphspec.json:"),
     ("core/receipt.json", "RECEIPT core/receipt.json:")],
)
def test_deep_nested_other_fixed_members_are_named_package_refusals(
    tmp_path, name, marker,
):
    source, log = logged_bundle(tmp_path)
    values = members(pack(source, log=log))
    deep = b"[" * 10000 + b"]" * 10000
    values[name] = deep
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"] if item["name"] == name)
    entry["sha256"] = "sha256:" + hashlib.sha256(deep).hexdigest()
    values["manifest.json"] = json.dumps(manifest, separators=(",", ":")).encode()

    verdict = verify_package(rebuilt(values))
    assert any(
        f"{marker} nesting beyond what this verifier can parse" in item
        for item in verdict.trace_violations
    )
    assert all("J1" not in item for item in verdict.trace_violations)
    log.close()


def test_package_cli_accepts_good_damaged_and_no_receipt_packages(tmp_path):
    path = tmp_path / "evidence.zip"
    path.write_bytes(pack(bundle()))
    result = subprocess.run(
        [sys.executable, "-m", "vitruvyan_motus.contract.validate", "package", str(path)],
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
        [sys.executable, "-m", "vitruvyan_motus.contract.validate", "package", str(damaged)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "transport_ok: False" in result.stdout
