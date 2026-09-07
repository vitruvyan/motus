"""Refuse a wheel whose METADATA still says something publication makes false.

ADR-033 decision 6. `pyproject.toml` declares `readme = "README.md"`, so the
long description PyPI renders as a distribution's project page IS its README,
and PyPI never allows a version to be re-uploaded — a false sentence shipped
in a version's METADATA stays on that page, for that version, permanently.

This reads the WHEEL, not the source file. A source file can be correct at
the moment someone reads it and wrong at the moment `build` last ran (or
right there and quoted inside a code fence, which a pattern over the raw file
cannot tell apart from an assertion) — only the built artifact answers what a
`pip install` actually ships. `zipfile` opens the archive and `email.parser`
reads `METADATA`, an RFC 5322-shaped file, because those are the parsers that
exist for those two formats; the repository's standing rule against a regex
over structured text applies here as much as anywhere else.

Standard library only. This tool is not shipped — `vitruvyan-motus` declares
zero runtime dependencies and a packaging test checks that against a real
wheel — but it costs nothing to hold the same discipline in a repo tool.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from email.parser import Parser
from email.message import Message
from pathlib import Path


# ---------------------------------------------------------------------------
# ADR-033's context section found thirteen sites repeating some form of these
# claims: four in README.md, nine more across docs/, demo/ and the two
# plugs/*/README.md files. Only six belong in this frozen list — not because
# the other seven are untrue today, but because they cannot reach a wheel's
# METADATA at all, so this gate has nothing to check there:
#
#   * docs/*.md and demo/*.py are neither any distribution's `readme` nor
#     declared package-data. Confirmed against a real built wheel's own file
#     list, not against pyproject.toml's promise to produce one: `docs/` and
#     `demo/` do not appear under any name in the archive.
#   * README.md IS `readme` for the root `vitruvyan-motus` distribution
#     (pyproject.toml), so its text becomes that wheel's Description verbatim
#     — confirmed by building the wheel and reading METADATA back.
#   * plugs/motus-anchor-opentimestamps/README.md and
#     plugs/motus-attest-rfc3161/README.md are each `readme` for their OWN
#     distribution's own pyproject.toml, so their text ships in THEIR wheel's
#     METADATA the day that plug is built — on its own release schedule,
#     independent of when vitruvyan-motus itself publishes.
#
# The other seven sites (docs/HANDOFF.md, docs/MCP.md, docs/SITE_ATTACK_DEMO.md
# x2, docs/TERRAVELER_MOTUS_TRON.md, demo/bundle_hiring.py,
# demo/bundle_scenarios.py) are a documentation problem: correcting them
# changes nothing a `pip install` ships, so a METADATA gate cannot see them
# and is not the right tool to hold them.
#
# Matching collapses whitespace — including embedded newlines — on both sides
# before comparing (see `_collapse_whitespace`): METADATA carries the source
# file's raw line-wrapped bytes, and Markdown treats a single newline inside
# a paragraph as a soft wrap, not a sentence break. A claim re-wrapped at a
# different column still asserts the same false thing and must still match.
FORBIDDEN_CLAIMS: dict[str, str] = {
    "README.md:21 (release-summary callout)":
        "not on PyPI — build the wheel from a checkout",
    "README.md:337 (Install for development)":
        "Motus is not on PyPI, and `pip install vitruvyan-motus` fails as written.",
    "README.md:339-340 (Install for development)":
        "Publication is a separate, explicit release action and it has not happened",
    "README.md:1097-1098 (Repository history)":
        "the `vitruvyan-axis` 0.4.0 distribution remains independently pinnable",
    "plugs/motus-anchor-opentimestamps/README.md:45 (Install)":
        "Not on PyPI, and neither is Motus.",
    "plugs/motus-attest-rfc3161/README.md:54 (Install)":
        "Not on PyPI, and neither is Motus.",
}

# Decision 6's other half: the project page must carry a way back to the
# repository, the contract that is this runtime's authority (ADR-001), and
# the licence. Labels, not URLs — the URL a label points at is free to
# change (a branch rename, a mirror); what must never regress silently is
# that a reader can reach these three at all.
REQUIRED_PROJECT_URL_LABELS = ("Repository", "Contract", "License")


def _collapse_whitespace(text: str) -> str:
    """Fold whitespace runs, including embedded newlines, to single spaces.

    Doing this before comparing is the correct reading of a Markdown
    paragraph's soft wraps, not a workaround for one: `str.split()` with no
    argument already splits on any whitespace run, so this is one join away
    from stdlib behaviour, not a hand-rolled normalisation.
    """
    return " ".join(text.split())


def read_metadata(wheel_path: Path) -> Message:
    """Parse the `*.dist-info/METADATA` inside a wheel.

    Raises `ValueError` for anything that is not a readable wheel with a
    METADATA member — callers turn that into a report line naming the file,
    not a traceback.
    """
    try:
        archive = zipfile.ZipFile(wheel_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{wheel_path}: not a readable zip archive ({exc})") from exc

    with archive:
        try:
            member = next(
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            )
        except StopIteration:
            raise ValueError(
                f"{wheel_path}: no *.dist-info/METADATA in this archive"
            ) from None
        try:
            raw = archive.read(member).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{wheel_path}: METADATA is not valid UTF-8 ({exc})") from exc

    return Parser().parsestr(raw)


def find_forbidden_claims(description: str) -> list[str]:
    """Return the FORBIDDEN_CLAIMS labels whose text appears in `description`."""
    haystack = _collapse_whitespace(description)
    return [
        label
        for label, claim in FORBIDDEN_CLAIMS.items()
        if _collapse_whitespace(claim) in haystack
    ]


def missing_project_url_labels(metadata: Message) -> list[str]:
    """Return which REQUIRED_PROJECT_URL_LABELS have no `Project-URL:` header.

    `Project-URL: <label>, <url>` is the one shape PEP 621's
    `[project.urls]` is ever turned into, so the label is exactly the text
    before the first comma.
    """
    present = set()
    for header in metadata.get_all("Project-URL", []):
        label, _, _ = header.partition(",")
        present.add(label.strip())
    return [label for label in REQUIRED_PROJECT_URL_LABELS if label not in present]


def check_wheel(wheel_path: Path) -> list[str]:
    """Return every reason `wheel_path` is not publishable (empty if none)."""
    try:
        metadata = read_metadata(wheel_path)
    except ValueError as exc:
        return [str(exc)]

    description = metadata.get_payload()
    if not isinstance(description, str):
        # None of this project's own tooling ever produces a multipart
        # METADATA; a wheel that does is not one this gate can reason about.
        return [f"{wheel_path}: METADATA body is not a single text payload"]

    problems = [
        f"{wheel_path}: forbidden claim shipped in METADATA, source {label}: "
        f"{FORBIDDEN_CLAIMS[label]!r}"
        for label in find_forbidden_claims(description)
    ]
    problems += [
        f"{wheel_path}: missing required Project-URL label: {label!r}"
        for label in missing_project_url_labels(metadata)
    ]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refuse a built wheel whose METADATA is not safe to publish "
            "(ADR-033 decision 6)."
        )
    )
    parser.add_argument(
        "wheels", nargs="+", type=Path, metavar="WHEEL",
        help="path to a built .whl to check",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    for wheel_path in args.wheels:
        problems.extend(check_wheel(wheel_path))

    if not problems:
        print(f"Publishable metadata: PASS ({len(args.wheels)} distribution(s))")
        return 0

    print("Publishable metadata: FAIL", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
