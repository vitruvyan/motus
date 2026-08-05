"""x5-07 -- `finish` is "optional on the sink's side", except to the type.

ADR-011 Decision 2: "**Optional on the sink's side.** The runtime calls
`finish` when the session provides it... A sink that omits it behaves as
before and forfeits only the distinction."

But `TraceRunSink` is `@runtime_checkable`, is exported in
`vitruvyan_motus.__all__`, and the package ships `py.typed`.  Adding `finish`
to the Protocol body makes it STRUCTURALLY REQUIRED for both
`isinstance(...)` and any static checker.  The package's own
`_InMemoryRunSink` -- what the exported `InMemoryTraceSink.open_run` returns,
annotated `-> TraceRunSink` -- does not satisfy it.
"""

from __future__ import annotations

from vitruvyan_motus import InMemoryTraceSink, Runtime, State, TraceRunSink, TraceSink
from vitruvyan_motus.observers import _InMemoryRunSink

from x5_common import LINEAR, Report, SYNC, registry


class LegacyRunSink:
    """A pre-ADR-011 session: exactly the protocol as ADR-004 published it."""

    def __init__(self) -> None:
        self.records = []

    def write(self, records) -> None:
        self.records.extend(records)


class LegacySink:
    def open_run(self, header) -> TraceRunSink:
        return LegacyRunSink()      # a static checker must now reject this


def main() -> int:
    r = Report("x5-07 'optional' vs the runtime_checkable Protocol")

    legacy = LegacyRunSink()
    r.record(
        "a sink that omits finish still satisfies isinstance(TraceRunSink)",
        isinstance(legacy, TraceRunSink),
        f"isinstance(LegacyRunSink(), TraceRunSink) = "
        f"{isinstance(legacy, TraceRunSink)} -- ADR-011 calls finish optional, "
        f"the runtime_checkable Protocol makes it required",
    )

    ims = InMemoryTraceSink()
    session = ims.open_run({"schema_version": "1.1.0", "run": {}})
    r.record(
        "the package's own InMemoryTraceSink returns a conforming TraceRunSink",
        isinstance(session, TraceRunSink),
        f"InMemoryTraceSink.open_run is annotated `-> TraceRunSink` and returns "
        f"{type(session).__name__}; isinstance = {isinstance(session, TraceRunSink)}; "
        f"hasattr(finish) = {hasattr(_InMemoryRunSink, 'finish')}",
    )

    r.record(
        "InMemoryTraceSink still satisfies isinstance(TraceSink)",
        isinstance(ims, TraceSink),
        f"{isinstance(ims, TraceSink)}",
    )

    # ...and yet the legacy sink runs perfectly: the runtime does NOT use the
    # Protocol for its check, so behaviour and type disagree.
    ls = LegacySink()
    res = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=ls).run(
        State.empty("legacy")
    )
    r.record(
        "the runtime accepts the legacy sink anyway (duck-typed, not isinstance)",
        res.status == "completed",
        f"status={res.status} -- so a downstream `assert isinstance(s, "
        f"TraceRunSink)` or a mypy gate now rejects a sink the runtime happily "
        f"drives",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
