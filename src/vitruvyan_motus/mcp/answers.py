"""An answer is made of spans, and there is nowhere in one to put prose.

ADR-022 decision 2 asks for a test that no prose lives in the server. A test
that reads the tools and judges their output is the version that decays: it
passes until somebody adds a sentence it did not think to look at.

So the rule is in the type instead, which is this project's rule about rules —
*a rule that has to be remembered is a rule that will be broken; put it in the
tool*. An answer is a sequence of spans, and a span is one of exactly three
things:

- ``Quoted`` — text that appears **verbatim** in a named citable source. It is
  checked when the span is constructed, so a quotation that has drifted from
  its document raises instead of shipping;
- ``Computed`` — text produced by running shipped code over the caller's
  artefact, carrying the command that reproduces it (decision 4a);
- ``Cannot`` — the permitted answer of decision 4d, carrying the commands that
  were tried and nothing else.

There is no fourth kind, and none of the three has a field an author could put
an opinion in: ``Quoted`` is checked against its source, ``Computed`` comes
from a program, and ``Cannot`` has no text at all.

The renderer needs a few fixed words to join spans into something readable.
They are enumerated in ``LABELS`` and constrained by a test: a label may not
contain any term of the protocol's own vocabulary, because that is what would
turn a label into a summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import sources

__all__ = ["Span", "Quoted", "Computed", "Cannot", "Answer", "LABELS"]


#: Every fixed word this module may put in a rendered answer. Enumerated so
#: that adding one is a visible diff, and constrained by test so that none of
#: them can carry protocol meaning.
LABELS: tuple[str, ...] = (
    "source",
    "computed by",
    "reproduce",
    "I cannot tell.",
    "tried",
)

#: Read by the renderers below, so that the enumeration is the thing rather
#: than a comment about it. A round found `LABELS` was read by nothing at all
#: while its docstring claimed a test constrained it — a decoration standing
#: where decision 2's second guard was supposed to be.
_SOURCE, _BY, _REPRODUCE, _CANNOT, _TRIED = LABELS


@dataclass(frozen=True)
class Span:
    """Base for the three kinds. Never constructed directly."""

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def render(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(frozen=True)
class Quoted(Span):
    """Text that is in the named source, checked here rather than promised.

    The check is at construction and not in a test because the failure it
    guards against is a document changing under a server that keeps answering.
    A test would notice on the next release; this notices on the next call.
    """

    source: str
    text: str

    def __post_init__(self) -> None:
        document = sources.read(self.source)
        if self.text not in document:
            raise ValueError(
                f"quoted text is not present in {self.source!r}: {self.text[:80]!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "quoted", "source": self.source, "text": self.text}

    def render(self) -> str:
        return f"{self.text}\n[{_SOURCE}: {self.source}]"


@dataclass(frozen=True)
class Computed(Span):
    """Text a program produced, with the command that produces it again.

    ADR-022 decision 4a, and it is the load-bearing one: a diagnostic tool is
    an authority, and an authority whose answers cannot be independently
    reproduced is the precise thing this product exists to make unnecessary.

    ``by`` names the shipped code that ran — a dotted path, so a reader can go
    and look at it. ``reproduce`` is a command line, and a test runs every one
    of them and compares the result, because a reproduce line that does not
    reproduce is worse than none.
    """

    by: str
    reproduce: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "computed", "by": self.by,
                "reproduce": self.reproduce, "text": self.text}

    def render(self) -> str:
        return (f"{self.text}\n[{_BY}: {self.by}]"
                f"\n[{_REPRODUCE}: {self.reproduce}]")


@dataclass(frozen=True)
class Cannot(Span):
    """The answer decision 4d requires the tool to be able to give.

    Guessing here costs more than silence, because a confident wrong diagnosis
    sends somebody to rewrite working code. It carries what was tried and no
    text, so there is no field in which a guess could be phrased as a hedge.
    """

    tried: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "cannot", "tried": list(self.tried)}

    def render(self) -> str:
        if not self.tried:
            return _CANNOT
        return _CANNOT + f"\n[{_TRIED}: " + "; ".join(self.tried) + "]"


@dataclass(frozen=True)
class Answer:
    """What every advertised tool returns.

    ``tool`` is here so that a caller holding a transcript can tell which tool
    produced which answer without the server having to say so in words.
    """

    tool: str
    spans: tuple[Span, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "spans": [s.to_dict() for s in self.spans]}

    def render(self) -> str:
        return "\n\n".join(span.render() for span in self.spans)

    @property
    def is_refusal(self) -> bool:
        """Whether this answer declined to reach a verdict."""
        return any(isinstance(span, Cannot) for span in self.spans)
