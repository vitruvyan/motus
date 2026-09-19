from __future__ import annotations

import hashlib
import inspect
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from vitruvyan_motus import Fact, GraphSpec, Runtime, State, TraceBundle
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork
from vitruvyan_motus.evidence import pack
from vitruvyan_motus.evidence_api import EvidenceAPI, EvidenceSource, LiveEvidenceSource


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _spec():
    return GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "evidence-api",
        "version": "1.0.0",
        "entry": "a",
        "nodes": [{"name": "a", "effect_class": "pure"}],
        "transitions": {"a": {"kind": "terminal"}},
    })


def _node(state):
    return state.with_fact(Fact("answer", 42, "test", NOW))


def _minimal_receipt(ref="acme/w1/0", run_id="run-1"):
    tenant, writer_id, raw_sequence = ref.split("/")
    return {
        "execution": {
            "ref": ref,
            "fingerprint": "sha256:" + "a" * 64,
            "run_id": run_id,
        },
        "segments": [{
            "begin": {
                "commitment": {
                    "kind": "begin",
                    "tenant": tenant,
                    "writer_id": writer_id,
                    "sequence": int(raw_sequence),
                    "run_id": run_id,
                }
            }
        }],
    }


def _snapshot_files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rebuilt(values):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(values.items()):
            archive.writestr(name, payload)
    return out.getvalue()


def _tamper_trace_but_recompute_manifest(data):
    values = _members(data)
    trace = json.loads(values["core/trace.json"])
    transition = next(record for record in trace["records"]
                      if record.get("kind") == "transition")
    transition["writes"]["facts"][0]["value"] = 43
    values["core/trace.json"] = json.dumps(
        trace, separators=(",", ":")).encode()
    manifest = json.loads(values["manifest.json"])
    entry = next(item for item in manifest["files"]
                 if item["name"] == "core/trace.json")
    entry["sha256"] = "sha256:" + hashlib.sha256(
        values["core/trace.json"]).hexdigest()
    values["manifest.json"] = json.dumps(
        manifest, separators=(",", ":")).encode()
    return _rebuilt(values)


def _logged_package(path, *, run_id="run-1", writer_id="w1"):
    spec = _spec()
    log = CommitmentLog(path, tenant="acme", writer_id=writer_id, fsync=False)
    result = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("seed"), run_id=run_id)
    log.seal("2026-01-01T00:00:01Z")
    ref = log.find_execution_ref(run_id)
    bundle = TraceBundle(spec, result.trace)
    package = pack(bundle, log=log, execution_ref=ref)
    log.close()
    return ref, package


class _Source:
    def __init__(self, receipt=None, package=b"not a zip file"):
        self.receipt = receipt or _minimal_receipt()
        self.package = package
        self.calls = []

    def receipt_for(self, execution_ref):
        self.calls.append(("receipt", execution_ref))
        return self.receipt

    def package_for(self, execution_ref):
        self.calls.append(("package", execution_ref))
        return self.package


def test_execution_ref_is_the_only_lookup_key_and_is_checked_before_source():
    source = _Source()
    api = EvidenceAPI(source)
    with pytest.raises(ValueError, match="invalid execution reference"):
        api.receipt_for("run-1")
    with pytest.raises(ValueError, match="invalid execution reference"):
        api.package_for("acme/w1/01")
    assert source.calls == []


def test_source_cannot_swap_a_different_receipt_under_the_requested_ref():
    source = _Source(receipt=_minimal_receipt("acme/w1/9", "other"))
    api = EvidenceAPI(source)
    with pytest.raises(ValueError, match="does not bind"):
        api.receipt_for("acme/w1/0")


def test_receipt_is_a_copy_so_a_bridge_cannot_mutate_source_evidence():
    source = _Source()
    api = EvidenceAPI(source)
    returned = api.receipt_for("acme/w1/0")
    returned["execution"]["run_id"] = "changed-by-renderer"
    assert source.receipt["execution"]["run_id"] == "run-1"


def test_source_cannot_swap_a_different_package_under_the_requested_ref(tmp_path):
    first_ref, _ = _logged_package(tmp_path / "first", run_id="first", writer_id="w1")
    second_ref, second_package = _logged_package(
        tmp_path / "second", run_id="second", writer_id="w2")
    source = _Source(package=second_package)
    api = EvidenceAPI(source)
    assert first_ref != second_ref
    with pytest.raises(ValueError, match="does not bind"):
        api.package_for(first_ref)
    with pytest.raises(ValueError, match="does not bind"):
        api.verify(first_ref)


def test_verify_runs_real_verifier_not_receipt_presence(tmp_path):
    ref, package = _logged_package(tmp_path)
    tampered = _tamper_trace_but_recompute_manifest(package)
    source = _Source(receipt=_minimal_receipt(ref), package=tampered)
    api = EvidenceAPI(source)
    verdict = api.verify(ref)
    assert verdict.transport_ok is True
    assert verdict.trace_violations
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") != "established"


