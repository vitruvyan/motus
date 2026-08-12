"""Three ways a node says something went wrong, and only one of them is a bug.

    python examples/05_how_a_node_reports.py

The mistake that costs most in a first integration is treating "the check did
not pass" as an error. It is not: it is a result, and a node that raises for it
throws away the run instead of recording its outcome. Motus has a distinct
primitive for each case, and the trace reads completely differently depending
on which one you reach for.

    the source is unreachable          a real error   -> raise
    the quotation is not in the source  a result      -> Rejection + Decision
    clean, but a human must sign it     a result      -> Decision + terminal

What a node may NEVER do is swallow the second case into an empty success.
node-protocol.md section 7.1 forbids it in those words, and the reason is
visible below: a Rejection carries the evidence, so the check that failed can
be argued with. An empty result cannot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vitruvyan_motus import (
    Decision, Fact, GraphSpec, InMemoryTraceSink, NodeFailed, Rejection,
    Runtime, State,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)

SOURCE = (
    "I saw with infinite pleasure the great object of my mission; "
    "the long sought for majestic Niger, glittering to the morning sun, "
    "as broad as the Thames at Westminster, and flowing slowly to the eastward."
)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "quotation-check",
    "version": "1.0.0",
    "entry": "fetch",
    "nodes": [
        # A read of the outside world: the RESULT is the effect, and the
        # archive is no different afterwards. `recorded_effect` -- not
        # `external_effect`, which is for nodes that CHANGE something out
        # there and therefore owe a receipt before a resume may restart them.
        # Replay never re-executes it either way: re-fetching to verify a
        # trace is not verification.
        {"name": "fetch", "effect_class": "recorded_effect"},
        {"name": "check", "effect_class": "pure"},
        {"name": "publish", "effect_class": "pure"},
        {"name": "refuse", "effect_class": "pure"},
    ],
    "transitions": {
        "fetch": {"kind": "next", "to": "check"},
        # Routing runs on a recorded Decision, so the branch taken is a fact in
        # the record rather than an event that happened inside the interpreter.
        "check": {"kind": "route", "on": "verified",
                  "map": {"yes": "publish", "no": "refuse"}},
        "publish": {"kind": "terminal"},
        "refuse": {"kind": "terminal"},
    },
})


class Archive:
    """A source that is down for its first `flaky` calls, then answers."""

    def __init__(self, flaky: int = 0) -> None:
        self.flaky = flaky
        self.calls = 0

    def get(self) -> str:
        self.calls += 1
        if self.calls <= self.flaky:
            # A real error: nothing about the submission is known yet, and no
            # outcome can honestly be recorded. Raising is correct here.
            raise ConnectionError("archive.org returned 503")
        return SOURCE


def make_fetch(archive: Archive):
    def fetch(state: State) -> State:
        text = archive.get()
        # The text itself is CAPTURED into state, not left in a module global
        # for the next node to reach for. That is what makes `check` honestly
        # pure: its output depends on recorded reads and nothing else, so
        # verify-replay re-executing it is a real test. A node that reads a
        # global is pure only by declaration -- change the global, leave the
        # body alone, and replay still agrees whenever the verdict happens to
        # come out the same.
        return (state
                .with_fact(Fact("source", text, "archive.org", NOW))
                .with_fact(Fact("source_length", len(text), "archive.org", NOW)))
    return fetch


def check(state: State) -> State:
    quotation = state.metadata("quotation")
    source = state.fact("source")                # what `fetch` recorded, not SOURCE
    found = quotation in source

    if not found:
        # NOT an exception. The check ran, reached a conclusion, and the
        # conclusion is negative. `evidence` is what makes it arguable later:
        # a reader can see what was searched and how big the haystack was.
        state = state.with_rejection(Rejection(
            what="quotation", reason="not located in the source text", ts=NOW,
            evidence={"searched_characters": len(source), "quotation": quotation},
        ))

    return state.with_decision(Decision(
        "verified", "yes" if found else "no", NOW,
        reason="verbatim match" if found else "no verbatim match",
    ))


def publish(state: State) -> State:
    return state.with_fact(Fact("outcome", "published", "desk", NOW))


def refuse(state: State) -> State:
    return state.with_fact(Fact("outcome", "refused", "desk", NOW))


def run(quotation: str, *, flaky: int = 0, max_attempts: int = 1):
    archive = Archive(flaky)
    runtime = Runtime(
        SPEC,
        {"fetch": make_fetch(archive), "check": check,
         "publish": publish, "refuse": refuse},
        sink=InMemoryTraceSink(),
        # Per node, not global. Retrying a fetch is sensible; retrying a
        # decision is not, and every attempt stays in the trace either way.
        max_attempts={"fetch": max_attempts},
    )
    return runtime.run(
        State.empty("check one quotation", metadata={"quotation": quotation}),
        run_id="check",
    )


def show(title: str, result) -> None:
    print(f"--- {title}")
    print(f"    status      : {result.status}")
    for rejection in result.state.rejections:
        print(f"    rejection   : {rejection.what} — {rejection.reason}")
        print(f"                  evidence: {json.dumps(rejection.evidence)}")
    for decision in result.state.decisions:
        print(f"    decision    : {decision.key}={decision.value} ({decision.reason})")
    print(f"    outcome     : {result.state.fact('outcome')}")
    attempts = [r for r in result.trace.records
                if r["kind"] == "attempt_started" and r["node"] == "fetch"]
    print(f"    fetch tried : {len(attempts)}x")
    print()


def main() -> None:
    # 1. The quotation is there. Nothing is rejected; the run publishes.
    show("verbatim match", run("majestic Niger, glittering to the morning sun"))

    # 2. The quotation is not there. This is the case that must NOT raise.
    result = run("glittering to the evening sun")
    show("no match — a result, not an error", result)

    # The routing record keeps the road not taken, which is how a reader can
    # tell a branch that was refused from a branch that could never be reached.
    routing = [r for r in result.trace.records
               if r["kind"] == "routing" and r["after"] == "check"][0]
    print("    the routing record, in full:")
    for candidate in routing["candidates"]:
        mark = "taken" if candidate["taken"] else "offered, not taken"
        print(f"      -> {candidate['target']:8} {mark}")
    print(f"      origin: the Decision written by record {routing['origin']['seq']}\n")

    # 3. The source is down twice, then answers. A real error, retried.
    show("source down twice, 3 attempts allowed", run(
        "majestic Niger, glittering to the morning sun", flaky=2, max_attempts=3))

    # 4. The source stays down. Under the default policy the run aborts -- and
    #    the trace of every attempt survives, which is the point of raising.
    try:
        run("majestic Niger", flaky=9, max_attempts=3)
    except NodeFailed as failure:
        attempts = [r for r in failure.trace.records if r["kind"] == "attempt_started"]
        terminal = failure.trace.records[-1]
        print("--- source never answers")
        print(f"    raised      : NodeFailed({failure.args[0]})")
        print(f"    attempts    : {len(attempts)} recorded, none erased")
        print(f"    terminal    : {terminal['kind']} / {terminal['cause']['kind']}")
        print(f"    state kept  : {failure.state.intent!r}")
        print("\n    A raised attempt hands nothing back, so its writes are")
        print("    structurally empty in the trace. What it consumed before")
        print("    failing is still recorded: evidence of what it read, never")
        print("    of what it produced.")


if __name__ == "__main__":
    main()
