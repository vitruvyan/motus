"""Attack A/B: does max_transitions actually bound a cyclic execution?

R11 forces every cyclic GraphSpec to declare max_transitions -- it is THE
safety limit for a cycle.  runtime.py counts `committed_transitions`, which is
incremented only when an attempt RETURNS.  Under EXPLORATION a raised attempt
takes disposition "continue" and routes onward without incrementing the
counter.  Hypothesis: a cyclic spec whose nodes always raise never reaches the
limit and never terminates.

The seeded decision keeps the cycle live: routing reads it from the initial
state, so no commit is ever needed to keep going round.
"""

from __future__ import annotations

import signal
import sys
from datetime import datetime, timezone

from vitruvyan_motus import Decision, GraphSpec, Policy, Runtime, State

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "cycle",
        "version": "1.0.0",
        "entry": "a",
        "max_transitions": 4,
        "nodes": [
            {"name": "a", "effect_class": "pure"},
            {"name": "b", "effect_class": "pure"},
            {"name": "z", "effect_class": "pure"},
        ],
        "transitions": {
            "a": {"kind": "route", "on": "k", "map": {"loop": "b", "stop": "z"}},
            "b": {"kind": "next", "to": "a"},
            "z": {"kind": "terminal"},
        },
    }
)

SEED = State.new("attack", decisions=[Decision("k", "loop", NOW)])


def boom(state):
    raise RuntimeError("node always fails")


def ok(state):
    return state


def run_guarded(label, runtime, state, seconds=5):
    def alarm(signum, frame):
        raise TimeoutError("run() did not terminate")

    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(seconds)
    try:
        result = runtime.run(state)
    except TimeoutError:
        signal.alarm(0)
        trace = runtime.trace
        print(f"{label:14}: DID NOT TERMINATE in {seconds}s")
        print(f"{'':14}  records accumulated =", len(trace.records))
        print(f"{'':14}  terminal present    =",
              any(r["kind"] in ("run_completed", "run_failed", "run_cancelled")
                  for r in trace.records))
        return None
    except Exception as exc:
        signal.alarm(0)
        print(f"{label:14}: raised {type(exc).__name__}: {exc}")
        return "raised"
    signal.alarm(0)
    print(f"{label:14}: status={result.status} records={len(result.trace.records)} "
          f"cause={result.trace.records[-1].get('cause', {}).get('kind')}")
    return result


def main() -> int:
    # Control 1: every node commits under EXPLORATION -> the limit stops it.
    run_guarded("control-ok", Runtime(SPEC, {"a": ok, "b": ok, "z": ok},
                                      policy=Policy.EXPLORATION), SEED)
    # Control 2: same under STRICT.
    run_guarded("control-strict", Runtime(SPEC, {"a": ok, "b": ok, "z": ok},
                                          policy=Policy.STRICT), SEED)
    # Attack: every attempt raises under EXPLORATION -> nothing ever commits.
    outcome = run_guarded(
        "attack",
        Runtime(SPEC, {"a": boom, "b": boom, "z": boom}, policy=Policy.EXPLORATION),
        SEED,
    )
    return 1 if outcome is None else 0


if __name__ == "__main__":
    sys.exit(main())
