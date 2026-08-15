"""The documents an answer may come from, and the refusal when one is absent.

ADR-022 decision 1: every answer is derived at call time from a source in the
repository or from an artefact the caller supplied, never from memory. This
module is the repository half. It does two things and refuses a third:

- it resolves a repository-relative path in both layouts Motus is read in — a
  checkout, where ``contract/`` sits beside ``src/``, and an installation,
  where the same documents travel inside the package;
- it reads that path, and **raises when it is absent** rather than falling back
  to anything. A missing source is a broken installation, and a server that
  answers anyway is answering from memory;
- it never fetches. Not from GitHub, not from a documentation site, not from a
  newer release. An answer derived from ``main`` while the caller runs 0.9.0 is
  wrong in the most convincing way available. The absence of any network import
  in this package is asserted by a test that parses the package rather than
  trusting this paragraph.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SourceMissing", "read", "resolve", "CITABLE"]


class SourceMissing(LookupError):
    """A source an answer would cite is not present in this installation.

    Deliberately not a subclass of ``MotusError``: nothing about the runtime
    has failed. The server's own distribution is incomplete, and the caller
    should be told that rather than handed an answer with a citation that
    cannot be checked.
    """


#: Everything the advertised tools may cite, enumerated rather than discovered.
#:
#: Enumeration is the point. A glob would let a citation appear because a file
#: exists, and adding a source would stop being a decision anybody sees. A test
#: asserts every entry resolves in a checkout, which is what makes a typo here
#: fail loudly instead of at the first call that needed it — and a second test
#: asserts every entry is actually cited by some tool, because a document
#: packaged that nothing quotes is weight in the wheel no decision put there.
CITABLE: tuple[str, ...] = (
    "contract/node-protocol.md",
    "contract/README.md",
    "adr/ADR-022-the-integration-mcp.md",
    "examples/01_first_run.py",
    "examples/04_parameterised_nodes.py",
    "examples/06_effects_and_receipts.py",
)


def _roots() -> tuple[Path, ...]:
    """Where a repository-relative path may live, most specific first.

    Installed, the documents travel inside the package: ``contract/`` is
    already mapped there as ``vitruvyan_motus.contract`` and the rest joins it.
    In a checkout the package is ``src/vitruvyan_motus``, so the repository
    root is two levels up. Both are tried, and neither is guessed at from an
    environment variable — a source the installation lacks must fail, and an
    override is a way to make it stop failing without making it true.
    """
    package = Path(__file__).resolve().parent.parent
    return (package, package.parent.parent)


def resolve(relative: str) -> Path:
    """The file for a repository-relative path, or raise ``SourceMissing``."""
    if relative not in CITABLE:
        # Not a permission check on the filesystem — it is a check that the
        # answer being built cites something this server declared it would.
        raise SourceMissing(
            f"{relative!r} is not a citable source; see mcp/sources.py CITABLE")
    for root in _roots():
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise SourceMissing(
        f"{relative!r} is not present in this installation of Motus. "
        f"Looked in: {', '.join(str(r) for r in _roots())}")


def read(relative: str) -> str:
    """The text of a citable source, read from disk on every call.

    **This was cached, and the cache was a defect that voided the ADR.** An
    adversarial round demonstrated it: a long-lived server kept answering
    `external_effect` after the row saying so had been edited to say otherwise,
    kept quoting a line that was no longer in the file, and kept answering
    after the document had been **deleted** — while the reproduce line it had
    just handed the caller, run fresh, gave the opposite verdict.

    ADR-022 decision 1 is unambiguous: *if the source changes the answer
    changes; if the source is deleted the tool fails instead of inventing.* A
    process-lifetime cache makes both sentences false for the only process that
    matters, because an MCP server over stdio lives as long as the session and
    `pip install -U` rewrites exactly these files underneath it.

    The cost of removing it is a few file reads per tool call, on a call that
    already crossed a process boundary. That was never the trade it looked
    like: what the cache actually bought was a snapshot, and a snapshot is the
    thing this component exists not to serve from.
    """
    return resolve(relative).read_text(encoding="utf-8")
