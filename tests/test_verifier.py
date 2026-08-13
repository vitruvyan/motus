"""What a receipt and its trace let somebody establish — and what they do not.

ADR-021 decision 8. Most of these assert a REFUSAL or a NOT ESTABLISHED,
because that is what the verifier is for: ADR-020 records that *a `VERIFIED` on
a chain the verifier cannot evaluate is the worst lie this system can tell*,
and a report listing only its successes is read as a clean bill.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State
from vitruvyan_motus.commitlog import CommitmentLog
from vitruvyan_motus.commitments import CommitmentKind

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = ROOT / "contract"
NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
AT = "2026-08-13T10:00:00Z"


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

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "verify", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


def _node(state):
    return state.with_fact(Fact("k", 1, "test", NOW))


@pytest.fixture()
def run(tmp_path):
    """A real run, a real receipt over its real commitments, and its trace."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    result = Runtime(SPEC, {"a": _node}, sink=InMemoryTraceSink(),
                     commitments=log).run(State.empty("x"), run_id="r1")
    checkpoint = log.seal(AT)

    def entry(kind, sequence):
        proof = log.proof_for("r1", kind, checkpoint.index, sequence=sequence)
        return {"commitment": proof.commitment.to_dict(),
                "proof": [{"side": s, "digest": d} for s, d in proof.path],
                "checkpoint": checkpoint.to_dict()}

    receipt = {
        "schema_version": "1.0.0", "mode": "local",
        "segments": [{"begin": entry(CommitmentKind.BEGIN, 0),
                      "end": entry(CommitmentKind.END, 1)}],
    }
    trace = result.trace.to_dict()
    digest = validate.checkpoint_digest(checkpoint.to_dict())
    log.close()
    return receipt, trace, digest


def test_integrity_is_established_only_by_recomputing_the_trace(run):
    receipt, trace, _ = run
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("INTEGRITY") == validate.ESTABLISHED
    assert not verdict.refused


def test_a_receipt_for_a_different_run_does_not_establish_integrity(run):
    """The whole point of deriving the root rather than reading it back."""
    receipt, trace, _ = run
    other = copy.deepcopy(trace)
    other["run"]["run_id"] = "somebody-else"
    verdict = validate.verify(receipt, other)
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED
    assert "two different documents" in verdict.findings[0].reason \
        or "derives no root" in verdict.findings[0].reason


def test_without_a_trace_integrity_says_so_rather_than_passing(run):
    """A receipt alone is internally consistent arithmetic. Reporting that as
    INTEGRITY would let a holder present a well-formed receipt for a run that
    never happened."""
    receipt, _, _ = run
    verdict = validate.verify(receipt)
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED
    assert "no trace was supplied" in verdict.findings[0].reason


def test_an_unknown_anchor_network_refuses_every_level(run):
    """ADR-020: a VERIFIED on a chain the verifier cannot evaluate is the worst
    lie this system can tell. It does not degrade the answer — it declines."""
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "somechain", "network": "unheard-of:main",
        "checkpoint": digest, "state": "anchored",
        "reference": "0xdead", "published_at": AT,
    }]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert all(f.status == validate.REFUSED for f in verdict.findings)


def test_an_unknown_digest_algorithm_refuses_every_level(run):
    """Fail closed on the arithmetic itself, before any value is compared."""
    receipt, trace, _ = run
    receipt["segments"][0]["begin"]["checkpoint"]["window_root"] = \
        "blake3:" + "a" * 64
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert "cannot recompute" in verdict.findings[0].reason


def test_a_pending_anchor_is_not_yet_and_never_yes(run):
    """An intention to publish is not a publication."""
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "tron", "network": "tron:nile", "checkpoint": digest,
        "state": "pending", "reference": None, "published_at": None,
    }]
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("EXISTENCE") == validate.NOT_YET
    assert verdict.status_of("RETENTION") == validate.NOT_YET
    assert verdict.status_of("INTEGRITY") == validate.ESTABLISHED


def test_an_anchored_checkpoint_establishes_existence_and_retention(run):
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "tron", "network": "tron:nile", "checkpoint": digest,
        "state": "anchored", "reference": "6010ded8", "published_at": AT,
    }]
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("EXISTENCE") == validate.ESTABLISHED
    assert verdict.status_of("RETENTION") == validate.ESTABLISHED
    assert "6010ded8" in verdict.findings[1].reason


def test_an_anchor_for_a_checkpoint_not_in_this_receipt_establishes_nothing(run):
    """P4 catches it as a violation, and the levels must not quietly pass on
    the strength of an anchor that covers somebody else's window."""
    receipt, trace, _ = run
    receipt["anchors"] = [{
        "anchor_id": "tron", "network": "tron:nile",
        "checkpoint": "sha256:" + "b" * 64, "state": "anchored",
        "reference": "6010ded8", "published_at": AT,
    }]
    verdict = validate.verify(receipt, trace)
    assert verdict.violations
    assert verdict.status_of("EXISTENCE") == validate.NOT_ESTABLISHED


def test_a_witness_acknowledgement_does_not_establish_continuity_unchecked(run):
    """The honest half of the mode table. WITNESSED is the mode a receipt
    CLAIMS; this verifier holds no key for any witness, so it says the
    acknowledgement is present and bound, and that its signature was NOT
    checked. Binding is what makes a signature checkable by somebody who holds
    the key — it is not a substitute for checking it."""
    receipt, trace, _ = run
    leaf = validate.commitment_leaf(
        receipt["segments"][0]["begin"]["commitment"])
    receipt["mode"] = "witnessed"
    receipt["segments"][0]["begin"]["witness"] = {
        "witness_id": "witness.example", "commitment": leaf, "position": 0,
        "acknowledged_at": AT, "algorithm": "ed25519", "signature": "AAAA",
    }
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("EXECUTION_CONTINUITY") == validate.NOT_ESTABLISHED
    reason = next(f.reason for f in verdict.findings
                  if f.level == "EXECUTION_CONTINUITY")
    assert "signature was not checked" in reason
    assert "witness.example" in reason


