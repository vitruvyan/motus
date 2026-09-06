"""Contract-boundary coverage for commitment dataclasses."""
import dataclasses
import json
import re
from pathlib import Path

import pytest

from vitruvyan_motus import commitments
from vitruvyan_motus.commitments import (
    AnchorReceipt, Attestation, Checkpoint, Commitment, Continuation, WitnessAck,
)
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork, InclusionProof

_CLASSES = [WitnessAck, AnchorReceipt, Attestation, Continuation, Commitment, Checkpoint]
CONTRACT_DIR = Path(__file__).parents[1] / "contract"

@pytest.mark.parametrize("cls", _CLASSES, ids=lambda cls: cls.__name__)
def test_every_dataclass_field_is_classified(cls):
    known = set(commitments._SCHEMA_FIELDS.get(cls, {})) | set(commitments._NON_SCHEMA_FIELDS.get(cls, {}))
    actual = {field.name for field in dataclasses.fields(cls)}
    assert actual <= known, f"{cls.__name__} has unclassified fields: {actual - known}"
    assert known <= actual, f"{cls.__name__} has stale classifications: {known - actual}"

def test_inclusion_proof_fields_are_nested_or_delegated():
    assert {field.name for field in dataclasses.fields(InclusionProof)} == {"commitment", "path", "checkpoint"}

def test_schema_field_bounds_match_contract():
    defs = json.loads((CONTRACT_DIR / "commitment.v1.schema.json").read_text())["$defs"]
    assert defs["Identifier"]["maxLength"] == commitments._IDENTIFIER_MAX_LENGTH
    assert defs["Identifier"]["minLength"] == 1
    assert defs["Timestamp"] == {"type": "string", "minLength": 1, "pattern": r"\S"}
    for value, valid in [("sha256:" + "a" * 64, True), ("sha256:" + "A" * 64, False),
                         ("sha1:" + "a" * 64, False), ("sha256:" + "a" * 63, False)]:
        assert bool(re.fullmatch(defs["Digest"]["pattern"], value)) is valid
        try: commitments._require_digest(value, "x")
        except ValueError: assert not valid
        else: assert valid
    for value, valid in [("bundle:sha256:" + "a" * 64, True), ("bundle:sha256:" + "A" * 64, False),
                         ("sha256:" + "a" * 64, False)]:
        assert bool(re.fullmatch(defs["BundleFingerprint"]["pattern"], value)) is valid
        try: commitments._require_bundle_fingerprint(value, "x")
        except ValueError: assert not valid
        else: assert valid

_DIGEST = "sha256:" + "a" * 64
_FINGERPRINT = "bundle:sha256:" + "a" * 64

def _begin(**changes):
    values = dict(kind=commitments.CommitmentKind.BEGIN, tenant="t", writer_id="w", sequence=0,
                  run_id="r", at="2026-01-01T00:00:00Z", nonce="n")
    values.update(changes)
    return Commitment(**values)

@pytest.mark.parametrize("field", ["tenant", "writer_id", "run_id", "nonce"])
def test_identifier_bound_is_refused_at_commitment_construction(field):
    with pytest.raises(ValueError, match=rf"{field}.*Identifier"): _begin(**{field: "x" * 201})

def test_timestamp_offset_is_currently_accepted_by_commitment_contract():
    _begin(at="2026-01-01T00:00:00+02:00")

@pytest.mark.parametrize("value", ["notsha256:" + "a" * 64, "sha256:" + "a" * 63])
def test_digest_bound_is_refused_at_construction(value):
    with pytest.raises(ValueError, match=r"root.*Digest"):
        Commitment(kind=commitments.CommitmentKind.END, tenant="t", writer_id="w", sequence=0,
                   run_id="r", at="at", nonce="n", root=value, outcome="done")

def test_pending_anchor_reference_is_checked_too():
    with pytest.raises(ValueError, match=r"reference.*Identifier"):
        AnchorReceipt("a", "n", _DIGEST, "pending", reference="r" * 201)

def test_witness_algorithm_is_an_identifier():
    with pytest.raises(ValueError, match=r"algorithm.*Identifier"):
        WitnessAck("w", _DIGEST, 0, "at", "sig", algorithm="a" * 201)

def test_continuation_identifier_bound_is_refused():
    with pytest.raises(ValueError, match=r"run_id.*Identifier"): Continuation("r" * 201, _FINGERPRINT)


def test_commitment_kind_must_be_the_closed_enum():
    from enum import Enum

    class ForeignKind(str, Enum):
        END = "end"

    with pytest.raises(ValueError, match=r"Commitment\.kind.*CommitmentKind"):
        _begin(kind=ForeignKind.END, outcome="done", root=_DIGEST)


def test_checkpoint_count_rejects_bool_at_construction():
    with pytest.raises(ValueError, match=r"checkpoint count.*non-negative integer"):
        Checkpoint("t", "w", 0, _DIGEST, True, 0, 0, "at")


def test_anchor_proof_must_be_a_dict():
    with pytest.raises(ValueError, match=r"AnchorReceipt\.proof.*dict"):
        AnchorReceipt("a", "n", _DIGEST, "pending", proof=42)


