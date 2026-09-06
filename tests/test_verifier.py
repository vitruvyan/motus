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
from vitruvyan_motus.commitlog import CommitmentLog, CommitmentLogFork
from vitruvyan_motus.commitments import AnchorReceipt, CommitmentKind, WitnessAck

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

    receipt = log.receipt_for("acme/w1/0")
    trace = result.trace.to_dict()
    digest = validate.checkpoint_digest(checkpoint.to_dict())
    log.close()
    return receipt, trace, digest


@pytest.mark.parametrize("leaf_count", [1, 5])
def test_inclusion_proof_output_is_a_receipt_entry_schema(leaf_count, tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="schema", fsync=False)
    for index in range(leaf_count):
        log.begin(f"r{index}", at=AT, nonce=f"n{index}")
    checkpoint = log.seal(AT)
    proof = log.proof_for("r0", CommitmentKind.BEGIN, checkpoint.index)

    entry = proof.to_dict()
    receipt = {"schema_version": "1.0.0", "mode": "local",
               "segments": [{"begin": entry}]}
    assert validate.validate_receipt(receipt) == []
    assert set(entry) == {"commitment", "proof", "checkpoint"}
    assert bool(entry["proof"]) is (leaf_count > 1)
    log.close()


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
    assert "two different executions" in verdict.findings[0].reason, (
        "the receipt and the trace disagree about WHOSE run this is, and the "
        "report must say that rather than reporting a digest mismatch")


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


def test_an_anchor_is_a_claim_until_somebody_looks_it_up(run):
    """The finding that mattered most on this PR.

    The first version read `state: "anchored"` out of the receipt and reported
    EXISTENCE established — so a holder could mint the property by typing it.
    An allow-listed network says we COULD evaluate that chain, never that we
    did, and this verifier contacts no network at all.

    What it offers instead of a verdict is the address of the thing it declined
    to check, which is more useful than a verdict we have not earned."""
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "tron", "network": "tron:nile", "checkpoint": digest,
        "state": "anchored", "reference": "6010ded8", "published_at": AT,
    }]
    verdict = validate.verify(receipt, trace)
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    assert verdict.status_of("RETENTION") == validate.UNCHECKED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "CLAIMS publication" in reason
    assert "contacts no network" in reason
    assert "nile.tronscan.org/#/transaction/6010ded8" in reason, (
        "the reader was not handed the lookup this verifier declined to make")
    assert verdict.status_of("INTEGRITY") == validate.ESTABLISHED


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
        "segments": [{"begin": proof.to_dict()}],
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
        return proof.to_dict()

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


def test_a_record_that_lies_about_its_own_predecessor_derives_no_root(run):
    """A stale `prev_hash` still passes the digest check, because the digest
    covers the value we RECOMPUTED and not the one the file carries.

    So the two are separate questions: does this record hash to what it says
    (payload_hash), and does it name the record before it (prev_hash). A
    document that recomputes perfectly while pointing at the wrong predecessor
    describes a chain it does not have, and would hand an anchor a root for it.
    """
    _, trace, _ = run
    assert len(trace["records"]) > 1
    lying = copy.deepcopy(trace)
    lying["records"][1]["integrity"]["prev_hash"] = "sha256:" + "f" * 64
    assert validate.derived_root(lying) is None


def test_a_receipt_about_another_run_is_caught(tmp_path):
    """Comparing roots alone is not enough. A holder can pair a receipt about
    run A with a valid trace for run B — every commitment real, every proof
    real — and the roots get compared without either document ever having
    claimed to be about the other. The root says WHAT was executed; the run_id
    says WHOSE."""
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    traces = {}
    for name in ("run-a", "run-b"):
        result = Runtime(SPEC, {"a": _node}, sink=InMemoryTraceSink(),
                         commitments=log).run(State.empty("x"), run_id=name)
        traces[name] = result.trace.to_dict()
    checkpoint = log.seal(AT)

    def entry(run_id, kind):
        proof = log.proof_for(run_id, kind, checkpoint.index)
        return proof.to_dict()

    about_a = {"schema_version": "1.0.0", "mode": "local", "segments": [{
        "begin": entry("run-a", CommitmentKind.BEGIN),
        "end": entry("run-a", CommitmentKind.END)}]}
    log.close()

    assert validate.validate_receipt(about_a) == [], "the receipt itself is sound"
    assert validate.verify(about_a, traces["run-a"]).status_of("INTEGRITY") == \
        validate.ESTABLISHED

    verdict = validate.verify(about_a, traces["run-b"])
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED
    assert "two different executions" in verdict.findings[0].reason


def test_an_unknown_witness_algorithm_is_refused(run):
    """ADR-021 decision 8 refuses an unknown attestation type. Reporting one as
    "present but unchecked" would put an object we cannot read on the same
    footing as one we can."""
    receipt, trace, _ = run
    leaf = validate.commitment_leaf(receipt["segments"][0]["begin"]["commitment"])
    receipt["mode"] = "witnessed"
    receipt["segments"][0]["begin"]["witness"] = {
        "witness_id": "witness.example", "commitment": leaf, "position": 0,
        "acknowledged_at": AT, "algorithm": "rot13", "signature": "AAAA",
    }
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert "'rot13'" in verdict.findings[0].reason


