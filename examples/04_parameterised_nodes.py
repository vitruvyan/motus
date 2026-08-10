"""Configuring a node without telling the trace you cannot reproduce it.

    python examples/04_parameterised_nodes.py

A real graph is parameterised: connection strings, a ruleset version, a cache.
The obvious Python for that is a factory that closes over a config object --
and it is the one shape Motus cannot re-identify. A closure's captured state is
not a JSON value, so the run header would record a configuration nobody can
reconstruct, and `replay_capability` says so rather than pretending otherwise:
`node:<name>:opaque_config`.

Two shapes it CAN re-identify, both in node-protocol.md section 6. They are not
a workaround. They are how you say, in a form the trace can carry, WHICH
configuration ran -- and the answer lands in `graph.code_fingerprint`, so a
different config is a different fingerprint.

What this does not buy you is purity. `motus_config()` is an attestation by the
class author, taken at its word; nothing inspects what `__call__` then does.
"""

from __future__ import annotations

import functools
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from vitruvyan_motus import (
    Fact, GraphSpec, InMemoryTraceSink, ReplayStatus, Runtime, State,
)

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "desk-review",
    "version": "1.0.0",
    "entry": "check",
    "nodes": [{"name": "check", "effect_class": "pure"}],
    "transitions": {"check": {"kind": "terminal"}},
})


@dataclass(frozen=True)
class DeskConfig:
    """Whatever a node needs to be told. Strict-JSON values only."""

    ruleset_version: str = "1.4.0"
    source_root: str = "https://archive.org"


# --- 1. the shape that costs you replay --------------------------------------

def make_check(config: DeskConfig):
    """A factory closing over the config: opaque to the runtime."""

    def check(state: State) -> State:
        return state.with_fact(Fact("ruleset", config.ruleset_version, "desk", NOW))

    return check


# --- 2. a partial over strict-JSON keywords ----------------------------------

def check_partial(state: State, *, ruleset_version: str, source_root: str) -> State:
    """A module-level function; the config arrives as fingerprintable keywords."""
    return state.with_fact(Fact("ruleset", ruleset_version, "desk", NOW))


# --- 3. a callable instance that attests its own configuration ---------------

class Check:
    """The shape for config that is not a flat bag of keywords."""

    def __init__(self, config: DeskConfig) -> None:
        self.config = config

    def motus_config(self) -> dict:
        """Pure, total, cheap, strict-JSON. Read at construction and each run."""
        return asdict(self.config)

    def __call__(self, state: State) -> State:
        return state.with_fact(Fact("ruleset", self.config.ruleset_version, "desk", NOW))


def run(node) -> tuple[str, list[str], str]:
    runtime = Runtime(SPEC, {"check": node}, sink=InMemoryTraceSink())
    result = runtime.run(
        State.empty("review submission 27"),
        run_id="review-27",
        # Without a declaration there is nothing to keep: capability is `none`
        # and the constraint is `undeclared`. Reproducibility is claimed, then
        # honoured or downgraded -- never assumed.
        replay=ReplayStatus.declared("full"),
    )
    replay = result.trace.records[-1]["replay"]
    fingerprint = result.trace.header["run"]["graph"]["code_fingerprint"]
    return replay["capability"], replay["constraints"], fingerprint


def main() -> None:
    config = DeskConfig()
    shapes = {
        "closure over the config ": make_check(config),
        "functools.partial       ": functools.partial(
            check_partial,
            ruleset_version=config.ruleset_version,
            source_root=config.source_root,
        ),
        "callable + motus_config ": Check(config),
    }

    print("declared: full\n")
    for label, node in shapes.items():
        capability, constraints, _ = run(node)
        print(f"  {label} -> {capability:7}  {constraints}")

    # --- the config is IN the fingerprint, which is the point ----------------

    print("\nthe same node, two configurations:\n")
    for version in ("1.4.0", "1.5.0"):
        _, _, fingerprint = run(Check(DeskConfig(ruleset_version=version)))
        print(f"  ruleset {version} -> {fingerprint}")

    print(
        "\nDifferent configuration, different code_fingerprint -- so a reader can\n"
        "tell whether two runs were judged by the same rules, and a replay that\n"
        "claims to reproduce one of them can be checked against the right one."
    )


if __name__ == "__main__":
    main()
