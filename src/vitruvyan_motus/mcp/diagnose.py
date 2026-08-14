"""The debug capability: the caller's artefact, our code, and what it said.

ADR-022 decision 4. Three of its clauses shape everything here:

**4e — the artefact is named by a path, never sent as content.** The argument is
used only as a filesystem path, so a server on another machine cannot work
rather than working while it leaks. That is the shape of the argument and not a
rule somebody has to remember.

**4a — every diagnosis ships the command that reproduces it.** A diagnostic
tool is an authority, and an authority whose answers cannot be independently
reproduced is the precise thing this product exists to make unnecessary.

**4c — structure, never payload.** This is where 4a and 4c stop being in
tension and start paying for each other: a violation's *message* can quote the
value that broke the rule, so the diagnosis reports the rule and the path and
withholds the message — and the reproduce line hands the caller the whole
verdict, message included, **on their own machine**. Withholding is not
withholding from them. It is withholding from the wire.
"""

from __future__ import annotations

import ast
import json
import shlex
import sys
from pathlib import Path

from .answers import Answer, Cannot, Computed, Quoted
from . import protocol, sources, tools

__all__ = ["diagnose", "ADR"]

ADR = "adr/ADR-022-the-integration-mcp.md"
CONTRACT = "contract/README.md"

#: ADR-022's own sentence, quoted when the refusal fires so that a caller meets
#: the decision rather than this module's opinion of it.
_DECISION_4E = ("So `motus_diagnose` takes a **filesystem path the server "
                "process can open**,\nand refuses inline artefact content.")


def _rule_rows() -> dict[str, str]:
    """Each commitment rule and the line `contract/README.md` describes it with.

    A violation reports a rule identifier, and an identifier is not an answer.
    The description belongs to the contract, so it is quoted from there rather
    than restated here — and a rule the document has not documented simply
    arrives without one, which is honest and visible.
    """
    document = sources.read(CONTRACT)
    marker = "| Rule | What it refuses |"
    start = document.find(marker)
    if start < 0:
        return {}
    rows: dict[str, str] = {}
    for cells in protocol.rows_of(document[start:], columns=2):
        named = protocol.terms_in(cells[0])
        if len(named) == 1:
            rows[named[0]] = "| " + " | ".join(cells) + " |"
    return rows


def _validate_module():
    from vitruvyan_motus.contract import validate
    return validate


