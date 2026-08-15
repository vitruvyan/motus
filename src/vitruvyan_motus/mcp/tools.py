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
import json
import shlex
import sys
from pathlib import Path

from .answers import Answer, Cannot, Computed, Quoted
from . import protocol, sources
from .protocol import SECTION

__all__ = ["classify", "review_graph", "review_node", "explain",
           "start_here", "where", "ADVERTISED"]

_ME = "vitruvyan_motus.mcp.tools"


def _piped(payload: str, *parts: str) -> str:
    """A reproduce line that carries the input the caller passed inline.

    `review-graph -` and `review-node -` read stdin, so the line as rendered
    blocked on a terminal or failed on empty input — and the suite masked it by
    supplying stdin itself. The spec or the source is something the caller
    already sent, so putting it back in the command crosses no boundary the
    request had not already crossed.
    """
    return ("printf %s " + shlex.quote(payload) + " | " + _command(*parts))


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

    reproduce = _piped(json.dumps(spec, sort_keys=True), "review-graph", "-")
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


#: The two call shapes that carry an effect's identity. Named rather than
#: matched loosely: the previous check asked whether `idempotency_key` appeared
#: as a keyword ANYWHERE in the node, so an unrelated call satisfied it — and
#: so did `idempotency_key=None`, which is the exact value §4.3 refuses.
_EFFECT_CALLS = ("record_effect", "EffectDescriptor")


def _effect_key_findings(tree: ast.AST) -> list[tuple[int, str]]:
    """Each effect call that cannot show §4.3 a non-empty idempotency key.

    Three verdicts, and the third is the honest one: absent, present but
    constant-empty, or an expression this cannot read. A key computed at run
    time is the normal case and is not a finding — but it is also not a
    clearance, so it is reported as unread rather than passed over.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else (
            target.id if isinstance(target, ast.Name) else "")
        if name not in _EFFECT_CALLS:
            continue
        supplied = [k for k in node.keywords if k.arg == "idempotency_key"]
        if not supplied:
            found.append((node.lineno, f"{name} passes no idempotency_key"))
            continue
        value = supplied[0].value
        if isinstance(value, ast.Constant):
            if not isinstance(value.value, str) or not value.value:
                found.append((node.lineno,
                              f"{name} passes idempotency_key={value.value!r}, "
                              "and §4.3 requires a non-empty key"))
        else:
            found.append((node.lineno,
                          f"{name} passes an idempotency_key this cannot read "
                          "from source — check it is non-empty at run time"))
    return found


def _module_state(tree: ast.AST) -> set[str]:
    """Names bound at module level by an ASSIGNMENT, and nothing else.

    A `def`, a `class` and an import are module-level bindings too, and a node
    calling a helper or constructing a `Fact` is reading them — which is
    ordinary and is not what ADR-022 decision 3 names. What it names is the
    side channel: state that lives outside the run and can differ between one
    execution and the next.
    """
    bound: set[str] = set()
    for statement in getattr(tree, "body", []):
        targets: list[ast.AST] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
            targets = [statement.target]
        for target in targets:
            for node in ast.walk(target):
                if isinstance(node, ast.Name):
                    bound.add(node.id)
    return bound


def _module_state_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """Where a function reads module-level state it does not bind itself.

    ADR-022 decision 3 names *"a `pure` node reading a module-level global"* as
    a case this tool reviews, and it was not implemented. This is that case,
    and it is structural: the read is a fact about the AST. It is reported as
    a read and never as a verdict, because a module-level constant and a
    mutable cache are indistinguishable from here — §4.1 is quoted beside it
    and the reader decides which they have.
    """
    module_state = _module_state(tree)
    if not module_state:
        return []
    found: list[tuple[int, str]] = []
    seen: set[tuple[str, str]] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local: set[str] = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                local.add(node.id)
        for group in (function.args.posonlyargs, function.args.args,
                      function.args.kwonlyargs):
            local.update(argument.arg for argument in group)
        for extra in (function.args.vararg, function.args.kwarg):
            if extra is not None:
                local.add(extra.arg)
        for node in ast.walk(function):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                    and node.id in module_state and node.id not in local
                    and (function.name, node.id) not in seen):
                seen.add((function.name, node.id))
                found.append((node.lineno,
                              f"{function.name} reads the module-level "
                              f"{node.id}"))
    return found


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
    reproduce = reproduce or _piped(source, "review-node", "-")
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

    if effect_class == "external_effect":
        keys = _effect_key_findings(tree)
        for line, detail in keys:
            spans.append(Computed(by=f"{_ME}.review_node", reproduce=reproduce,
                                  text=f"line {line}: {detail}"))
        if not keys:
            spans.append(Computed(by=f"{_ME}.review_node", reproduce=reproduce,
                                  text="no call in this source records an effect"))
        spans.append(Quoted(SECTION, protocol.clause("4.3")))

    if effect_class == "pure":
        # Scoped to the class ADR-022 decision 3 names, and not run wider. The
        # obligation being cited is §4.1's `pure` row; a `recorded_effect` node
        # reading a module-level constant breaks nothing, and reporting it
        # would be a fact nobody asked for beside a clause that does not
        # govern it. Reviewed without a declared class, this stays silent.
        reads = _module_state_reads(tree)
        for line, detail in reads:
            spans.append(Computed(by=f"{_ME}.review_node", reproduce=reproduce,
                                  text=f"line {line}: {detail}"))
        if reads:
            spans.append(Quoted(SECTION, protocol.clause("4.1")))

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
    """Which module of the kernel owns which kind of code, and which of them
    your words touch.

    The answer is the modules' own docstrings. Motus states what each file is
    for on its first line (ADR-001 gives `errors.py` its subject outright), so
    the question has a source and does not need one written for it.

    **The whole map travels every time, and the intent decides nothing** — it
    is reported as a lexical fact about the caller's text, the same shape
    `classify` took and for the same reason. Two rounds cost this tool its
    verdict: matching on shared words named seven of fourteen modules for the
    intent `"the"`, and then returning everything while keeping an `intent`
    parameter it never read. **An argument that is ignored is a lie in the
    signature**, and it is worse than the wrong selection it replaced, because
    the caller cannot see that their question was never read.
    """
    reproduce = _command("where", intent)
    summaries = _module_summaries()
    words = _words(intent)
    spans: list[object] = []

    touched = [(module, sorted(words & _words(module + " " + summary)))
               for module, summary in summaries]
    shared = {module: terms for module, terms in touched if terms}
    if shared:
        spans.append(Computed(
            by=f"{_ME}.where", reproduce=reproduce,
            text="words your text shares with a module's own line: "
                 + "; ".join(f"{module} ({', '.join(terms)})"
                             for module, terms in sorted(shared.items()))))
    else:
        spans.append(Cannot(tried=(reproduce,)))

    spans.extend(
        Computed(by=f"vitruvyan_motus.{module}.__doc__", reproduce=reproduce,
                 text=f"{module}.py — {summary}")
        for module, summary in summaries)
    return Answer(tool="motus_where", spans=tuple(spans))


# -- motus_find -------------------------------------------------------------

#: How much of a document travels with a hit. A paragraph is the unit a
#: normative document is written in, and a line is not: the sentence that
#: DEFINES a term is regularly not the line the term appears on.
_MAX_PASSAGES = 6


def _blocks(document: str) -> list[str]:
    """Paragraphs, with a fenced code block kept whole.

    Splitting on blank lines alone cut the answer to #107 in half: the
    paragraph that names the closure case carries the closure as fenced Python,
    and a fence has blank lines in it. **A passage that ends mid-sentence is
    worse than no passage** -- it reads as the whole answer.

    Line-by-line with a fence flag, not a pattern over the document: a regular
    expression matching ``` pairs has no idea which of them are inside a fence
    that started earlier, and this file has already paid once for a pattern
    answering a question about structure.
    """
    blocks: list[str] = []
    current: list[str] = []
    fenced = False
    for line in document.splitlines():
        if line.lstrip().startswith("```"):
            if not fenced and not current and blocks:
                # A fence illustrates the prose above it -- that is what a
                # fence MEANS in these documents. Quoting it away from that
                # prose hands a reader code with no sentence saying what it is.
                current = [blocks.pop(), ""]
            fenced = not fenced
            current.append(line)
            continue
        if not line.strip() and not fenced:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _passages(document: str, term: str) -> list[str]:
    """The paragraphs of ``document`` containing ``term``, longest first.

    Case-insensitive, because a reader arrives holding `opaque_config` from a
    trace and the contract may have written it inside a sentence. Substring and
    not word-boundary: `motus_config()` and `opaque_config` are the shapes
    these questions actually take, and a word boundary does not see either.
    """
    needle = term.casefold()
    hits = [block for block in _blocks(document) if needle in block.casefold()]
    # Longest first: the paragraph that DEFINES a term says more about it than
    # the one that mentions it, and length is the only signal available here
    # that does not require this server to have an opinion about meaning.
    hits.sort(key=len, reverse=True)
    return hits[:_MAX_PASSAGES]


