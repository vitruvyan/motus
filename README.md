# Vitruvyan Motus

Motus is an embeddable, trace-first graph execution runtime. A run records
what every node read, wrote and caused, including routing provenance, attempts,
time, randomness and generated identifiers. The trace is execution evidence,
not an after-the-fact log.

Motus is domain-neutral. It does not interpret facts, make epistemic claims,
provide an agent framework or absorb Vitruvyan OS modules. Consumers build
those layers above it.

> **Current release:** [Motus 0.5.0](https://github.com/vitruvyan/motus/releases/tag/v0.5.0),
> the first formal source release. It is licensed under Apache-2.0 and is not
> yet published on PyPI. Motus 0.6 is being developed around compiled topology,
> effect receipts, replay/resume and portable trace explanation.

## Why Motus

- Immutable state with write-boundary isolation and append-only history.
- A validated `GraphSpec`; invalid topology never starts.
- One interpreter and one execution semantics.
- Causal routing: the trace identifies the exact decision it observed.
- Transactional attempts: raised or cancelled attempts commit no writes.
- Explicit replay capability and recorded `ctx.now()`, `ctx.rand()` and
  `ctx.uuid()` draws.
- Separate durable (`TraceSink`), live (`Listener`) and consumer-paced
  (`StreamDriver`) observation surfaces.
- Strict RFC 8259 values and structural redaction that preserves causality
  without storing the secret.
- A bounded Axis 0.4 compatibility view for Terraveler migration.

## Install for development

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
The `vitruvyan-motus` wheel has no runtime dependencies and contains only the
`vitruvyan_motus` package; the historical `axis/` source tree is not shipped.

## Quick start

```python
from vitruvyan_motus import Fact, GraphSpec, ReplayStatus, Runtime, State


def observe(state, ctx):
    return state.with_fact(
        Fact(
            key="temperature_c",
            value=23.5,
            source="sensor:room-7",
            ts=ctx.now(),
        )
    )


spec = GraphSpec.from_dict(
    {
        "schema_version": "1.0.0",
        "name": "temperature-sample",
        "version": "1.0.0",
        "entry": "observe",
        "nodes": [
            {
                "name": "observe",
                "effect_class": "recorded_effect",
                "writes_declared": ["temperature_c"],
            }
        ],
        "transitions": {"observe": {"kind": "terminal"}},
    }
)

result = Runtime(spec, {"observe": observe}).run(
    State.empty("sample room 7"),
    replay=ReplayStatus.declared("full"),
)

print(result.state.fact("temperature_c"))
print(result.status, result.succeeded)
print(result.trace.to_json())
```

Nodes may have either `node(state)` or `node(state, ctx)` shape. Use the second
when a reproducible run needs time, randomness or generated identifiers.

## Native package surface

The public surface is explicitly listed in `vitruvyan_motus.__all__`. Its main
groups are:

- topology: `GraphSpec`, `NodeDecl`, `Transition`;
- execution: `Runtime`, `Policy`, `DurabilityProfile`, `RunResult`;
- state and values: `State`, `Fact`, native `Decision`, `Rejection`, `redact`;
- evidence: `Trace`, `ReplayStatus`, `ContextDraw`;
- observation: `TraceSink`, run-scoped `TraceRunSink`, `Listener`,
  `StreamDriver`;
- failures: `NodeFailed`, `SinkFailed`, `GraphSpecValidationError`.

The native decision and the legacy type are deliberately unambiguous:

```python
from vitruvyan_motus import Decision
from vitruvyan_motus.compat import LegacyDecision
```

`vitruvyan_motus.compat` does not export a symbol named `Decision`.

Runtime configuration and its node registry are frozen after construction so
the recorded fingerprints cannot diverge from the code and durability profile
actually used. One Runtime may be reused sequentially; overlapping runs are
refused. `Trace.to_dict()`, `.run` and `.records` return isolated values, so
even low-level mutation of a returned object cannot alter the evidence.

Node exception text is not copied into native trace v1: the trace records the
exception type and a deterministic safe message, while `NodeFailed.cause`
retains the original exception in process. This prevents an accidental secret
inside `str(exc)` from becoming persisted evidence.

Redaction hashes are deterministic, unkeyed SHA-256 evidence. They prevent the
content from entering the trace, but they do not hide equality and do not
protect low-entropy values from offline guessing. Consumers needing that
property must redact a keyed or salted token as the Value; changing the wire
hash scheme itself requires a schema decision.

Durable sinks are explicitly run-bound by ADR-004:
`TraceSink.open_run(header)` returns a `TraceRunSink` that receives only that
run's ordered record batches. A shared sink can therefore partition concurrent
or sequential runs without out-of-band knowledge, and each persisted stream
has both the header and records needed to reconstruct trace v1.

## Contract and verification

The normative surfaces live in [`contract/`](contract/):

- `graphspec.v1.schema.json` and R1-R12;
- `trace.v1.schema.json` and T/E/SB/H/J/JSONL rules;
- `node-protocol.md`;
- `guarantees.md`.

Run the suite and the executable contract validator with:

```console
python -m pytest tests/ -q
python contract/validate.py trace path/to/trace.json --spec path/to/graph.json
```

Performance evidence and the reference-environment gate are described in
[`docs/MOTUS_PERFORMANCE_STATUS.md`](docs/MOTUS_PERFORMANCE_STATUS.md).

## Release status

- **0.5.0:** complete trace-first interpreter, formal GitHub release.
- **0.6.0:** in development; compiled execution plan without a second
  semantics, effect receipts, playback/verify/resume, trace bundles and
  deterministic explanation.
- **PyPI:** intentionally not published yet. Installation remains from a
  checkout or a locally built wheel until a separate publication decision.

Hash-chain activation, budgets, MCP exposure, capability enforcement and
dynamic `PlanDelta` remain outside the 0.6 scope; they require their own
contract decisions instead of being smuggled into replay or compilation.

## Repository history

`axis/`, `orders/`, `poc/` and the older Axis documents remain byte-preserved
historical evidence. They are not the Motus native runtime and are excluded
from the wheel. Axis 0.4 remains independently pinnable for existing consumers
until they explicitly migrate to `vitruvyan_motus.compat` or the native API.

## License

Vitruvyan Motus is licensed under the
[Apache License 2.0](LICENSE). The license permits commercial and private use,
modification and distribution subject to its notice and attribution terms.