def _kinds() -> dict[str, frozenset[str]]:
    """Each contract surface and the top-level keys its schema requires.

    Read from the installed schemas rather than listed here, so that a surface
    whose required keys change is recognised by the new ones. It also means a
    schema this installation lacks simply stops being a kind this can name,
    which is decision 1 applied to identification.
    """
    validate = _validate_module()
    directory = Path(validate.__file__).resolve().parent
    found: dict[str, frozenset[str]] = {}
    for path in sorted(directory.glob("*.v1.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        required = document.get("required")
        if isinstance(required, list) and required:
            found[path.name.split(".", 1)[0]] = frozenset(required)
    return found


def _identify(document: object) -> tuple[str, ...]:
    """Which contract surfaces this document could be, by its own required keys.

    Returns every match rather than the first. A first match would be this
    project's oldest defect shape — *the first match won where several were
    possible* — and a document satisfying two schemas is a fact the caller
    needs, not one for this function to resolve by ordering.
    """
    if not isinstance(document, dict):
        return ()
    present = frozenset(document)
    return tuple(sorted(kind for kind, required in _kinds().items()
                        if required <= present))


def _refuse_inline() -> Answer:
    """The 4e refusal, for a string that carries the artefact instead of naming it."""
    return Answer(tool="motus_diagnose", spans=(
        Quoted(ADR, _DECISION_4E),
        Cannot(tried=()),
    ))


def _refuse_unopenable(artefact: str) -> Answer:
    """A path this process cannot open, said as that and not as 4e.

    A round found every non-file answered *"refuses inline artefact content"* —
    a typo'd filename, a directory, a dangling symlink. The answer asserted
    something false about the caller's request and hid the commonest real
    cause. Failing closed is about refusing to ACCEPT content; it never
    required mislabelling why a path failed.
    """
    return Answer(tool="motus_diagnose", spans=(
        Computed(by="pathlib.Path.is_file",
                 reproduce=(shlex.quote(sys.executable)
                            + " -m vitruvyan_motus.mcp diagnose "
                            + shlex.quote(artefact)),
                 text="not a file this process can open"),
        Cannot(tried=(f"open({artefact!r})",)),
    ))


def _trace_command(path: Path) -> str:
    """A line that reproduces the root and the record count.

    `validate <kind> <file>` prints violations and nothing else, so the spans
    carrying `derived root` and the record summary were quoting a command that
    does not produce them — a reproduce line that does not reproduce, which 4a
    calls worse than none.

    It is this tool's own CLI rather than a one-liner, and the reason is worth
    stating because 4a's phrasing is *"without us"*. A one-liner would have to
    reach for either `Trace.from_json`, which refuses any trace document
    carrying an extra top-level key and so crashes on real archives, or the
    validator's strict loader, which is private and should not be pressed into
    a line handed to a caller. What the caller gets instead is shipped code on
    their own machine, run from a shell, with no server involved — and the
    violation spans above still carry the validator's own command, so the half
    that can be checked against a different program still is.
    """
    return (shlex.quote(sys.executable) + " -m vitruvyan_motus.mcp diagnose "
            + shlex.quote(str(path)))


def _root_span(document: dict, by: str, reproduce: str) -> Computed:
    """What `derived_root` returned, and why — the two cases it distinguishes.

    Reporting `derives no root` for both collapses a distinction the function
    itself makes: below schema 3.0.0 the digests do not cover `prev_hash`, so
    the terminal hash covers one record rather than the run and **there is no
    root to have** (ADR-019). A customer holding a conformant 1.x archive was
    being handed the sentence written for a corrupted trace.
    """
    validate = _validate_module()
    root = validate.derived_root(document)
    if root:
        text = f"derived root: {root}"
    else:
        version = document.get("schema_version")
        text = ("derives no root"
                if version == "3.0.0"
                else f"derives no root: schema_version {version!r} is below "
                     "3.0.0, where a terminal digest covers one record rather "
                     "than the run — there is none to have")
    return Computed(by=by, reproduce=reproduce, text=text)


def _violation_spans(violations, by: str, reproduce: str) -> list[object]:
    """Rule and path for each violation, with the rule's own description once.

    The message is withheld: it can quote the value that broke the rule, and
    the reproduce line gives the caller the whole verdict on their own machine.
    """
    documented = _rule_rows()
    quoted: set[str] = set()
    spans: list[object] = []
    for violation in violations:
        spans.append(Computed(by=by, reproduce=reproduce,
                              text=f"{violation.rule} at {violation.path}"))
        if violation.rule in documented and violation.rule not in quoted:
            quoted.add(violation.rule)
            spans.append(Quoted(CONTRACT, documented[violation.rule]))
    return spans


def _json_diagnosis(path: Path, kind: str, document: dict) -> tuple[object, ...]:
    """What the shipped validator says about a document of a known kind."""
    validate = _validate_module()
    checker = {
        "trace": validate.validate_trace,
        "graphspec": validate.validate_graphspec,
        "commitment": validate.validate_commitment,
        "checkpoint": validate.validate_checkpoint,
        "receipt": validate.validate_receipt,
    }[kind]
    reproduce = (shlex.quote(sys.executable)
                 + " -m vitruvyan_motus.contract.validate "
                 f"{kind} {shlex.quote(str(path))}")
    by = f"vitruvyan_motus.contract.validate.{checker.__name__}"

    violations = checker(document)
    spans: list[object] = _violation_spans(violations, by, reproduce)
    if not violations:
        spans.append(Computed(by=by, reproduce=reproduce,
                              text=f"{kind}: no violation"))

    if kind == "trace":
        derived = _trace_command(path)
        spans.append(_root_span(document, "vitruvyan_motus.trace.Trace.root", derived))
        records = document.get("records")
        if isinstance(records, list):
            terminal = records[-1].get("kind") if records and isinstance(
                records[-1], dict) else None
            spans.append(Computed(
                by=by, reproduce=derived,
                text=f"{len(records)} records, last kind {terminal!r}"))
    return tuple(spans)


def _jsonl_diagnosis(path: Path, raw: str) -> tuple[object, ...]:
    """The JSONL half of the trace surface, which used to be read as Python.

    `JsonlTraceSink` is the only durable sink Motus ships, and its output has
    no schema file of its own — so `_identify` could not name it, `json.loads`
    refused it, and control reached `ast.parse`, **which succeeds**: a JSON
    object literal is a valid Python expression. Every trace the shipped sink
    writes was answered `I cannot tell` with a command about reviewing a node.
    """
    validate = _validate_module()
    reproduce = (shlex.quote(sys.executable)
                 + " -m vitruvyan_motus.contract.validate jsonl "
                 f"{shlex.quote(str(path))}")
    by = "vitruvyan_motus.contract.validate.validate_jsonl"
    violations, document = validate.validate_jsonl(raw)
    spans: list[object] = _violation_spans(violations, by, reproduce)
    if not violations:
        spans.append(Computed(by=by, reproduce=reproduce, text="jsonl: no violation"))
    if isinstance(document, dict):
        spans.append(_root_span(document, by, reproduce))  # JSONL has no such line
    return tuple(spans)


def _looks_like_a_stream(raw: str) -> bool:
    """Whether this text is a JSONL stream rather than one JSON document.

    Structural and cheap: more than one non-empty line, and the first one is a
    JSON object on its own. It is not a guess about content — a document that
    passes this and then fails `validate_jsonl` gets that validator's verdict,
    which is the right answer either way.
    """
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        return isinstance(json.loads(lines[0]), dict)
    except ValueError:
        return False


def diagnose(artefact: str, symptom: str = "") -> Answer:
    """Run the shipped code over the caller's artefact and report what it said.

    ``artefact`` is a filesystem path this process can open. It is never
    artefact content: decision 4e, and the refusal quotes it.

    ``symptom`` is what the caller observed. It never steers the analysis —
    that would be reasoning about the artefact from memory with an artefact
    attached — but when it names a Motus error, that error's own docstring
    joins the answer.
    """
    path = Path(artefact)
    try:
        is_file = path.is_file()
    except (OSError, ValueError):
        # A path long enough or malformed enough to make the OS refuse is not
        # a path, and is very likely content that arrived where a path belongs.
        is_file = False
    if not is_file:
        return (_refuse_inline() if _carries_content(artefact)
                else _refuse_unopenable(artefact))

    validate = _validate_module()
    try:
        # Bytes, then an explicit decode — never `read_text`, and never
        # `errors="replace"`. `validate.main` carries the same instruction and
        # the reason: the file is what the contract judges, and a reader that
        # substitutes U+FFFD for a byte the contract refuses has laundered the
        # artefact before the rules ran. A round produced a trace with one
        # invalid byte that this reported as verifying while its own reproduce
        # line exited 2.
        raw = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return Answer(tool="motus_diagnose", spans=(
            Cannot(tried=(f"{path.name}: decode utf-8",)),))
    except OSError:
        return _refuse_unopenable(artefact)

    spans: list[object] = []
    unidentified_reproduce = (shlex.quote(sys.executable)
                              + " -m vitruvyan_motus.mcp diagnose "
                              + shlex.quote(str(path)))

    document: object = None
    parsed = True
    try:
        # The shipped strict reader, not `json.loads`. A round handed this a
        # document containing `records` twice — two readings, one of which it
        # reported as verifying with a derived root, while the reproduce line
        # refused it as J1. Running the shipped RULES over a document produced
        # by a reader that is not the shipped READER is not running shipped
        # code over the artefact.
        document = validate._loads_strict(raw)
    except validate.NonCanonicalNumberError:
        spans.append(Computed(by="vitruvyan_motus.contract.validate._loads_strict",
                              reproduce=unidentified_reproduce, text="J2 at $"))
        parsed = False
    except validate.StrictJSONError:
        spans.append(Computed(by="vitruvyan_motus.contract.validate._loads_strict",
                              reproduce=unidentified_reproduce, text="J1 at $"))
        parsed = False
    except RecursionError:
        return Answer(tool="motus_diagnose", spans=(
            Cannot(tried=(f"{path.name}: strict JSON parse (nesting)",)),))
    except ValueError:
        document = None
        parsed = False

    if parsed:
        kinds = _identify(document)
        if len(kinds) == 1:
            spans.extend(_json_diagnosis(path, kinds[0], document))
        else:
            spans.append(Cannot(tried=(
                shlex.quote(sys.executable)
                + " -m vitruvyan_motus.contract.validate "
                f"<kind> {shlex.quote(str(path))}",)))
            spans.append(Computed(
                by=f"{__name__}._identify",
                reproduce=unidentified_reproduce,
                text=("matches no contract surface" if not kinds
                      else "matches more than one contract surface: "
                           + ", ".join(kinds))))
    elif not spans and _looks_like_a_stream(raw):
        spans.extend(_jsonl_diagnosis(path, raw))
    elif not spans:
        try:
            ast.parse(raw)
        except (SyntaxError, ValueError, RecursionError):
            spans.append(Cannot(tried=(f"{path.name}: strict JSON parse",
                                       f"{path.name}: JSONL stream",
                                       f"{path.name}: Python source")))
        else:
            spans.extend(tools.review_node(
                raw,
                reproduce=(shlex.quote(sys.executable)
                           + " -m vitruvyan_motus.mcp review-node "
                           + shlex.quote(str(path)))).spans)

    if symptom:
        explained = tools.explain(symptom)
        if not explained.is_refusal:
            spans.extend(explained.spans)

    return Answer(tool="motus_diagnose", spans=tuple(spans))


def _carries_content(artefact: str) -> bool:
    """Whether this string is an artefact rather than a name for one.

    Only the message differs — the mechanism is the same either way, because
    the argument is used **only** as a path and never as content. This decides
    which true sentence the caller is told, not whether they are refused.
    """
    if "\n" in artefact or len(artefact) > 512:
        return True
    stripped = artefact.strip()
    return stripped.startswith(("{", "[")) and stripped.endswith(("}", "]"))
