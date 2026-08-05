# Vitruvyan Motus

> **Orchestrate intelligence. Preserve the evidence.**

Motus is an embeddable, trace-first graph orchestration runtime for intelligent
systems.

Unlike orchestration frameworks that treat observability as an afterthought,
Motus produces a structured, contract-validatable trace together with every
result. The trace describes how the execution progressed, what each node read
and wrote, which decision selected a route, and which effects were observed.

The trace is not reconstructed from logs after execution.

**The trace is part of the execution itself.**

> **Current release:** [Motus 0.6.0](https://github.com/vitruvyan/motus/releases/tag/v0.6.0)
>
> Apache-2.0 · dependency-free runtime · source release · not yet published on PyPI

## The problem

Modern AI applications rarely consist of a single model or function. A single
request may involve:

- intent classification;
- retrieval;
- specialist agents;
- tools and APIs;
- reasoning models;
- routing decisions;
- validation;
- response composition.

An orchestration runtime coordinates this execution. Most runtimes answer one
question:

> *How do I execute a graph?*

Motus answers a second:

> *How can I later demonstrate exactly how that execution happened?*

## What Motus does

Given a validated graph, a node registry, and an initial state, Motus:

1. validates the topology;
2. compiles it into immutable execution data;
3. executes nodes under one interpreter and one execution semantics;
4. isolates each attempt;
5. commits only successful writes;
6. applies routing from recorded decisions;
7. captures reads, writes, retries, context draws, effects, and receipts;
8. returns both the resulting state and the trace.

Execution and evidence are produced together.

## What makes Motus different

Traditional observability usually reconstructs execution afterwards from
logs, callbacks, and telemetry. **Motus records causal evidence while
execution is happening. That evidence is a native runtime output.**

The UI does not invent the story.

**The runtime already knows the story.**

## Intelligence lives in the nodes

Motus deliberately remains domain-neutral. A node may invoke:

- ordinary Python code;
- an LLM or AI agent;
- a rules engine;
- a retrieval pipeline;
- a database query;
- an external API;
- a human-approval system.

Motus does not interpret the business domain, certify facts as true, or impose
an agent framework. Its responsibility is to orchestrate execution, preserve
state integrity, apply routing, and record causal evidence.

> **Nodes think. Motus orchestrates.**

## Why graphs

Graphs express execution in which different inputs require different paths.
Nodes may branch, terminate, fail, or retry. Motus applies explicit execution
semantics while preserving structural causal evidence under the run's declared
durability profile.

```text
User request
      │
      ▼
+------------------+
|  Motus Runtime   |
+------------------+
      │
      ▼
    Node A
      │
      ▼
    Node B ───────────────► Decision recorded
      │
      ▼
    Node C
      │
      ▼
 Final result  +  Execution trace
```

## What the evidence can establish

A complete Motus trace can establish, within the recorded execution:

- which GraphSpec and graph version were used;
- which nodes ran and in which causal order;
- which state values they read;
- which writes were committed;
- which attempts failed, retried, or were cancelled;
- which recorded decision selected each route;
- which context draws supplied time, randomness, or generated identifiers;
- which effects were observed and which adapter-supplied receipts were recorded;
- how the final committed state emerged.

Motus does **not** certify that external information is objectively true. A
`Fact` is a recorded assertion, not verified truth. An effect receipt is
adapter-supplied evidence, not proof invented by the runtime.

Motus certifies neither legal compliance nor exactly-once delivery. It
preserves the execution evidence from which operators, auditors, and domain
systems can perform their own assessment.

## Design principles

- **Execution first.** Graphs exist to execute work.
- **Evidence by design.** Execution produces structured evidence natively.
- **Immutable state.** Writes create new state; committed history is append-only.
- **Causal routing.** A route names the exact recorded decision it observed.
- **Transactional attempts.** Raised or cancelled attempts commit no writes.
- **Declared nondeterminism.** Time, randomness, and generated identifiers can
  be recorded through `RunContext`.
- **Domain neutrality.** Business meaning belongs to consumers and nodes.
- **One execution semantics.** Compilation changes lookup cost, not behavior.
- **Embeddability.** Motus integrates into existing Python applications.
- **Minimal core.** No LLM SDK, database, or agent-framework dependency.

## Where Motus is useful

**Motus is designed for systems where execution matters and may later need to
be inspected or reconstructed:**

- AI and multi-agent orchestration;
- RAG and knowledge pipelines;
- automated decision support;
- enterprise workflows and approvals;
- research and scientific pipelines;
- security operations and incident response;
- financial-services workflows;
- insurance and claims processing;
- healthcare and life-sciences workflows;
- public-sector and critical-infrastructure automation;
- compliance-sensitive processes.

### Regulated and audit-sensitive environments

Some sectors have legal or regulatory duties concerning record keeping,
traceability, supervision, or reconstruction of decisions. **Motus can provide
run-level technical evidence for systems operating in those environments.**

- **Investment services and capital markets.** MiFID II Article 16(6) requires
  investment firms to keep records of services, activities, and transactions
  sufficient for competent-authority supervision. Commission Delegated
  Regulation (EU) 2017/565 further addresses retention and the recording of
  client orders, decisions to deal, transactions, and order processing. A
  Motus trace can help preserve the internal execution path that led to an
  automated workflow decision alongside the firm's legally required records.
- **High-risk AI systems in the European Union.** Articles 12, 19, and 26 of
  the EU AI Act establish logging and log-retention duties for covered
  high-risk systems. Motus can supply detailed application-level causal
  records as one component of the wider logging, monitoring, and governance
  architecture.
- **Privacy and data governance.** GDPR accountability and records of
  processing activities operate at an organisational level. Motus traces can
  complement—not replace—those records by showing how a particular automated
  run accessed and transformed declared state.
- **Healthcare, life sciences, insurance, public administration, and critical
  infrastructure.** These domains may be subject to additional jurisdiction-
  and use-case-specific requirements for validation, retention, access
  control, signatures, incident evidence, or human oversight. Motus provides
  execution provenance but does not decide which requirements apply.

Relevant primary sources:

- [Directive 2014/65/EU (MiFID II)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32014L0065)
- [Commission Delegated Regulation (EU) 2017/565](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0565)
- [Regulation (EU) 2024/1689 (EU AI Act)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689)
- [Regulation (EU) 2016/679 (GDPR)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679)

Motus alone is not a compliance system. A regulated deployment still needs
the appropriate persistent `TraceSink`, retention policy, access controls,
security controls, clock governance, privacy measures, review procedures, and
any legally required signatures or validated storage. The current release
does not activate cryptographic hash-chain integrity.

## Install for development

Motus is not yet published on PyPI. Install it from a checkout:

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

On Windows, use `.venv\Scripts\python.exe` in place of
`.venv/bin/python`. The wheel contains only `vitruvyan_motus`, includes
`py.typed`, and declares no runtime dependencies.

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
                "effect_class": "pure",
                "writes_declared": ["temperature_c"],
            }
        ],
        "transitions": {"observe": {"kind": "terminal"}},
    }
)