def test_live_source_retrieval_is_read_only_and_verifies_exact_bytes(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("seed"), run_id="run-1")
    log.seal("2026-01-01T00:00:01Z")
    ref = log.find_execution_ref("run-1")
    bundle = TraceBundle(spec, result.trace)
    seen = []
    bundles = {ref: bundle}

    def bundle_for(execution_ref):
        seen.append(execution_ref)
        return bundles[execution_ref]

    source = LiveEvidenceSource(log, bundle_for)
    api = EvidenceAPI(source)
    before = _snapshot_files(tmp_path)
    receipt = api.receipt_for(ref)
    package = api.package_for(ref)
    assert seen == [ref]
    verdict = api.verify(ref, package=package)
    assert seen == [ref]
    after = _snapshot_files(tmp_path)

    assert before == after
    assert receipt["execution"]["ref"] == ref
    assert verdict.transport_ok is True
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") == "established"
    log.close()


def test_exact_execution_ref_disambiguates_a_retried_run_id_and_pack_refuses_swap(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)

    first = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("first"), run_id="retry")
    log.seal("2026-01-01T00:00:01Z")
    first_ref = log.find_execution_ref("retry", sequence=0)

    second = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("second"), run_id="retry")
    log.seal("2026-01-01T00:00:02Z")
    second_ref = log.find_execution_ref("retry", sequence=2)

    bundles = {
        first_ref: TraceBundle(spec, first.trace),
        second_ref: TraceBundle(spec, second.trace),
    }
    api = EvidenceAPI(LiveEvidenceSource(log, bundles.__getitem__))
    package = api.package_for(second_ref)
    verdict = api.verify(second_ref, package=package)
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") == "established"

    with pytest.raises(ValueError, match="does not bind bundle root"):
        pack(bundles[first_ref], log=log, execution_ref=second_ref)
    log.close()


def test_unfinished_repeated_run_id_cannot_be_disambiguated_by_coordinate(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("retry", at="2026-01-01T00:00:00Z", nonce="n0")
    log.seal("2026-01-01T00:00:01Z")
    first_ref = log.find_execution_ref("retry", sequence=0)
    log.begin("retry", at="2026-01-01T00:00:02Z", nonce="n1")
    log.seal("2026-01-01T00:00:03Z")

    result = Runtime(spec, {"a": _node}).stream(
        State.empty("unrelated"), run_id="retry")
    next(result)
    unfinished = TraceBundle(spec, result.trace)
    with pytest.raises(CommitmentLogFork):
        pack(unfinished, log=log, execution_ref=first_ref)
    result.close()
    log.close()


def test_resumed_receipt_accepts_continuation_ref_not_only_top_level_ref(tmp_path):
    from vitruvyan_motus import InMemoryTraceSink, ReplayEngine
    from tests.test_commit_lifecycle import SPEC_CHAIN, _chain_nodes

    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    runtime = Runtime(
        SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(), commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="seg-1")
    next(driver)
    next(driver)
    first = driver.trace
    resumed = ReplayEngine(TraceBundle(SPEC_CHAIN, first)).resume(
        Runtime(
            SPEC_CHAIN, _chain_nodes(), sink=InMemoryTraceSink(), commitments=log),
        run_id="seg-2")
    log.seal("2026-01-01T00:00:01Z")
    continuation_ref = log.find_execution_ref("seg-2")
    bundle = TraceBundle(SPEC_CHAIN, resumed.trace)
    api = EvidenceAPI(
        LiveEvidenceSource(log, {continuation_ref: bundle}.__getitem__))

    receipt = api.receipt_for(continuation_ref)
    assert receipt["execution"]["ref"] != continuation_ref
    package = api.package_for(continuation_ref)
    verdict = api.verify(continuation_ref, package=package)
    assert verdict.verdict is not None
    log.close()


def test_pack_refuses_an_execution_ref_without_a_commitment_log():
    spec = _spec()
    result = Runtime(spec, {"a": _node}).run(State.empty("x"), run_id="r")
    with pytest.raises(ValueError, match="requires a commitment log"):
        pack(TraceBundle(spec, result.trace), execution_ref="acme/w1/0")


def test_canonical_surface_has_no_product_specific_parameters():
    expected = {
        (EvidenceAPI, "receipt_for"): ["self", "execution_ref"],
        (EvidenceAPI, "package_for"): ["self", "execution_ref"],
        (EvidenceAPI, "verify"): ["self", "execution_ref", "package"],
        (EvidenceSource, "receipt_for"): ["self", "execution_ref"],
        (EvidenceSource, "package_for"): ["self", "execution_ref"],
    }
    for (owner, name), parameters in expected.items():
        assert list(inspect.signature(getattr(owner, name)).parameters) == parameters
