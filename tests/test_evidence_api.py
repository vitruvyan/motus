from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

import vitruvyan_motus.evidence_api as evidence_api_module
from vitruvyan_motus import Fact, GraphSpec, Runtime, State, TraceBundle
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork
from vitruvyan_motus.commitments import AnchorReceipt, Attestation
from vitruvyan_motus.evidence import pack, verify_package
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


def _replace_json_member_and_rehash(data, name, value):
    values = _members(data)
    values[name] = json.dumps(value, separators=(",", ":")).encode()
    if name != "manifest.json":
        manifest = json.loads(values["manifest.json"])
        entry = next(item for item in manifest["files"] if item["name"] == name)
        entry["sha256"] = "sha256:" + hashlib.sha256(values[name]).hexdigest()
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


def test_receipt_for_rejects_schema_invalid_bound_receipt():
    api = EvidenceAPI(_Source(receipt=_minimal_receipt()))
    with pytest.raises(ValueError, match="schema-invalid receipt"):
        api.receipt_for("acme/w1/0")

def test_validator_failure_is_not_mislabeled_as_invalid_evidence(
    tmp_path, monkeypatch,
):
    from vitruvyan_motus.contract import validate as contract_validate

    ref, package = _logged_package(tmp_path)
    receipt = json.loads(_members(package)["core/receipt.json"])
    api = EvidenceAPI(_Source(receipt=receipt))

    def fail_validator(document):
        raise RuntimeError("validator unavailable")

    monkeypatch.setattr(contract_validate, "validate_receipt", fail_validator)
    with pytest.raises(RuntimeError, match="validator unavailable"):
        api.receipt_for(ref)


@pytest.mark.parametrize(
    ("field", "forged"),
    [
        ("run_id", "forged-run"),
        ("fingerprint", "sha256:" + "b" * 64),
    ],
)
def test_receipt_for_refuses_forged_derived_execution_identity(
    tmp_path, field, forged,
):
    ref, package = _logged_package(tmp_path)
    receipt = json.loads(_members(package)["core/receipt.json"])
    receipt["execution"][field] = forged
    api = EvidenceAPI(_Source(receipt=receipt))
    with pytest.raises(ValueError, match=f"execution\\.{field}"):
        api.receipt_for(ref)


def test_receipt_is_a_copy_so_a_bridge_cannot_mutate_source_evidence(tmp_path):
    ref, package = _logged_package(tmp_path)
    receipt = json.loads(_members(package)["core/receipt.json"])
    source = _Source(receipt=receipt)
    api = EvidenceAPI(source)
    returned = api.receipt_for(ref)
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


def test_manifest_identity_cannot_disagree_with_valid_receipt(tmp_path):
    ref, package = _logged_package(tmp_path)
    values = _members(package)
    manifest = json.loads(values["manifest.json"])
    manifest["execution"]["run_id"] = "forged-manifest-run"
    tampered = _replace_json_member_and_rehash(package, "manifest.json", manifest)

    # The underlying evidence still verifies: this test is specifically the
    # consumer-boundary identity substitution ADR-034 requires Motus to catch.
    raw_verdict = verify_package(tampered)
    assert raw_verdict.verdict is not None
    assert raw_verdict.verdict.status_of("INTEGRITY") == "established"

    api = EvidenceAPI(_Source(package=tampered))
    with pytest.raises(ValueError, match="manifest execution identity disagrees"):
        api.package_for(ref)
    with pytest.raises(ValueError, match="manifest execution identity disagrees"):
        api.verify(ref)


def test_malformed_manifest_is_rejected_by_retrieval_but_verified_fail_closed(tmp_path):
    ref, package = _logged_package(tmp_path)
    manifest = json.loads(_members(package)["manifest.json"])
    manifest.pop("files")
    manifest["execution"]["run_id"] = "forged-manifest-run"
    malformed = _replace_json_member_and_rehash(
        package, "manifest.json", manifest)
    api = EvidenceAPI(_Source(package=malformed))

    with pytest.raises(ValueError, match="malformed manifest: missing file list"):
        api.package_for(ref)

    verdict = api.verify(ref, package=malformed)
    assert verdict.transport_ok is False
    assert any("manifest.json: no file list" in item for item in verdict.damaged)


def test_verify_schema_invalid_receipt_preserves_fail_closed_verdict(tmp_path):
    ref, package = _logged_package(tmp_path)
    malformed = _replace_json_member_and_rehash(package, "core/receipt.json", {})
    api = EvidenceAPI(_Source(package=malformed))

    verdict = api.verify(ref)
    assert verdict.transport_ok is True
    assert verdict.verdict is not None
    assert verdict.verdict.violations or verdict.verdict.refused
    with pytest.raises(ValueError, match="schema-invalid receipt"):
        api.package_for(ref)


