from __future__ import annotations

import inspect
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from vitruvyan_motus import Fact, GraphSpec, Runtime, State, TraceBundle
from vitruvyan_motus.commitlog import CommitmentLog
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


def _snapshot_files(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _Source:
    def __init__(self, receipt=None, package=b"not a zip file"):
        self.receipt = receipt or {
            "execution": {
                "ref": "acme/w1/0",
                "fingerprint": "sha256:" + "a" * 64,
                "run_id": "run-1",
            }
        }
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


def test_source_cannot_swap_a_different_execution_under_the_requested_ref():
    source = _Source(receipt={
        "execution": {
            "ref": "acme/w1/9",
            "fingerprint": "sha256:" + "b" * 64,
            "run_id": "other",
        }
    })
    api = EvidenceAPI(source)
    with pytest.raises(ValueError, match="does not bind"):
        api.receipt_for("acme/w1/0")

def test_receipt_is_a_copy_so_a_bridge_cannot_mutate_source_evidence():
    source = _Source()
    api = EvidenceAPI(source)
    returned = api.receipt_for("acme/w1/0")
    returned["execution"]["run_id"] = "changed-by-renderer"
    assert source.receipt["execution"]["run_id"] == "run-1"


def test_verify_runs_the_real_verifier_instead_of_inferring_from_identity():
    source = _Source()
    api = EvidenceAPI(source)
    assert api.receipt_for("acme/w1/0")["execution"]["fingerprint"] is not None
    verdict = api.verify("acme/w1/0")
    assert verdict.transport_ok is False
    assert verdict.verdict is None
    assert source.calls[-1] == ("package", "acme/w1/0")


def test_live_source_retrieval_is_read_only_and_verifies(tmp_path):
    spec = _spec()
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(spec, {"a": _node}, commitments=log).run(
        State.empty("seed"), run_id="run-1")
    log.seal("2026-01-01T00:00:01Z")
    ref = log.find_execution_ref("run-1")
    bundle = TraceBundle(spec, result.trace)
    source = LiveEvidenceSource(log, lambda execution_ref: bundle)
    api = EvidenceAPI(source)

    before = _snapshot_files(tmp_path)
    receipt = api.receipt_for(ref)
    package = api.package_for(ref)
    verdict = api.verify(ref)
    after = _snapshot_files(tmp_path)

    assert before == after
    assert receipt["execution"]["ref"] == ref
    assert isinstance(package, bytes)
    assert verdict.transport_ok is True
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") == "established"
    log.close()


def test_exact_execution_ref_disambiguates_a_retried_run_id(tmp_path):
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

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        receipt = json.loads(archive.read("core/receipt.json"))
    assert receipt["execution"]["ref"] == second_ref
    verdict = api.verify(second_ref)
    assert verdict.verdict is not None
    assert verdict.verdict.status_of("INTEGRITY") == "established"
    log.close()


def test_pack_refuses_an_execution_ref_without_a_commitment_log():
    spec = _spec()
    result = Runtime(spec, {"a": _node}).run(State.empty("x"), run_id="r")
    with pytest.raises(ValueError, match="requires a commitment log"):
        pack(TraceBundle(spec, result.trace), execution_ref="acme/w1/0")


def test_canonical_surface_has_no_product_specific_parameters():
    for owner, names in (
        (EvidenceAPI, ("receipt_for", "package_for", "verify")),
        (EvidenceSource, ("receipt_for", "package_for")),
    ):
        for name in names:
            parameters = list(inspect.signature(getattr(owner, name)).parameters)
            assert parameters == ["self", "execution_ref"]
