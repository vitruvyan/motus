"""The debug capability's three constraints, checked rather than intended.

ADR-022's decisions 4a–4e are the reason `motus_diagnose` is a capability and
not a seventh describing tool. Each of them is a promise about what leaves the
caller's machine or what the answer is allowed to suggest, and a promise of
that kind held only by the author's care is held until the author changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from vitruvyan_motus import Fact, GraphSpec, InMemoryTraceSink, Runtime, State
from vitruvyan_motus.contract import validate
from vitruvyan_motus.mcp.answers import Cannot, Computed, Quoted
from vitruvyan_motus.mcp.diagnose import diagnose

SECRET = "4111-1111-1111-1111-and-nothing-else-looks-like-this"
NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "diagnosed", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


@pytest.fixture()
def trace_with_a_secret(tmp_path):
    """A real trace carrying a value no diagnosis may repeat."""
    def node(state):
        return state.with_fact(Fact("card", SECRET, "test", NOW))

    result = Runtime(SPEC, {"a": node}, sink=InMemoryTraceSink()).run(
        State.empty("x"), run_id="r1")
    document = result.trace.to_dict()
    assert SECRET in json.dumps(document), "the fixture no longer carries it"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


# -- 4e: the artefact is named by a path ------------------------------------

def test_artefact_content_is_refused_and_the_decision_is_quoted():
    """A trace passed inline has already left the customer's machine before
    any response-side filtering runs.

    So the refusal is the shape of the argument and not a rule: a path is
    meaningless to a server on another machine, which is what makes a remote
    deployment fail rather than work while it leaks.
    """
    answer = diagnose(json.dumps({"schema_version": "3.0.0", "records": []}))
    assert answer.is_refusal
    quoted = [span for span in answer.spans if isinstance(span, Quoted)]
    assert quoted, "the refusal must carry the decision that requires it"
    assert quoted[0].source.endswith("ADR-022-the-integration-mcp.md")


def test_a_multi_line_artefact_is_refused_the_same_way(trace_with_a_secret):
    """Content that arrived where a path belongs is refused whatever its size,
    including the case an OS rejects the string outright."""
    _, document = trace_with_a_secret
    answer = diagnose(json.dumps(document, indent=2))
    assert answer.is_refusal
    assert SECRET not in answer.render()


def test_a_path_the_process_can_open_is_answered(trace_with_a_secret):
    path, _ = trace_with_a_secret
    answer = diagnose(str(path))
    assert not answer.is_refusal
    assert any(isinstance(span, Computed) for span in answer.spans)


# -- 4c: structure, never payload -------------------------------------------

def test_no_violation_message_reaches_the_answer(trace_with_a_secret):
    """The general form, and the reason 4a and 4c are not in tension.

    A violation's *message* can quote the value that broke the rule, so the
    diagnosis reports the rule and the path and withholds the message — while
    the reproduce line hands the caller the whole verdict on their own machine.
    Asserted over every message the validator actually produced for this
    document, rather than over the one a test author thought to plant.
    """
    path, document = trace_with_a_secret
    # A genuine trace validates clean, so the check needs one that does not:
    # a link rewritten, with every recorded value left exactly where it was.
    document["records"][-1]["integrity"]["prev_hash"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")

    rendered = diagnose(str(path)).render()
    messages = [v.message for v in validate.validate_trace(document)]
    assert messages, "this document produces no violations, so it checks nothing"
    for message in messages:
        assert message not in rendered, message


def test_a_recorded_value_never_appears_in_a_diagnosis(trace_with_a_secret):
    """The concrete half: a real fact value, in a real trace, on the primary
    path. An MCP that quietly widens what leaves a customer's machine to make
    an error message friendlier has made a decision that was not its to make."""
    path, _ = trace_with_a_secret
    assert SECRET not in diagnose(str(path)).render()
    assert SECRET not in json.dumps(diagnose(str(path)).to_dict())


def test_a_diagnosis_names_records_by_structure(trace_with_a_secret):
    path, _ = trace_with_a_secret
    rendered = diagnose(str(path)).render()
    assert "records, last kind" in rendered


# -- 4b: never propose a change that removes evidence -----------------------

#: The shortest path an agent under pressure would take, in the words it would
#: take it in. Not a filter on the output — the answer has no field to phrase
#: one in — but the check that says so, over answers actually produced.
EVIDENCE_REMOVING = (
    "regenerate", "re-generate", "delete the trace", "remove the record",
    "bypass", "disable the check", "overwrite", "declare it pure",
    "start over", "re-run and replace",
)


def test_no_diagnosis_suggests_removing_the_evidence(tmp_path, trace_with_a_secret):
    """Decision 4b, over a corpus of things that went wrong.

    Only `Computed` spans are checked, and the exemption is worth stating: a
    `Quoted` span is text that is in a repository document, so a forbidden
    phrase found there is a finding about the document and belongs in a
    different test — but it is also not something this server composed.
    """
    path, _ = trace_with_a_secret
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": "3.0.0", "run": {}, "records": []}',
                      encoding="utf-8")
    unreadable = tmp_path / "unreadable.txt"
    unreadable.write_text("this is neither JSON nor Python (", encoding="utf-8")
    node = tmp_path / "node.py"
    node.write_text("import datetime\ndef n(s):\n    return datetime.now()\n",
                    encoding="utf-8")

    for artefact in (path, broken, unreadable, node):
        answer = diagnose(str(artefact), symptom="ReplayMismatch")
        for span in answer.spans:
            if not isinstance(span, Computed):
                continue
            lowered = span.text.lower()
            for phrase in EVIDENCE_REMOVING:
                assert phrase not in lowered, (
                    f"{artefact.name}: a diagnosis proposed {phrase!r}")


# -- 4d: I cannot tell ------------------------------------------------------

def test_an_artefact_no_shipped_code_reads_gets_the_refusal(tmp_path):
    """A symptom the shipped code cannot reproduce gets that verdict and the
    commands that were tried. Guessing costs more than silence: a confident
    wrong diagnosis sends somebody to rewrite working code."""
    artefact = tmp_path / "mystery.bin"
    artefact.write_text("not json, not python (", encoding="utf-8")
    answer = diagnose(str(artefact))
    refusals = [span for span in answer.spans if isinstance(span, Cannot)]
    assert refusals, answer.render()
    assert refusals[0].tried, "a refusal must say what was tried"


def test_a_document_matching_no_contract_surface_is_not_guessed_at(tmp_path):
    artefact = tmp_path / "other.json"
    artefact.write_text('{"hello": "world"}', encoding="utf-8")
    answer = diagnose(str(artefact))
    assert answer.is_refusal
    assert any("matches no contract surface" in span.text
               for span in answer.spans if isinstance(span, Computed))


# -- the symptom does not steer the analysis --------------------------------

def test_a_symptom_naming_an_error_adds_that_error_and_nothing_else(
        trace_with_a_secret):
    """The symptom never steers the analysis — that would be reasoning about
    the artefact from memory with an artefact attached. When it names a Motus
    error, that error's own docstring joins the answer."""
    path, _ = trace_with_a_secret
    plain = diagnose(str(path))
    with_symptom = diagnose(str(path), symptom="UnsafeResume")
    added = with_symptom.spans[len(plain.spans):]
    assert [span.to_dict() for span in with_symptom.spans[:len(plain.spans)]] == \
           [span.to_dict() for span in plain.spans]
    assert any("cannot be resumed" in span.text for span in added
               if isinstance(span, Computed))


