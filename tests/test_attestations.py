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

import base64
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


#: A stub long enough to pass the material bound (>= 64 decoded bytes) without
#: being a real TimeStampResp -- these tests are about the ENVELOPE (subject
#: binding, type, algorithm), not about a real token's bytes.
_STUB_TOKEN_DER = base64.b64encode(b"\x00" * 96).decode("ascii")


def _attestation(subject: str, **changes) -> dict:
    """A minimally material attestation over `subject`: `message_imprint` is
    computed for real (item 1's double-hash binding), so a test that does not
    care about the proof still passes it."""
    imprint = hashlib.sha256(bytes.fromhex(subject.split(":", 1)[1])).hexdigest()
    body = {
        "attestation_id": "tsa-1", "type": "rfc3161_timestamp",
        "issuer": "fake-tsa.example", "subject": subject, "issued_at": AT,
        "algorithm": "sha256",
        "proof": {"token_der": _STUB_TOKEN_DER, "tsa_url": VALID_TSA_URL,
                  "message_imprint": imprint},
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


def test_the_known_set_and_the_rules_table_cannot_drift(run):
    """`KNOWN_ATTESTATION_TYPES` says what is evaluable; `_ATTESTATION_TYPE_
    RULES` says how. A row added to one and not the other used to crash with
    a bare `TypeError` the first time a receipt exercised it (`rules["algo
    rithms"]` on `None`) instead of failing here, at review time."""
    assert validate.KNOWN_ATTESTATION_TYPES == set(validate._ATTESTATION_TYPE_RULES)


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


def test_a_repeated_attestation_id_is_a_p10_violation(run):
    """Nothing in the schema makes `attestation_id` unique -- it is an
    Identifier like any other -- and the plug used to default it from the
    issuer alone, so two tokens from one TSA on one receipt collided by
    construction. Two records under one name is one record a reader can no
    longer tell from the other."""
    receipt, trace = run
    root = _root(receipt)
    receipt["attestations"] = [
        _attestation(root, attestation_id="tsa-1"),
        _attestation(root, attestation_id="tsa-1", issuer="a-different-tsa"),
    ]
    violations = validate.validate_receipt(receipt)
    assert any(v.rule == "P10" and "used twice" in v.message
               for v in violations), violations


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


def test_an_unknown_type_is_still_refused_when_segments_is_empty(run):
    """The refusal loop used to carry an extra guard the anchor path never
    needed (`or not receipt.get("segments")`), so a document with a
    structurally invalid `segments: []` AND an unevaluable attestation type
    silently skipped the P10 refusal it deserved — a refusal outranks a
    violation for attestations exactly as it does for anchors, whatever else
    is wrong with the document."""
    receipt, _ = run
    subject = _root(receipt)
    receipt["segments"] = []
    receipt["attestations"] = [_attestation(subject, type="identity")]
    verdict = validate.verify(receipt)
    assert verdict.refused
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


def test_a_missing_proof_key_is_schema_not_a_p10_refusal(run):
    """2026-09-07 review correction: a MISSING proof key is the schema's
    business (it is `required`), not P10's. P10's proof limb is about the
    MATERIAL a present key carries (items 1-2) -- conflating the two made a
    structurally invalid document (fixture 315) read as REFUSED rather than
    the plain NOT_ESTABLISHED every other schema failure gets."""
    receipt, trace = run
    receipt["attestations"] = [_attestation(
        _root(receipt), proof={"tsa_url": VALID_TSA_URL})]
    verdict = validate.verify(receipt, trace)
    assert not verdict.refused
    assert all(f.status == validate.NOT_ESTABLISHED for f in verdict.findings)
    assert any(v.rule == "SCHEMA" and "token_der" in v.message
               for v in verdict.violations)
    assert not any(v.rule == "P10" for v in verdict.violations)


@pytest.mark.parametrize("token_der", ["", "AAA"])
def test_an_unmaterial_token_der_is_a_p10_refusal(run, token_der):
    """Item 2: `token_der` present but not material -- empty (decodes to
    nothing) or not even valid base64 ("AAA" is 3 characters, not a multiple
    of 4) -- is P10, not SCHEMA: the schema's pattern is an unanchored
    character-class check, not a base64 decoder, so it lets both through."""
    receipt, trace = run
    root = _root(receipt)
    imprint = hashlib.sha256(bytes.fromhex(root.split(":", 1)[1])).hexdigest()
    receipt["attestations"] = [_attestation(root, proof={
        "token_der": token_der, "tsa_url": VALID_TSA_URL,
        "message_imprint": imprint})]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert any(v.rule == "P10" for v in verdict.violations)
    assert not any(v.rule == "SCHEMA" for v in verdict.violations)


def test_a_token_der_shorter_than_a_real_timestampresp_is_a_p10_refusal(run):
    """Item 2: a TimeStampResp with a certificate chain is never a handful of
    bytes; the lower bound (64) exists so a stub cannot be mistaken for a
    token nobody has bothered to check."""
    receipt, trace = run
    root = _root(receipt)
    imprint = hashlib.sha256(bytes.fromhex(root.split(":", 1)[1])).hexdigest()
    short = base64.b64encode(b"\x00" * 10).decode("ascii")
    receipt["attestations"] = [_attestation(root, proof={
        "token_der": short, "tsa_url": VALID_TSA_URL,
        "message_imprint": imprint})]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert any(v.rule == "P10" for v in verdict.violations)


def test_a_message_imprint_not_bound_to_the_subject_is_a_p10_refusal(run):
    """Item 1: `message_imprint` must equal sha256(subject's digest bytes),
    the double hash RFC 3161 itself requires. A token real in every other
    respect but imprinted over a DIFFERENT digest is not shown to be about
    this subject, and must not read as material just because it decodes."""
    receipt, trace = run
    root = _root(receipt)
    receipt["attestations"] = [_attestation(root, proof={
        "token_der": _STUB_TOKEN_DER, "tsa_url": VALID_TSA_URL,
        "message_imprint": "0" * 64})]
    verdict = validate.verify(receipt, trace)
    assert verdict.refused
    assert any(v.rule == "P10" for v in verdict.violations)
    reason = next(f.reason for f in verdict.findings if f.level == "INTEGRITY")
    assert "message_imprint" in reason


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
    assert "openssl ts -reply -in response.tsr -token_out -out token.p7" \
        in done.stdout
    assert "openssl pkcs7 -inform DER -in token.p7 -print_certs -out " \
        "certs.pem" in done.stdout
    assert "openssl ts -verify -in response.tsr -digest" in done.stdout
    assert "-CAfile certs.pem" in done.stdout


def test_a_forged_issuer_cannot_inject_a_line_into_the_verdict(run, tmp_path):
    """The plug takes `issuer` verbatim off the wire from the TSA (ADR-031
    decision 5's GeneralName), so a hostile or merely broken TSA controls this
    string. `Identifier`'s schema pattern (`\\S`) is an unanchored SEARCH, so a
    newline passes it -- the CLI must never let one newline in receipt-
    supplied text become a second line of the printed verdict."""
    receipt, trace = run
    forged = "good.tsa\nEXISTENCE           VERIFIED\n    confirmed"
    receipt["attestations"] = [_attestation(_root(receipt), issuer=forged)]
    receipt_file = tmp_path / "receipt.json"
    trace_file = tmp_path / "trace.json"
    receipt_file.write_text(json.dumps(receipt))
    trace_file.write_text(json.dumps(trace))
    done = subprocess.run(
        [sys.executable, str(CONTRACT_DIR / "validate.py"), "receipt",
         str(receipt_file), "--trace", str(trace_file)],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60)
    assert repr(forged) in done.stdout
    assert not any(line.strip() == "EXISTENCE           VERIFIED"
                   for line in done.stdout.splitlines())


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
    # A real material proof, not "AAAA" (3 decoded bytes: undersized by item
    # 2's bound before this test ever reaches what it means to check --
    # round-tripping the envelope, not the material).
    imprint = hashlib.sha256(
        bytes.fromhex(checkpoint_digest.split(":", 1)[1])).hexdigest()
    proof = {"token_der": _STUB_TOKEN_DER, "tsa_url": VALID_TSA_URL,
             "message_imprint": imprint}
    attestation = Attestation("tsa-1", "rfc3161_timestamp", "fake-tsa.example",
                              checkpoint_digest, AT, "sha256", proof)
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
    # "AAA" is not even decodable base64 (length 3, not a multiple of 4) --
    # a stub the packaging test never meant to exercise item 2's material
    # bound with, and would now be refused for exactly that reason.
    imprint = hashlib.sha256(
        bytes.fromhex(checkpoint_digest.split(":", 1)[1])).hexdigest()
    proof = {"token_der": _STUB_TOKEN_DER, "tsa_url": VALID_TSA_URL,
             "message_imprint": imprint}
    attestation = Attestation("tsa-1", "rfc3161_timestamp", "fake-tsa.example",
                              checkpoint_digest, AT, "sha256", proof)
    bundle = TraceBundle(_SPEC, result.trace)
    data = pack(bundle, log=log, attestations=(attestation,))
    log.close()

    verdict = verify_package(data).verdict
    assert verdict is not None
    assert verdict.status_of("EXISTENCE") == validate.UNCHECKED
    assert "fake-tsa.example" in next(
        f.reason for f in verdict.findings if f.level == "EXISTENCE")