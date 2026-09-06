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
from vitruvyan_motus.trace import NonIntegerNumber, Trace, _canonical_bytes

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


def test_the_emitted_schema_is_3_2_0():
    assert TRACE_SCHEMA_VERSION == "3.2.0"
    assert _run().trace.to_dict()["schema_version"] == "3.2.0"


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


# --------------------------------------------------------------------------
# The three attacks an adversarial round found on the FIX, within a day of it
# being written. Each one produced a root that agreed with a rewritten
# document, and each is closed by deriving the root instead of reading it.
# --------------------------------------------------------------------------

def test_a_rewritten_header_with_a_stale_first_link_yields_no_root():
    """The worst of the three: the same defect ADR-019 closed, one level up.

    A digest covers the STRING that names its predecessor, not the predecessor.
    So an editor rewrites the header, leaves records[0].prev_hash pointing at
    the old header digest, reseals, and every hash recomputes perfectly while
    the terminal digest does not move. Only recomputing the header ourselves
    catches it.
    """
    document = _run().trace.to_dict()
    original_root = _root_of(document)
    document["run"]["policy"] = "exploration"
    document["run"]["metadata"] = {"reviewer": "someone else"}

    prev = document["records"][0]["integrity"]["prev_hash"]      # left stale
    for record in document["records"]:
        payload = dict(record)
        payload["integrity"] = {"payload_hash": None, "prev_hash": prev}
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        record["integrity"] = {"payload_hash": digest, "prev_hash": prev}
        prev = digest

    assert _root_of(document) == original_root, (
        "the forgery no longer reproduces — rewrite this test around the "
        "attack that replaced it, do not delete it"
    )
    assert Trace.from_dict(document).root is None, (
        "a rewritten header with a stale first link was handed a root: the "
        "root is being read back instead of derived, and an anchor comparison "
        "would agree with this document"
    )


def test_a_2_0_0_document_relabelled_3_0_0_yields_no_root():
    document = _run().trace.to_dict()
    document["schema_version"] = "2.0.0"
    _reseal(document, bind_prev=False)
    unanchorable = _root_of(document)

    document["schema_version"] = "3.0.0"        # one edited string
    assert _root_of(document) == unanchorable
    assert Trace.from_dict(document).root is None


def test_an_invented_chain_yields_no_root():
    document = _run().trace.to_dict()
    for record in document["records"]:
        record["integrity"] = {"payload_hash": "sha256:" + "de" * 32,
                               "prev_hash": "sha256:" + "de" * 32}
    assert Trace.from_dict(document).root is None, (
        "from_dict accepts any document; root must not repeat its claims"
    )


@pytest.mark.parametrize("integrity", ["a-string", 42, [], None, True, 3.5])
def test_a_malformed_integrity_block_yields_no_root_and_no_crash(integrity):
    """`from_dict` accepts an unvalidated document, so `root` meets whatever is
    in the file. Reaching `.get` on a string raised AttributeError out of a
    property whose entire contract is to answer None when the document has not
    earned a root — an anchor ingesting a malformed file must be told "do not
    anchor this", never handed a crash to catch.

    The document is relabelled to 3.1.0 so the question being asked is the
    one this test asks — what a malformed INTEGRITY block does to `root`.
    A 3.2.0 document carrying the float `3.5` anywhere is refused at load by
    rule J4 (ADR-030), which is a different and earlier question.
    """
    document = _run().trace.to_dict()
    document["schema_version"] = "3.1.0"
    _reseal(document, bind_prev=True)
    document["records"][0]["integrity"] = integrity
    assert Trace.from_dict(document).root is None


@pytest.mark.parametrize("integrity", ["a-string", 42, [], None, True, 3.5])
def test_a_malformed_integrity_block_at_3_2_0_is_decided_not_hidden(integrity):
    """The same malformed block, at the CURRENT version, made an explicit
    decision rather than left to fall out of the test above by relabelling:
    every OTHER malformed shape still answers `root is None` exactly as it
    did before rule J4 (ADR-030) existed — J4 has nothing to say about a
    string, an int, a list, `None` or a bool. `3.5` is different: at 3.2.0 a
    float anywhere in the document is not what the document claims to be
    (every number a JSON integer), so it is refused before `root` is asked
    to make sense of it at all, rather than quietly answering `None` for a
    reason that has nothing to do with rule J4.
    """
    document = _run().trace.to_dict()
    assert document["schema_version"] == TRACE_SCHEMA_VERSION
    _reseal(document, bind_prev=True)
    document["records"][0]["integrity"] = integrity
    if integrity == 3.5:
        with pytest.raises(NonIntegerNumber):
            Trace.from_dict(document)
    else:
        assert Trace.from_dict(document).root is None


def test_the_version_guard_fails_closed():
    """An allow-list, not a deny-list: a version nobody enumerated must not
    be treated as chained just because it was not listed as unchained."""
    document = _run().trace.to_dict()
    for version in ("2.0.1", "3.0", "4.0.0", "3.0.0 "):
        trace = Trace(document["run"], schema_version=version)
        for record in document["records"]:
            trace = trace._append_runtime(record)
        assert trace.root is None, f"{version!r} was handed a root"


