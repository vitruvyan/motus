"""The chain, and what its root is a commitment to (ADR-019).

Schema 2.0.0 shipped a chain whose digests did not cover ``prev_hash``. Every
record's hash was therefore independent of the link beside it, and the terminal
record's hash — the value README, rule T11 and the Terraveler migration brief
all call *the root* — covered the terminal record and nothing else. An editor
could rewrite any record, or the whole header, reseal by the published recipe,
pass the validator, and the anchored value did not move.

These tests pin the corrected recipe by the PROPERTY it exists for, not by its
implementation: after any rewrite, the root must differ. A test that only
recomputed the digest the way the code does would agree with the code about a
recipe that was wrong for two releases.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vitruvyan_motus import (
    Fact, GraphSpec, JsonlTraceSink, Runtime, State, TRACE_SCHEMA_VERSION,
)
from vitruvyan_motus.trace import Trace, _canonical_bytes

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parent.parent

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "verdetto", "version": "1.0.0",
    "entry": "check",
    "nodes": [{"name": "check", "effect_class": "pure", "writes_declared": ["verdict"]}],
    "transitions": {"check": {"kind": "terminal"}},
})


def _run(verdict: str = "APPROVED", run_id: str = "sub-4471"):
    def check(state: State) -> State:
        return state.with_fact(Fact("verdict", verdict, "curator", NOW))
    return Runtime(SPEC, {"check": check}).run(
        State.empty("verdict on submission 4471"), run_id=run_id
    )


def _reseal(document: dict, *, bind_prev: bool) -> dict:
    """Reseal every record. `bind_prev=False` is the 2.0.0 recipe.

    This is what an editor does, and it needs no privilege: the recipe is
    rule T11, published, and covers no secret.
    """
    prev = "sha256:" + hashlib.sha256(_canonical_bytes(
        {"schema_version": document["schema_version"], "run": document["run"]}
    )).hexdigest()
    for record in document["records"]:
        payload = dict(record)
        payload["integrity"] = {
            "payload_hash": None, "prev_hash": prev if bind_prev else None,
        }
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        record["integrity"] = {"payload_hash": digest, "prev_hash": prev}
        prev = digest
    return document


def _reseal_with_kernel(document: dict) -> dict:
    """Reseal using the KERNEL's own recipe, not this file's idea of it.

    An editor holds the same library the writer did, so this is the realistic
    attack — and it is the only version of it that tests the kernel. Resealing
    with a recipe written here would pass whatever the kernel does, which is how
    the first draft of these tests survived the fix being neutered.
    """
    trace = Trace(document["run"], schema_version=document["schema_version"])
    for record in document["records"]:
        bare = dict(record)
        bare["integrity"] = {"payload_hash": None, "prev_hash": None}
        trace = trace._append_runtime(trace._seal(bare))
    return trace.to_dict()


def _record(document: dict, kind: str) -> dict:
    return next(r for r in document["records"] if r["kind"] == kind)


def _root_of(document: dict) -> str:
    return document["records"][-1]["integrity"]["payload_hash"]


def test_the_emitted_schema_is_3_0_0():
    assert TRACE_SCHEMA_VERSION == "3.0.0"
    assert _run().trace.to_dict()["schema_version"] == "3.0.0"


def test_the_recipe_reproduces_what_the_runtime_sealed():
    """The published recipe and the kernel agree — otherwise nothing below is
    a test of the kernel, only of this file's idea of it."""
    document = _run().trace.to_dict()
    assert _reseal(json.loads(json.dumps(document)), bind_prev=True) == document