@pytest.mark.parametrize(
    "bad, leaf, error",
    [
        ({"one": {"two": {"three": b"bytes"}}}, "three", TypeError),
        ({"one": {"two": {"three": {1: "value"}}}}, "three", TypeError),
        ({"one": {"two": {"three": {"value": float("nan")}}}}, "value", ValueError),
        ({"one": {"two": {"three": {"value": {1, 2}}}}}, "value", TypeError),
    ],
)
def test_anchor_proof_refuses_non_json_values_with_nested_path(bad, leaf, error):
    with pytest.raises(error, match=rf"AnchorReceipt\.proof.*{leaf}.*J1"):
        AnchorReceipt("a", "n", _DIGEST, "pending", proof=bad)


def test_anchor_proof_accepts_valid_json_at_arbitrary_depth():
    proof = value = {}
    for index in range(100):
        value["level"] = {}
        value = value["level"]
    value["answer"] = [None, True, 3, 4.5, "text"]
    receipt = AnchorReceipt("a", "n", _DIGEST, "pending", proof=proof)
    assert receipt.proof == proof


def test_anchor_proof_uses_one_items_snapshot_for_dict_subclasses():
    class Sneaky(dict):
        def items(self):
            yield "kind", "redacted"
            yield "payload", b"not-json"

    with pytest.raises(ValueError, match=r"AnchorReceipt\.proof.*J1"):
        AnchorReceipt("a", "n", _DIGEST, "pending", proof=Sneaky({"safe": 1}))


def test_anchor_proof_isolated_from_source_mutation():
    source = {"safe": {"value": 1}}
    receipt = AnchorReceipt("a", "n", _DIGEST, "pending", proof=source)
    source["unsafe"] = b"not-json"
    assert receipt.to_dict()["proof"] == {"safe": {"value": 1}}


def test_anchor_proof_accessor_mutation_is_refused_on_serialization():
    receipt = AnchorReceipt("a", "n", _DIGEST, "pending", proof={"safe": True})
    receipt.proof["unsafe"] = float("nan")
    with pytest.raises(ValueError, match=r"AnchorReceipt\.proof.*unsafe.*J1"):
        receipt.to_dict()


def test_nullable_schema_classification_is_exhaustive():
    expected = {
        (Commitment, "root"), (Commitment, "outcome"),
        (Checkpoint, "previous"), (Continuation, "writer_id"),
        (AnchorReceipt, "reference"), (AnchorReceipt, "published_at"),
    }
    assert commitments._NULLABLE_SCHEMA_FIELDS == expected
    required = {
        (cls, field) for cls, fields in commitments._SCHEMA_FIELDS.items()
        for field in fields
    } - expected
    assert required

    instances = {
        WitnessAck: WitnessAck("w", _DIGEST, 0, "at", "sig"),
        AnchorReceipt: AnchorReceipt("a", "n", _DIGEST, "pending"),
        Attestation: Attestation("aid", "type", "issuer", _DIGEST, "at",
                                 "sha256", proof={"token_der": "AAA=",
                                                    "tsa_url": "tsa.example"}),
        Continuation: Continuation("r", _FINGERPRINT),
        Commitment: _begin(),
        Checkpoint: Checkpoint("t", "w", 0, _DIGEST, 1, 0, 0, "at"),
    }
    for cls, field in required:
        with pytest.raises(ValueError, match=rf"{field}"):
            dataclasses.replace(instances[cls], **{field: None})


def test_public_begin_and_seal_refuse_required_none(tmp_path):
    log = CommitmentLog(tmp_path, tenant="t", writer_id="w", fsync=False)
    with pytest.raises(ValueError, match="run_id"):
        log.begin(None, at="at", nonce="n")
    log.begin("r", at="at", nonce="n")
    with pytest.raises(ValueError, match="sealed_at"):
        log.seal(None)
    log.close()


def test_legacy_overlong_commitment_replay_is_a_fork(tmp_path):
    log = CommitmentLog(tmp_path, tenant="t", writer_id="w", fsync=False)
    log.begin("r", at="at", nonce="n")
    log.seal("sealed")
    log.close()
    path = log.directory / "window-000000.jsonl"
    body = json.loads(path.read_text().splitlines()[0])
    body["commitment"]["run_id"] = "x" * 201
    path.write_text(json.dumps(body) + "\n")
    reopened = CommitmentLog(tmp_path, tenant="t", writer_id="w", fsync=False)
    with pytest.raises(CommitmentLogFork, match="unreadable commitment"):
        reopened.find_execution_ref("r")
    reopened.close()


def test_kind_and_nested_values_require_exact_dataclass_types():
    class SpoofKind:
        value = "end"
        @property
        def __class__(self):
            return commitments.CommitmentKind

    with pytest.raises(ValueError, match="Commitment.kind"):
        _begin(kind=SpoofKind(), outcome="done", root=_DIGEST)
    with pytest.raises(ValueError, match="Commitment.witness"):
        _begin(witness=object())
    with pytest.raises(ValueError, match="Commitment.continues"):
        _begin(continues=object())
