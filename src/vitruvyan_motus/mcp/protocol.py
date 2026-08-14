"""Reading `contract/node-protocol.md` §4.4, rather than restating it.

The table in §4.4 classifies named operations, and this module parses it. That
is the whole design: if a row changes, the answer changes; if the section is
deleted, every tool that depends on it raises rather than falling back to what
the author remembered it saying.

**Why a parse and not a pattern over the file.** A regular expression matches
characters and knows nothing about what they mean, so over structured text it
answers a different question from the one asked. The exception this project
allows is a single opaque token whose grammar we own, and a markdown inline
code span is exactly that: our own document, our own delimiter, one token. So
the terms are lifted by scanning for backticks and everything else — finding
the section, splitting the table, reading a row — goes through the table's
structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from . import sources

__all__ = ["Row", "table", "SECTION", "STRICTNESS", "strictest",
           "terms_in", "clause", "ambient_terms", "rows_of", "ProtocolUnreadable"]

SECTION = "contract/node-protocol.md"

#: 4.1's three classes in the order §4.4 calls strictest-first. Read from the
#: document below rather than trusted from here: this tuple only fixes the
#: order in which they are compared, and a test asserts every name in it is a
#: class the document actually defines.
STRICTNESS: tuple[str, ...] = ("external_effect", "recorded_effect", "pure")


class ProtocolUnreadable(RuntimeError):
    """§4.4 is not in the installed document in a shape this can read.

    Raised rather than defaulted. A server that answers a classification
    question when it cannot find the classification table is answering from
    memory, which is the one thing ADR-022 forbids.
    """


@dataclass(frozen=True)
class Row:
    """One line of §4.4's table.

    ``line`` is the raw markdown row, kept so that an answer can quote it
    verbatim and have the quotation verified against the document.
    """

    terms: tuple[str, ...]
    effect_class: str
    cost: str
    line: str


def terms_in(cell: str) -> tuple[str, ...]:
    """The inline-code terms of a table cell, in order.

    A markdown inline code span is a single opaque token in a grammar we own,
    which is the one place this project permits character-level scanning.
    """
    found: list[str] = []
    rest = cell
    while True:
        opened = rest.find("`")
        if opened < 0:
            break
        closed = rest.find("`", opened + 1)
        if closed < 0:
            break
        token = rest[opened + 1:closed].strip()
        if token:
            found.append(token)
        rest = rest[closed + 1:]
    return tuple(found)


def _cells(line: str) -> list[str]:
    """The cells of a markdown table row, without its outer pipes."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ProtocolUnreadable(f"not a table row: {line!r}")
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def rows_of(text: str, columns: int) -> tuple[tuple[str, ...], ...]:
    """The body rows of the first markdown table in ``text``.

    One reader for every table the protocol carries, because two readers is how
    two tables come to disagree about what a row is. ``columns`` is asserted
    rather than inferred: a table that grew a column is a document change the
    caller must decide about, not one to absorb silently.
    """
    body: list[tuple[str, ...]] = []
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = _cells(stripped)
            if len(cells) != columns:
                raise ProtocolUnreadable(
                    f"table row has {len(cells)} columns and this reader is "
                    f"written for {columns}: {stripped!r}")
            if set(cells[1]) <= set("-: "):     # the header underline
                started = True
                continue
            if not started:                      # the header itself
                continue
            body.append(tuple(cells))
            continue
        if started and stripped:
            break                                # the table ended
    return tuple(body)


@lru_cache(maxsize=1)
def table() -> tuple[Row, ...]:
    """§4.4's rows, parsed from the installed document.

    Cached for the process, for the same reason ``sources.read`` is: an answer
    may consult it several times and the document does not move underneath a
    running server in any case worth designing for.
    """
    rows: list[Row] = []
    for cells in rows_of(clause("4.4"), columns=3):
        declared = terms_in(cells[1])
        if len(declared) != 1:
            raise ProtocolUnreadable(
                f"§4.4 row names {len(declared)} effect classes: {cells!r}")
        rows.append(Row(terms=terms_in(cells[0]),
                        effect_class=declared[0],
                        cost=cells[2],
                        line="| " + " | ".join(cells) + " |"))
    if not rows:
        raise ProtocolUnreadable(
            f"§4.4 carries no rows in {SECTION!r}: this installation's "
            "protocol document does not classify any operation")
    return tuple(rows)


def strictest(classes: set[str]) -> str:
    """The class §4.4 gives a node whose work falls in more than one row.

    *Mutating is not cancelled by also reading* — the document's sentence, and
    the reason this is a fold over ``STRICTNESS`` rather than a first match.
    """
    for candidate in STRICTNESS:
        if candidate in classes:
            return candidate
    raise ProtocolUnreadable(
        f"§4.4 named classes this reader does not rank: {sorted(classes)}")


def _is_clause_start(line: str) -> bool:
    """Whether a line opens a numbered clause like ``4.2.`` or ``6.1.``.

    The document numbers its own clauses, so this reads that numbering rather
    than matching a shape: the first whitespace-delimited token, with its
    trailing dot removed, must be dot-separated digits.
    """
    token = line.split(" ", 1)[0].rstrip(".")
    parts = token.split(".")
    return len(parts) >= 2 and all(part.isdigit() for part in parts)


def clause(number: str) -> str:
    """The text of one numbered clause of the protocol, verbatim.

    Quoting a clause by its number is what keeps an answer's citation
    checkable: the reader is told which sentence, not which document. A clause
    that is not in the installed file raises — a tool whose citation has been
    deleted must fail, not paraphrase.
    """
    document = sources.read(SECTION)
    lines = document.splitlines()
    opening = f"{number}."
    for index, line in enumerate(lines):
        if line.startswith(opening) and _is_clause_start(line):
            body = [line]
            for following in lines[index + 1:]:
                if following.startswith("## ") or _is_clause_start(following):
                    break
                body.append(following)
            return "\n".join(body).rstrip()
    raise ProtocolUnreadable(
        f"clause {number} is not in {SECTION!r} in this installation")


@lru_cache(maxsize=1)
def ambient_terms() -> tuple[tuple[str, str], ...]:
    """§6.4's ambient draws paired with the mediated form 6.1 requires.

    Not a list this module keeps. §6.4 is where the protocol names the draws,
    so a row added there is a draw the node reviewer starts finding, with no
    code change here — and a row deleted there is one it stops claiming.
    """
    found: list[tuple[str, str]] = []
    for cells in rows_of(clause("6.4"), columns=2):
        mediated = terms_in(cells[1])
        if len(mediated) != 1:
            raise ProtocolUnreadable(
                f"§6.4 row names {len(mediated)} mediated forms: {cells!r}")
        for draw in terms_in(cells[0]):
            found.append((draw, mediated[0]))
    if not found:
        raise ProtocolUnreadable(
            "§6.4 names no ambient draws in this installation")
    return tuple(found)
