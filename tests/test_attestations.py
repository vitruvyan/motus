"""ADR-031 — the attestation container. What a receipt may carry, what the
validator refuses, and what a verified receipt may STILL not be told.

The second container ADR-020 decision 8 reserved. Every rule here exists
because a holder can otherwise mint a property by typing it: an attestation's
`subject` must be a digest THIS receipt carries (P9); its `type`, `algorithm`
and `proof` must be ones the validator can at least name (P10, refused rather
than downgraded); and the verdict it supports is CLAIMED about what the
subject covers — never ESTABLISHED, never about more than what the subject's
checkpoint seals (ADR-031 decision 7, review correction 1).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State
from vitruvyan_motus.commitlog import CommitmentLog
from vitruvyan_motus.commitments import Attestation
from vitruvyan_motus.evidence import pack, verify_package

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = ROOT / "contract"
NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
AT = "2026-08-13T10:00:00Z"

_SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "verify", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})

VALID_TSA_URL = "https://tsa.example/timestamp"


def _validate_module():
    name = "motus_contract_validate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = _validate_module()


def _node(state):
    return state.with_fact(Fact("k", 1, "test", NOW))


@pytest.fixture()
def run(tmp_path):
    """A real, completed, single-window run and its receipt (as test_verifier)."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(_SPEC, {"a": _node}, sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="r1")
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/0")
    trace = result.trace.to_dict()
    log.close()
    return receipt, trace


def _attestation(subject: str, **changes) -> dict:
    body = {
        "attestation_id": "tsa-1", "type": "rfc3161_timestamp",
        "issuer": "fake-tsa.example", "subject": subject, "issued_at": AT,
        "algorithm": "sha256",
        "proof": {"token_der": "AAAA", "tsa_url": VALID_TSA_URL},
    }
    body.update(changes)
    return body


def _root(receipt) -> str:
    return receipt["segments"][-1]["end"]["commitment"]["root"]


def _completed(tmp_path):
    """A completed two-window run (BEGIN in window 0, END in window 1)."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r1", at=AT, nonce="n0")
    first = log.seal(AT)
    log.end("r1", root="sha256:" + "d" * 64, outcome="completed",
            at=AT, nonce="n1")
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/0")
    log.close()
    return receipt, first


# --------------------------------------------------------------------------- #
# ADR-031 decision 4 — the closed table of four rows                         #
# --------------------------------------------------------------------------- #


def test_the_four_row_attestation_table_is_exactly_what_a_receipt_may_carry(run):
    """The table of ADR-031 decision 4, as a frozen fact. One row is added at
    1.0.0; three are named and refused. A fifth name — or a refused row moved
    into the known set — fails this test until somebody writes its row (its
    semantics, algorithms, proof keys and verdict wording) in the ADR and
    here."""
    rows = {
        "rfc3161_timestamp": "added",
        "qualified_timestamp": "refused",
        "electronic_seal": "refused",
        "identity": "refused",
    }
    assert set(rows) == {"rfc3161_timestamp", "qualified_timestamp",
                         "electronic_seal", "identity"}
    assert validate.KNOWN_ATTESTATION_TYPES == frozenset(
        {kind for kind, row in rows.items() if row == "added"})


@pytest.mark.parametrize("kind", ["qualified_timestamp", "electronic_seal", "identity"])
def test_every_refused_row_refuses_the_whole_verdict(run, kind):
    """Each refused row is a refusal, not a downgrade. ADR-021 decision 8: a
    report about a document the verifier cannot read is the worst lie this
    system can tell — and 'not established, the document is wrong' would be
    the P5 mistake: the document may be perfectly correct, just about a claim
    this verifier cannot evaluate."""
    receipt, _ = run
    receipt["attestations"] = [_attestation(_root(receipt), type=kind)]
    verdict = validate.verify(receipt)
    assert verdict.refused
    assert all(f.status == validate.REFUSED for f in verdict.findings)
    reason = next(f.reason for f in verdict.findings if f.level == "INTEGRITY")
    assert repr(kind) in reason


def test_the_added_row_is_never_refused(run):
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt))]
    assert not validate.verify(receipt, trace).refused


# --------------------------------------------------------------------------- #
# the verdict: CLAIMED for what the subject covers, nothing more              #
# --------------------------------------------------------------------------- #


def test_an_attestation_over_the_run_root_claims_existence(run):
    """ADR-031 decision 5: EXISTENCE as CLAIMED, naming the issuer and the
    incantation a reader can run, never as established — this validator holds
    no TSA key and checks no CMS signature, same posture as an anchor."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt))]
    verdict = validate.verify(receipt, trace)
    assert not verdict.refused
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    assert verdict.status_of("INTEGRITY") == validate.ESTABLISHED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "fake-tsa.example" in reason
    assert "openssl ts -verify -in response.tsr" in reason
    assert "CLAIMS the" in reason
    assert "ESTABLISHED" not in reason.upper()


