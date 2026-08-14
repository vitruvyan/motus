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


def _refuse_inline(artefact: str) -> Answer:
    return Answer(tool="motus_diagnose", spans=(
        Quoted(ADR, _DECISION_4E),
        Cannot(tried=()),
    ))


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
    reproduce = ("python -m vitruvyan_motus.contract.validate "
                 f"{kind} {shlex.quote(str(path))}")
    by = f"vitruvyan_motus.contract.validate.{checker.__name__}"

    spans: list[object] = []
    violations = checker(document)
    documented = _rule_rows()
    quoted: set[str] = set()
    for violation in violations:
        # Rule and path only: the message may quote the value that broke the
        # rule, and the reproduce line above gives the caller all of it locally.
        spans.append(Computed(by=by, reproduce=reproduce,
                              text=f"{violation.rule} at {violation.path}"))
        if violation.rule in documented and violation.rule not in quoted:
            quoted.add(violation.rule)
            spans.append(Quoted(CONTRACT, documented[violation.rule]))
    if not violations:
        spans.append(Computed(by=by, reproduce=reproduce,
                              text=f"{kind}: no violation"))

    if kind == "trace":
        root = validate.derived_root(document)
        spans.append(Computed(
            by="vitruvyan_motus.contract.validate.derived_root",
            reproduce=reproduce,
            text=f"derived root: {root}" if root else "derives no root"))
        records = document.get("records")
        if isinstance(records, list):
            terminal = records[-1].get("kind") if records and isinstance(
                records[-1], dict) else None
            spans.append(Computed(
                by=by, reproduce=reproduce,
                text=f"{len(records)} records, last kind {terminal!r}"))
    return tuple(spans)


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
        return _refuse_inline(artefact)

    text = path.read_text(encoding="utf-8", errors="replace")
    spans: list[object] = []

    document: object = None
    parsed_json = True
    try:
        document = json.loads(text)
    except ValueError:
        parsed_json = False

    if parsed_json:
        kinds = _identify(document)
        if len(kinds) == 1:
            spans.extend(_json_diagnosis(path, kinds[0], document))
        else:
            spans.append(Cannot(tried=(
                "python -m vitruvyan_motus.contract.validate "
                f"<kind> {shlex.quote(str(path))}",)))
            spans.append(Computed(
                by=f"{__name__}._identify",
                # This tool, on this path. A reproduce line whose job is to
                # show the caller how the artefact was identified has no
                # shorter honest form than the identification itself.
                reproduce=("python -m vitruvyan_motus.mcp diagnose "
                           + shlex.quote(str(path))),
                text=("matches no contract surface" if not kinds
                      else "matches more than one contract surface: "
                           + ", ".join(kinds))))
    else:
        try:
            ast.parse(text)
        except SyntaxError:
            spans.append(Cannot(tried=(f"json.loads({path.name})",
                                       f"ast.parse({path.name})")))
        else:
            spans.extend(tools.review_node(text).spans)

    if symptom:
        explained = tools.explain(symptom)
        if not explained.is_refusal:
            spans.extend(explained.spans)

    return Answer(tool="motus_diagnose", spans=tuple(spans))
