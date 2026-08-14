"""The advertised surface. Every answer here is quoted, computed, or refused.

ADR-022 decision 3 names six describing tools; ``diagnose`` (decision 4) lives
in its own module because three decisions apply to it and to nothing else.

None of these tools contains a sentence about the protocol. Where one appears
to explain something, it is quoting `contract/node-protocol.md` and the
quotation was verified against the installed file when the span was built. That
is not a convention being followed carefully — ``answers.Quoted`` refuses to
exist otherwise.
"""

from __future__ import annotations

import ast
import shlex
import sys
from pathlib import Path

from .answers import Answer, Cannot, Computed, Quoted
from . import protocol
from .protocol import SECTION

__all__ = ["classify", "review_graph", "review_node", "explain",
           "start_here", "where", "ADVERTISED"]

_ME = "vitruvyan_motus.mcp.tools"


def _command(*parts: str) -> str:
    """A reproduce line for this package's own CLI.

    Every ``Computed`` span carries one, and a test runs all of them: a
    reproduce line that does not reproduce is worse than none, because it
    invites a trust it has not earned.
    """
    # `sys.executable`, not the word `python`. The interpreter that has Motus
    # installed is the one that can run the line, and on a plain Debian or a
    # pipx install there is no `python` on PATH at all.
    return (shlex.quote(sys.executable) + " -m vitruvyan_motus.mcp "
            + " ".join(shlex.quote(p) for p in parts))


def _words(text: str) -> set[str]:
    """The lowercase word tokens of a description.

    Deliberately not a pattern over the text. The tokens are compared against
    terms the protocol document itself marks, so this only has to agree with
    the document about where a word ends.
    """
    out: list[str] = []
    current: list[str] = []
    for character in text:
        if character.isalnum() or character == "_":
            current.append(character.lower())
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return set(out)


#: The inflections an English writer puts on the terms §4.4 marks. Enumerated
#: rather than stemmed: a stemmer is a model of a language, and a model that is
#: wrong about a word here turns a citation into a misquotation. The cost of
#: this list being short is a caller getting `I cannot tell` and the table —
#: which is a safe answer. The cost of it being clever is a confident wrong one.
_INFLECTIONS = ("", "s", "es", "ed", "d", "ing")


def _matches(term: str, words: set[str]) -> bool:
    """Whether a description containing ``words`` names ``term``.

    A term of several words needs all of them; a single word is matched
    against the inflections above, so *POSTs*, *inserting* and *sends* reach
    the rows that `POST`, `INSERT` and `send` name. Nothing here strips a
    prefix or matches a substring: `sum` must not be found in `summary`.
    """
    parts = _words(term)
    if len(parts) != 1:
        return parts <= words
    (root,) = parts
    return any(root + suffix in words for suffix in _INFLECTIONS)


def _paragraph(text: str, opening: str) -> str:
    """The paragraph of ``text`` beginning with ``opening``, verbatim."""
    for block in text.split("\n\n"):
        if block.lstrip().startswith(opening):
            return block.strip()
    raise protocol.ProtocolUnreadable(
        f"no paragraph opening {opening!r} in this installation's {SECTION}")


# -- motus_classify ---------------------------------------------------------

#: The paragraph §4.4 uses to say why the two errors are not symmetric. Quoted
#: on every classification, because the class alone is the half that was
#: already being guessed correctly half the time.
_ASYMMETRY = "**The asymmetry is the whole point,"
_FALLBACK = "**When the operation is not in this table**"
_STRICTEST = "**A node whose work falls in more than one row"
#: The paragraph that says what a program may do with the marked terms. Quoted
#: on every answer, and the reason there is no verdict to quote it beside.
_NOT_SENTENCES = "**This table classifies operations."