def test_the_sold_levels_are_reported_as_absent_not_omitted(run):
    """A level omitted from a report reads as a level passed."""
    receipt, trace, _ = run
    verdict = validate.verify(receipt, trace)
    assert [f.level for f in verdict.findings] == list(validate.LEVELS)
    for level in ("PROVENANCE", "IDENTITY", "LEGAL_TIME"):
        assert verdict.status_of(level) == validate.NOT_ESTABLISHED


def test_a_begin_with_no_end_is_an_execution_that_left_no_completion(tmp_path):
    """ADR-020 decision 3, and the sentence is load-bearing: a process can die
    and a stream driver can be abandoned mid-iteration, so this is a question
    and never a finding of suppression."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    runtime = Runtime(SPEC, {"a": _node}, sink=InMemoryTraceSink(),
                      commitments=log)
    driver = runtime.stream(State.empty("x"), run_id="crashed")
    next(driver)
    del driver
    checkpoint = log.seal(AT)
    proof = log.proof_for("crashed", CommitmentKind.BEGIN, checkpoint.index)
    receipt = {
        "schema_version": "1.0.0", "mode": "local",
        "segments": [{"begin": {
            "commitment": proof.commitment.to_dict(),
            "proof": [{"side": s, "digest": d} for s, d in proof.path],
            "checkpoint": checkpoint.to_dict(),
        }}],
    }
    log.close()

    verdict = validate.verify(receipt)
    notes = " ".join(verdict.notes)
    assert "AN EXECUTION THAT LEFT NO COMPLETION" in notes
    assert "never a finding of suppression" in notes
    assert "suppressed" not in notes.lower().replace("suppression", "")


def test_a_multi_segment_run_says_its_chain_claim_is_uncheckable_here(tmp_path):
    """ADR-023 decision 4: the continuation claim is checkable only against the
    predecessor's whole BUNDLE, and its absence makes the claim UNVERIFIABLE
    rather than false. A verifier reporting the whole receipt VERIFIED in that
    state has told the strongest available lie."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    first = log.begin("seg-1", at=AT, nonce="n1")
    second = log.begin("seg-2", at=AT, nonce="n2", continues="seg-1",
                       continues_fingerprint="bundle:sha256:" + "c" * 64)
    checkpoint = log.seal(AT)

    def entry(commitment):
        proof = log.proof_for(commitment.run_id, CommitmentKind.BEGIN,
                              checkpoint.index, sequence=commitment.sequence)
        return {"commitment": commitment.to_dict(),
                "proof": [{"side": s, "digest": d} for s, d in proof.path],
                "checkpoint": checkpoint.to_dict()}

    receipt = {"schema_version": "1.0.0", "mode": "local",
               "segments": [{"begin": entry(first)}, {"begin": entry(second)}]}
    log.close()

    verdict = validate.verify(receipt)
    notes = " ".join(verdict.notes)
    assert "UNVERIFIABLE rather than false" in notes
    assert "bundle_version" in notes


def test_the_cli_exits_nonzero_on_a_refusal(run, tmp_path):
    """A caller reading only the exit code must never be able to mistake
    "I cannot tell" for "verified"."""
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "x", "network": "unheard-of:main", "checkpoint": digest,
        "state": "anchored", "reference": "0x1", "published_at": AT,
    }]
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


def test_the_cli_reports_every_level_and_exits_zero_on_a_good_receipt(run, tmp_path):
    receipt, trace, _ = run
    receipt_file = tmp_path / "receipt.json"
    trace_file = tmp_path / "trace.json"
    receipt_file.write_text(json.dumps(receipt))
    trace_file.write_text(json.dumps(trace))

    done = subprocess.run(
        [sys.executable, str(CONTRACT_DIR / "validate.py"), "receipt",
         str(receipt_file), "--trace", str(trace_file)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    for level in validate.LEVELS:
        assert level in done.stdout
    assert "INTEGRITY             ESTABLISHED" in done.stdout


def test_the_root_is_derived_and_not_read_back(run):
    """The surviving mutant from the round on this file, and it is the property
    ADR-019 exists for.

    A document whose header was rewritten while `records[0].prev_hash` was left
    stale has a perfectly recomputable terminal `payload_hash` — reading that
    field back returns a root and hands an anchor a value for a run whose
    run_id, policy and metadata are not the ones it commits to. Deriving means
    hashing the header ourselves and refusing when the first link disagrees."""
    receipt, trace, _ = run
    assert validate.derived_root(trace) is not None

    forged = copy.deepcopy(trace)
    forged["run"]["run_id"] = "somebody-elses-run"
    # Every declared digest is untouched, so a validator that TRUSTS the
    # document still finds a well-formed chain and a terminal hash.
    assert forged["records"][-1]["integrity"]["payload_hash"] == \
        trace["records"][-1]["integrity"]["payload_hash"]

    assert validate.derived_root(forged) is None, (
        "the root was read back from the terminal record instead of being "
        "recomputed from the header down"
    )
    verdict = validate.verify(receipt, forged)
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED


def test_a_trace_below_schema_3_derives_no_root(run):
    """ADR-019: below 3.0.0 the digests do not cover prev_hash, so the
    terminal's hash covers one record rather than the run. Relabelling a 2.0.0
    document 3.0.0 used to turn an unanchorable value into an anchorable-
    looking one."""
    _, trace, _ = run
    older = copy.deepcopy(trace)
    older["schema_version"] = "2.0.0"
    assert validate.derived_root(older) is None
