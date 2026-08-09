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

> **Current release:** [Motus 0.8.1](https://github.com/vitruvyan/motus/releases/tag/v0.8.1)
> · `pip install vitruvyan-motus`
>
> Apache-2.0 · stdlib-only kernel · validator included

0.8.1 **still does not pass its own cumulative performance gate**, and ships
under ADR-018 rather than by weakening it. Every failing ratio, measured
against the v0.6.1 anchor across three independent dispatches:

| metric | cumulative | budget |
|---|---:|---:|
| Per-node overhead | **+84.4 %** | +20 % |
| 100-node no-op | **+123.2 %** | +20 % |
| Trace materialization | **+25.2 %** | +20 % |

The cost is the integrity chain (ADR-017), paid in 0.8.0 and unchanged here:
0.8.1 against 0.8.0 is +0.3 %, −1.5 % and −0.4 %, inside the per-release budget
with no exception declared. On the 100-node no-op the paired spread is 106 %
— wider than the effect — so on that metric the measurement cannot answer, and
says so rather than pretending.

**And against a real request it is not visible.** ADR-012 pre-registered the
test — executor share of run wall-clock, under 1 % — before any measurement
existed. Measured on 2026-08-09 against a live `api_graph` service with a real
user query: **3.1 ms**, which is 0.017 % of that request and **0.070 %** of the
fastest response the service gave all day. That figure is an upper bound: it
contains the consumer's own node code, not only Motus.

Both things are true and neither cancels the other. The engine is genuinely
twice the cost it was at v0.6.1 on nodes that do nothing, and a consumer whose
nodes are pure computation will pay that in full. Issue #38 stays open.

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
any legally required signatures or validated storage. Since 0.8.0 the trace
carries a cryptographic hash chain and a per-trace root (ADR-017); what it does
NOT carry is an **anchor** — a root published where the operator cannot rewrite
it — and without one the chain proves internal consistency, not immutability
(issue #51).

## Install for development

Motus is on PyPI. Install it the ordinary way:

```console
pip install vitruvyan-motus
```

To work on Motus itself, from a checkout:

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

On Windows, use `.venv\Scripts\python.exe` in place of
`.venv/bin/python`. The wheel contains `vitruvyan_motus`, `py.typed`, and the
contract validator with the two schemas it checks against.

Two claims about dependencies, and they are not the same claim:

- the **kernel** imports nothing outside the standard library — no LLM SDK, no
  database driver, no agent framework — and a test proves it against a running
  interpreter rather than against this sentence;
- the **distribution** installs one thing, `jsonschema`, which the shipped
  validator needs.

The validator ships because a trace nobody can check is a log. It runs as
`motus-validate` or as `python -m vitruvyan_motus.contract.validate`, in a
process that need not be the one that produced the evidence:

```console
motus-validate trace run.json --spec graph.json
motus-validate jsonl run.jsonl
```

The contract prose and the frozen conformance fixtures stay in this
repository. The reader who needs those is already reading it; the validator is
needed by everyone, without having to know it exists.

## Where to put your code

Motus never sees your filesystem. `Runtime` receives a mapping of node names to
callables; which module, package, or directory they came from is invisible to
it, and no contract clause constrains it. What follows is a convention that has
worked, not a rule — Motus cannot check it and does not try.

```
your_project/
  graphs/
    review/
      spec.py       # the GraphSpec: nodes, effect classes, declarations, routes
      nodes.py      # the functions that do the work
    ingest/
      spec.py
      nodes.py
```

The split that earns its keep is **spec apart from nodes**. The spec is the
file someone opens to learn what a graph is permitted to do — which steps
exist, what each one may read and write, which effects it may perform, and
where each branch can lead — without reading a line of logic. Keeping it in its
own file makes the directory tree say the same thing the trace says.

A node is an ordinary function. There is no base class to inherit and no
registration step: a graph that calls a model is a node whose body calls a
model, declared `recorded_effect` because it reaches outside. Motus has no
notion of an agent, and needs none.

## Run something

Three examples, each standalone and each printing what it did:

```console
python examples/01_first_run.py          # a graph, a run, and the trace it left
python examples/02_durable_evidence.py   # write evidence to disk, then check it without trusting the writer
python examples/03_async_and_streaming.py # async nodes, live records, stopping mid-run
```

The second one is the one to read if you only read one. It writes a run to a
file, validates that file from a **separate process** using only the published
contract, shows a truncated copy being refused, and then replays it against
changed code to watch the mismatch get caught.

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

`reads_declared` / `writes_declared` are optional, and declaring them is
all-or-nothing: the runtime checks a declaration against everything the node
actually read or wrote, so a node that touches anything it left out fails. A
partial declaration is worse than none — none is not checked; a partial one is,
and it fails (`node-protocol.md` §3.2).

Nodes may have either `node(state)` or `node(state, ctx)` shape. Use
`RunContext` when a reproducible run needs time, randomness, generated
identifiers, or explicit effect evidence.

Ambient nondeterminism enters through the context, and the context's sources
are injected at the `Runtime`, so a node never reaches for the wall clock
itself — which is what lets the same run be replayed:

```python
from datetime import datetime, timezone

def observe(state, ctx):
    return state.with_fact(
        Fact("seen_at", ctx.now().isoformat(), "sensor", ctx.now())
    )  # also: ctx.rand() -> float, ctx.uuid() -> str

runtime = Runtime(
    spec, {"observe": observe},
    clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),  # fixed time
    identity=lambda: "00000000-0000-4000-8000-000000000000",  # fixed ids
    random_source=lambda: 0.5,                                 # fixed randomness
)
```

Every draw is recorded, so `verify` re-runs the node against the exact values
it saw. Omit the sources and the run still executes; it simply declares no
reproducibility, which the trace records rather than pretending otherwise.

## Synchronous or asynchronous, one semantics

A graph that calls anything over a network has to be asynchronous, so Motus
drives nodes either way:

```python
result = runtime.run(state)            # answers on the calling thread
result = await runtime.arun(state)     # awaits nodes that are awaitable

with runtime.stream(state) as driver:          # records as they happen
    for record in driver:
        ...

async with runtime.astream(state) as driver:   # the same, awaited
    async for record in driver:
        ...
```

A graph may mix `def` and `async def` nodes freely.

What does **not** change is the part that matters. There is one state machine.
`_execute` does not call nodes — it yields an invocation request and receives
the outcome back, so the synchronous and asynchronous drivers are about twenty
lines each and differ only in how they obtain a node's result. `guarantees.md`
invariant I forbids co-equal engines and requires any alternative path to prove
trace-equivalence per release, in CI; here there is no second engine to
diverge, so the invariant holds **by construction rather than by test**. The
439 tests that predated the asynchronous surface pass against it with zero
edits, which is the evidence that inversion changed how the machine is driven
and not what it decides.

An `async def` node handed to `run()` is refused by name — *"node 'fetch' is
asynchronous; drive it with Runtime.arun() or Runtime.astream()"* — rather than
failing somewhere deep with an `AttributeError` about a coroutine.

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

Motus provides three explicit replay operations:

```python
from vitruvyan_motus import ReplayEngine, TraceBundle

bundle = TraceBundle(spec, result.trace)
engine = ReplayEngine(bundle)

# Reconstruct committed state without executing node code.
restored = engine.playback()

# Re-execute pure nodes against their recorded context draws.
verified = engine.verify({"observe": observe})

# The same comparison, awaited -- so a node that is both `async def` and
# `pure` is verifiable too. One comparison state machine, two drivers, for
# the same reason execution has two: a second copy could drift from the first.
verified = await engine.averify({"observe": observe})

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
- `StreamDriver` / `AsyncStreamDriver`: consumer-paced execution with explicit
  backpressure; the runtime cannot advance until you ask for the next record.

`JsonlTraceSink` is the shipped durable sink. It writes one JSONL document per
run — the trace header on line one, one record per line — in the exact form
`contract/validate.py` accepts, and `fsync`s by default because that is what
the `synchronous` profile promises. A run that reaches its terminal lands as
`<run>.jsonl`; one cut short lands as `<run>.partial.jsonl`; one whose process
died mid-write stays `<run>.jsonl.part`, because nothing ever declared it over.
A truncated account is still evidence — it just is not a whole one, and the
name says so.

It is written against the sink protocol and nothing else: it imports no schema
version and inspects no record kind, which is enforced by a test. If the
protocol were insufficient, that file could not exist.

The run header declares one durability profile:

- `in-memory`: no persistence guarantee after process loss;
- `buffered`: durable up to the last confirmed flush, with a declared loss window;
- `synchronous`: commit is gated by sink acknowledgement, subject to the
  persistent medium's own guarantees.

A profile-required or explicitly supplied sink failure prevents logical
success. If the required sink itself
fails, the final failure record is necessarily best-effort because the
component responsible for persisting it is unavailable.

Which is why every outcome answers a second question, separately from whether
the run succeeded:

```python
result = runtime.run(state, run_id="r1")
if result.evidence != "persisted":
    # the run has an outcome; its durable account does not
```

`RunResult.evidence`, and the same attribute on `NodeFailed` and `SinkFailed`,
is one of `persisted`, `incomplete`, or `not-required` — no sink was configured,
so nothing durable was promised. It is deliberately not folded into the status.
A node that raised while the archive was also down has two facts to report, and
the more important one is the node: replacing it would tell a caller its
archive is unavailable while hiding that its model never answered.

`incomplete` means what is stored is an honest prefix, and a validator reading
that artifact reports `T3/INCOMPLETE`. The sink was always told, through
`finish(complete=False)`; the caller — the only party still able to retry, alert
or withhold the result — was not, on any terminal but `run_completed`. Both are
now read from the same fact, after the final flush, so they cannot disagree.

## Performance profile

**A release is gated on how much slower it is than the release before it**, not
on an absolute ceiling. Both halves are measured in the same CI job on the same
host, interleaved, so machine speed cancels out (ADR-012).

0.8.0 against v0.7.0 — three independent dispatches, canonical value is the
median of the job ratios:

| metric | canonical | across jobs | ceiling |
|---|---:|---:|---:|
| per-node overhead | **+70.3 %** | +58.3 … +72.6 % | +78 % |
| 100-node no-op overhead | **+97.3 %** | +81.4 … +98.5 % | +105 % |
| trace materialization | **+25.6 %** | +23.1 … +30.7 % | +35 % |

**Every row exceeds the +10 % per-release budget, and the reason is the
integrity chain**: 0.8.0 hashes every record, and on nodes that do no work that
cost is most of the measurement. In absolute terms it is **+65 microseconds per
node** — invisible against a node that calls a model, a doubling of the engine
on pure computation.

They ship as scoped, machine-enforced exceptions keyed on
`(v0.7.0, 0.8.0, metric)` with ADR-017 behind them, and each ceiling is set just
above the measured range rather than at a round number, so a later regression
cannot hide inside the allowance. Optimisation was attempted **before** an
exception was asked for; four approaches are recorded in ADR-017, including one
that measured *slower*.

A release that publishes its own regression is worth more than one that implies
there wasn't one.

0.7.0 against v0.6.1, for comparison: per-node **+5.7 %**, 100-node no-op
**+10.2 %** (a scoped exception, [due for
falsification](https://github.com/vitruvyan/motus/issues/38)), trace
materialization **+0.3 %**.

**Why the change.** The previous ceilings came from one host and were guarded
by checking the CPU model contained `EPYC`. That is a brand, not a performance
class: the same v0.6.1 code measures 51.2 µs/node on the EPYC 9V74 the ceilings
were derived from and 66.1 µs/node on an EPYC 7763 allocated later. **v0.6.1's
own released code fails its own ceiling on today's hardware.** A gate that
answers differently on identical code is measuring the runner.

Absolute figures are still published — always with the machine that produced
them, because a performance number without its host is not a fact about
anything — and gate nothing. The gate still recomputes every aggregate from the
raw runs, still matches interpreter and runtime identity, and still refuses an
incomplete trace: completeness is an invariant, never a measurement.

See [`docs/MOTUS_PERFORMANCE_STATUS.md`](docs/MOTUS_PERFORMANCE_STATUS.md) and
`benchmarks/relative-0.8.1/` for the committed observations.

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
python benchmarks/check_slo_baseline.py --candidate benchmarks/candidate-v0.8.1-epyc-py310.json
python benchmarks/check_relative_baseline.py benchmarks/relative-0.8.1/*.json
```

## Native package surface

The public API is explicitly listed in `vitruvyan_motus.__all__`:

- topology: `GraphSpec`, `NodeDecl`, `Transition`, `TransitionKind`,
  `CompiledPlan`;
- execution: `Runtime`, `Policy`, `DurabilityProfile`, `EvidenceStatus`,
  `RunResult`;
- state and values: `State`, `Fact`, native `Decision`, `Rejection`, `redact`;
- replay: `TraceBundle`, `ReplayEngine`, `ReplayResult`, `ReplayStatus`;
- effects: `EffectDescriptor`, `EffectReceipt`, `EffectClass`;
- identity: `__version__`;
- observation: `TraceSink`, `TraceRunSink`, `Listener`, `InMemoryTraceSink`,
  `JsonlTraceSink`, `StreamDriver`, `AsyncStreamDriver`;
- evidence: `Trace`, `TRACE_SCHEMA_VERSION`, `RedactedValue`, `ContextDraw`,
  `RunContext`;
- failures: `MotusError`, `NodeFailed`, `SinkFailed`, `UnsafeResume`,
  `ReplayError`, `ReplayMismatch`, `ReplayUnsupported`, `DeclarationViolation`,
  `GraphSpecViolation`, `GraphSpecValidationError`, `NodeConfigurationError`.

Alongside it, `vitruvyan_motus.contract` carries `validate.py` and the two
schemas — mapped in from `contract/`, which remains the authority (ADR-001),
not copied. `validate_trace`, `validate_graphspec` and `validate_jsonl` are
importable directly for a consumer who would rather check in-process than
shell out.

The native and legacy decision types are deliberately unambiguous:

```python
from vitruvyan_motus import Decision
from vitruvyan_motus.compat import LegacyDecision
```

## Shipped in 0.7

- asynchronous execution — `arun`, `astream`, `AsyncStreamDriver` — with one
  state machine and no second engine;
- `JsonlTraceSink`: a durable sink, so `buffered` and `synchronous` stop being
  profiles nobody can reach;
- a sink protocol a conforming sink can be written against alone: the header
  handed over is the document's, and a session is told when it will receive
  nothing more;
- a persisted artifact may be absent, or a prefix, but never
  self-contradicting;
- `averify`, the asynchronous twin of the replay surface, so a node that is
  both `async def` and `pure` is verifiable rather than refused;
- performance gated on the ratio to the previous release, measured in one job
  on one host, instead of an absolute ceiling that a change of runner could
  pass or fail on its own;
- three runnable examples, executed by the test suite on every commit.

## Shipped in 0.6

- immutable compiled topology without a second execution semantics;
- effect receipts and fail-closed external-effect resume;
- playback, pure-node verification, and causally linked resume;
- deterministic explanation and portable trace bundles;
- standalone offline HTML trace viewer;
- strict read/write declaration enforcement;
- additive trace schema 1.1;
- executable performance regression gate.

## Known limitations

Recorded here rather than left for you to find, because a project whose thesis
is honest evidence should not be coy about its own.

- **Fan-out is not implemented.** Execution is single-lane: one node at a time.
  A declared concurrent topology has no representation in GraphSpec v1 or trace
  schema v1, deliberately, and adding it is a schema change rather than a
  feature ([`guarantees.md` §5](contract/guarantees.md)).
- **`resume` drives new work synchronously**, so a resumed segment cannot
  contain an `async def` node. `averify` landed in 0.7; `aresume` did not.
- **A required sink's refusal does not reach the caller** on `run_cancelled`
  and `run_failed(route_miss)` — the artifact is an honest truncated prefix and
  the session is told, but the caller is not
  ([#32](https://github.com/vitruvyan/motus/issues/32)). It is left open
  because the obvious answers are each wrong in a different way, and the two
  readings of the contract disagree.
- **A supervisor closing a `StreamDriver` still races a reader** at roughly 6
  escapes per 300 trials. Before 0.7 the same probe gave 325 escapes and 163
  wedged runs, so this is an incomplete fix rather than a regression, and the
  number is published rather than described.
- **Nobody outside this repository has run Motus.** Every benchmark is a
  synthetic graph, every test was written by its author, and the performance
  claims are relative measurements on CI runners. That is the largest unknown
  here and no amount of internal review substitutes for it.

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

The predecessor runtime and its satellites — `axis/`, `orders/`, `poc/`,
`examples/` and the Axis-era planning documents — were removed from the
working tree by ADR-009. They are not lost: the tag `v0.6.1` holds them
byte-identical, and the `vitruvyan-axis` 0.4.0 distribution remains
independently pinnable for consumers who have not yet migrated to
`vitruvyan_motus.compat` or the native API. The compatibility surface those
consumers depend on lives in `src/vitruvyan_motus/compat.py` and is exercised
by the frozen corpora in `tests/compat/` and `tests/contract/`, which are
unchanged.

## License

Vitruvyan Motus is licensed under the [Apache License 2.0](LICENSE). It permits
commercial and private use, modification, and distribution subject to its
notice and attribution terms.

---

> **Motus orchestrates intelligent execution and preserves the evidence of how every result was produced.**

Execution is transient.

**Evidence is designed to outlive it.**