def classify(description: str) -> Answer:
    """Which §4.4 terms are present in ``description``, and the whole table.

    **This tool does not classify. It used to, and the verdict it produced was
    wrong on most realistic descriptions** — two independent adversarial
    lenses measured 15 of 20 and 6 of 6, in both directions, and it never
    refused once. Worse than the count is the direction: *"computes the invoice
    total and stores it in Postgres"* was answered `pure`, because `compute` is
    a marked term and `stores` is not, and a write declared `pure` never meets
    the resume guard at all.

    The class of the defect is one this project has written down: **a pattern
    answering a question about meaning.** Matching characters against a
    vocabulary tells you which marked terms a sentence contains; it cannot tell
    you which operation the sentence names in words the table does not mark,
    and that is exactly the operation that decides the class. Adding terms
    repairs the instance and leaves the class, so the terms were not added.

    What is left is what the measurement actually asked for. The error we
    observed was not that readers could not run a matcher — it was that nobody
    had told them **what the conservative choice costs**. So every answer
    carries the whole table, the asymmetry, the strictest-class rule, the
    fallback clause and 4.1, and the matched terms arrive as what they are: a
    fact about the caller's text.
    """
    rows = protocol.table()
    words = _words(description)
    found = [term for row in rows for term in row.terms if _matches(term, words)]
    reproduce = _command("classify", description)

    spans: list[object] = []
    if found:
        spans.append(Computed(
            by=f"{_ME}.classify", reproduce=reproduce,
            # Terms, never a class. The rows below carry the classes, and they
            # carry them as the document wrote them.
            text="§4.4 terms present in your text: " + ", ".join(sorted(set(found)))))
    else:
        spans.append(Cannot(tried=(reproduce,)))

    spans.append(Quoted(SECTION, _paragraph(protocol.clause("4.4"), _NOT_SENTENCES)))
    spans.extend(Quoted(SECTION, row.line) for row in rows)
    spans.append(Quoted(SECTION, _paragraph(protocol.clause("4.4"), _ASYMMETRY)))
    spans.append(Quoted(SECTION, _paragraph(protocol.clause("4.4"), _STRICTEST)))
    spans.append(Quoted(SECTION, _paragraph(protocol.clause("4.4"), _FALLBACK)))
    spans.append(Quoted(SECTION, protocol.clause("4.1")))
    return Answer(tool="motus_classify", spans=tuple(spans))


# -- motus_review_graph -----------------------------------------------------

def review_graph(spec: dict) -> Answer:
    """The verdict of the code that will refuse this graph at runtime.

    Not advice. ``GraphSpec.from_dict`` is the same call the runtime makes, so
    a caller who passes here passes there, and a violation reported here is the
    violation they would have met.
    """
    from vitruvyan_motus import GraphSpec
    from vitruvyan_motus.errors import GraphSpecValidationError

    reproduce = _command("review-graph", "-")
    try:
        parsed = GraphSpec.from_dict(spec)
    except GraphSpecValidationError as refused:
        # The message travels here and not in `diagnose`: the caller handed
        # this spec over inline, so nothing in it crosses a boundary it had not
        # already crossed. A trace is the opposite case, and 4c governs there.
        return Answer(tool="motus_review_graph", spans=tuple(
            Computed(by="vitruvyan_motus.graph.GraphSpec.from_dict",
                     reproduce=reproduce,
                     text=f"{violation.rule} at {violation.path}: {violation.message}")
            for violation in refused.violations))
    except Exception as refused:               # noqa: BLE001 - reported, not swallowed
        return Answer(tool="motus_review_graph", spans=(
            Computed(by="vitruvyan_motus.graph.GraphSpec.from_dict",
                     reproduce=reproduce,
                     text=f"{type(refused).__name__}: {refused}"),))

    return Answer(tool="motus_review_graph", spans=(
        Computed(by="vitruvyan_motus.graph.GraphSpec.from_dict",
                 reproduce=reproduce,
                 text=f"accepted: {len(parsed.nodes)} nodes, entry {parsed.entry!r}, "
                      f"graph_fingerprint {parsed.graph_fingerprint}"),))


