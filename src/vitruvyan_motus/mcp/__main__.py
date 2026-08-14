"""The command line every answer's `reproduce` line points at.

ADR-022 decision 4a asks that a diagnosis ship the command that reproduces it,
and the reason is not convenience: an authority whose answers cannot be checked
independently is what Motus exists to make unnecessary. That obligation is
taken here for the describing tools too, so no answer this server gives is one
the caller has to take on trust.

A test runs every reproduce line this package emits and compares the result
with the answer that carried it. A reproduce line that does not reproduce is
worse than none.
"""

from __future__ import annotations

import argparse
import json
import sys

from .answers import Answer
from . import diagnose as diagnose_module
from . import tools

__all__ = ["main"]


def _read(source: str) -> str:
    """The text of a path, or of stdin when the argument is ``-``."""
    return sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog="Every answer names its source or the code that produced it.")
    sub = parser.add_subparsers(dest="command", required=True)

    classify = sub.add_parser("classify", help="the effect class of an operation")
    classify.add_argument("description")

    graph = sub.add_parser("review-graph", help="the runtime's own verdict on a GraphSpec")
    graph.add_argument("file", help="a JSON file, or - for stdin")

    node = sub.add_parser("review-node", help="what the protocol says about a node's calls")
    node.add_argument("file", help="a Python file, or - for stdin")
    node.add_argument("--effect-class", default=None)

    explain = sub.add_parser("explain", help="what a Motus error means")
    # Optional, because an answer that could not name the error carries the
    # names it does know and a reproduce line for that list. A flag would have
    # been a second spelling of the same request.
    explain.add_argument("error", nargs="?", default="")

    sub.add_parser("start-here", help="the shape of a Motus program")

    where = sub.add_parser("where", help="which module already owns this kind of code")
    where.add_argument("intent")

    diagnose = sub.add_parser("diagnose", help="run the shipped code over an artefact")
    diagnose.add_argument("path", help="a filesystem path; content is refused")
    diagnose.add_argument("--symptom", default="")

    parser.add_argument("--json", action="store_true",
                        help="the answer as structured spans")
    args = parser.parse_args(argv)

    if args.command == "classify":
        answer = tools.classify(args.description)
    elif args.command == "review-graph":
        answer = tools.review_graph(json.loads(_read(args.file)))
    elif args.command == "review-node":
        answer = tools.review_node(_read(args.file), effect_class=args.effect_class)
    elif args.command == "explain":
        answer = tools.explain(args.error)
    elif args.command == "start-here":
        answer = tools.start_here()
    elif args.command == "where":
        answer = tools.where(args.intent)
    else:
        answer = diagnose_module.diagnose(args.path, symptom=args.symptom)

    _print(answer, as_json=args.json)
    # Exit 0 whether or not a rule fired. This program reports what the shipped
    # code said; the caller decides what a finding is worth, and an exit code
    # that called it a failure would be this server entering the merits.
    return 0


def _print(answer: Answer, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(answer.to_dict(), indent=2, sort_keys=True))
    else:
        print(answer.render())


if __name__ == "__main__":       # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
