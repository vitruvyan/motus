# Node Protocol (normative)

**Status: v1 — accepted by ADR-001 on 2026-08-03.** The key words MUST,
MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted as described in
RFC 2119.

This document binds whoever writes node code. Its enforcement model is
**record-and-compare**: the runtime captures what a node actually did; a
declaration, when present, is checked against captured reality. A node cannot
lie its way past the trace — it can only be caught by it.

## 1. Shape

1.1. A node is a Python callable with one of two signatures:

```python
def node(state: State) -> State: ...
def node(state: State, ctx: RunContext) -> State: ...
```

1.2. A node MUST return a state derived from the one it received. It MUST NOT
mutate the received state, any value previously read from it, or any shared
module-level object as a way of passing information.

**Three of these are enforced structurally. The fourth is prohibited and not
enforced, and this clause used to claim otherwise.** Passing information
through a shared module-level object touches nothing the runtime observes: the
node reads no state, so there is no read to record and nothing to compare a
declaration against. Motus cannot see it, and no mechanism described below
detects it. In Python nothing could without inspecting node bodies, which this
runtime does not do and does not promise to.

Saying so costs a sentence and buys the only thing that matters here: a reader
who believes an unenforced prohibition is enforced will design around a guard
that is not there. Verify-replay narrows it — a `pure` node fed a side channel
that is empty or different at replay time diverges — but a channel that
survives to replay time, a table or a file or a warm cache, produces no
divergence at all. A narrow guarantee that holds is worth more than a broad one
that does not.

What *is* structural, and is: (a) values are isolated at the write boundary —
the runtime stores its own copy, so a node mutating the object it wrote
afterwards mutates only its own garbage; (b) reads return values a node
cannot use to reach the stored ones (a fresh copy or an immutable view —
which of the two is an implementation choice, the non-aliasing guarantee is
the contract); (c) the returned state's lineage is checked on commit — it
must extend the input state's history (prefix preservation, an O(1)
structural check), so a node cannot truncate history, substitute prior
values, or return a foreign state. Verify-replay (0.6) additionally catches
behavioral impurity *that manifests as a different answer*; it is a second net,
not the first defense, and it is not a purity analyser — a node reading the
system clock or an environment variable is re-executed, returns the same value,
and is reported verified. Retroactive corruption of recorded evidence is
impossible by construction in 0.5.

1.3. A node MUST NOT retain the received state, the returned state, or the
RunContext beyond the call. What a node wants remembered, it writes as a fact.

1.4. A node has no knowledge of the runner, the spec, other nodes, or its own
position in the graph. A node that needs to influence routing writes a
**decision**; the spec's route table dispatches on it, and the run records
which decision it dispatched on — addressed to the exact entry, the same way a
read names the exact value it consumed. Control flow never
lives inside node code as a side channel (no environment reads that change
topology, no callable swapping).

## 2. Writing

2.1. Everything a node contributes is appended through the state API: facts,
decisions, rejections. Same-key writes accumulate as history; the most recent
wins on read, and the overwrite is visible in the trace rather than silent.

2.2. Every value written MUST be a strict RFC 8259 JSON value or an explicit
redacted value (§5). Strict means: object keys are strings; NaN and Infinity
are refused (Python's `json` accepts them by default — the Motus boundary
does not); every string denotes a sequence of Unicode scalar values, so an
unpaired surrogate is refused (rule `J1`, ADR-026) while every escape form of
a real character is accepted; the value round-trips structurally (what you wrote is what a
reader decodes — tuples become lists *before* the boundary or are refused,
never silently reshaped after it). A value that cannot be written down under
these rules is refused at the boundary — the runtime never carries what it
cannot record (invariant V distinguishes *recording structure* from *storing
content*; refusal is about structure).

2.3. A node that declines to act SHOULD say so: a rejection with a reason is
the difference between "didn't" and "wouldn't", and it is what why-not
explanations are made of.

## 3. Reading — record-and-compare

3.1. The runtime captures every read a node actually performs and records it
in the transition record with a structured **origin** — the exact value read,
addressed by collection and index (initial state or a prior transition's
writes), not merely the transition it came from. Capture is not optional and
not declarative — it is what the runtime observed.

