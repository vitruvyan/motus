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

> **Current release:** [Motus 0.20.0](https://github.com/vitruvyan/motus/tree/v0.20.0)
>
> Commitment window files written by releases through 0.13.0 use the legacy
> envelope key `c`; releases from 0.14.0 write `commitment`.
> Install: `pip install vitruvyan-motus`
>
> Apache-2.0 · stdlib-only kernel · validator included

0.20.0 adds the jurisdiction-neutral AI System Registry v1 boundary. Immutable
registration claims, separate append-only lifecycle event claims, and bounded
registry snapshots remain distinct evidence types. Supplied-document
verification reports exact identities, lineage, manifest bindings, correction
conflicts, and subset-scoped lifecycle projection without deciding legal
AI-system status, deployment, approval, official registration, compliance,
global current state, or inventory completeness.

The release passes the unchanged per-release budget against v0.19.0
at **-3.6%, -2.3%, and -2.4%** across three independent Jenkins dispatches.
The job ranges are -5.3% to -3.5%, -3.3% to +5.3%, and -6.7% to -0.2%; the
paired spread is wider than every measured effect, so the honest conclusion is
that the instrument does not resolve a matching code effect.

The cumulative arm remains red against the unchanged v0.6.1 anchor and ships
under ADR-018 without moving its budget:

| metric | cumulative | budget |
|---|---:|---:|
| Per-node overhead | **+106.9%** | +20% |
| 100-node no-op | **+131.3%** | +20% |
| Trace materialization | **+24.6%** | +20% |

The independently re-taken real-workload costs are **6.7, 2.6, 2.9, 4.7,
and 4.0 ms** against requests of 5.887, 9.928, 7.050, 8.696, and 10.272
seconds. The conservative share — the worst Motus cost over the fastest
request — is **0.114%**, below ADR-018's pre-registered 1% ceiling. The five
traces validate cleanly with integrity chains. The current Orbis graph runtime
is bound to `localhost:8001/run`; its missing health metadata keeps the probe's
service verdict honestly `degraded`, which does not weaken this Motus cost
measurement. Complete evidence is committed under
`benchmarks/relative-0.20.0/` and `benchmarks/real-workload-0.20.0.txt`.

### Historical 0.15.0 performance record

0.15.0 **still does not pass its cumulative performance gate**, and ships
under ADR-018 rather than by weakening it. Every failing ratio, measured
against the v0.6.1 anchor across three independent dispatches:

| metric | cumulative | budget |
|---|---:|---:|
| Per-node overhead | **+109.0 %** | +20 % |
| 100-node no-op | **+129.0 %** | +20 % |
| Trace materialization | **+23.0 %** | +20 % |

The cost is the integrity chain (ADR-017, corrected by ADR-019), paid in 0.8.0.

Against v0.14.0, in the same jobs on the same runner, the three canonical
figures are **+5.3 %, −1.0 % and +0.9 %** — all inside the unchanged +10 %
per-release budget. Across the three jobs they range from +2.4 to +6.1 %, −3.1
to +3.3 %, and −3.5 to +3.5 %. The 100-node measurement remains noisy (the
third dispatch reports an 87.3 % paired spread), so a small signed movement is
not evidence of a matching code effect.

The per-release arm and the cumulative arm answer different questions. The
former interleaves 0.14.0 and 0.15.0 on the same host and passes; the latter
still records the integrity-era debt against v0.6.1 and fails at **+109.0 %,
+129.0 % and +23.0 %**. Those cumulative figures are disclosed under ADR-018,
not waived, widened, or used to conceal a new release regression. The same
ADR-018 paragraph records the independently re-taken real-workload share:
**0.175 %** (18.8 ms worst Motus cost over the fastest 10.740 s request), below
the pre-registered 1 % ceiling.

**And there is a change this instrument cannot see at all.** 0.12.0 moved the
READ path — `_loads_strict` 11–15 % faster, `Trace.from_json` 17–41 % slower —
and none of the figures above shifted, because nothing in `benchmarks/`
measures reading. That is **#113**, still open: a gate that cannot see a
change is not evidence the change was free. 0.15.0 adds the risk/control
registry and ControlApplication evidence model; those contract surfaces are
not directly exercised by the current three benchmark metrics either. The
0.16.0 contract work adds HumanOversightReceipt as another boundary-only
validation surface; it likewise does not enter the measured execution path.

**And against a real request it remains below the release ceiling.** ADR-012 pre-registered the
test — executor share of run wall-clock, under 1 % — before any measurement
existed, and ADR-018 §4 requires it re-taken for every release: a share
measured against 0.14.0 says nothing about 0.15.0, and an inherited number is
the same error as an inherited baseline. Measured on 2026-09-21 with Motus
0.15.0 (`release/0.15.0` at `0b3c39e`) against the live `orbis_graph` service
(`POST /run`) with five fixed Italian queries, on an AMD EPYC Processor (with
IBPB), Linux 6.8.0-139, CPython 3.12.3, with load average 7.75, 9.03, 8.41:
**3.0, 18.8, 5.7, 2.7 and 7.2 ms** of Motus against requests of 20.055 s,
29.620 s, 10.740 s, 26.735 s and 23.210 s. The conservative share is the worst
cost over the fastest response — 18.8 ms of 10.740 s — which is **0.175 %**.
That is an upper bound because it includes the consumer's node code. All five
traces validated clean against trace schema 3.2.0 with integrity chains
present. The deployed consumer omitted health metadata expected by the harness,
so each run was honestly classified `degraded`; this is Motus overhead evidence,
not evidence that the live Orbis response surface is healthy. The harness is
`e2e/pipeline_query.py`; the complete output is committed as
`benchmarks/real-workload-0.15.0.txt`.

### Trace schema 3.1 migration

From trace schema 3.1.0, transitions for nodes whose spec declares neither
`reads_declared` nor `writes_declared` record `violations: null`; `[]` means a
declaration was checked and matched. Readers must therefore treat the field as
nullable (for example, use `len(record.get("violations") or ())`). Nodes with
one declared half still record a list for that half.

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
carries a cryptographic hash chain, and under trace schema 3.1.0 a per-trace
**root** that commits to the whole run (ADR-019). What it does NOT carry is an **anchor** — a
root published where the operator cannot rewrite it — and without one the chain
proves internal consistency, not immutability (issue #51).

The distinction is not academic, and this project got it wrong: the root shipped
in 0.8.0 and 0.8.1 covered only the terminal record, so an editor who rewrote the
trace and resealed it by the published recipe left that value untouched. Traces
from those versions are valid, replayable evidence and are **not anchorable**;
the validator says so when it reads one.

### The root, for whoever anchors it

The root is **`trace.root`**, and the emphasis is on the accessor rather than on
the value. It is the terminal record's `integrity.payload_hash` — derived, never
stored twice, stated normatively in rule T11 of `contract/trace.v1.schema.json`
— but the property recomputes it from the document rather than reading it back,
and that difference is the whole protection:

```python
root = result.trace.root          # 'sha256:1ca0f5f6…' — 71 characters, not 64
if root is None:
    ...                           # do not anchor anything
```

**Do not reach past it.** `records[-1]["integrity"]["payload_hash"]` returns the
string the document happens to carry, and there are three documents where that
string is worthless: one sealed under 2.0.0, one relabelled from 2.0.0, and one
whose header was rewritten while the first record's `prev_hash` was left stale —
that last leaves every declared digest self-consistent and the terminal one
unmoved. `trace.root` returns `None` for all three. The raw field returns a
value for all three, and it is the value an anchor would agree with.

`None` is an answer, not an error: the trace is unfinished, or below 3.0.0, or
its chain does not verify. Anchor nothing and find out which.

From 3.0.0 onward, that one value commits to the whole run — records, run id, policy,
metadata and `graph.code_fingerprint` alike — with one stated exception:
**numbers commit as parsed, at binary64 precision**, so a float in a trace
commits to its IEEE-754 double rather than to the literal in the file. Anchoring
anything larger than the root buys nothing and is more fragile.

The value **names its hash function**: a digest that does not say what produced
it cannot be recomputed. That costs seven characters, which matters when the
carrier is sized — a TRON memo holds 100, leaving 29 for a namespace prefix.
Budget from the string, not from the digest.

Motus ships no anchor and holds no chain credentials, and will not: the
repository provides the socket. What an anchor implementation owes its users,
learned from one that runs in production, is a `verify()` that re-reads from the
chain rather than trusting the receipt's own copy of the payload — a local file
that certifies itself certifies nothing. Issue #51 is where that interface is
being designed.

## Install

Install the released runtime and its validator from PyPI:

```console
python -m pip install vitruvyan-motus
```

For development, install from a checkout:

```console
python -m venv .venv
.venv/bin/pip install .
```

A consumer who cannot run a build inside each container vendors the wheel
instead, and pays for it: the first integration carried three byte-identical
copies, one per service image, with nothing keeping them identical through the
next release. #49 holds that cost and closes when the upload happens.

To work on Motus itself, from a checkout:

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
```

On Windows, use `.venv\Scripts\python.exe` in place of
`.venv/bin/python`. The wheel contains `vitruvyan_motus`, `py.typed`, and the
contract validator with the six schemas it checks against — trace, graphspec,
commitment, checkpoint, receipt and system manifest.

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

The frozen conformance fixtures stay in this repository — 660 KB of corpus,
and whoever needs it is already reading the repository. **Some of the prose
now travels**: `README.md`, `node-protocol.md` and `guarantees.md` ship inside
the wheel, because the MCP quotes them at call time and a citation an agent
cannot resolve after installing is not a citation (ADR-022). The set is exactly
what `mcp/sources.py` declares citable, and a packaging test holds the two
lists equal so neither can grow alone.

## Building against Motus with an agent

`pip install vitruvyan-motus[mcp]` ships an MCP server whose every answer is
either quoted from a document in the installation or produced by running the
shipped code, with the command that reproduces it. Nothing in it is written
from memory, and if a document changes the answer changes.

It exists because of a measurement, not a hunch: the effect classification has
been read wrong twice by careful readers, once by an automated reviewer that
inverted it twice in one pull request, and once by an integrator who declared
read-only HTTP calls `external_effect` believing it conservative. The contract
permits that; nothing told them it costs safe resumes, silently, forever.

Install it into a virtualenv of its own or with `pipx`: the kernel imports
nothing outside the standard library, and this extra pulls about two dozen
packages.

It answers narrowly on purpose. `motus_classify` reports which of the
protocol's marked terms your description contains and hands you the table; it
does not decide the class, because a version that did was measured wrong on
most realistic descriptions and wrong in the direction that costs correctness.

```
.venv/bin/pip install ".[mcp]"
.venv/bin/python -m vitruvyan_motus.mcp classify "I need to INSERT a row"
```

`motus_find` is the one to reach for when a trace, an error or a review names
something you do not recognise — `opaque_config`, `durability_profile`, a rule
id like `J2`. It quotes every passage in the shipped contract and examples that
contains the term, with the file each came from. It exists because the first
external integrator held `node:check:opaque_config` from a real trace, asked
this server what to do about it, and no tool could reach the answer (#107).

See [`docs/MCP.md`](docs/MCP.md) for the surface, the client configuration, and
the two things it refuses to do. It is off until installed: nothing on any
runtime path imports it.

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

Six examples, each standalone and each printing what it did:

```console
python examples/01_first_run.py          # a graph, a run, and the trace it left
python examples/02_durable_evidence.py   # write evidence to disk, then check it without trusting the writer
python examples/03_async_and_streaming.py # async nodes, live records, stopping mid-run
python examples/04_parameterised_nodes.py # configure a node without forfeiting replay
python examples/05_how_a_node_reports.py # raise, Rejection or Decision -- and why only one of them is a bug
python examples/06_effects_and_receipts.py # declaring what you touched, and the safe restart it buys
```

Read 05 before writing your first node. The mistake that costs most in a first
integration is treating "the check did not pass" as an error: it is a result,
and a node that raises for it throws the run away instead of recording what it
concluded.

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

### Parameterising a node without losing replay

Replay capability reports whether the runtime can **re-identify each node's
configuration**, and nothing wider. A real graph is parameterised — a
connection string, a ruleset version, a cache — and the obvious Python for that
is a factory closing over a config object. That is the one shape Motus cannot
re-identify: a closure's captured state is not a JSON value, so the run is
recorded as `partial` with the constraint `node:<name>:opaque_config` rather
than claiming a reproducibility it cannot honour.

Two shapes it can re-identify (node-protocol.md §6):

```python
# 1. a partial over strict-JSON keywords
node = functools.partial(check, ruleset_version="1.4.0", source_root=ROOT)

# 2. a callable instance that attests its own configuration
class Check:
    def motus_config(self) -> dict:      # pure, total, cheap, strict-JSON
        return asdict(self.config)
    def __call__(self, state): ...
```

Either way the configuration is fingerprinted into `graph.code_fingerprint`, so
two runs under different rules carry different fingerprints and a reader can
tell them apart. `python examples/04_parameterised_nodes.py` prints all three
shapes side by side.

Neither shape is a purity certificate. `motus_config()` is an attestation by
the class author, taken at its word; nothing inspects what `__call__` does, and
§1.2 of the node protocol says why nothing can. Equally, `partial` is not an
accusation: it says one node's configuration could not be reduced to a JSON
value, which is a limit of this fingerprinting recipe and not a finding about
the node. Nothing refuses a run for its capability — it is recorded so a reader
can judge, and rule T10 only enforces that it never improves over a run.

A capability is claimed before it is honoured: a run started without
`replay=ReplayStatus.declared(...)` is `none` / `undeclared` whatever its nodes
look like. Constraints you add to that declaration stay in the terminal record,
so a graph whose nodes are all re-identifiable can still report `partial`
because the caller said so.

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

**Give it a directory the process can write to.** Reported by an integrator
who lost an hour to it: Docker creates a bind-mount target as `root` when the
host path does not exist, and a container running as a non-root uid then gets
`Permission denied` on the first record. The sink is doing exactly what it
should — a durable sink that silently discarded evidence would be worse — but
the failure arrives at the first write rather than at configuration time, which
is late. Create the directory with the runtime's uid, or `chown` it in the
image, before the first run.

The same integrator found that their own trace-directory setting had carried
this defect for months without anybody noticing, because the feature was never
switched on. A path that is only exercised when somebody enables evidence is a
path that has never been tested.

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

0.15.0 against v0.14.0 — three independent Jenkins dispatches, canonical value is the
median of the job ratios:

| metric | canonical | across jobs | budget |
|---|---:|---:|---:|
| per-node overhead | **+5.3 %** | +2.4 … +6.1 % | +10 % |
| 100-node no-op overhead | **−1.0 %** | −3.1 … +3.3 % | +10 % |
| trace materialization | **+0.9 %** | −3.5 … +3.5 % | +10 % |

The third dispatch's 100-node paired spread is **87.3 %** — wider than the
effect it measures — and the checker prints that rather than letting the number
stand alone. *Within noise* is not *no difference*; it is the measurement saying
it cannot resolve a change of that size.

**And what this table does not contain is worth as much as what it does.**
0.12.0 changed the READ path: `_loads_strict` got 11–15 % faster and
`Trace.from_json` 17–41 % slower. Not one figure above moved, because nothing
in `benchmarks/` measures reading — not `from_json`, not `_loads_strict`, not
`validate_trace`. That is #113. A gate that cannot see a change is not evidence
that the change was free.

No exception is declared and none is needed: 0.9.0 adds a contract revision and
a new module, and costs almost nothing to run. The worst paired spread across
the three jobs is 5.1 %, narrower than every effect measured, so these figures
are the code rather than the runner — which is not always true and is stated
here because on 0.8.1's 100-node no-op it was not.

The release that did not fit its budget was 0.8.0 against v0.7.0, and its
exceptions stand:

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

**The absolute gate still depends on which runner it lands on.** Of three
dispatches taken for this release, **two were discarded before any measurement
was compared** — the host precondition wants an AMD EPYC and GitHub allocated
two Intel Xeons ([issue #70](https://github.com/vitruvyan/motus/issues/70)).
Discarding on architecture is not the same as retrying until a number is
convenient — nothing was measured on the discarded hosts — but the ratio is
recorded here rather than left implicit. The relative gate had no such problem:
it measures both versions on the same host in the same job, which is the point.

See [`docs/MOTUS_PERFORMANCE_STATUS.md`](docs/MOTUS_PERFORMANCE_STATUS.md) and
`benchmarks/relative-0.15.0/` for the committed observations.

## Contract and verification

The normative surfaces live in [`contract/`](contract/):

- `graphspec.v1.schema.json` and rules R1-R12;
- `trace.v1.schema.json` and the T/E/SB/H/J/JSONL rules;
- `system-manifest.v1.schema.json` and rules SM1-SM3 — a versioned system
  declaration whose binding verification remains explicitly separate;
- `commitment.v1.schema.json` (rule `C1`) and `checkpoint.v1.schema.json`
  (rules `K1`, `K2`) — the commitment log's BEGIN/END and the sealed windows
  over them;
- `receipt.v1.schema.json` — what a receipt must carry to prove a RUN and not
  merely a checkpoint (ADR-021 §7). ADR-027 optionally adds `execution`, whose
  `ref` locates the original BEGIN, whose `fingerprint` is the Trace.root (or
  null for an unfinished execution), and whose `run_id` is embedder correlation,
  not a key;
- `node-protocol.md`;
- `guarantees.md`.

Run the complete suite and contract validator with:

```console
python -m pytest tests/ -q
python contract/validate.py trace path/to/trace.json --spec path/to/graph.json
python benchmarks/check_slo_baseline.py
python benchmarks/check_relative_baseline.py benchmarks/relative-0.15.0/*.json
```

**Verification is open and stays open.** A receipt is checked against the trace
it claims to be about, offline, with no account and against no server of ours:

```console
motus-validate receipt path/to/receipt.json --trace path/to/trace.json
```

A packaged bundle is checked from the zip alone:

```console
motus-validate package path/to/evidence.zip
```

It reports all seven of ADR-020's attestation levels — `INTEGRITY`,
`EXISTENCE`, `RETENTION`, `EXECUTION_CONTINUITY`, `PROVENANCE`, `IDENTITY`,
`LEGAL_TIME` — **including the ones it could not reach**, and refuses outright
on a digest algorithm or a network it cannot recompute. A refusal outranks a
violation: reporting "this document is wrong" about a document that may be
perfectly correct on a chain we cannot read would be the wrong answer twice.

### Anchors and witnesses ship separately, and one of them ships today

ADR-021 defines two interfaces and states that **no implementation of either
Protocol ships in `vitruvyan-motus`**. That sentence is about this
distribution, and it stays true: the kernel imports no network client, mints no
identity and reaches nothing unless configured. The implementations are
separate distributions under [`plugs/`](plugs/), with their own dependencies
and their own suites.

| interface | question it answers | status |
|---|---|---|
| `Anchor` | was this block of evidence not rewritten afterwards? | **`motus-anchor-opentimestamps` ships** — free, Bitcoin, no wallet, no key custody |
| `Witness` | did this commitment exist before the outcome was known? | protocol only. `EXECUTION_CONTINUITY` is defined, verifiable and not yet reachable |

An outside integrator read the ADR sentence, found nothing about `plugs/` in
this file, and concluded that anchoring was unimplemented — while six demo
roots sat in Bitcoin blocks 962770 to 962798, three independent OpenTimestamps
calendars each. The sentence was right and this README was silent, which is the
same outcome as being wrong. Hence this section.

**The two are not interchangeable.** An anchor protects the chain; a witness
protects the two-phase commitment, and only a witness answers *"was this run
registered before anybody knew how it would turn out?"* — see `sealing.py` for
the arithmetic, including the consequence that an anchor cannot give execution
continuity to a run shorter than its own cadence.

## Native package surface

The public API is explicitly listed in `vitruvyan_motus.__all__`:

- topology: `GraphSpec`, `NodeDecl`, `Transition`, `TransitionKind`,
  `CompiledPlan`;
- execution: `Runtime`, `Policy`, `DurabilityProfile`, `EvidenceStatus`,
  `RunResult`;
- state and values: `State`, `Fact`, native `Decision`, `Rejection`, `redact`;
- replay: `TraceBundle`, `ReplayEngine`, `ReplayResult`, `ReplayStatus`;
- evidence packaging: `pack`, `verify_package`, `evidence_package_fingerprint`,
  `PackageVerdict`;
- evidence access: `EvidenceAPI`, `EvidenceSource`, `LiveEvidenceSource`;
- system manifest: `verify_system_manifest_bindings`, `SystemManifestBindingVerdict`,
  `SystemManifestBindingFinding`;
- risk and control: `verify_control_application_bindings`,
  `ControlApplicationBindingVerdict`, `ControlApplicationBindingFinding`,
  `controls_for_risk`, `risks_for_control`;
- human oversight: `verify_human_oversight_bindings`,
  `HumanOversightBindingVerdict`, `HumanOversightBindingFinding`;
- regulatory evidence profiles: `assess_evidence_profile`,
  `RegulatoryEvidenceAssessment`, `RegulatoryEvidenceFinding`;
- regulatory evidence dossiers: `pack_regulatory_dossier`,
  `verify_regulatory_dossier`, `verify_regulatory_dossier_lineage`,
  `regulatory_dossier_export_fingerprint`, `RegulatoryDossierVerdict`,
  `RegulatoryDossierEntryVerdict`, `RegulatoryDossierLineageVerdict`,
  `RegulatoryDossierFinding`;
- incident and CAPA: `verify_incident_capa_ledger`,
  `order_incident_capa_entries`, `IncidentCAPAVerdict`,
  `IncidentCAPAFinding`;
- retention and legal hold: `verify_retention_lineage`,
  `resolve_supplied_retention_scope`, `verify_retention_application_bindings`,
  `evaluate_supplied_retention_blocker`, `RetentionFinding`,
  `RetentionArtifactIdentity`, `RetentionLineageVerdict`, `RetentionScopeVerdict`,
  `RetentionApplicationBindingVerdict`, `RetentionBlockerVerdict`;
- AI System Registry: `verify_ai_system_registry_lineage`,
  `verify_ai_system_registration_binding`, `project_supplied_ai_system_lifecycle`,
  `verify_ai_system_registry_snapshot`, `AISystemRegistryFinding`,
  `AISystemRegistryLineageVerdict`, `AISystemRegistrationBindingVerdict`,
  `AISystemLifecycleProjection`, `AISystemRegistrySnapshotVerdict`;
- effects: `EffectDescriptor`, `EffectReceipt`, `EffectClass`;
- identity: `__version__`;
- observation: `TraceSink`, `TraceRunSink`, `Listener`, `InMemoryTraceSink`,
  `JsonlTraceSink`, `StreamDriver`, `AsyncStreamDriver`;
- evidence: `Trace`, `TRACE_SCHEMA_VERSION`, `RedactedValue`, `ContextDraw`,
  `RunContext`, `NonCanonicalNumber`, `NonIntegerNumber`;
- failures: `MotusError`, `NodeFailed`, `SinkFailed`, `UnsafeResume`,
  `ReplayError`, `ReplayMismatch`, `ReplayUnsupported`, `DeclarationViolation`,
  `GraphSpecViolation`, `GraphSpecValidationError`, `NodeConfigurationError`.

### System Manifest binding verification

ADR-035 keeps document validity and binding verification separate. A valid
System Manifest is a well-formed declaration; it is not proof that the declared
runtime, graph, code, policy or control was used.

The public verifier compares the manifest with the Motus distribution executing
the check and with validated Motus artifacts supplied by the caller:

```python
from vitruvyan_motus import verify_system_manifest_bindings

verdict = verify_system_manifest_bindings(
    manifest,
    graph_specs=[spec],
    traces=[trace],
)

if verdict.bindings_complete:
    ...
```

The status vocabulary is deliberately narrow: `matched`, `mismatched`, and
`not verified`. Missing evidence is never a match. `graph_fingerprint` is
recomputed from the supplied GraphSpec. `code_fingerprint` is only compared
with the value carried by a matching validated Motus trace; that establishes
agreement with execution evidence and does not independently recompute node code
identity. `bindings_complete` therefore means only that every v1 binding this verifier
knows how to compare matched the supplied Motus artifacts. It does not mean
compliant, certified, approved or deployed.

### Risk & Control Registry and ControlApplication binding

ADR-036 keeps governance intent separate from evidence that a control was
evaluated or applied. `controls_for_risk()` and `risks_for_control()` query one
validated Registry revision and return detached declarations; they do not
infer effectiveness, coverage sufficiency, or a regulatory conclusion.

The public binding verifier joins one ControlApplication to the exact Registry
revision and, when supplied, the System Manifest and Motus receipt:

```python
from vitruvyan_motus import verify_control_application_bindings

verdict = verify_control_application_bindings(
    application,
    registry=registry,
    manifest=manifest,
    receipt=receipt,
)

if verdict.bindings_complete:
    ...
```

The status vocabulary is the same narrow `matched`, `mismatched`, and `not
verified`. The registry and application fingerprints are independently derived
from canonical JSON. The control must exist in that exact registry revision;
an operator-declared enforcement point, when present, must match; a registry
System Manifest binding is checked against the supplied manifest; and the
receipt must contain a BEGIN at the application's canonical ADR-027
`execution_ref`.

`bindings_complete` means only that those document and identity bindings
matched. Receipt presence is not receipt verification, an `outcome` describes
one event rather than global control effectiveness, and no ControlApplication
raises EXISTENCE, RETENTION, IDENTITY, LEGAL_TIME, or another ADR-020 assurance
level.

### HumanOversightReceipt binding verification

ADR-037 keeps a claimed human oversight event separate from evidence that its
Motus artifact references agree. The public verifier compares only the
contract-valid artifacts supplied by the caller:

```python
from vitruvyan_motus import verify_human_oversight_bindings

verdict = verify_human_oversight_bindings(
    oversight_receipt,
    execution_receipt=execution_receipt,
    manifest=manifest,
    registry=registry,
    control_application=application,
)

if verdict.bindings_complete:
    ...
```

Manifest, Registry, and ControlApplication fingerprints are independently
derived from canonical JSON. The execution receipt must contain a BEGIN at the
oversight event's ADR-027 `execution_ref`; a supplied ControlApplication bound
by the event must name that same execution. Missing source material is `not
verified`, never matched.

`bindings_complete` means only that every reference carried by this receipt
and understood by this verifier matched the supplied documents. It does not
prove that the actor is human, identified, authorised, independent, or legally
competent; that the recorded event occurred; that the review was sufficient;
or that any compliance or ADR-020 assurance level was reached.

### Regulatory Evidence Profile assessment

ADR-038 lets any Motus consumer map opaque external requirement references to
evidence kinds Motus already owns. The public `assess_evidence_profile()`
function reports only evidence states: `missing`, `not_verified`,
`mismatched`, or `matched`.

The mechanism is deliberately jurisdiction-neutral. Motus ships no AI Act,
ISO 42001, NIS2, national-law, procurement, or customer-specific mapping in the
kernel, and a complete evidence mapping is not a compliance verdict. Profiles
can be maintained and versioned independently by any Motus user; Orbis is one
possible consumer, not an architectural dependency.

Version 1 evaluates the candidate artifact set supplied by the caller. It does
not discover which artifact among a collection is legally or semantically
relevant to a requirement; `matched` therefore establishes neither relevance
nor legal sufficiency. Richer selectors require a later ADR backed by a real
integration need.

Where an evidence kind already has a Motus binding verifier, the assessment
composes it rather than downgrading verification to schema validity. In
particular, System Manifest reaches `matched` only when its supplied GraphSpec
and trace bindings are complete; a receipt verifier refusal is reported as
`not_verified`, not rewritten as a contradiction.

### Regulatory Evidence Dossier export

ADR-042 packages one exact, bounded set of recognized Motus artifacts without
turning the package into a report or filing. `pack_regulatory_dossier()` first
validates the manifest and checks every supplied byte length and SHA-256 digest;
it then writes `dossier.json` plus the unmodified member bytes to a deterministic
ZIP. Identical manifest and member bytes therefore produce identical exports.

`verify_regulatory_dossier()` keeps three identities distinct: the canonical
dossier fingerprint, each artifact's existing Motus semantic fingerprint, and
the export fingerprint over the exact ZIP bytes. It refuses duplicate, unsafe,
linked, encrypted, undeclared, missing, unsupported, or resource-unbounded ZIP
members before treating them as evidence, and dispatches recognized artifacts
to their existing Motus validators. The exact included profile is assessed only
when each profiled kind has at most one unambiguous candidate; later Motus
artifact kinds remain individually verified without silently expanding the
ADR-038 profile vocabulary.

`verify_regulatory_dossier_lineage()` evaluates only caller-supplied manifests.
It preserves missing or invalid predecessors, competing roots, forks and cycles;
it performs no discovery and chooses no winner. None of these helpers emits a
compliance, completeness, legal-sufficiency, official-submission or acceptance
verdict. The v1 archive bounds are 1000 artifact members, 28 MiB per member,
128 MiB aggregate declared member bytes and 160 MiB compressed transport bytes.

### Incident / CAPA Ledger

ADR-039 records immutable producer claims without turning Motus into incident
case management. An `IncidentDeclaration` describes one observed or suspected
incident; a `CAPAAction` describes one corrective or preventive action and
names the exact incident revision it was created against. Neither a valid
record nor the words `completed` and `closed` prove effectiveness, adequacy,
reportability, compliance, or legal closure.

```python
from vitruvyan_motus import (
    order_incident_capa_entries,
    verify_incident_capa_ledger,
)

verdict = verify_incident_capa_ledger(
    ledger,
    execution_receipts=receipts,
    manifests=manifests,
    registries=registries,
    control_applications=control_applications,
    human_oversight_receipts=oversight_receipts,
    evidence_packages=package_blobs,
)
ordered_entries = order_incident_capa_entries(ledger)
```

Corrections append a new exact revision through `supersedes`; they do not
rewrite or delete the prior claim. Entry array order has no evidentiary
meaning. Present parents are ordered before children and exact fingerprints
break ties. Missing predecessors are `not_verified`, while competing children
are preserved as `conflict`; Motus chooses no winner.

`bindings_complete` means only that every binding represented in the verifier's
findings matched the supplied evidence. The verifier performs no network or
database lookup and derives all exact fingerprints independently. `missing`
means the caller omitted referenced evidence, including material required by
a composed outbound verifier; `not_verified` means the material was present
but the verifier could not establish its required outbound bindings. Exact
receipt documents and evidence-package
bytes can also be referenced; package identity is the exact transport-byte
fingerprint returned by `evidence_package_fingerprint()`, while
`verify_package()` remains authoritative for package contents.

### Retention and legal-hold evidence

ADR-040 separates policy and hold declarations, exact scope snapshots,
application records, and custody observations. The read-only helpers inspect
only the documents supplied by the caller. For an exact-artifact policy:

```python
from vitruvyan_motus import (
    evaluate_supplied_retention_blocker,
    resolve_supplied_retention_scope,
    verify_retention_application_bindings,
    verify_retention_lineage,
)

lineage = verify_retention_lineage("legal-hold-declaration", holds)
scope = resolve_supplied_retention_scope(policy)
bindings = verify_retention_application_bindings(
    application, policy=policy, holds=holds, snapshots=snapshots,
)
blocker = evaluate_supplied_retention_blocker(
    application["artifacts"][0], holds=holds, snapshots=snapshots,
)
```

For an execution-reference or tenant/writer selector, pass one exact
`snapshot=` to `resolve_supplied_retention_scope`; an absent snapshot leaves
membership unverified. `blocked_by_supplied_hold` means a matching supplied
producer hold claim, not legal authority or enforcement. A release or
cancellation is another producer claim and does not erase a placement.
`no_blocker_in_supplied_evidence` describes only the caller's subset; it does
not establish that other holds are absent or authorize disposal. Application
outcomes and custody observations do not prove continued custody.

### AI System Registry evidence

ADR-041 separates one exact System Manifest from immutable registration claims,
lifecycle events, and bounded snapshots. The helpers operate only on documents
supplied by the caller:

```python
from vitruvyan_motus import (
    project_supplied_ai_system_lifecycle,
    verify_ai_system_registration_binding,
    verify_ai_system_registry_lineage,
    verify_ai_system_registry_snapshot,
)

binding = verify_ai_system_registration_binding(
    registration, manifests=[manifest],
)
lineage = verify_ai_system_registry_lineage(
    "ai-system-registration", registration_revisions,
)
lifecycle = project_supplied_ai_system_lifecycle(
    registration["registration_id"],
    registrations=registration_revisions,
    events=events,
)
snapshot_result = verify_ai_system_registry_snapshot(
    snapshot, registrations=registration_revisions, events=events,
)
```

`terminal_action` is derived only when the supplied correction and lifecycle
chains are complete and conflict-free. It is not global current state or proof
of deployment. Snapshot membership is not proof that the inventory is complete.
Actor, party, filing, lifecycle and external-reference fields remain producer
claims; validity does not establish authority, legal status or compliance.

### Evidence API for bridges

ADR-034 separates evidence ownership from presentation. Motus owns the receipt,
package and verification result; Orbis, Limen or another application may expose
them through a bridge without rebuilding or reinterpreting them.

The canonical boundary is Python and transport-neutral:

```python
from vitruvyan_motus import EvidenceAPI, LiveEvidenceSource

source = LiveEvidenceSource(commitment_log, bundle_for_execution_ref)
evidence = EvidenceAPI(source)

receipt = evidence.receipt_for(execution_ref)
package = evidence.package_for(execution_ref)
verdict = evidence.verify(execution_ref, package=package)
```

Every lookup takes the ADR-027 `execution_ref` (`tenant/writer/sequence`),
never `run_id`. `receipt_for()` performs contract and internal-identity
validation before exposing a receipt; that is document validation, not
execution verification. `package_for()` is strict retrieval: it returns only a
readable package whose manifest has the fixed envelope shape defined by the
evidence-package format, whose contract-valid receipt contains the requested
execution, and whose manifest execution identity agrees with that receipt. The
manifest remains transport metadata, not evidence and not a source of
execution validity. `verify()` has a
different hostile-input duty: malformed or schema-invalid stored evidence is
returned as the shipped verifier’s fail-closed `PackageVerdict`, while a
readable, contract-valid package for a different execution is refused as source
substitution. Retrieving a receipt or package is not verification.

If the bridge already holds the package bytes, it passes them back to
`verify(..., package=package)` so the verdict necessarily describes the same
artifact it displays. A UI that displays “verified” must therefore display
that verdict, not infer it from a fingerprint or from receipt presence.

`LiveEvidenceSource` is the local reference adapter for an embedder that owns a
live `CommitmentLog` and can resolve the corresponding `TraceBundle`. Optional
`anchors_for` and `attestations_for` callbacks let it carry already-produced
proof artifacts into the package; the Evidence API does not create or interpret
those proofs. A deployment using a database, object store or evidence service
implements the same `EvidenceSource` protocol; storage layout is not part of
the public API.

`Trace.from_json` is the loader to prefer when the document's **text** is in
reach, and `NonCanonicalNumber` is what it raises. A number's digest is taken
over its parsed value, so a genuine `5e+18` and a rewritten
`5000000000000000511.0` are the same IEEE-754 double and would share a root —
while `jq`, `git diff` and a human read different numbers. The characters are
the evidence, and a parser destroys them, so `from_dict` cannot make this check
and no implementation could: by the time it is called, the two documents are
one object (ADR-024).

**And from trace schema 3.2.0 every number in a trace is a JSON integer with
|n| ≤ 2^53 − 1 (rule `J4`, ADR-030).** `NonIntegerNumber` is what a `Fact`,
`Decision` or `Rejection` value, a metadata value, or the CLI's document
parser raises for a float — integral ones included, because `-14` and `-14.0`
are the same RFC 8259 number and two different canonical texts — or for an
integer beyond `Number.MAX_SAFE_INTEGER`, with the rule id and the JSON path
in the message. **The refusal lives at the writer that stamps
`schema_version`, not on `State` (2026-09-06 review correction) — `State`
does not know what trace version, if any, a value will end up in.**
`Trace.__init__` (the header, and any records handed to the constructor
directly), `Trace.append` and `Trace._append_runtime` (a record) raise it
*before anything is written*; the runtime's in-node writes check raises it
for a node's own facts, decisions and rejections, wrapped into that node's
`NodeFailed` rather than out of `.run()` bare; and the CLI's document parser
raises it reading a document from disk. Readers — `Trace.from_json`/
`from_dict` — are scoped by the document's own declared version, so a
0.12.0 trace carrying `-14.0` loads, replays and verifies exactly as it
always did. A quantity that is not an integer is carried at a declared scale
— basis points, milliseconds, whole units — or as a string the producer
owns; the contract does not choose the scale. `ctx.rand()` draws are recorded
as the 53-bit integer they are made from and handed to the node as `n / 2^53`,
so a 3.2.0 trace survives `JSON.parse` + `JSON.stringify` in a browser with
its root unchanged — the property #116 was filed for.

**Which escape form a string was written in is not checked, and ADR-026 settles
that as intended rather than as a hole.** `"appro\u0076ed"` and `"approved"`
are the same JSON string: every conforming reader gets the same value from
both, so they share a root, correctly. Refusing one would be a false accusation
against a document identical in meaning to one we accept. A rule for escape
forms was written and withdrawn the same day; #98 is closed with the reasons.

**What a string may not be is text that denotes nothing.** An unpaired
surrogate — U+D800–U+DFFF with no partner — denotes no character, has no UTF-8
encoding, and conforming JSON implementations disagree about it, so a document
carrying one has more than one reading and is refused (rule `J1`, ADR-026). A
surrogate *pair* is a character and is accepted. The refusal is scoped by the
document's own declared trace schema version, because releases up to 0.7.0
wrote such traces and their own validators called them valid; the producing
side is scoped by nothing. If you need to carry bytes that are not Unicode —
a filename a filesystem handed over — encode them explicitly rather than
smuggling them through a string.

Alongside it, `vitruvyan_motus.contract` carries `validate.py` and the contract
schemas — mapped in from `contract/`, which remains the authority (ADR-001),
not copied. `validate_trace`, `validate_graphspec`, `validate_jsonl`,
`validate_commitment`, `validate_checkpoint`, `validate_receipt` and
`validate_system_manifest` are importable directly for a consumer who would
rather check in-process than shell out.

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
byte-identical. Consumers that have not yet migrated can pin the `v0.6.1`
source tag and use `vitruvyan_motus.compat` while they move to the native API.
The compatibility surface those
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