def test_verify_malformed_package_returns_fail_closed_verdict_not_exception():
    source = _Source(package=b"not a zip file")
    api = EvidenceAPI(source)
    verdict = api.verify("acme/w1/0")
    assert verdict.transport_ok is False
    assert verdict.verdict is None
    assert verdict.damaged == ("<not a zip file>",)
    assert source.calls == [("package", "acme/w1/0")]


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


def test_verify_no_receipt_package_is_transport_clean_but_establishes_no_verdict():
    spec = _spec()
    result = Runtime(spec, {"a": _node}).run(State.empty("x"), run_id="r")
    package = pack(TraceBundle(spec, result.trace))
    api = EvidenceAPI(_Source(package=package))
    verdict = api.verify("acme/w1/0")
    assert verdict.transport_ok is True
    assert verdict.damaged == ()
    assert verdict.verdict is None


def test_live_source_carries_anchor_and_attestation_providers_into_package(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("seed"), run_id="run-1")
    checkpoint = log.seal("2026-01-01T00:00:01Z")
    ref = log.find_execution_ref("run-1")
    bundle = TraceBundle(spec, result.trace)
    root = result.trace.root
    assert root is not None
    anchor = AnchorReceipt(
        "anchor-1", "tron:nile", checkpoint.digest, "pending", proof={})
    imprint = hashlib.sha256(bytes.fromhex(root.split(":", 1)[1])).hexdigest()
    attestation = Attestation(
        "tsa-1", "rfc3161_timestamp", "tsa.example", root,
        "2026-01-01T00:00:02Z", "sha256",
        {"token_der": base64.b64encode(b"\\x00" * 96).decode("ascii"),
         "tsa_url": "https://tsa.example/timestamp",
         "message_imprint": imprint},
    )
    calls = []

    def anchors_for(execution_ref):
        calls.append(("anchors", execution_ref))
        return (anchor,)

    def attestations_for(execution_ref):
        calls.append(("attestations", execution_ref))
        return (attestation,)

    api = EvidenceAPI(LiveEvidenceSource(
        log, {ref: bundle}.__getitem__,
        anchors_for=anchors_for, attestations_for=attestations_for,
    ))
    package = api.package_for(ref)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        receipt = json.loads(archive.read("core/receipt.json"))
    assert receipt["anchors"] == [anchor.to_dict()]
    assert receipt["attestations"] == [attestation.to_dict()]
    assert calls == [("anchors", ref), ("attestations", ref)]
    log.close()


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
    raw_receipt = log.receipt_for(continuation_ref)
    original_ref = raw_receipt["execution"]["ref"]
    assert original_ref != continuation_ref
    api = EvidenceAPI(LiveEvidenceSource(
        log, {original_ref: bundle, continuation_ref: bundle}.__getitem__))

    receipt = api.receipt_for(continuation_ref)
    assert receipt["execution"]["ref"] == original_ref
    for ref in (original_ref, continuation_ref):
        package = api.package_for(ref)
        verdict = api.verify(ref, package=package)
        assert verdict.verdict is not None
        assert verdict.verdict.status_of("INTEGRITY") == "established"
    log.close()


def test_legacy_pack_without_explicit_ref_keeps_verifier_driven_mismatch_behavior(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("legacy", at="2026-01-01T00:00:00Z", nonce="n0")
    log.seal("2026-01-01T00:00:01Z")
    result = Runtime(spec, {"a": _node}).run(State.empty("x"), run_id="legacy")

    # Before ADR-034, pack(bundle, log=...) produced bytes and left the
    # unfinished/completed mismatch to the verifier. The new explicit-ref path
    # must not turn that legacy call into a construction-time exception.
    package = pack(TraceBundle(spec, result.trace), log=log)
    verdict = verify_package(package)
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") != "established"
    log.close()


def test_pack_refuses_an_execution_ref_without_a_commitment_log():
    spec = _spec()
    result = Runtime(spec, {"a": _node}).run(State.empty("x"), run_id="r")
    with pytest.raises(ValueError, match="requires a commitment log"):
        pack(TraceBundle(spec, result.trace), execution_ref="acme/w1/0")


def test_canonical_surface_has_only_generic_parameters():
    expected = {
        (EvidenceAPI, "receipt_for"): ["self", "execution_ref"],
        (EvidenceAPI, "package_for"): ["self", "execution_ref"],
        (EvidenceAPI, "verify"): ["self", "execution_ref", "package"],
        (EvidenceSource, "receipt_for"): ["self", "execution_ref"],
        (EvidenceSource, "package_for"): ["self", "execution_ref"],
    }
    for (owner, name), parameters in expected.items():
        assert list(inspect.signature(getattr(owner, name)).parameters) == parameters


def test_canonical_module_contains_no_consumer_product_model():
    source = inspect.getsource(evidence_api_module).casefold()
    for forbidden in ("orbis", "limen", "customer_id", "provider_id", "vertical_id"):
        assert forbidden not in source