3.1a. **The readable surface is closed and enumerated.** A node can read:
facts by key, decisions by key, the run's intent, the run metadata by key
(origin kind `header`), and whole collections by scan (facts, decisions,
rejections, events — origin kind `scan`, coarse by nature). A lookup that
finds nothing is recorded with origin kind `absent` — a read-miss can steer
control flow as much as a present value, so it is evidence, never dropped.
Nothing else is readable from node code; the compatibility view documents in
guarantees.md §4 how legacy access maps onto this surface.

3.2. `reads_declared` / `writes_declared` in the GraphSpec are OPTIONAL. When
present, the runtime compares captured reality against the declaration and
records any mismatch in the transition record's `violations` field. From trace
schema 3.1.0, a node whose spec entry declares neither `reads_declared` nor
`writes_declared` records `violations: null` — distinguishing checked and
clean (`[]`) from never checked (`null`); a declaration of one half still
produces the list for that half (ADR-029).
**"Captured reality" means every read, of every origin kind** — there is no
exempt corner of the readable surface: a key that was looked up and missed
(`absent`) is declared by that key, a scanned collection is declared by the
collection's name, and a header read is declared as `intent` or as the
metadata key taken. A node that touched something undeclared owes a violation
for it whatever shape the touch had. (Cross-review v3 found `absent`, `scan`
and `header` reads exempted, which let a node probe an undeclared key and
still publish an empty violations list — a declaration that covers three
quarters of the reads is not a declaration.) In trace 1.0 / Motus 0.5,
mismatches were recorded only. Motus 0.6 enforces captured mismatches under
`strict` before commit; `exploration` records them and continues.

3.3. A node MUST NOT read state through any channel that evades capture
(direct attribute access on internals, closures over prior states). The
captured read set is evidence; evading it is a protocol violation.

## 4. Effects

4.1. Every node has exactly one effect class, declared in the GraphSpec or
defaulted:

| Class | Meaning | Obligations |
|---|---|---|
| `pure` | Output depends only on captured reads and context draws; no observable outside effect | MUST NOT perform I/O, mutate externals, or draw ambient nondeterminism. The claim is falsifiable: verify-replay (0.6) re-executes pure nodes and compares. |
| `recorded_effect` | Performs reads of the outside world or model/tool calls whose *results* are the effect (LLM calls, HTTP GET, file reads) | Records each effect through `RunContext.record_effect`. Result values are recorded like any write; an adapter receipt may be attached in trace schema 1.1. |
| `external_effect` | Mutates something outside the run (writes a row, sends a message, POSTs) | Default class for any undeclared node — the conservative reading. Resume is permitted only with a non-empty idempotency key and completed receipt. The bounded claim is at-least-once under that idempotency contract; exactly-once is never promised. |

4.2. Misclassification is not a crime the runtime can always detect —
but it is one the trace makes falsifiable over time (verify-replay for `pure`;
effect enforcement for `external_effect`). Classify honestly or conservatively.

4.3. Motus 0.6 receipts are opaque adapter assertions. Resume requires a
completed receipt and non-empty idempotency key for every recorded external
effect. `unknown` is evidence, not failure, but it blocks automatic resume.
Exactly-once is never inferred.