runtime = Runtime(spec, {"observe": observe})
result = runtime.run(
    State.empty("sample room 7"),
    replay=ReplayStatus.declared("full"),
)

print(result.state.fact("temperature_c"))
print(result.status, result.succeeded)
print(result.trace.to_json())
```

Nodes may have either `node(state)` or `node(state, ctx)` shape. Use
`RunContext` when a reproducible run needs time, randomness, generated
identifiers, or explicit effect evidence.

## Routing is recorded causally

Routes dispatch on native `Decision` values. The trace records the decision's
exact origin, the candidate routes, the selected target, and the outcome.

```python
from vitruvyan_motus import Decision


def classify(state, ctx):
    return state.with_decision(
        Decision("risk", "review", ctx.now(), reason="score above threshold")
    )
```

```python
transitions = {
    "classify": {
        "kind": "route",
        "on": "risk",
        "map": {"accept": "approve", "review": "human_review"},
        "default": "reject",
    },
    "approve": {"kind": "terminal"},
    "human_review": {"kind": "terminal"},
    "reject": {"kind": "terminal"},
}
```

Before resume, persisted routing is recomputed against the bundled GraphSpec
and committed state. An edited route cannot redirect execution merely because
its forged target is another declared node.

## Replay and portable evidence

Motus 0.6 provides three explicit replay operations:

```python
from vitruvyan_motus import ReplayEngine, TraceBundle

bundle = TraceBundle(spec, result.trace)
engine = ReplayEngine(bundle)

# Reconstruct committed state without executing node code.
restored = engine.playback()

# Re-execute pure nodes against their recorded context draws.
verified = engine.verify({"observe": observe})

