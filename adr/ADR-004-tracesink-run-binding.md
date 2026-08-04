# ADR-004 — Run-scoped TraceSink binding

| | |
|---|---|
| Status | **ACCEPTED** |
| Date | 2026-08-04 |
| Deciders | Davide Baldoni, founder · Codex, implementation and adversarial review |
| Approved by | Davide Baldoni, founder — 2026-08-04 |
| Amends | `contract/guarantees.md` §6, TraceSink protocol only |

## Context

Trace v1 is a header plus an ordered record sequence. The accepted 0.5
`TraceSink.write(records)` surface delivered only records. Records deliberately
do not repeat `run_id`, graph fingerprint, policy, replay declaration or
durability profile, because those facts belong to the header.

Consequently a durable sink could not reconstruct a complete trace document
from the protocol alone, and two runtimes sharing one sink could not partition
their records without out-of-band knowledge. Treating a sink instance as
implicitly single-run would hide an architectural precondition and would make
concurrent use unsafe.

## Decision

`TraceSink` is the factory for a run-scoped durable session:

```python
class TraceSink(Protocol):
    def open_run(self, header: dict[str, object]) -> TraceRunSink: ...

class TraceRunSink(Protocol):
    def write(self, records: tuple[dict[str, object], ...]) -> None: ...
```

The runtime calls `open_run` exactly once after constructing the trace header
and before attempting to persist the first record. The header delivered to the
sink is isolated from the runtime's evidence. Every subsequent batch for that
run goes only to the returned `TraceRunSink`.

Opening a required run session is part of the durability promise. Refusal is a
sink failure and prevents logical success exactly like refusal of a record.
The returned session owns partitioning and persistence for one run; the
terminal record closes the logical sequence. Version 0.5 adds no separate
`close()` acknowledgement because terminal acceptance is already the success
boundary defined by invariant II.

`in-memory` runs do not require or open an external sink. Buffered and
synchronous profiles require `open_run` and a run session.

## Consequences

- A sink receives everything needed to persist an independently verifiable
  trace: one header and one ordered, complete record sequence.
- One TraceSink may safely create several sequential or concurrent run
  sessions without mixing their evidence.
- The 0.5 API is intentionally incompatible with the pre-release write-only
  draft. Motus 0.5 has not been published, so no public migration debt exists.
- Trace schema 1.0.0, GraphSpec, execution rules and record cardinality do not
  change.
- Sink mutation remains non-intervening: both header and record batches are
  isolated before delivery.

## Verification

The implementation must prove:

1. the sink receives a header structurally equal to `Trace.run`;
2. two runs opened on the same sink remain separately addressable;
3. header mutation by a sink cannot alter the returned Trace;
4. `open_run` refusal prevents logical success;
5. synchronous and buffered delivery retain ordered, complete records;
6. the full contract, Terraveler compatibility and isolated-wheel suites stay
   green.

## Approval

Accepted by Davide Baldoni on 2026-08-04 with the instruction to proceed with
the ADR, commit and push Motus 0.5, and publish the first milestone tag. The
license and PyPI publication remain separate later decisions.