def test_a_document_that_is_not_a_receipt_is_reported_not_raised():
    """`verify([])` raised AttributeError where the plain validator printed the
    violation and exited 1 — introduced by putting the refusal checks, which
    read fields, ahead of the schema return."""
    for junk in ([], "receipt", 7, None, {}, {"segments": "no"}):
        verdict = validate.verify(junk)
        assert verdict.findings
        assert all(f.status in (validate.NOT_ESTABLISHED, validate.REFUSED)
                   for f in verdict.findings)


def test_the_networks_we_ship_an_anchor_for_are_networks_this_verifier_knows():
    """A production-default anchor whose receipts the validator refuses is a
    product that cannot verify its own output. `plugs/` is where the anchors
    live and this is the list they must appear in."""
    assert "opentimestamps:bitcoin" in validate.KNOWN_ANCHOR_NETWORKS
    assert "tron:nile" in validate.KNOWN_ANCHOR_NETWORKS
    for network in validate.KNOWN_ANCHOR_NETWORKS:
        assert network in validate.ANCHOR_LOOKUPS, (
            f"{network} is accepted and a reader is given no way to settle it")


def test_an_opentimestamps_anchor_is_claimed_and_names_no_transaction(run):
    """An `.ots` attestation says the commitment sits under a block's merkle
    root. Handing the reader an explorer URL would send them looking for a
    transaction that does not exist, so the lookup for this network is an
    instruction rather than a link."""
    receipt, trace, digest = run
    receipt["anchors"] = [{
        "anchor_id": "opentimestamps", "network": "opentimestamps:bitcoin",
        "checkpoint": digest, "state": "anchored",
        "reference": "bitcoin-block:812345", "published_at": AT,
    }]
    verdict = validate.verify(receipt, trace)
    assert not verdict.refused
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    reason = next(f.reason for f in verdict.findings if f.level == "EXISTENCE")
    assert "ots verify" in reason
    assert "tronscan" not in reason