def test_a_2_0_0_trace_sealed_by_the_3_0_0_recipe_still_gets_no_root():
    """Where the allow-list earns its keep beyond the chain recomputation.

    Deriving the root already refuses a well-formed 2.0.0 trace, because its
    digests do not reproduce under the 3.0.0 recipe. But a writer that seals
    2.0.0 records the NEW way produces a chain that would recompute — and its
    root would still be worthless, because 2.0.0 is not the version whose
    readers were promised a commitment. The version decides, not the shape.
    """
    document = _run().trace.to_dict()
    document["schema_version"] = "2.0.0"
    _reseal(document, bind_prev=True)               # the 3.0.0 recipe, on 2.0.0
    assert Trace.from_dict(document).root is None


def test_the_note_fires_on_the_jsonl_artifact_too(tmp_path):
    """The artifact an integrator actually holds is the JSONL one — it is what
    the only durable sink writes — so the half that was tested was the half
    nobody's archive is in."""
    document = _run().trace.to_dict()
    document["schema_version"] = "2.0.0"
    _reseal(document, bind_prev=False)
    stream = tmp_path / "old.jsonl"
    stream.write_text("\n".join(
        [json.dumps({"schema_version": document["schema_version"], "run": document["run"]})]
        + [json.dumps(r) for r in document["records"]]) + "\n", encoding="utf-8")

    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"), "jsonl", str(stream)],
        capture_output=True, text=True,
    )
    assert check.returncode == 0, check.stdout
    assert "not an anchorable commitment" in check.stderr


def test_the_root_survives_the_document_and_jsonl_encodings(tmp_path):
    """The root is over the canonical object form, so the carrier cannot move
    it: a root DERIVED from the JSONL stream alone must equal the one derived
    from the document.

    The earlier version of this test compared two reads of the same sealed
    dict and could not have failed for the reason it named. This one rebuilds
    a Trace from the stream and derives the root from it.
    """
    sink = JsonlTraceSink(tmp_path, fsync=False)
    def check(state: State) -> State:
        return state.with_fact(Fact("verdict", "APPROVED", "curator", NOW))
    result = Runtime(SPEC, {"check": check}, sink=sink).run(
        State.empty("verdict on submission 4471"), run_id="sub-4471")
    lines = [json.loads(l) for l in sink.artifacts[0].read_text().splitlines()]
    from_stream = Trace.from_dict({
        "schema_version": lines[0]["schema_version"],
        "run": lines[0]["run"],
        "records": lines[1:],
    })
    assert from_stream.root is not None, "the stream's own chain does not verify"
    assert from_stream.root == result.trace.root


# --------------------------------------------------------------------------
# Two ways a document could be evidence of more than one thing. Neither is
# about the chain, and both defeat an anchor: a root proves nothing about a
# file whose contents are not a single well-defined document.
# --------------------------------------------------------------------------

def test_a_document_with_a_repeated_member_is_refused(tmp_path):
    """Two complete accounts of one run in one file, both well sealed.

    Python, jq, node and jsonb all keep the last member; a human, `git diff`
    and a first-wins reader see the first. The validator used to hash whichever
    one its parser kept and reproduce the root exactly.
    """
    document = _run().trace.to_dict()
    other = json.loads(json.dumps(document))
    other["run"]["run_id"] = "sub-9999"
    other["run"]["policy"] = "exploration"
    twice = (
        '{"schema_version":%s,"run":%s,"records":%s,"run":%s,"records":%s}' % (
            json.dumps(document["schema_version"]),
            json.dumps(other["run"]), json.dumps(other["records"]),
            json.dumps(document["run"]), json.dumps(document["records"]),
        )
    )
    artifact = tmp_path / "two-readings.json"
    artifact.write_text(twice, encoding="utf-8")
    check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "contract" / "validate.py"), "trace", str(artifact)],
        capture_output=True, text=True,
    )
    assert check.returncode == 1, check.stdout + check.stderr
    assert "appears more than once" in check.stdout


def test_a_lone_surrogate_fails_the_node_rather_than_the_run():
    """It is a Python str, so every other J1 check admits it, and it has no
    UTF-8 encoding, so it exploded inside the seal — out of run(), past
    NodeFailed, leaving the sink holding a run with no terminal record.

    `json.loads('"\\\\ud800"')` produces one silently, so any node parsing an
    external payload can reach this without an attacker.
    """
    from vitruvyan_motus import NodeFailed

    def bad(state: State) -> State:
        return state.with_fact(Fact("verdict", "OK\ud800", "curator", NOW))

    try:
        Runtime(SPEC, {"check": bad}).run(State.empty("i"), run_id="sub-4471")
    except NodeFailed as failure:
        assert failure.trace is not None
        assert failure.trace.records[-1]["kind"] == "run_failed", (
            "the run must reach a terminal record, as it does for NaN and tuples"
        )
    else:
        raise AssertionError("a lone surrogate was accepted into the trace")