def test_an_attestation_over_the_end_sealing_checkpoint_claims_the_run(run):
    """A checkpoint whose sealed range includes the END covers the execution
    (ADR-031 decision 7)."""
    receipt, trace = run
    end_checkpoint = validate.checkpoint_digest(
        receipt["segments"][-1]["end"]["checkpoint"])
    receipt["attestations"] = [_attestation(end_checkpoint)]
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    assert "openssl ts -verify -in response.tsr" in next(
        f.reason for f in verdict.findings if f.level == "EXISTENCE")


def test_an_attestation_over_the_begins_checkpoint_alone_is_not_the_run(tmp_path):
    """ADR-031 decision 7, review correction 1 — the exact two-window case.
    BEGIN seals in window 0, the END in a later window, the token covers only
    the first checkpoint: existence of the BEGIN no later than T, EXISTENCE
    for the run NOT ESTABLISHED. Proving a checkpoint never proves a run
    (ADR-021 decision 7)."""
    receipt, first = _completed(tmp_path)
    receipt["attestations"] = [_attestation(
        validate.checkpoint_digest(first.to_dict()))]
    verdict = validate.verify(receipt)
    assert not verdict.refused
    assert verdict.status_of("EXISTENCE") == validate.NOT_ESTABLISHED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "existence of the BEGIN no later than" in reason
    assert "END" in reason


def test_the_anchor_path_says_the_same_sentence_for_a_begin_only_checkpoint(tmp_path):
    """ADR-031 decision 7: the coverage sentence is said for an anchor whose
    checkpoint does not seal the END too — the ADR says it for both."""
    receipt, first = _completed(tmp_path)
    receipt["anchors"] = [{
        "anchor_id": "tron", "network": "tron:nile",
        "checkpoint": validate.checkpoint_digest(first.to_dict()),
        "state": "anchored", "reference": "6010ded8", "published_at": AT,
    }]
    verdict = validate.verify(receipt)
    assert verdict.status_of("EXISTENCE") == validate.NOT_ESTABLISHED
    assert verdict.status_of("RETENTION") == validate.NOT_ESTABLISHED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "existence of the BEGIN no later than" in reason


def test_an_attestation_over_an_unfinished_receipt_claims_the_begin(tmp_path):
    """A receipt with no END never conflates the token with a completion that
    did not happen: the claim is the BEGIN, and only the BEGIN."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r1", at=AT, nonce="n0")
    first = log.seal(AT)
    receipt = log.receipt_for("acme/w1/0")
    log.close()
    receipt["attestations"] = [_attestation(
        validate.checkpoint_digest(first.to_dict()))]
    verdict = validate.verify(receipt)
    assert not verdict.refused
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "existence of the BEGIN no later than" in reason


def test_empty_attestations_is_identical_to_absent(run):
    """ADR-031 decision 2: absent and empty mean the same thing — nobody
    asserted anything. The whole verdict must be equal, byte for byte."""
    receipt, trace = run
    before = validate.verify(receipt, trace)
    receipt["attestations"] = []
    after = validate.verify(receipt, trace)
    assert before == after


def test_an_attestation_never_moves_the_mode(run):
    """ADR-031 decision 6: no attestation moves `mode` and P3 stays — a
    receipt claiming QUALIFIED is refused by P3 whatever attestations it
    carries."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt))]
    receipt["mode"] = "qualified"
    verdict = validate.verify(receipt, trace)
    assert any(v.rule == "P3" for v in verdict.violations)
    assert not verdict.refused


