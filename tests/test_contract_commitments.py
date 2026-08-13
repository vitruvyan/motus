"""The contract's commitment side, checked against the implementation.

`contract/validate.py` re-implements the leaf, node and checkpoint digests
rather than importing them. That is deliberate — the contract is the authority
(ADR-001), and a validator that called the implementation would check the
implementation against itself and agree with any drift. The cost of two
implementations is that they can silently diverge, and this file is what makes
that loud.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from vitruvyan_motus.commitlog import CommitmentLog
from vitruvyan_motus.commitments import CommitmentKind, merkle_root

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "contract"
AT = "2026-08-13T09:00:00Z"
BUNDLE = "bundle:sha256:" + "c" * 64


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


@pytest.fixture()
def store(tmp_path):
    log = CommitmentLog(tmp_path, tenant="acme", writer_id="w1", fsync=False)
    yield log
    log.close()


def test_the_two_implementations_of_the_leaf_digest_agree(store):
    """The whole reason the validator may be trusted about somebody else's
    receipt: it computed the digest itself and got the same answer."""
    store.begin("seg-1", at=AT, nonce="n1")
    written = [
        store.begin("job-1", at=AT, nonce="n2",
                    continues="seg-1", continues_fingerprint=BUNDLE),
        store.end("job-1", root="sha256:" + "a" * 64, outcome="completed",
                  at=AT, nonce="n3"),
    ]
    for commitment in written:
        assert validate.commitment_leaf(commitment.to_dict()) == commitment.leaf


def test_the_two_implementations_of_the_checkpoint_digest_agree(store):
    store.begin("job-1", at=AT, nonce="n1")
    first = store.seal(AT)
    store.begin("job-2", at=AT, nonce="n2")
    second = store.seal(AT)

    assert validate.checkpoint_digest(first.to_dict()) == first.digest
    assert validate.checkpoint_digest(second.to_dict()) == second.digest
    assert second.previous == first.digest


def test_the_validator_walks_a_real_path_to_a_real_root(store):
    for index in range(5):
        store.begin(f"job-{index}", at=AT, nonce=f"n{index}")
    checkpoint = store.seal(AT)

    for index in range(5):
        proof = store.proof_for(f"job-{index}", CommitmentKind.BEGIN,
                                checkpoint.index)
        path = [{"side": side, "digest": digest} for side, digest in proof.path]
        reached = validate._fold_path(
            validate.commitment_leaf(proof.commitment.to_dict()), path)
        assert reached == checkpoint.window_root


def test_a_path_element_with_an_unknown_side_is_refused_not_skipped():
    """Unreachable through the schema, which pins `side` to left|right — and
    kept because `_fold_path` is reachable from an API caller who validated
    nothing.

    Skipping is the dangerous handling: a shorter walk still lands on a real
    root, so junk appended to a valid path would verify. This is the same
    defect the round on `verify_merkle_path` found, in the second
    implementation."""
    leaf = "sha256:" + "a" * 64
    sibling = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="cannot be walked"):
        validate._fold_path(leaf, [{"side": "sideways", "digest": sibling}])
    with pytest.raises(ValueError, match="cannot be walked"):
        validate._fold_path(leaf, [{"digest": sibling}])


def test_the_two_implementations_of_the_window_root_agree(store):
    leaves = []
    for index in range(7):                      # odd, so the promotion matters
        leaves.append(
            store.begin(f"job-{index}", at=AT, nonce=f"n{index}").leaf)
    checkpoint = store.seal(AT)

    walked = validate._fold_path(
        leaves[0],
        [{"side": side, "digest": digest}
         for side, digest in store.proof_for("job-0", CommitmentKind.BEGIN,
                                             checkpoint.index).path],
    )
    assert walked == merkle_root(tuple(leaves)) == checkpoint.window_root


def test_a_receipt_built_from_a_real_store_validates_clean(store):
    store.begin("seg-1", at=AT, nonce="n1")
    begin = store.begin("job-1", at=AT, nonce="n2",
                        continues="seg-1", continues_fingerprint=BUNDLE)
    store.end("job-1", root="sha256:" + "a" * 64, outcome="completed",
              at=AT, nonce="n3")
    checkpoint = store.seal(AT)
    proof = store.proof_for("job-1", CommitmentKind.BEGIN, checkpoint.index)

    receipt = {
        "schema_version": "1.0.0",
        "mode": "local",
        "commitment": begin.to_dict(),
        "proof": [{"side": s, "digest": d} for s, d in proof.path],
        "checkpoint": checkpoint.to_dict(),
    }
    assert validate.validate_receipt(receipt) == []
    assert validate.validate_checkpoint(checkpoint.to_dict()) == []
    assert validate.validate_commitment(
        {"commitment": begin.to_dict(), "witness": None}) == []


def test_the_validator_never_imports_the_implementation():
    """Authority order (ADR-001): contract, then implementation. A validator
    that imported `vitruvyan_motus` would check the implementation against
    itself and agree with any drift — and the agreement tests above would keep
    passing while meaning nothing.

    Asserted against the SOURCE rather than against sys.modules, because an
    import inside a function would not show up in a module that was never
    called."""
    source = (CONTRACT_DIR / "validate.py").read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in source.splitlines()
        if "vitruvyan_motus" in line and (
            line.lstrip().startswith("import ")
            or line.lstrip().startswith("from ")
        )
    ]
    assert offenders == [], (
        "contract/validate.py imports the implementation it is the authority "
        f"over: {offenders}"
    )
