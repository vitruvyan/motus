"""The integration MCP answers from its sources, or it does not answer.

ADR-022 decision 2 asks for a test that no prose lives in the server. This file
is that test, and it is written to survive a tool being added: it walks the
advertised surface rather than naming what each tool says, so a seventh tool
that invents a sentence fails here without anybody remembering to come back.

The strongest one in the file is
`test_editing_the_table_changes_the_answer`. Everything else checks that an
answer is SHAPED like a derivation; that one checks it IS one.
"""

from __future__ import annotations

import ast
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from vitruvyan_motus.mcp import diagnose as diagnose_module
from vitruvyan_motus.mcp import protocol, sources, tools
from vitruvyan_motus.mcp import answers
from vitruvyan_motus.mcp.answers import Cannot, Computed, Quoted

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "vitruvyan_motus" / "mcp"

GRAPH = {
    "schema_version": "1.0.0", "name": "g", "version": "1.0.0",
    "entry": "a", "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}

NODE_SOURCE = (
    "import datetime\n"
    "def node(state, ctx):\n"
    "    stamp = datetime.now()\n"
    "    client.post('/rows', json={'stamp': stamp})\n"
    "    return state\n"
)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> tuple:
    """One answer from every advertised tool, plus the debug capability.

    Assembled once and walked by several tests, because the properties below
    are about the surface and not about any one call: a check that runs on the
    answers a test author happened to think of is a check on the test author.
    """
    directory = tmp_path_factory.mktemp("mcp-corpus")
    fixture = json.loads((ROOT / "contract" / "fixtures"
                          / "216-receipt-p3-qualified-cannot-be-established-here.json"
                          ).read_text(encoding="utf-8"))
    broken_receipt = directory / "receipt.json"
    broken_receipt.write_text(json.dumps(fixture["instance"]), encoding="utf-8")

    trace = directory / "trace.json"
    trace.write_text(json.dumps({
        "schema_version": "3.0.0", "run": {"run_id": "r"}, "records": [],
    }), encoding="utf-8")
    return (
        # A document from the contract's own negative corpus, so that the
        # commitment-rule half of the surface is exercised by something the
        # contract itself calls broken rather than by a receipt written here.
        diagnose_module.diagnose(str(broken_receipt)),
        tools.classify("the node runs an INSERT"),
        tools.classify("it thinks about the weather"),
        tools.review_graph(GRAPH),
        tools.review_graph({"schema_version": "1.0.0"}),
        tools.review_node(NODE_SOURCE, effect_class="recorded_effect"),
        tools.explain("UnsafeResume"),
        tools.explain("NoSuchErrorExists"),
        tools.start_here(),
        tools.where("I want to add a durable sink"),
        tools.where("zzzz"),
        # Every citable source, because `find` is the tool that reaches all of
        # them -- a term each document contains, so this corpus walks the whole
        # declared surface instead of the two examples `start_here` quotes.
        tools.find("opaque_config"),
        tools.find("durability_profile"),
        tools.find("effect_class"),
        tools.find("guarantee"),
        tools.find("Motus"),
        tools.find("zzzz-no-such-term"),
        tools.find(""),
        diagnose_module.diagnose(str(trace)),
        diagnose_module.diagnose(str(directory / "absent.json")),
        # Content where a path belongs — the branch that quotes decision 4e.
        diagnose_module.diagnose('{"schema_version": "3.0.0", "records": []}'),
    )


# -- the shape of an answer -------------------------------------------------

def test_every_span_is_quoted_computed_or_refused(corpus):
    """There is no fourth kind, so there is nowhere to put a sentence.

    The rule is in the type rather than in a reviewer's attention, which is
    this project's rule about rules: one that has to be remembered will be
    broken, so it goes in the tool.
    """
    for answer in corpus:
        assert answer.spans, f"{answer.tool} answered with nothing at all"
        for span in answer.spans:
            assert isinstance(span, (Quoted, Computed, Cannot)), (
                f"{answer.tool} produced a {type(span).__name__}")


def test_every_quotation_is_in_the_document_it_names(corpus):
    """Checked again here, though `Quoted` refuses to be built otherwise.

    The construction-time check is what protects a running server; this is
    what protects the construction-time check, and they fail differently: a
    `Quoted` that stopped verifying would still pass a test that only builds
    answers and never reads them.
    """
    for answer in corpus:
        for span in answer.spans:
            if isinstance(span, Quoted):
                assert span.text in sources.read(span.source), (
                    f"{answer.tool} quotes {span.source} with text that is not in it")


def test_a_quotation_that_drifted_from_its_source_cannot_be_built():
    """The construction-time check, exercised rather than assumed.

    Found by a mutation probe: disabling this check left every test in this
    file passing, because every quotation the tools produce today is genuine.
    A check nothing can distinguish from its absence is a check that will be
    removed by the next person who finds it expensive.
    """
    genuine = protocol.clause("4.1")
    assert Quoted(protocol.SECTION, genuine).text == genuine

    with pytest.raises(ValueError) as caught:
        Quoted(protocol.SECTION, "a sentence nobody wrote in that document")
    assert protocol.SECTION in str(caught.value)


def test_a_source_the_installation_lacks_raises_rather_than_pointing_at_it(
        monkeypatch, tmp_path):
    """`resolve` refuses; it does not hand back a path that is not there.

    Also found by a probe. Returning `root / relative` unchecked would turn a
    broken installation into a `FileNotFoundError` from somewhere deeper — a
    failure that reads as a bug in the caller's machine rather than as this
    distribution being incomplete, which is what it is.
    """
    monkeypatch.setattr(sources, "_roots", lambda: (tmp_path,))
    with pytest.raises(sources.SourceMissing) as caught:
        sources.resolve(protocol.SECTION)
    assert str(tmp_path) in str(caught.value), (
        "the refusal must say where it looked")


def test_a_tool_with_nothing_to_say_says_so(corpus):
    """4d: `I cannot tell` is a permitted answer and must be reachable.

    A confident wrong diagnosis sends somebody to rewrite working code, so the
    absence of this answer is not a sign of a better tool.
    """
    refusals = [answer.tool for answer in corpus if answer.is_refusal]
    assert {"motus_classify", "motus_explain",
            "motus_diagnose"} <= set(refusals), refusals


# -- the reproduce lines ----------------------------------------------------

def test_every_reproduce_line_reproduces(corpus):
    """4a, checked by running the line **as rendered** and reading its output.

    Two rounds found this test too weak in the same way. It used to split the
    command on spaces and feed stdin itself, so a line that read `review-node -`
    — which blocks on a terminal and fails on empty input — passed while being
    unrunnable for the caller. And it asserted only that something was printed,
    so spans claiming `derived root` under a command that never prints one
    passed too.

    So: through a shell, exactly as a caller would paste it, and the span's own
    text must appear in what comes back. A reproduce line that does not
    reproduce is worse than none, because it asks for a trust it has not
    earned.
    """
    carried: dict[str, set[str]] = {}
    for answer in corpus:
        for span in answer.spans:
            if isinstance(span, Computed):
                carried.setdefault(span.reproduce, set()).add(span.text)
    assert carried, "no answer in the corpus carried a reproduce line"

    for command, texts in sorted(carried.items()):
        finished = subprocess.run(
            command, shell=True, cwd=ROOT, capture_output=True, text=True,
            timeout=180,
        )
        # 1 is the validator reporting violations, which is a verdict and not
        # a failure to run. Anything else, or a traceback, means the line this
        # server handed the caller does not work on their machine either.
        assert finished.returncode in (0, 1), (
            f"{command!r} exited {finished.returncode}\n{finished.stderr}")
        assert "Traceback" not in finished.stderr, (
            f"{command!r} crashed\n{finished.stderr}")

        if " -m vitruvyan_motus.mcp " in command:
            for text in texts:
                assert text in finished.stdout, (
                    f"{command!r} does not produce the span it carries:\n"
                    f"  span: {text!r}\n  output: {finished.stdout[:400]!r}")
        else:
            # The validator prints `RULE PATH: message`; the span withholds the
            # message (4c), so the rule and the path are what must line up.
            for text in texts:
                rule = text.split(" ", 1)[0]
                assert rule in finished.stdout or "no violation" in text, (
                    f"{command!r} does not report {rule!r}\n{finished.stdout[:400]!r}")


def test_a_computed_span_agrees_with_its_own_command():
    """Not only that the command runs — that it says the same thing.

    Run for one tool rather than all of them, and deliberately the one whose
    verdict is a single word: a mismatch here is unambiguous, where comparing
    whole renderings would fail on formatting and teach nobody anything.
    """
    answer = tools.classify("the node runs an INSERT")
    verdict = next(span for span in answer.spans if isinstance(span, Computed))
    finished = subprocess.run(
        [sys.executable, "-m", "vitruvyan_motus.mcp", "classify",
         "the node runs an INSERT"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines()[0] == verdict.text


# -- the derivation itself --------------------------------------------------

@pytest.fixture()
def installation(monkeypatch, tmp_path):
    """A copy of the citable sources that a test may edit or delete.

    Every derivation test below works on **files**, not on a monkeypatched
    reader. That distinction is the whole lesson of the round that produced
    this fixture: the previous version patched `sources.read`, so it tested a
    mock of the mechanism, and the real defect — a process-lifetime cache that
    kept answering from a document that had been DELETED — passed it silently.
    """
    for relative in sources.CITABLE:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(sources.read(relative), encoding="utf-8")
    monkeypatch.setattr(sources, "_roots", lambda: (tmp_path,))
    return tmp_path


def test_editing_the_document_changes_the_answer(installation):
    """The one test that checks the answer is DERIVED and not merely shaped
    like a derivation.

    Every other property in this file would still hold if `classify` returned a
    constant and quoted a matching line. This one edits the file on disk under
    a live process and requires the answer to follow it.
    """
    document = installation / protocol.SECTION
    original = document.read_text(encoding="utf-8")
    assert "external_effect" in tools.classify("an INSERT").render()

    document.write_text(original.replace(
        "| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
        "`TRUNCATE` | `external_effect` |",
        "| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
        "`TRUNCATE` | `recorded_effect` |"), encoding="utf-8")
    assert document.read_text(encoding="utf-8") != original, "the fixture drifted"

    rendered = tools.classify("an INSERT").render()
    assert ("| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
            "`TRUNCATE` | `recorded_effect` |") in rendered, rendered
    assert ("| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
            "`TRUNCATE` | `external_effect` |") not in rendered


def test_a_deleted_document_makes_the_tool_fail_and_not_invent(installation):
    """Decision 1: if the source is deleted the tool fails instead of inventing.

    Deleted from disk, not intercepted. A round found a server that kept
    answering `external_effect` — and kept building quotations citing the file
    — for as long as the process lived after the document was removed.
    """
    (installation / protocol.SECTION).unlink()
    with pytest.raises(sources.SourceMissing):
        tools.classify("an INSERT")
    with pytest.raises(sources.SourceMissing):
        sources.read(protocol.SECTION)


def test_a_table_this_reader_cannot_read_is_refused(installation):
    """A document present but shaped differently is not a licence to guess."""
    document = installation / protocol.SECTION
    document.write_text(document.read_text(encoding="utf-8").replace(
        "| a SQL `SELECT` | `recorded_effect` | permitted; blocks a resume "
        "that was safe |",
        "| a SQL `SELECT` | `recorded_effect` | permitted; blocks a resume "
        "that was safe | new |"), encoding="utf-8")
    with pytest.raises(protocol.ProtocolUnreadable):
        tools.classify("an INSERT")


def test_a_row_that_lost_its_marks_is_refused_and_not_skipped(installation):
    """A row whose operation cell carries no marked term stops the reader.

    Found by a round: restyling `` `INSERT` `` to `**INSERT**` — an ordinary
    maintainer edit — used to drop that row silently, leaving eleven rows and
    an answer that softened from `external_effect` to `recorded_effect`. A
    partial table is the one state this reader must never operate in, because
    the missing row is invisible in the answer.
    """
    document = installation / protocol.SECTION
    document.write_text(document.read_text(encoding="utf-8").replace(
        "| `send`, `email`, `sms`, `webhook`, `notify` |",
        "| **send**, **email**, **sms**, **webhook**, **notify** |"),
        encoding="utf-8")
    with pytest.raises(protocol.ProtocolUnreadable):
        tools.classify("we send the mail")


def test_the_strictness_ranking_names_classes_the_document_declares(installation):
    """`STRICTNESS` is a literal, and this is the test its docstring claimed.

    A round found the claim false. The order itself cannot be derived — it is
    what `strictest` is FOR — but every name in it must be a class §4.4 uses,
    so a class renamed in the document cannot leave a stale name ranking above
    a live one.
    """
    declared = {row.effect_class for row in protocol.table()}
    assert set(protocol.STRICTNESS) == declared, (
        f"ranked {sorted(protocol.STRICTNESS)}, document declares {sorted(declared)}")


def test_every_citable_source_is_actually_cited(corpus):
    """The other direction, and it is about the wheel.

    A source listed as citable and quoted by nothing is weight in the
    distribution that no decision put there — and it reads, to anybody
    auditing `CITABLE`, as a claim that some tool consults it.
    """
    cited = {span.source for answer in corpus for span in answer.spans
             if isinstance(span, Quoted)}
    uncited = sorted(set(sources.CITABLE) - cited)
    assert not uncited, (
        f"CITABLE names sources no tool quotes: {uncited}")


def test_every_citable_source_resolves_in_a_checkout():
    for relative in sources.CITABLE:
        assert sources.resolve(relative).is_file()


def test_a_source_outside_the_declared_set_is_refused():
    """`CITABLE` is a check on the answer, not a check on the filesystem."""
    assert (ROOT / "README.md").is_file()
    with pytest.raises(sources.SourceMissing):
        sources.read("README.md")


# -- what the server may not do ---------------------------------------------

NETWORK = {"socket", "http", "urllib", "requests", "httpx", "ftplib",
           "telnetlib", "smtplib", "asyncio", "ssl", "xmlrpc"}


def test_the_package_imports_nothing_that_could_fetch_a_source():
    """Decision 1: the server never fetches a source it did not install.

    Parsed rather than grepped. The question is *what does this package
    import*, and a pattern over the text answers a different one — it finds the
    word `urllib` in this docstring, and it misses `__import__("urllib")`.
    Every module in the package is walked, so a file added later is covered
    without this list being revisited.

    `server.py` is walked too: the SDK it imports is a transport, and a
    transport that reached for a document would be reaching past every check
    in this file.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in NETWORK:
                    offenders.append(f"{path.name}:{node.lineno} imports {name}")
    assert not offenders, offenders


def test_the_tools_work_without_the_sdk_installed():
    """The derivation is the valuable half of ADR-022; the transport is not.

    Asserted in a subprocess where importing `mcp` raises, so this is about the
    import graph and not about what happens to be installed on the machine
    running the suite.
    """
    program = (
        "import sys\n"
        "class Blocked:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ImportError('the SDK is not installed')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocked())\n"
        "from vitruvyan_motus.mcp import tools\n"
        "print(tools.classify('an INSERT').spans[0].text)\n"
    )
    finished = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "§4.4 terms present in your text: INSERT"


def test_running_a_graph_does_not_load_the_server():
    """The house rule of ADR-021 decision 1, kept for this component too.

    A library that grows a server by default has become a service and nobody
    decided that. Checked the way the commitment modules are: run a real graph
    in a subprocess and look at what got imported.
    """
    program = (
        "import sys\n"
        "from vitruvyan_motus import Fact, GraphSpec, Runtime, State\n"
        "spec = GraphSpec.from_dict({'schema_version': '1.0.0', 'name': 'n',\n"
        "  'version': '1.0.0', 'entry': 'a',\n"
        "  'nodes': [{'name': 'a', 'effect_class': 'pure'}],\n"
        "  'transitions': {'a': {'kind': 'terminal'}}})\n"
        "Runtime(spec, {'a': lambda state: state}).run(State.empty('x'))\n"
        "print([m for m in sys.modules if 'mcp' in m])\n"
    )
    finished = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "[]", finished.stdout


# -- the error this whole component exists because of -----------------------

#: The descriptions two independent adversarial lenses answered wrongly, in
#: both directions, when this tool computed a class from word matching.
WERE_ANSWERED_WRONGLY = [
    "the node formats the receipt and mails it to the customer",
    "the node computes the invoice total and stores it in Postgres",
    "the node derives the customer tier and saves it to our billing system",
    "the node reads the intent and derives a route decision",
    "it reads facts by key and formats a summary",
    "it formats the message and produces it onto the Kafka topic",
    "derives the diff and then git-pushes the branch",
    "the node writes a fact recording the score",
    "appends a decision to the state for the router to dispatch on",
]

EFFECT_CLASSES = {"pure", "recorded_effect", "external_effect"}


@pytest.mark.parametrize("description", WERE_ANSWERED_WRONGLY + [
    "the node issues an HTTP GET",
    "I need to INSERT a row",
    "it just does some work",
])
def test_no_answer_is_a_class_this_tool_decided(description):
    """The withdrawal, pinned.

    `classify` computed an effect class from word matching and was wrong on
    most realistic descriptions — `pure` for a node that mails a receipt,
    because `compute` is marked and `mails` is not. Adding terms would repair
    the instance; the class of the defect is **a pattern answering a question
    about meaning**, and it does not have a vocabulary big enough to fix.

    So no span may be a bare class name. What the caller gets is which marked
    terms their text contains — a fact about the text — and the document.
    """
    answer = tools.classify(description)
    for span in answer.spans:
        if isinstance(span, Computed):
            assert span.text.strip() not in EFFECT_CLASSES, (
                f"{description!r}: the tool decided a class again")


@pytest.mark.parametrize("description", WERE_ANSWERED_WRONGLY)
def test_a_description_that_was_answered_wrongly_now_carries_the_table(description):
    """And the answer is not merely silent — it is the document.

    The measured error was never that readers could not run a matcher. It was
    that nothing told them what the conservative choice costs. Every answer now
    carries the whole table, the asymmetry, the strictest-class rule and 4.1.
    """
    rendered = tools.classify(description).render()
    assert "This table classifies operations" in rendered
    assert "blocks a resume that was safe" in rendered
    assert "never\nmeets the resume guard at all" in rendered
    assert "takes the strictest class any of" in rendered
    for row in protocol.table():
        assert row.line in rendered, row.line


def test_the_matched_terms_are_reported_as_terms(corpus):
    """Which is a fact about the caller's text, and checkable by them."""
    answer = tools.classify("I need to INSERT a row and read the config")
    reported = next(span for span in answer.spans if isinstance(span, Computed))
    assert reported.text.startswith("§4.4 terms present in your text:")
    assert "INSERT" in reported.text and "read" in reported.text


def test_review_node_no_longer_accuses_the_shipped_examples():
    """It read `payload.get` as an HTTP GET and `seen.append` as a file write.

    On the example whose own docstring says the node *stays `pure`, which is
    what makes it verifiable during replay*, it reported `declared pure, calls
    imply external_effect` — and an agent taking the shortest path from that
    re-declares a genuinely pure node and forfeits the falsifiability §4.1
    promises. Same class as `classify`'s withdrawal, over identifiers.
    """
    for name in ("03_async_and_streaming.py", "05_how_a_node_reports.py"):
        source = (ROOT / "examples" / name).read_text(encoding="utf-8")
        rendered = tools.review_node(source, effect_class="pure").render()
        assert "calls imply" not in rendered, name
        assert "not\nexhaustive" in rendered, (
            f"{name}: a silent refusal reads as *no ambient draws present*")


def test_review_node_still_finds_what_is_structural():
    """The two checks that survive are AST facts, not word matches."""
    ambient = tools.review_node("import datetime\ndef n(s):\n    return datetime.now()\n")
    assert any("datetime.now" in span.text for span in ambient.spans
               if isinstance(span, Computed))

    missing_key = tools.review_node(
        "def n(s, ctx):\n    ctx.record_effect(EffectDescriptor(kind='http'))\n",
        effect_class="external_effect")
    assert any("idempotency_key" in span.text for span in missing_key.spans
               if isinstance(span, Computed))


def test_the_labels_carry_no_protocol_meaning():
    """`answers.LABELS` said a test constrained it. A round found no such test,
    and found `LABELS` read by nothing at all — a decoration standing where
    decision 2's second guard was meant to be. The renderers read it now."""
    for label in answers.LABELS:
        lowered = label.lower()
        assert not (EFFECT_CLASSES & set(lowered.split())), label
        assert "motus" not in lowered and "trace" not in lowered

    rendered = tools.classify("an INSERT").render()
    assert f"[{answers.LABELS[0]}: " in rendered
    assert f"[{answers.LABELS[1]}: " in rendered


def test_the_advertised_descriptions_make_no_claim_about_the_protocol():
    """The one place text crosses the wire without being quoted.

    A tool description and the server's `instructions` reach the client at
    `initialize`, before any tool has run — so there is nothing yet to derive
    them from, and ADR-022 decision 2's type rule cannot reach them. A round
    found two of them materially false as advertisements and `instructions`
    asserting *"Nothing here is written from memory"*, which was itself written
    from memory.

    They are held to a narrower rule instead, and it is this test: they say
    what an argument is and what comes back, and they claim nothing about the
    protocol's content or about this server's own honesty.
    """
    pytest.importorskip("mcp")
    import asyncio

    from vitruvyan_motus.mcp.server import build

    server = build()
    advertised = [tool.description or "" for tool in asyncio.run(server.list_tools())]
    advertised.append(server.instructions or "")
    assert len(advertised) == 9

    forbidden = tuple(EFFECT_CLASSES) + (
        "from memory", "nothing here", "always", "cannot drift", "honest")
    for text in advertised:
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{phrase!r} in {text!r}"


def test_where_reports_what_the_intent_touched_and_hides_nothing():
    """#110: it accepted an intent and ignored it.

    Two repairs overshot each other. Matching on shared words named seven of
    fourteen modules for the intent `"the"`; returning everything instead left
    the parameter in the signature, unread — **an argument that is ignored is a
    lie in the signature**, and worse than the wrong selection it replaced,
    because the caller cannot see that their question was never read.

    So the intent is reported as a lexical fact and the whole map travels
    regardless: `sinks (a, durable, sink)` beside `commitlog (a)` shows the
    caller which match is theirs and which is an article.
    """
    answer = tools.where("I want to add a durable sink")
    reported = next(span for span in answer.spans if isinstance(span, Computed))
    assert reported.text.startswith("words your text shares with")
    assert "sinks (a, durable, sink)" in reported.text

    modules = {span.text.split(".py")[0] for span in answer.spans
               if isinstance(span, Computed) and ".py — " in span.text}
    assert {"sinks", "runtime", "trace", "errors"} <= modules, (
        "the whole map travels whatever the intent matched")

    # And an intent that touches nothing says so, rather than selecting at random.
    empty = tools.where("zzzz")
    assert empty.is_refusal
    assert len([s for s in empty.spans if isinstance(s, Computed)]) == len(modules)


# -- motus_find -------------------------------------------------------------
#
# The tool #107 is about. The first external integrator held
# `node:check:opaque_config` from a real trace, asked this server what to do
# about it, and no tool could reach the answer: `explain` knows exception class
# names, `where` returns the module map, `classify` reads a description. The
# answer was in `contract/node-protocol.md` the whole time.

def test_find_answers_the_question_that_was_asked_and_could_not_be():
    answer = tools.find("opaque_config")
    assert not answer.is_refusal
    sources_named = {span.source for span in answer.spans}
    assert protocol.SECTION in sources_named, (
        "the term is defined in the node protocol and the answer must reach it")
    assert any("motus_config" in span.text for span in answer.spans), (
        "found the constraint and not the way out of it")


def test_find_can_only_quote(corpus):
    """The design, asserted: `find` has no `Computed` span carrying prose and
    no place to put one. Its every hit is a `Quoted`, which verifies at
    construction that the text is in the file it names -- so the worst this
    tool can do is quote the wrong paragraph, and the caller can see which
    file to go and read."""
    for answer in corpus:
        if answer.tool != "motus_find" or answer.is_refusal:
            continue
        assert all(isinstance(span, Quoted) for span in answer.spans), (
            [type(span).__name__ for span in answer.spans])


def test_find_refuses_rather_than_guessing():
    answer = tools.find("a term nobody has ever written here")
    assert answer.is_refusal
    rendered = answer.render()
    for relative in sources.CITABLE:
        assert relative in rendered, (
            "a refusal must say what was searched, or it reads as 'nowhere'")


def test_find_with_nothing_to_find_asks_nothing_of_the_filesystem():
    assert tools.find("").is_refusal
    assert tools.find("   ").is_refusal


def test_a_fenced_block_travels_with_the_paragraph_that_introduces_it():
    """A passage that ends mid-sentence reads as the whole answer.

    Found by using the tool: the §6.3 answer to #107 introduces a closure in
    fenced Python, and splitting on blank lines alone cut the sentence off at
    the fence. A fence illustrates the prose above it -- quoting it away from
    that prose hands a reader code with nothing saying what it is.
    """
    document = "intro paragraph\n\nsays this:\n\n```python\nx = 1\n\ny = 2\n```\n\nafter\n"
    blocks = tools._blocks(document)
    assert "intro paragraph" in blocks
    fenced = [block for block in blocks if "```" in block]
    assert len(fenced) == 1, blocks
    assert fenced[0].startswith("says this:"), "the fence lost its sentence"
    assert "y = 2" in fenced[0], "the fence was split at its own blank line"


def test_find_is_derived_from_the_file_and_not_from_memory(monkeypatch, tmp_path):
    """The strongest property this server has, applied to the new tool.

    Copy the citable set into a temporary root, edit one document, and the
    answer must change. A tool that kept answering from the shipped text would
    pass every other test in this file.
    """
    for relative in sources.CITABLE:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sources.read(relative), encoding="utf-8")

    edited = tmp_path / protocol.SECTION
    edited.write_text(
        "A paragraph naming zzzzunique that the shipped document does not have.\n",
        encoding="utf-8")
    monkeypatch.setattr(sources, "_roots", lambda: (tmp_path,))

    answer = tools.find("zzzzunique")
    assert not answer.is_refusal, "the tool did not read the file it was pointed at"
    assert any("zzzzunique" in span.text for span in answer.spans)

    # And the other direction: the protocol section no longer answers for a
    # term it defines in the shipped tree. The example still does -- it was
    # copied unedited -- which is the tool reading each file rather than
    # remembering what any of them said.
    still = tools.find("opaque_config")
    assert protocol.SECTION not in {span.source for span in still.spans}
    assert "examples/04_parameterised_nodes.py" in {span.source for span in still.spans}