def find(term: str) -> Answer:
    """Where a term is written down in this installation, quoted verbatim.

    The gap this closes was measured, not supposed. The first external
    integrator forfeited replay on seven of nine nodes to `opaque_config`, went
    to the MCP for the way out, and no tool could reach it: `explain` knows only
    exception class names, `where` returns the module map, and the answer was in
    `contract/node-protocol.md` all along (#107).

    **Every span is `Quoted`, so this tool cannot say anything.** A `Quoted`
    verifies at construction that its text is in the named source, so the worst
    this can do is quote the wrong paragraph -- and the caller sees the file it
    came from and can go and read the rest. That is the whole design: ADR-022
    decision 2 keeps prose out of the server, and the way to keep it out is to
    have no place to put it.

    A term that is in no citable source gets 4d's answer and the list of what
    was searched -- never a guess, and never a fallback to the internet
    (`sources.py` refuses to fetch, and a test parses this package to prove no
    network import exists).
    """
    wanted = term.strip()
    reproduce = _command("find", wanted)
    if not wanted:
        return Answer(tool="motus_find", spans=(Cannot(tried=(reproduce,)),))

    spans: list[object] = []
    for relative in sources.CITABLE:
        try:
            document = sources.read(relative)
        except sources.SourceMissing:
            # A missing source is a broken installation, and it must not look
            # like a term that is absent. The other sources still answer.
            continue
        for passage in _passages(document, wanted):
            spans.append(Quoted(source=relative, text=passage))

    if not spans:
        return Answer(tool="motus_find", spans=(
            Cannot(tried=(reproduce,)),
            Computed(by=f"{_ME}.find", reproduce=reproduce,
                     text="searched, and the term is in none of them: "
                          + ", ".join(sources.CITABLE)),))
    return Answer(tool="motus_find", spans=tuple(spans))


#: The advertised describing surface, named once. The test that walks it reads
#: this, so a tool added without being listed here is a tool nobody checked.
ADVERTISED = {
    "motus_classify": classify,
    "motus_review_graph": review_graph,
    "motus_review_node": review_node,
    "motus_explain": explain,
    "motus_start_here": start_here,
    "motus_where": where,
    "motus_find": find,
}