def test_receipt_for_continuation_ref_walks_back_to_original(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("old", at=AT, nonce="n0")
    log.seal(AT)
    log.begin("new", at=AT, nonce="n1", continues="old",
              continues_fingerprint="bundle:sha256:" + "c" * 64)
    log.end("new", root="sha256:" + "d" * 64, outcome="completed",
            at=AT, nonce="n2")
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/1")
    assert receipt["execution"]["ref"] == "acme/w1/0"
    assert receipt["execution"]["run_id"] == "old"
    assert [s["begin"]["commitment"]["run_id"] for s in receipt["segments"]] == ["old", "new"]
    assert validate.validate_receipt(receipt) == []
    log.close()


def test_receipt_for_walks_a_three_segment_chain(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("s0", at=AT, nonce="n0")
    log.seal(AT)
    log.begin("s1", at=AT, nonce="n1", continues="s0",
              continues_fingerprint="bundle:sha256:" + "a" * 64)
    log.seal(AT)
    log.begin("s2", at=AT, nonce="n2", continues="s1",
              continues_fingerprint="bundle:sha256:" + "b" * 64)
    log.end("s2", root="sha256:" + "d" * 64, outcome="completed",
            at=AT, nonce="n3")
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/2")
    assert len(receipt["segments"]) == 3
    assert receipt["execution"]["ref"] == "acme/w1/0"
    assert receipt["execution"]["run_id"] == "s0"
    assert validate.validate_receipt(receipt) == []
    log.close()


def test_unfinished_receipt_null_fingerprint_is_not_p8_when_trace_has_root(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("unfinished", at=AT, nonce="n0")
    log.seal(AT)
    result = Runtime(SPEC, {"a": _node}, sink=InMemoryTraceSink()).run(
        State.empty("x"), run_id="unfinished")
    receipt = log.receipt_for("acme/w1/0")
    verdict = validate.verify(receipt, result.trace.to_dict())
    assert not any(v.rule == "P8" for v in verdict.violations)
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED
    log.close()


def test_continuation_without_local_predecessor_is_reported(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("new", at=AT, nonce="n0", continues="old",
              continues_fingerprint="bundle:sha256:" + "c" * 64)
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/0")
    verdict = validate.verify(receipt)
    assert "predecessor" in " ".join(verdict.notes)
    assert "omitted" in " ".join(verdict.notes)
    log.close()


@pytest.mark.parametrize("ref", ["bad", "acme/w1", "acme/w1/01", "acme//0"])
def test_receipt_for_rejects_malformed_refs(tmp_path, ref):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    with pytest.raises(ValueError, match="invalid execution reference"):
        log.receipt_for(ref)
    log.close()


def test_receipt_for_rejects_unknown_and_end_refs(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n0")
    log.end("r", root="sha256:" + "d" * 64, outcome="completed",
            at=AT, nonce="n1")
    log.seal(AT)
    with pytest.raises(ValueError, match="was not found"):
        log.receipt_for("acme/w1/99")
    with pytest.raises(ValueError, match="names an END"):
        log.receipt_for("acme/w1/1")
    log.close()


def test_receipt_for_distinguishes_an_unsealed_begin(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n0")
    with pytest.raises(ValueError, match="not yet sealed"):
        log.receipt_for("acme/w1/0")
    log.close()


def test_receipt_for_includes_supplied_anchors(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n0")
    checkpoint = log.seal(AT)
    anchor = AnchorReceipt("a", "tron:nile", validate.checkpoint_digest(checkpoint.to_dict()), "pending")
    receipt = log.receipt_for("acme/w1/0", anchors=(anchor,))
    assert receipt["anchors"] == [anchor.to_dict()]
    assert validate.validate_receipt(receipt) == []
    log.close()


def test_execution_fingerprint_tamper_is_p8(run):
    receipt, trace, _ = run
    receipt["execution"]["fingerprint"] = "sha256:" + "0" * 64
    verdict = validate.verify(receipt, trace)
    assert any(v.rule == "P8" for v in verdict.violations)
    assert verdict.status_of("INTEGRITY") == validate.NOT_ESTABLISHED


def test_execution_ref_with_unbounded_decimal_is_p7_not_validator_crash(run):
    receipt, _, _ = run
    receipt["execution"]["ref"] = "acme/w1/" + "9" * 5000
    violations = validate.validate_receipt(receipt)
    assert any(v.rule == "P7" for v in violations)


def test_commitment_construction_rejects_run_id_that_receipt_schema_cannot_carry(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    with pytest.raises(ValueError, match="run_id.*Identifier"):
        log.begin("x" * 201, at=AT, nonce="n0")
    log.close()


def test_receipt_for_rejects_unencodable_log_identity_and_huge_sequence(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme/prod", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n0")
    log.seal(AT)
    with pytest.raises(ValueError, match="cannot produce receipt.v1"):
        log.receipt_for("acme/w1/0")
    log.close()

    other = CommitmentLog(tmp_path / "other", tenant="acme", writer_id="w1", fsync=False)
    with pytest.raises(ValueError, match="invalid execution reference"):
        other.receipt_for("acme/w1/" + "9" * 5000)
    other.close()


def test_receipt_for_refuses_continuation_forks(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("s0", at=AT, nonce="n0")
    log.seal(AT)
    for run_id, nonce in (("a", "n1"), ("b", "n2")):
        log.begin(run_id, at=AT, nonce=nonce, continues="s0",
                  continues_fingerprint="bundle:sha256:" + "a" * 64,
                  continues_sequence=0)
    log.seal(AT)
    with pytest.raises(CommitmentLogFork, match="multiple continuation"):
        log.receipt_for("acme/w1/0")
    log.close()


def test_receipt_for_refuses_ambiguous_double_end(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n0")
    log.end("r", root="sha256:" + "1" * 64, outcome="one", at=AT, nonce="n1")
    log.end("r", root="sha256:" + "2" * 64, outcome="two", at=AT, nonce="n2")
    log.seal(AT)
    with pytest.raises(CommitmentLogFork, match="multiple END"):
        log.receipt_for("acme/w1/0")
    log.close()


def test_resumed_receipt_uses_weakest_included_mode(tmp_path):
    class Notary:
        def acknowledge(self, payload):
            import hashlib
            return WitnessAck("notary", "sha256:" + hashlib.sha256(
                b"\x00" + payload).hexdigest(), 0, AT, "sig")

    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False,
                        witness_deadline=1)
    log.begin("s0", at=AT, nonce="n0", ask=Notary())
    log.seal(AT)
    log.begin("s1", at=AT, nonce="n1", continues="s0",
              continues_fingerprint="bundle:sha256:" + "a" * 64)
    log.end("s1", root="sha256:" + "d" * 64, outcome="completed",
            at=AT, nonce="n2")
    log.seal(AT)
    receipt = log.receipt_for("acme/w1/1")
    assert receipt["mode"] == "local"
    assert validate.validate_receipt(receipt) == []
    log.close()


@pytest.mark.parametrize("field", ["tenant", "writer_id", "nonce"])
def test_commitment_construction_rejects_oversized_identifiers(tmp_path, field):
    tenant = "acme" if field != "tenant" else "t" * 201
    writer = "w1" if field != "writer_id" else "w" * 201
    log = CommitmentLog(tmp_path, tenant=tenant, writer_id=writer, fsync=False)
    with pytest.raises(ValueError, match=rf"{field}.*Identifier"):
        log.begin("r", at=AT, nonce="n" * 201 if field == "nonce" else "n")
    log.close()


def test_end_outcome_identifier_is_rejected_at_construction(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    log.begin("r", at=AT, nonce="n")
    with pytest.raises(ValueError, match="outcome.*Identifier"):
        log.end("r", root="sha256:" + "d" * 64, outcome="o" * 201,
                at=AT, nonce="n1")
    log.close()