def test_a_symptom_naming_nothing_adds_nothing(trace_with_a_secret):
    path, _ = trace_with_a_secret
    plain = diagnose(str(path))
    noisy = diagnose(str(path), symptom="it just feels slow")
    assert [span.to_dict() for span in noisy.spans] == \
           [span.to_dict() for span in plain.spans]


# -- the reader must be the shipped reader ----------------------------------

def test_a_document_with_two_readings_is_refused_not_rooted(tmp_path):
    """It was parsed by `json.loads`, so the shipped RULES ran over a document
    the shipped READER had never seen.

    A trace carrying `records` twice was reported as verifying **with a derived
    root**, while the reproduce line it shipped in the same answer refused it
    as J1. Two readings is precisely what J1 exists to refuse, and a root
    derived from one of them is the defect ADR-019 corrects, restated.
    """
    artefact = tmp_path / "two-readings.json"
    artefact.write_text(
        '{"schema_version":"3.0.0","run":{},"records":[],"records":[]}',
        encoding="utf-8")
    rendered = diagnose(str(artefact)).render()
    assert "J1 at $" in rendered
    assert "derived root" not in rendered
    assert "no violation" not in rendered


def test_a_byte_the_contract_refuses_is_not_laundered(tmp_path):
    """`errors="replace"` substituted U+FFFD and the answer said the trace
    verified, while the reproduce line exited 2 on the decode.

    `validate.main` already carried the instruction this violated: *the file is
    what the contract judges; the reader must not launder it.*
    """
    artefact = tmp_path / "not-utf8.json"
    artefact.write_bytes(b'{"schema_version":"3.0.0","run":{},"records":["\xff"]}')
    answer = diagnose(str(artefact))
    assert answer.is_refusal
    assert "no violation" not in answer.render()
    assert any("decode utf-8" in tried
               for span in answer.spans if isinstance(span, Cannot)
               for tried in span.tried)