@pytest.mark.parametrize("rewrite,what", [
    (lambda d: _record(d, "transition")["writes"]["facts"][0].__setitem__(
        "value", "REJECTED"),
     "the verdict"),
    (lambda d: d["run"].__setitem__("run_id", "sub-0000"), "the run id"),
    (lambda d: d["run"].__setitem__("policy", "exploration"), "the policy"),
    (lambda d: d["run"]["graph"].__setitem__("code_fingerprint", "code:sha256:" + "0" * 64),
     "the code fingerprint"),
    (lambda d: d["run"].__setitem__("metadata", {"reviewer": "someone else"}),
     "the metadata"),
])
def test_a_rewrite_moves_the_root_even_when_the_chain_is_resealed(rewrite, what):
    """The defect ADR-019 closes, stated as the property that failed.

    Each of these passed the validator under 2.0.0 with the root unchanged.
    Anchoring bought nothing, because the anchored value agreed with a document
    that had been rewritten.
    """
    result = _run()
    original = result.trace.to_dict()
    edited = json.loads(json.dumps(original))
    rewrite(edited)
    assert edited != original, f"the rewrite of {what} changed nothing"

    edited = _reseal_with_kernel(edited)
    assert _root_of(edited) != _root_of(original), (
        f"rewriting {what} and resealing left the root where it was — the "
        "digests have stopped covering prev_hash, and an anchor over this root "
        "agrees with a rewritten trace"
    )


def test_the_digest_covers_prev_hash_directly():
    """The mechanism, isolated: change only the link, and the hash must fail."""
    document = _run().trace.to_dict()
    record = document["records"][1]
    payload = dict(record)
    payload["integrity"] = {
        "payload_hash": None, "prev_hash": record["integrity"]["prev_hash"],
    }
    as_sealed = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    assert as_sealed == record["integrity"]["payload_hash"]

    payload["integrity"] = {"payload_hash": None, "prev_hash": "sha256:" + "0" * 64}
    with_a_different_link = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    assert with_a_different_link != as_sealed, (
        "the digest is the same with a different predecessor: prev_hash is "
        "outside it, which is ADR-019's defect exactly"
    )


def test_root_is_none_below_3_0_0():
    """`root` exists to be handed to an anchor. Below 3.0.0 there is no value
    that means what the caller will assume, so there is no value."""
    document = _run().trace.to_dict()
    document["schema_version"] = "2.0.0"
    _reseal(document, bind_prev=False)
    downgraded = Trace.from_dict(document)
    assert downgraded.root is None
    assert _run().trace.root is not None


def test_a_2_0_0_trace_still_validates_and_says_what_its_root_is_worth(tmp_path):
    """Traces already written are truthful records of real runs. They keep
    validating, and the CLI states what their root does not do."""
    document = _run().trace.to_dict()
    document["schema_version"] = "2.0.0"
    _reseal(document, bind_prev=False)
    artifact = tmp_path / "old.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")

    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"),
         "trace", str(artifact)],
        capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stdout
    assert "not an anchorable commitment" in check.stderr
    assert "2.0.0" in check.stderr


def test_a_3_0_0_trace_is_not_told_it_is_unanchorable(tmp_path):
    artifact = tmp_path / "new.json"
    artifact.write_text(json.dumps(_run().trace.to_dict()), encoding="utf-8")
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"),
         "trace", str(artifact)],
        capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stdout
    assert "anchorable" not in check.stderr


def test_a_3_0_0_trace_sealed_by_the_old_recipe_is_refused(tmp_path):
    """The failure mode of a writer that upgrades its version string and not
    its recipe — which is every writer that reads the version and stops."""
    document = _run().trace.to_dict()
    _reseal(document, bind_prev=False)          # 3.0.0 in the header, 2.0.0 seal
    artifact = tmp_path / "mismatched.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"),
         "trace", str(artifact)],
        capture_output=True, text=True,
    )
    assert check.returncode == 1
    assert "T11" in check.stdout


def test_the_root_survives_the_document_and_jsonl_encodings(tmp_path):
    """The root is over the canonical object form, so the carrier cannot move
    it — an anchor published from a file must match one recomputed from a
    stream of the same run."""
    sink = JsonlTraceSink(tmp_path, fsync=False)
    def check(state: State) -> State:
        return state.with_fact(Fact("verdict", "APPROVED", "curator", NOW))
    result = Runtime(SPEC, {"check": check}, sink=sink).run(
        State.empty("verdict on submission 4471"), run_id="sub-4471")
    lines = [json.loads(l) for l in sink.artifacts[0].read_text().splitlines()]
    assert lines[-1]["integrity"]["payload_hash"] == result.trace.root