# -- motus_review_node ------------------------------------------------------

def _called_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Every dotted name that appears in call position, with its line.

    An AST walk and not a search over the text, because the question is *what
    does this node call* and a pattern over characters answers a different one:
    a term inside a docstring or a comment is not a call, and a call spelled
    across two lines is still a call.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        if parts:
            found.append((".".join(reversed(parts)), node.lineno))
    return found


def _keyword_used(tree: ast.AST, name: str) -> bool:
    """Whether any call passes ``name`` as a keyword argument."""
    return any(keyword.arg == name
               for node in ast.walk(tree) if isinstance(node, ast.Call)
               for keyword in node.keywords)


def review_node(source: str, effect_class: str | None = None,
                reproduce: str | None = None) -> Answer:
    """What the protocol says about the calls this node makes.

    Two questions, both answered structurally: does it draw ambiently (§6.4,
    reported as §6.2 requires), and — for a node declared `external_effect` —
    does anything supply the idempotency key resume requires (§4.3).

    **A third check was here and is withdrawn.** It compared the last dotted
    component of every call against §4.4's marked terms, and so it read
    `payload.get` as an HTTP `GET` and `seen.append` as appending to a file. On
    Motus's own shipped examples it accused a node whose docstring says it
    *"stays `pure`, which is what makes it verifiable during replay"* of
    implying `external_effect`. An agent taking the shortest path from that
    verdict re-declares a genuinely pure node and forfeits the falsifiability
    §4.1 promises — the exact harm ADR-022 exists to prevent, produced by the
    tool built to prevent it. It is the same class as `classify`'s withdrawal:
    a pattern answering a question about meaning, over identifiers this time
    instead of over English.
    """
    # `diagnose` passes the path it was given. Reading it from a file the
    # caller already has beats asking them to pipe the source back in: a round
    # found this line emitted as `review-node -`, which blocks on a terminal
    # when pasted — a reproduce line that does not reproduce.
    reproduce = reproduce or _command("review-node", "-")
    try:
        tree = ast.parse(source)
    except SyntaxError as broken:
        return Answer(tool="motus_review_node", spans=(
            Computed(by="ast.parse", reproduce=reproduce,
                     text=f"line {broken.lineno}: {broken.msg}"),))

    calls = _called_names(tree)
    spans: list[object] = []

    drawn = [(name, line, mediated)
             for name, line in calls
             for draw, mediated in protocol.ambient_terms()
             if name == draw or name.endswith("." + draw)]
    for name, line, mediated in drawn:
        spans.append(Computed(by=f"{_ME}.review_node",
                              reproduce=reproduce,
                              text=f"line {line}: {name} — 6.1 draws this "
                                   f"through {mediated}"))
    if drawn:
        spans.append(Quoted(SECTION, protocol.clause("6.2")))

    if effect_class == "external_effect" and not _keyword_used(tree, "idempotency_key"):
        spans.append(Computed(by=f"{_ME}.review_node", reproduce=reproduce,
                              text="no call passes idempotency_key"))
        spans.append(Quoted(SECTION, protocol.clause("4.3")))

    if not spans:
        spans.append(Cannot(tried=(reproduce,)))
    # Always, and especially when nothing was found: §6.4 says its own table is
    # not exhaustive, and a silent `I cannot tell` reads as *none present*.
    spans.append(Quoted(SECTION, _paragraph(protocol.clause("6.4"),
                                            "This table names the ones")))
    return Answer(tool="motus_review_node", spans=tuple(spans))


# -- motus_explain ----------------------------------------------------------

