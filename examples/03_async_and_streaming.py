"""Asynchronous nodes, live streaming, and stopping a run mid-flight.

    python examples/03_async_and_streaming.py

A graph that calls anything over a network has to be async, so Motus drives
nodes either way. The important part is what *doesn't* change: there is one
state machine, and `run` and `arun` take the same path through it. The trace a
run produces is the same trace whichever way you drove it.

Streaming inverts the control: instead of waiting for a result, you consume
records as they happen — and because the runtime cannot advance until you ask
for the next one, stopping is just not asking again.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from vitruvyan_motus import Fact, GraphSpec, Runtime, State

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "enrichment",
    "version": "1.0.0",
    "entry": "fetch",
    "nodes": [
        {"name": "fetch", "effect_class": "external_effect"},
        {"name": "score", "effect_class": "pure"},
        {"name": "store", "effect_class": "external_effect"},
    ],
    "transitions": {
        "fetch": {"kind": "next", "to": "score"},
        "score": {"kind": "next", "to": "store"},
        "store": {"kind": "terminal"},
    },
})


async def fetch(state: State) -> State:
    await asyncio.sleep(0.01)          # stands in for a network call
    return state.with_fact(Fact("payload", {"signals": 7}, "upstream", NOW))


def score(state: State) -> State:
    """A graph may mix `def` and `async def` freely. This one is neither slow
    nor doing I/O, so it stays synchronous — and stays `pure`, which is what
    makes it verifiable during replay."""
    payload = state.fact("payload") or {}
    return state.with_fact(Fact("score", payload.get("signals", 0) * 10, "model", NOW))


async def store(state: State) -> State:
    await asyncio.sleep(0.01)
    return state.with_fact(Fact("stored", True, "warehouse", NOW))


NODES = {"fetch": fetch, "score": score, "store": store}


async def main() -> None:
    # --- awaiting a whole run ------------------------------------------------

    result = await Runtime(SPEC, NODES).arun(State.empty("enrich"), run_id="awaited")
    print(f"arun     : {result.status}, score={result.state.fact('score')}, "
          f"{len(result.trace.records)} records")

    # --- consuming it record by record --------------------------------------

    seen: list[str] = []
    async with Runtime(SPEC, NODES).astream(State.empty("enrich"), run_id="streamed") as driver:
        async for record in driver:
            seen.append(record["kind"])
    print(f"astream  : {len(seen)} records, ending {seen[-1]}")

    # --- stopping early ------------------------------------------------------

    # Leaving the block cancels the run. The point is what happens next: the
    # trace still ends in a terminal record saying it was cancelled, and which
    # node was in flight. A stopped run is evidence, not a gap.
    runtime = Runtime(SPEC, NODES)
    async with runtime.astream(State.empty("enrich"), run_id="stopped") as driver:
        await driver.__anext__()
        await driver.__anext__()

    terminal = driver.trace.records[-1]
    print(f"stopped  : {terminal['kind']}, reason={terminal['reason']!r}, "
          f"was running {terminal['active_attempt']['node']!r}")

    # --- an async node under the synchronous driver -------------------------

    # Refused with a message that says what to do about it, rather than an
    # AttributeError from somewhere deep inside.
    try:
        Runtime(SPEC, NODES).run(State.empty("enrich"))
    except Exception as exc:
        cause = exc.__cause__ or exc
        print(f"run()    : refused -- {cause}")


if __name__ == "__main__":
    asyncio.run(main())