4.4. **The operations, named.** 4.1 gives the rule and 4.2 gives the latitude,
and a reader holding a concrete operation has still had to apply them. Two
measured misreadings say that step is where this protocol is lost: an automated
reviewer inverted the classification twice in one pull request (#77), and an
integrator's field report declared HTTP GETs `external_effect` and called it
conservative — which 4.2 permits and no document told them the price of.

This table classifies named operations under 4.1. It adds no obligation: where
a row and 4.1 disagree, **the row is wrong**. The honest class is the one 4.1's
definition yields; the conservative class is `external_effect`, always
permitted by 4.2, and the cost column is what nobody was being told.

The terms in `code` are the ones a reader is holding when they ask. They are
marked so that a program can find them without guessing at English, which is a
deliberate property of this table and not typography: the integration MCP
(ADR-022) reads this table at call time rather than restating it, so a row
edited here changes what it reports, and a row it cannot read stops it.

**This table classifies operations. It does not classify sentences, and no
program may use it to.** A program can find the marked terms that are present
in a description; it cannot find the operation a description names in words
this table does not mark, and *that* is the operation that would have changed
the answer. So a term found here is a fact about the text and never a verdict
about the node — a node described as *"computes the total and mails the
receipt"* contains `compute` and no marked term for the mailing, and any
program that folded that into a class would answer `pure` about a node that
sends mail. A tool reporting matched terms reports them as matches, quotes this
paragraph beside them, and leaves the classification to the reader holding the
node.

| Operation | Honest class | Declaring `external_effect` instead |
|---|---|---|
| an HTTP `GET`, `HEAD` or `OPTIONS` | `recorded_effect` | permitted; blocks a resume that was safe |
| an HTTP `POST`, `PUT`, `PATCH` or `DELETE` | `external_effect` | — |
| a SQL `SELECT` | `recorded_effect` | permitted; blocks a resume that was safe |
| a SQL `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `UPSERT` or `TRUNCATE` | `external_effect` | — |
| reading a file — `read`, `load`, `stat`, `glob` | `recorded_effect` | permitted; blocks a resume that was safe |
| writing a file — `write`, `append`, `unlink`, `rmtree` | `external_effect` | — |
| an LLM or model call — `complete`, `chat`, `embed`, `rerank` | `recorded_effect` | permitted; blocks a resume that was safe |
| a tool call whose *result* is the effect — `lookup`, `search`, `fetch` | `recorded_effect` | permitted; blocks a resume that was safe |
| `send`, `email`, `sms`, `webhook`, `notify` | `external_effect` | — |
| `enqueue`, `publish` to a queue or topic | `external_effect` | — |
| `charge`, `refund`, `transfer`, `payout` | `external_effect` | — |
| computing over state already captured — `compute`, `derive`, `format`, `sum` | `pure` | permitted; **blocks every later resume of the run** and forfeits verify-replay |

`DELETE` appears twice on purpose: an HTTP `DELETE` and a SQL `DELETE` are the
same class, so a reader who cannot tell which one they meant has still been
answered.

**A node whose work falls in more than one row takes the strictest class any of
those rows names** — `external_effect` over `recorded_effect` over `pure`.
Mutating is not cancelled by also reading.

This is stated here because it is the case a reader actually has — real nodes
read and then write — and leaving it to be inferred is how the softer class
gets chosen. **Two things about it are said plainly rather than left to be
discovered.** It is not a restatement: 4.1 gives three overlapping descriptions
and no precedence, and 4.2's latitude permits the conservative class without
requiring it, so this clause decides a case 4.1 left open. And **nothing
enforces it**: the runtime detects the sub-case where a node declared
`recorded_effect` voluntarily records an `external_effect` descriptor, and a
node that simply performs the write is invisible to every gate Motus has. A
contract is binding exactly where a gate checks it; this clause is a rule for
the person declaring the node, and it says so rather than implying a check.

**The asymmetry is the whole point, and it is why the two classes are not
symmetric mistakes.** A read declared `external_effect` costs safe resumes — a
real price, paid silently, forever. A write declared `recorded_effect` **never
meets the resume guard at all**: the run resumes and the write happens twice,
with no idempotency key required and no receipt demanded. One error costs
availability. The other costs correctness, in a system whose purpose is to be
believed.

**When the operation is not in this table**, 4.1 governs and the question to
ask is 4.1's: does this node's effect on the outside world survive the run? If
the answer is unclear, 4.2's latitude exists, the price above is what it costs,
and paying it deliberately is a decision — being unaware of it is not.

## 5. Redaction

5.1. Sensitive content MUST be written through the redaction API, which
records `{kind: "redacted", hash, policy_ref}` in place of the value.
Causality survives; the secret does not enter the trace.

5.2. A node MUST NOT hand-construct an object whose top-level `kind` equals
`"redacted"` outside the redaction API. The runtime rejects such values at
the write boundary, keeping redacted values **reserved and runtime-produced**
within a trace the runtime wrote. (Not cryptographically unforgeable: a JSON
trace received from outside is only as trustworthy as its provenance until
integrity activates in schema 1.1.)

5.3. Prompts, credentials, tool arguments containing user content, and any
value covered by a consumer's data policy SHOULD be redacted by default and
exposed only by explicit policy. **The redactable surfaces are exactly the
Value positions** — fact/decision values, rejection evidence, context draws,
metadata values. Intent, error messages, effect descriptions and reason
fields are plain-text surfaces that identify and explain: they are NOT
redactable, and a node MUST NOT place secret content there (cross-review
MF2-06, narrowed by decision — a prompt is a value, never an intent string).

## 6. Nondeterminism and identity

6.1. A node in a run that declares reproducibility MUST draw time, randomness
and generated identifiers from the RunContext (`ctx.now()`, `ctx.rand()`,
`ctx.uuid()`). Each draw is recorded in the transition record in draw order.

6.2. A node using ambient sources (`datetime.now()`, `random`, `uuid4`,
unrecorded network reads) in a reproducibility-declared run downgrades the
run's `replay_capability` to `partial` or `none` when detected, with a named
constraint. Detection is best-effort in v1.0; the honest path is §6.1.

6.3. Node identity: the runtime resolves a node's reported name through
decorator chains (unwrap) so the trace names the function that did the work,
never its wrapper. `code_fingerprint` recipe, fixed exactly: for each
declared node in declaration order, the four-element JSON array
`[declared_name, qualified_name, source_hash, config_fingerprint]` where
`source_hash` is the lowercase-hex SHA-256 of
`inspect.getsource(inspect.unwrap(f))` encoded as UTF-8, and
`config_fingerprint` binds the callable's *configuration* — because source
alone does not identify behavior for partials, bound methods, class
instances or closures:

- a function that captured nothing contributes `"none"` — the test is the
  interpreter's own `__closure__ is None`, and it is deliberately not narrowed
  to module-level functions: a `def` returned by a graph-builder factory
  captures nothing and has no configuration to fingerprint, and treating it as
  opaque put a false constraint on the most common shape real code takes;
- a `functools.partial` contributes the fingerprint of its canonical-JSON
  args/keywords (which MUST therefore be strict JSON values);
- a callable instance contributes the fingerprint of its `motus_config()`
  return (a strict JSON value the class opts into providing);
- anything else — closures over non-JSON state, instances without
  `motus_config()` — contributes `"opaque"`, and an opaque entry downgrades
  the run's `replay_capability` at most to `partial` with the constraint
  `node:<name>:opaque_config`.

**If you arrived here from `node:<name>:opaque_config` in a real trace, this
is the paragraph you want.** A factory that closes over a config object is the
obvious way to write a parameterised graph in Python, and it is the one shape
this recipe cannot re-identify — a captured object is not a JSON value, so the
run header would record a configuration nobody can reconstruct. The first
external integrator wrote exactly that and forfeited replay on seven of nine
nodes. The two bullets above are the way out and neither is a workaround: a
`functools.partial` over strict-JSON keywords, or a callable instance whose
`motus_config()` returns the same configuration as a JSON value. Both turn
`opaque` into a fingerprint, so a reader can tell whether two runs were judged
by the same rules — which is what the constraint was protecting.
`examples/04_parameterised_nodes.py` runs all three shapes side by side and
prints what each does to the fingerprint.

**Rewriting a closure as a callable instance is mechanical**: the captured
names become `__init__` parameters, the inner `def` becomes `__call__`, and
`motus_config()` returns them as a JSON value. The behaviour is unchanged; what
changes is that the configuration is now written down.

```python
def make_nodes(cfg):                       # every inner def captures cfg
    def check(state): ...                  # -> node:check:opaque_config
    return {"check": check}

class Check:                               # the same behaviour, fingerprinted
    def __init__(self, cfg): self.cfg = cfg
    def motus_config(self): return asdict(self.cfg)
    def __call__(self, state): ...
```

The fingerprint is the SHA-256 of the canonical JSON encoding
(contract/README.md) of the list of those arrays. Nodes whose source is
unavailable (C extensions, REPL) contribute `"unavailable"` as source_hash
with the same downgrade rule.

**What `replay_capability` does and does not mean.** It reports whether this
runtime can RE-IDENTIFY each node's configuration, and nothing wider. The name
invites a stronger reading — *can this run be reproduced* — and that reading is
wrong in both directions, so both are stated here rather than left to be
discovered:

- `full` is not a purity certificate. `motus_config()` is an ATTESTATION by the
  class author, taken at its word (see `motus_config()` below). A callable that provides it and reads
  a module-level object reports `full`, because nothing inspects what
  `__call__` does. §1.2 says why nothing can.
- `partial` is not an accusation. It says one node's configuration could not be
  reduced to a JSON value, which is a limit of this fingerprinting recipe and
  not a finding about the node.

Nothing in the runtime or the validator refuses a run for its capability: it is
recorded so a reader can judge, and rule T10 only enforces that it never
improves over a run. A field that reads as a verdict and is a description must
say which it is.

`motus_config()` is an attestation method, not an event hook. A callable that
provides it MUST make it pure, total, cheap and strict-JSON-valued. The runtime
evaluates it at construction and at each run start so mutable configuration
cannot retain a stale fingerprint. Failure is reported as
`NodeConfigurationError` before `run_started`: without valid identity material
there is no run whose code fingerprint Motus can truthfully record.

6.4. **The ambient draws, named.** 6.1 says a node MUST draw time, randomness
and identifiers from the RunContext, and 6.2 says what happens when it does
not. Neither names the calls, and a reader auditing their own node is holding
calls.

This table names the ones a program can find in source. It is **not
exhaustive** and does not try to be — 6.1 governs, and a draw absent from this
table is not thereby permitted. Its purpose is that a tool reading a node can
say which clause it is applying and to what, rather than reporting a suspicion.

| Ambient draw | Mediated form |
|---|---|
| `datetime.now`, `datetime.utcnow`, `time.time`, `time.monotonic` | `ctx.now()` |
| `random.random`, `random.choice`, `random.randint`, `random.shuffle` | `ctx.rand()` |
| `uuid.uuid1`, `uuid.uuid4`, `os.urandom`, `secrets.token_hex` | `ctx.uuid()` |

**A draw found here is a finding about `replay_capability`, never about
honesty.** 6.2's downgrade is a description of what this runtime can
re-identify, and a node that draws ambiently for a run that declares no
reproducibility has broken nothing. Any tool reporting a row of this table
reports it as 6.2 does, or it is reporting something the protocol does not
say.

## 7. Failure, attempts, and dispositions

7.1. A node fails by raising. The trace separates **what the attempt did**
(`outcome`: returned | raised | cancelled) from **what the runner then did
about it** (`disposition`: commit | retry | abort | continue). A returned
attempt is committed; a raised attempt is retried, aborts the run (strict),
or is continued past (exploration); a cancelled attempt aborts. A node MUST
NOT swallow its own failure into a silently "successful" empty result; if it
can degrade meaningfully, it returns a state recording a rejection or a
decision saying so.

7.2. **The transactional rule for writes**: with the state→state protocol, an
attempt that raised or was cancelled handed nothing back — its writes are
structurally empty in the trace (schema-enforced), and whatever it did
internally before raising never reached the run. Captured reads and context
draws up to the interruption ARE preserved: they are evidence of what the
attempt consumed, not of what it produced.

7.3. Every attempt is opened by an `attempt_started` record before the node
is invoked and closed by its transition record. Retries produce a fresh pair
per attempt. Retry exhaustion preserves the full trace including every
attempt (inherited conformance corpus, guarantees.md §5). An attempt
interrupted by hard cancellation or crash leaves its `attempt_started`
unclosed — visible, attributable evidence, never an erased gap; whether its
external effects happened is recorded as unknown, not guessed.

## 8. What nodes may never do

- Import or call the runtime's internals, observers, or sinks.
- Spawn concurrency that outlives the call or touches the state after return.
- Read topology, environment flags, or the spec to change behavior — a node
  behaves identically wherever it is placed; variation enters through state.
- Block on external human input (that is an escalation pattern at the
  application layer, recorded as a decision + terminal, not a hung node).
