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
        diagnose_module.diagnose(str(trace)),
        diagnose_module.diagnose(str(directory / "absent.json")),
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
    assert {"motus_classify", "motus_explain", "motus_where",
            "motus_diagnose"} <= set(refusals), refusals


# -- the reproduce lines ----------------------------------------------------

def test_every_reproduce_line_reproduces(corpus):
    """4a, checked by running them.

    A reproduce line that does not reproduce is worse than none, because it
    asks for a trust it has not earned. Commands are deduplicated and each is
    run once — the property is about the command, not about how many spans
    carried it.
    """
    commands = {span.reproduce for answer in corpus for span in answer.spans
                if isinstance(span, Computed)}
    assert commands, "no answer in the corpus carried a reproduce line"

    for command in sorted(commands):
        # `shlex`, not `str.split`: the reproduce line is written for a shell,
        # and splitting on spaces would take a quoted description apart and
        # then blame the command for the pieces.
        parts = shlex.split(command)
        assert parts[0] == "python", command
        stdin = ""
        if parts[-1] == "-":
            stdin = json.dumps(GRAPH) if "review-graph" in parts else NODE_SOURCE
        finished = subprocess.run(
            [sys.executable, *parts[1:]], cwd=ROOT, input=stdin,
            capture_output=True, text=True, timeout=120,
        )
        # 1 is the validator reporting violations, which is a verdict and not
        # a failure to run. Anything else, or a traceback, means the line this
        # server handed the caller does not work on their machine either.
        assert finished.returncode in (0, 1), (
            f"{command!r} exited {finished.returncode}\n{finished.stderr}")
        assert "Traceback" not in finished.stderr, (
            f"{command!r} crashed\n{finished.stderr}")
        assert finished.stdout.strip(), f"{command!r} printed nothing"


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

def test_editing_the_table_changes_the_answer(monkeypatch):
    """The one test that checks the answer is DERIVED and not merely shaped
    like a derivation.

    Every other property in this file would still hold if `classify` returned
    a constant and quoted a matching line. This one moves the document out from
    under it: `INSERT` is re-declared `recorded_effect` in the text the server
    reads, and the verdict must follow the document rather than the author.
    """
    original = sources.read(protocol.SECTION)
    edited = original.replace(
        "| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
        "`TRUNCATE` | `external_effect` |",
        "| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or "
        "`TRUNCATE` | `recorded_effect` |")
    assert edited != original, "the fixture no longer matches the document"

    assert tools.classify("an INSERT").spans[0].text == "external_effect"

    monkeypatch.setattr(sources, "read",
                        lambda relative: edited if relative == protocol.SECTION
                        else sources.resolve(relative).read_text(encoding="utf-8"))
    protocol.table.cache_clear()
    try:
        assert tools.classify("an INSERT").spans[0].text == "recorded_effect"
    finally:
        monkeypatch.undo()
        protocol.table.cache_clear()

    # And back, with the document restored — a cache that kept the edited
    # answer would make this test pass once and lie afterwards.
    assert tools.classify("an INSERT").spans[0].text == "external_effect"


def test_a_deleted_source_makes_the_tool_fail_and_not_invent(monkeypatch):
    """Decision 1: if the source is deleted the tool fails instead of inventing.

    The failure is the feature. A server that keeps answering when its document
    is gone has revealed that the document was never where the answer came
    from.
    """
    def gone(relative: str) -> str:
        raise sources.SourceMissing(relative)

    monkeypatch.setattr(sources, "read", gone)
    protocol.table.cache_clear()
    try:
        with pytest.raises(sources.SourceMissing):
            tools.classify("an INSERT")
    finally:
        protocol.table.cache_clear()


def test_a_table_this_reader_cannot_read_is_refused(monkeypatch):
    """A document present but shaped differently is not a licence to guess."""
    original = sources.read(protocol.SECTION)
    # A fourth column: the shape a table drifts into, not a shape nobody would
    # write. The reader is told how many columns it was written for and says so
    # rather than reading the first three and hoping.
    widened = original.replace(
        "| a SQL `SELECT` | `recorded_effect` | permitted; blocks a resume "
        "that was safe |",
        "| a SQL `SELECT` | `recorded_effect` | permitted; blocks a resume "
        "that was safe | new |")
    assert widened != original, "the fixture no longer matches the document"
    monkeypatch.setattr(sources, "read",
                        lambda relative: widened if relative == protocol.SECTION
                        else original)
    protocol.table.cache_clear()
    try:
        with pytest.raises(protocol.ProtocolUnreadable):
            tools.classify("an INSERT")
    finally:
        protocol.table.cache_clear()


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
    assert finished.stdout.strip() == "external_effect"


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

@pytest.mark.parametrize(("description", "expected"), [
    ("the node issues an HTTP GET", "recorded_effect"),
    ("check_verbatim issues nothing but HTTP GETs", "recorded_effect"),
    ("I need to INSERT a row", "external_effect"),
    ("it POSTs the result to their API", "external_effect"),
    ("we send an email when the run finishes", "external_effect"),
    ("it reads the file and then writes the report", "external_effect"),
    # pure and recorded together, which is the pair alphabetical order gets
    # wrong: `pure` sorts first and is the softer class. A probe found that
    # nothing here covered it.
    ("it derives a total and reads a file", "recorded_effect"),
])
def test_the_measured_misreading_is_answered(description, expected):
    """#77 inverted this twice in one pull request, and a field report against
    v0.10.0 declared HTTP GETs `external_effect` believing it conservative.

    The last row is the one prose never delivered: a node that reads AND writes
    is the strict class, because mutating is not cancelled by also reading.
    """
    answer = tools.classify(description)
    assert not answer.is_refusal, description
    assert answer.spans[0].text == expected


def test_a_classification_always_carries_its_cost():
    """The class alone is the half that was already being guessed correctly
    half the time. What nobody was told is the price of the other choice."""
    answer = tools.classify("the node issues an HTTP GET")
    rendered = answer.render()
    assert "blocks a resume that was safe" in rendered
    assert "never\nmeets the resume guard at all" in rendered