# --------------------------------------------------------------------------- #
# P9 — the subject must be a checkpoint or root of THIS receipt              #
# --------------------------------------------------------------------------- #


def test_a_real_token_from_receipt_a_pasted_into_receipt_b_is_p9(run, tmp_path):
    """The adversary's move: take a genuine token from receipt A and paste it
    into receipt B. The token is real and its subject is a genuine digest —
    just not one that belongs to THIS receipt."""
    receipt_a, trace_a = run
    receipt_a["attestations"] = [_attestation(_root(receipt_a))]

    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w2", fsync=False)
    Runtime(_SPEC, {"a": _node}, sink=InMemoryTraceSink(), commitments=log).run(
        State.empty("x"), run_id="other")
    log.seal(AT)
    receipt_b = log.receipt_for("acme/w2/0")
    log.close()
    receipt_b["attestations"] = [copy.deepcopy(receipt_a["attestations"][0])]

    verdict = validate.verify(receipt_b)
    assert any(v.rule == "P9" for v in verdict.violations)
    assert verdict.status_of("EXISTENCE") == validate.NOT_ESTABLISHED


def test_a_digest_of_something_else_in_the_receipt_is_still_p9(run):
    """P9 is not 'is this digest present in the document' but 'is this a
    checkpoint digest or the run root'. The window root and the leaf digest
    both appear in the receipt — neither is what a token asserts about."""
    receipt, trace = run
    candidates = (
        receipt["segments"][0]["begin"]["checkpoint"]["window_root"],
        validate.commitment_leaf(receipt["segments"][0]["begin"]["commitment"]),
    )
    for subject in candidates:
        tampered = copy.deepcopy(receipt)
        tampered["attestations"] = [_attestation(subject)]
        violations = validate.validate_receipt(tampered)
        assert {v.rule for v in violations} == {"P9"}, violations


# --------------------------------------------------------------------------- #
# P10 — unknown type / unknown algorithm / missing proof: refusal            #
# --------------------------------------------------------------------------- #


def test_an_unknown_attestation_type_is_refused_every_level(run):
    """A violation AND a refusal, exit non-zero, the P5 pattern. Reporting
    only the violation would call a document wrong that may be perfectly
    correct under a type this verifier cannot read."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt), type="rot13-time")]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert any(v.rule == "P10" for v in verdict.violations)
    assert all(f.status == validate.REFUSED for f in verdict.findings)


def test_a_known_type_with_an_unadmitted_algorithm_is_refused(run):
    """rfc3161_timestamp admits sha256 only (ADR-031 decision 4)."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt), algorithm="sha1")]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert any(v.rule == "P10" for v in verdict.violations)
    reason = next(f.reason for f in verdict.findings if f.level == "INTEGRITY")
    assert "'sha1'" in reason


