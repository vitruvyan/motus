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
module-level object as a way of passing information. Enforcement is layered:
top-level mutation of the state raises by construction; interior mutation of
container values (a list inside a fact) is a protocol violation that v1.0
does **not** detect — verify-replay (0.6) is what eventually catches a node
whose "pure" output depended on it.

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

2.2. Every value written MUST be JSON-serializable or an explicit redacted
value (§5). A value that cannot be written down is refused at the boundary —
the runtime never carries what it cannot record (invariant V distinguishes
*recording structure* from *storing content*; refusal is about structure).

2.3. A node that declines to act SHOULD say so: a rejection with a reason is
the difference between "didn't" and "wouldn't", and it is what why-not
explanations are made of.

## 3. Reading — record-and-compare

3.1. The runtime captures the set of state keys a node actually reads and
records them, with the causal reference to the transition that wrote each
value, in the node's transition record. Capture is not optional and not
declarative — it is what the runtime observed.

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
| `external_effect` | Mutates something outside the run (writes a row, sends a message, POSTs) | Default class for any undeclared node — the conservative reading. SHOULD provide an idempotency key per effect; MUST once effect enforcement lands (0.7). The runtime promises at-least-once with idempotent effects, never exactly-once. |

4.2. Misclassification is not a crime the runtime can always detect in v1.0 —
but it is one the trace makes falsifiable over time (verify-replay for `pure`;
effect enforcement for `external_effect`). Classify honestly or conservatively.

## 5. Redaction

5.1. Sensitive content MUST be written through the redaction API, which
records `{kind: "redacted", hash, policy_ref}` in place of the value.
Causality survives; the secret does not enter the trace.

5.2. A node MUST NOT hand-construct an object whose top-level `kind` equals
`"redacted"` outside the redaction API. The runtime rejects such values at the
write boundary to keep redacted values unforgeable within a trace.

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
never its wrapper. `code_fingerprint` recipe, fixed exactly: for each declared
node in declaration order, the two-element JSON array
`[qualified_name, lowercase_hex_sha256_of_source]` where source is
`inspect.getsource(inspect.unwrap(f))` encoded as UTF-8; the fingerprint is
the SHA-256 of the canonical JSON encoding (contract/README.md) of the list
of those arrays. Nodes whose source is unavailable (C extensions, REPL)
contribute `[qualified_name, "unavailable"]` and downgrade
`replay_capability` at most to `partial`.

## 7. Failure

7.1. A node fails by raising. The runtime records the failure — type, message,
attempt — in the transition record, preserves the accumulated trace, and
applies policy. A node MUST NOT swallow its own failure into a silently
"successful" empty result; if it can degrade meaningfully, it records a
rejection or a decision saying so.

7.2. Retries, when configured, produce one transition record per attempt.
Retry exhaustion preserves the full trace including every attempt (inherited
conformance corpus, guarantees.md §5).

## 8. What nodes may never do

- Import or call the runtime's internals, observers, or sinks.
- Spawn concurrency that outlives the call or touches the state after return.
- Read topology, environment flags, or the spec to change behavior — a node
  behaves identically wherever it is placed; variation enters through state.
- Block on external human input (that is an escalation pattern at the
  application layer, recorded as a decision + terminal, not a hung node).