def explain(error: str) -> Answer:
    """What a Motus error means, from the class that raises it.

    The docstring of the installed exception is the answer, because it is the
    sentence the runtime's own author wrote next to the raise. A name that is
    not an error in this installation gets 4d's answer and the names that are.
    """
    from vitruvyan_motus import errors

    name = error.strip().split(":", 1)[0].split("(", 1)[0].strip()
    reproduce = _command("explain", name)
    candidate = getattr(errors, name, None)
    known = tuple(sorted(
        attribute for attribute in errors.__all__
        if isinstance(getattr(errors, attribute, None), type)
        and issubclass(getattr(errors, attribute), BaseException)))

    if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
        return Answer(tool="motus_explain", spans=(
            Cannot(tried=(reproduce,)),
            Computed(by="vitruvyan_motus.errors.__all__",
                     reproduce=shlex.quote(sys.executable) + " -m vitruvyan_motus.mcp explain",
                     text=", ".join(known)),))

    inherited = " -> ".join(base.__name__ for base in candidate.__mro__[1:]
                            if issubclass(base, BaseException))
    return Answer(tool="motus_explain", spans=(
        Computed(by=f"vitruvyan_motus.errors.{name}.__doc__",
                 reproduce=reproduce,
                 text=(candidate.__doc__ or "").strip()),
        Computed(by=f"vitruvyan_motus.errors.{name}.__mro__",
                 reproduce=reproduce,
                 text=f"{name} is a {inherited}"),))


# -- motus_start_here -------------------------------------------------------

_FIRST_RUN = "examples/01_first_run.py"
_EFFECTS = "examples/06_effects_and_receipts.py"


def start_here() -> Answer:
    """The shape of a Motus program, as the shipped example writes it.

    Quoted whole rather than excerpted. An excerpt is a summary with the
    author's judgement in it, and the example is already the thing somebody
    would be handed.
    """
    from . import sources
    return Answer(tool="motus_start_here", spans=(
        Quoted(_FIRST_RUN, sources.read(_FIRST_RUN)),
        # The second example is here because effect declaration is the part
        # this protocol measurably loses readers on (#77, and Terraveler's
        # field report). A first run that never declares one teaches the shape
        # and leaves out the decision.
        Quoted(_EFFECTS, sources.read(_EFFECTS)),
    ))


# -- motus_where ------------------------------------------------------------

def _module_summaries() -> tuple[tuple[str, str], ...]:
    """Every kernel module's first docstring line, read from installed source.

    Read rather than imported: importing the package to ask where code goes
    would load modules the runtime keeps unloaded until configured, and a tool
    that answers a question by changing the process is answering the wrong way.
    """
    package = Path(__file__).resolve().parent.parent
    summaries: list[tuple[str, str]] = []
    for path in sorted(package.glob("*.py")):
        if path.name.startswith("_"):
            continue
        docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        if docstring:
            summaries.append((path.stem, docstring.splitlines()[0]))
    return tuple(summaries)


def where(intent: str) -> Answer:
    """Which module of the kernel owns which kind of code.

    The answer is the modules' own docstrings. Motus states what each file is
    for on its first line (ADR-001 gives `errors.py` its subject outright), so
    the question has a source and does not need one written for it.

    **The whole map travels every time, and the intent selects nothing.** A
    round found `where("the")` naming seven of fourteen modules and `where("a")`
    naming four: matching on shared words, articles included, produced a
    selection carrying no signal while presenting itself as *which module
    already owns this kind of code*. Fourteen one-line docstrings is a short
    answer; a wrong seven of them is not a shorter one.
    """
    reproduce = _command("where", intent)
    return Answer(tool="motus_where", spans=tuple(
        Computed(by=f"vitruvyan_motus.{module}.__doc__", reproduce=reproduce,
                 text=f"{module}.py — {summary}")
        for module, summary in _module_summaries()))


#: The advertised describing surface, named once. The test that walks it reads
#: this, so a tool added without being listed here is a tool nobody checked.
ADVERTISED = {
    "motus_classify": classify,
    "motus_review_graph": review_graph,
    "motus_review_node": review_node,
    "motus_explain": explain,
    "motus_start_here": start_here,
    "motus_where": where,
}
