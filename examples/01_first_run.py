"""A first run, and the evidence it leaves behind.

    python examples/01_first_run.py

A Motus graph is two things kept apart on purpose: a **GraphSpec**, which
declares the shape of the work and what each step is allowed to do, and the
**node functions** that do it. The runtime executes the spec, not the code — so
what a run was permitted to do is a fact you can read, not a convention you
have to trust.

Every run produces a trace: an ordered account of what happened, complete
enough for someone who was not there to check it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from vitruvyan_motus import Decision, Fact, GraphSpec, Runtime, State

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)

# ---------------------------------------------------------------- the spec

# `effect_class` is a claim about what the node does, and the runtime holds it
# to that claim. "pure" means: no outside world, same inputs give the same
# answer — which is what makes a node re-executable during replay.
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "triage",
    "version": "1.0.0",
    "entry": "assess",
    "nodes": [
        # A declared write set is optional -- but when you declare one, the
        # runtime checks it against what actually happened and fails the node
        # if they disagree. Decisions count as writes, like facts do.
        {"name": "assess", "effect_class": "pure",
         "writes_declared": ["amount", "risk"]},
        {"name": "approve", "effect_class": "pure"},
        {"name": "review", "effect_class": "pure"},
    ],
    # `route` branches on a decision a node recorded. The mapping lives in the
    # spec, so the branch taken is visible in the trace as a routing record --
    # not buried in an `if` no reader can see.
    "transitions": {
        "assess": {
            "kind": "route",
            "on": "risk",
            "map": {"low": "approve", "high": "review"},
            "default": "review",
        },
        "approve": {"kind": "terminal"},
        "review": {"kind": "terminal"},
    },
})


# ---------------------------------------------------------------- the nodes

def assess(state: State) -> State:
    """A node receives a State and returns a new one. It never mutates."""
    amount = state.metadata("amount") or 0
    return (
        state
        .with_fact(Fact("amount", amount, "request", NOW))
        .with_decision(Decision("risk", "low" if amount < 1000 else "high", NOW))
    )


def approve(state: State) -> State:
    return state.with_fact(Fact("outcome", "approved", "policy", NOW))


def review(state: State) -> State:
    return state.with_fact(Fact("outcome", "sent to review", "policy", NOW))


# ---------------------------------------------------------------- run it

def main() -> None:
    runtime = Runtime(SPEC, {"assess": assess, "approve": approve, "review": review})

    for amount in (250, 5000):
        result = runtime.run(
            State.empty("triage a request", metadata={"amount": amount}),
            run_id=f"request-{amount}",
        )

        print(f"\namount {amount}")
        print(f"  status   : {result.status}")
        print(f"  outcome  : {result.state.fact('outcome')}")

        # The trace is the point. Every step is here, in order, with the
        # branch that was taken and why.
        routing = next(r for r in result.trace.records if r["kind"] == "routing")
        print(f"  routed on: {routing['on']}={routing['value']!r} -> {routing['selected']}")
        print(f"  records  : {len(result.trace.records)}")

    print(
        "\nNothing above inspected the node functions to find out what happened."
        "\nIt all came from the trace, which is what a third party would read."
    )


if __name__ == "__main__":
    main()
