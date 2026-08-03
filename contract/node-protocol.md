# Node Protocol (normative)

**Status: DRAFT v1.** The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY
are to be interpreted as described in RFC 2119.

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
module-level object as a way of passing information. **Enforcement in 0.5 is
structural, not deferred**: (a) values are isolated at the write boundary —
the runtime stores its own copy, so a node mutating the object it wrote
afterwards mutates only its own garbage; (b) reads return values a node
cannot use to reach the stored ones (a fresh copy or an immutable view —
which of the two is an implementation choice, the non-aliasing guarantee is
the contract); (c) the returned state's lineage is checked on commit — it
must extend the input state's history (prefix preservation, an O(1)
structural check), so a node cannot truncate history, substitute prior
values, or return a foreign state. Verify-replay (0.6) additionally catches
behavioral impurity; it is a second net, not the first defense — retroactive
corruption of recorded evidence is impossible by construction in 0.5.

1.3. A node MUST NOT retain the received state, the returned state, or the
RunContext beyond the call. What a node wants remembered, it writes as a fact.

1.4. A node has no knowledge of the runner, the spec, other nodes, or its own
position in the graph. A node that needs to influence routing writes a
**decision**; the spec's route table dispatches on it. Control flow never
lives inside node code as a side channel (no environment reads that change
topology, no callable swapping).

## 2. Writing

2.1. Everything a node contributes is appended through the state API: facts,
decisions, rejections. Same-key writes accumulate as history; the most recent
wins on read, and the overwrite is visible in the trace rather than silent.

2.2. Every value written MUST be a strict RFC 8259 JSON value or an explicit
redacted value (§5). Strict means: object keys are strings; NaN and Infinity
are refused (Python's `json` accepts them by default — the Motus boundary
does not); the value round-trips structurally (what you wrote is what a
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
records any mismatch in the transition record's `violations` field. In v1.0
(Motus 0.5) mismatches are **recorded only** — enforcement (an undeclared
write failing the node under `strict`) is version-gated to 0.6 with the rest
of declaration enforcement, per the approved 0.5 exclusions.

3.3. A node MUST NOT read state through any channel that evades capture
(direct attribute access on internals, closures over prior states). The
captured read set is evidence; evading it is a protocol violation.

## 4. Effects

4.1. Every node has exactly one effect class, declared in the GraphSpec or
defaulted:

| Class | Meaning | Obligations |
|---|---|---|
| `pure` | Output depends only on captured reads and context draws; no observable outside effect | MUST NOT perform I/O, mutate externals, or draw ambient nondeterminism. The claim is falsifiable: verify-replay (0.6) re-executes pure nodes and compares. |
| `recorded_effect` | Performs reads of the outside world or model/tool calls whose *results* are the effect (LLM calls, HTTP GET, file reads) | SHOULD describe each effect in the transition record. Result values are recorded like any write. Receipts and reuse-on-replay arrive in 0.6 as additive schema. |
| `external_effect` | Mutates something outside the run (writes a row, sends a message, POSTs) | Default class for any undeclared node — the conservative reading. SHOULD provide an idempotency key per effect; MUST once effect enforcement lands (0.7). **Delivery honesty (0.5): the runtime makes NO general delivery guarantee for external effects** beyond the explicitly configured retry policy of the attempt — at-least-once-with-idempotent-effects becomes a stated guarantee only in the release that ships effect log + receipts + resume, and exactly-once is promised in no version, ever. |

4.2. Misclassification is not a crime the runtime can always detect in v1.0 —
but it is one the trace makes falsifiable over time (verify-replay for `pure`;
effect enforcement for `external_effect`). Classify honestly or conservatively.

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
exposed only by explicit policy.

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

- a plain module-level function contributes `"none"`;
- a `functools.partial` contributes the fingerprint of its canonical-JSON
  args/keywords (which MUST therefore be strict JSON values);
- a callable instance contributes the fingerprint of its `motus_config()`
  return (a strict JSON value the class opts into providing);
- anything else — closures over non-JSON state, instances without
  `motus_config()` — contributes `"opaque"`, and an opaque entry downgrades
  the run's `replay_capability` at most to `partial` with the constraint
  `node:<name>:opaque_config`.

The fingerprint is the SHA-256 of the canonical JSON encoding
(contract/README.md) of the list of those arrays. Nodes whose source is
unavailable (C extensions, REPL) contribute `"unavailable"` as source_hash
with the same downgrade rule.

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