# Produce deterministic explanation data and standalone offline HTML.
explanation = bundle.explain()
viewer_html = bundle.to_html()
```

`ReplayEngine.resume(runtime)` starts a new, causally linked run segment from
an incomplete trace. Persisted history is never rewritten. Resume fails closed
for graph mismatches, inconsistent routing, ambiguous boundaries, and external
effects without both a non-empty idempotency key and a completed adapter
receipt. Motus never claims exactly-once delivery.

## Observation and durability

Motus separates three surfaces:

- `TraceSink`: run-bound durable evidence;
- `Listener`: isolated, synchronous live observation; callbacks can delay the
  runner and cancellation remains trace-visible;
- `StreamDriver`: consumer-paced execution with explicit backpressure.

The run header declares one durability profile:

- `in-memory`: no persistence guarantee after process loss;
- `buffered`: durable up to the last confirmed flush, with a declared loss window;
- `synchronous`: commit is gated by sink acknowledgement, subject to the
  persistent medium's own guarantees.

A required sink failure prevents logical success. If the required sink itself
fails, the final failure record is necessarily best-effort because the
component responsible for persisting it is unavailable.

## Performance profile

Motus 0.6.1 has a separately characterized native profile based on five
independent AMD EPYC 9V74 / Python 3.10.12 runs:

- 51.2301 microseconds per node for a realistic 1,000-node full trace;
- 3.43542 milliseconds overhead for a 100-node no-op run;
- 1.749x cold trace materialization versus `json.dumps`;
- 3.47% positive superlinear accumulation in the measured profile;
- 3,002 trace records and zero declaration violations, with no sampling.

The CI gate recomputes aggregates from the committed raw evidence, verifies
the runner identity, and rejects regressions beyond the ADR-006 ceilings using
the corrected ADR-007 method. See
[`docs/MOTUS_PERFORMANCE_STATUS.md`](docs/MOTUS_PERFORMANCE_STATUS.md).

## Contract and verification

The normative surfaces live in [`contract/`](contract/):

- `graphspec.v1.schema.json` and rules R1-R12;
- `trace.v1.schema.json` and the T/E/SB/H/J/JSONL rules;
- `node-protocol.md`;
- `guarantees.md`.

Run the complete suite and contract validator with:

```console
python -m pytest tests/ -q
python contract/validate.py trace path/to/trace.json --spec path/to/graph.json
python benchmarks/check_slo_baseline.py --candidate benchmarks/candidate-v0.6.1-epyc-py310.json
```

## Native package surface

The public API is explicitly listed in `vitruvyan_motus.__all__`:

- topology: `GraphSpec`, `NodeDecl`, `Transition`, `CompiledPlan`;
- execution: `Runtime`, `Policy`, `DurabilityProfile`, `RunResult`;
- state and values: `State`, `Fact`, native `Decision`, `Rejection`, `redact`;
- replay: `TraceBundle`, `ReplayEngine`, `ReplayResult`, `ReplayStatus`;
- effects: `EffectDescriptor`, `EffectReceipt`, `EffectClass`;
- observation: `TraceSink`, `TraceRunSink`, `Listener`, `StreamDriver`;
- failures: `NodeFailed`, `SinkFailed`, `UnsafeResume`, `ReplayMismatch`.

The native and legacy decision types are deliberately unambiguous:

```python
from vitruvyan_motus import Decision
from vitruvyan_motus.compat import LegacyDecision
```

## Shipped in 0.6

- immutable compiled topology without a second execution semantics;
- effect receipts and fail-closed external-effect resume;
- playback, pure-node verification, and causally linked resume;
- deterministic explanation and portable trace bundles;
- standalone offline HTML trace viewer;
- strict read/write declaration enforcement;
- additive trace schema 1.1;
- executable performance regression gate.

## Future direction

Future capabilities may extend Motus without silently changing its execution
semantics. Candidate areas include:

- activated cryptographic trace integrity;
- capability enforcement;
- execution budgets;
- dynamic but auditable graph evolution;
- declared concurrent topology;
- distributed scheduling;
- richer developer tooling and visualization;
- MCP exposure after the evidence and explanation surfaces are stable.

These are directions, not commitments. Each contract-sensitive capability
requires its own ADR and executable tests.

## Repository history

`axis/`, `orders/`, `poc/`, and older Axis documents remain byte-preserved
historical evidence. They are not the native Motus runtime and are excluded
from the wheel. Axis 0.4 remains independently pinnable for existing consumers
until they explicitly migrate to `vitruvyan_motus.compat` or the native API.

## License

Vitruvyan Motus is licensed under the [Apache License 2.0](LICENSE). It permits
commercial and private use, modification, and distribution subject to its
notice and attribution terms.

---

> **Motus orchestrates intelligent execution and preserves the evidence of how every result was produced.**

Execution is transient.

**Evidence is designed to outlive it.**