def test_the_stream_the_shipped_sink_writes_is_read_as_a_trace(tmp_path):
    """Every JSONL trace was answered `I cannot tell`, with a command about
    reviewing a node.

    `JsonlTraceSink` is the only durable sink Motus ships and its output has no
    schema file of its own, so nothing could name it, `json.loads` refused it —
    and `ast.parse` **accepted** it, because a JSON object literal is a valid
    Python expression. The artefact was misidentified as source code and the
    misidentification was stated as fact.
    """
    from vitruvyan_motus.sinks import JsonlTraceSink

    directory = tmp_path / "sink"
    directory.mkdir()
    Runtime(SPEC, {"a": lambda state: state}, sink=JsonlTraceSink(directory)).run(
        State.empty("x"), run_id="r-jsonl")
    written = list(directory.glob("*.jsonl"))
    assert written, "the shipped sink wrote no stream"

    rendered = diagnose(str(written[0])).render()
    assert "jsonl" in rendered
    assert "review-node" not in rendered


def test_a_version_below_3_0_0_is_told_why_it_has_no_root(trace_with_a_secret):
    """`derives no root` is a defect for a 3.0.0 trace and a property of the
    format for a 1.x or 2.x one, and both got the same sentence.

    ADR-019: below 3.0.0 the terminal digest covers one record rather than the
    run, so **there is none to have**. A customer holding a conformant archive
    was handed the sentence written for a corrupted trace.
    """
    path, document = trace_with_a_secret
    document["schema_version"] = "2.0.0"
    path.write_text(json.dumps(document), encoding="utf-8")
    rendered = diagnose(str(path)).render()
    assert "is below 3.0.0" in rendered
    assert "there is none to have" in rendered


def test_a_path_that_is_not_there_is_not_called_inline_content(tmp_path):
    """Every non-file was answered *refuses inline artefact content* — a typo'd
    filename, a directory, a dangling symlink.

    The answer asserted something false about the caller's request and hid the
    commonest real cause. Failing closed is about refusing to ACCEPT content;
    it never required mislabelling why a path failed.
    """
    for artefact in (tmp_path / "absent.json", tmp_path):
        answer = diagnose(str(artefact))
        assert answer.is_refusal
        rendered = answer.render()
        assert "inline artefact content" not in rendered, artefact
        assert "not a file this process can open" in rendered, artefact

    # And content still meets the decision that refuses it.
    assert "inline artefact content" in diagnose(
        '{"schema_version": "3.0.0", "records": []}').render()


def test_a_file_that_cannot_be_opened_is_answered_and_not_raised(tmp_path):
    """4d: `I cannot tell` is permitted **and the tool must be able to give
    it**. A file the process may stat and not open used to raise."""
    artefact = tmp_path / "forbidden.json"
    artefact.write_text("{}", encoding="utf-8")
    artefact.chmod(0o000)
    try:
        answer = diagnose(str(artefact))
    finally:
        artefact.chmod(0o600)
    assert answer.is_refusal