def test_a_known_type_with_no_verification_material_is_refused(run):
    """A token nobody can check is not an attestation. The schema if/then
    refuses the missing proof key as SCHEMA and the verifier ALSO refuses —
    a document with unreadable material must never sit on the same footing as
    a checkable one (review correction 3)."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(
        _root(receipt), proof={"tsa_url": VALID_TSA_URL})]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert all(f.status == validate.REFUSED for f in verdict.findings)
    assert any(v.rule == "SCHEMA" and "token_der" in v.message
               for v in verdict.violations)


def test_the_cli_exits_nonzero_on_an_attestation_refusal(run, tmp_path):
    """A caller reading only the exit code must never mistake 'I cannot tell'
    for 'verified'."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt), type="identity")]
    receipt_file = tmp_path / "receipt.json"
    trace_file = tmp_path / "trace.json"
    receipt_file.write_text(json.dumps(receipt))
    trace_file.write_text(json.dumps(trace))
    done = subprocess.run(
        [sys.executable, str(CONTRACT_DIR / "validate.py"), "receipt",
         str(receipt_file), "--trace", str(trace_file)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert done.returncode == 1, done.stdout
    assert "REFUSED" in done.stdout


def test_the_cli_exits_zero_on_a_claimed_attestation(run, tmp_path):
    """CLAIMED is not a refusal: a receipt whose only support for EXISTENCE is
    an attestation still validates, and the report says whose word that is."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(_root(receipt))]
    receipt_file = tmp_path / "receipt.json"
    trace_file = tmp_path / "trace.json"
    receipt_file.write_text(json.dumps(receipt))
    trace_file.write_text(json.dumps(trace))
    done = subprocess.run(
        [sys.executable, str(CONTRACT_DIR / "validate.py"), "receipt",
         str(receipt_file), "--trace", str(trace_file)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert done.returncode == 0, done.stdout
    assert "fake-tsa.example" in done.stdout
    assert "openssl ts -verify -in response.tsr" in done.stdout


# --------------------------------------------------------------------------- #
# the plumbing: Attestation dataclass, receipt_for, the evidence package      #
# --------------------------------------------------------------------------- #


def test_an_attestation_round_trips_strict_plain_json():
    proof = {"token_der": "AAAA", "tsa_url": VALID_TSA_URL}
    att = Attestation("tsa-1", "rfc3161_timestamp", "fake-tsa.example",
                      "sha256:" + "a" * 64, AT, "sha256", proof)
    assert att.to_dict() == {
        "attestation_id": "tsa-1", "type": "rfc3161_timestamp",
        "issuer": "fake-tsa.example", "subject": "sha256:" + "a" * 64,
        "issued_at": AT, "algorithm": "sha256", "proof": proof,
    }
    with pytest.raises(ValueError, match=r"Attestation\.proof.*J1"):
        Attestation("a", "t", "i", "sha256:" + "a" * 64, AT, "sha256",
                    {"bad": float("nan")})


def test_receipt_for_carries_attestations_into_the_receipt(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    root = "sha256:" + hashlib.sha256(b"r").hexdigest()
    log.begin("r1", at=AT, nonce="n0")
    log.end("r1", root=root, outcome="completed", at=AT, nonce="n1")
    checkpoint = log.seal(AT)
    checkpoint_digest = validate.checkpoint_digest(checkpoint.to_dict())
    attestation = Attestation("tsa-1", "rfc3161_timestamp", "fake-tsa.example",
                              checkpoint_digest, AT, "sha256",
                              {"token_der": "AAAA", "tsa_url": VALID_TSA_URL})
    receipt = log.receipt_for("acme/w1/0", attestations=(attestation,))
    log.close()
    assert receipt["attestations"] == [attestation.to_dict()]
    assert validate.validate_receipt(receipt) == []


def test_verify_package_walks_attestations_on_the_packed_receipt(tmp_path):
    """The evidence package carries attestations like anchors: pack puts them
    on the receipt, verify_package reports the same CLAIMED verdict a bare
    verify would."""
    from vitruvyan_motus.replay import TraceBundle

    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(_SPEC, {"a": _node}, sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="r1")
    checkpoint = log.seal(AT)
    checkpoint_digest = validate.checkpoint_digest(checkpoint.to_dict())
    attestation = Attestation("tsa-1", "rfc3161_timestamp", "fake-tsa.example",
                              checkpoint_digest, AT, "sha256",
                              {"token_der": "AAA", "tsa_url": VALID_TSA_URL})
    bundle = TraceBundle(_SPEC, result.trace)
    data = pack(bundle, log=log, attestations=(attestation,))
    log.close()

    verdict = verify_package(data).verdict
    assert verdict is not None
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    assert "fake-tsa.example" in next(
        f.reason for f in verdict.findings if f.level == "EXISTENCE")