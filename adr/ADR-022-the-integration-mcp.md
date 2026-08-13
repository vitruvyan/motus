# ADR-022 — the integration MCP: every answer derived, and a diagnosis that ships the command to reproduce it

- **Status:** PROPOSED
- **Date:** 2026-08-13
- **Authority:** CTO, on the founder's direction that the MCP tells an agent
  how to read Motus and how Motus wants nodes designed, never entering the
  merits of an audit — and on the founder's addition of 2026-08-13 that it
  must also carry a **debug capability**
- **Depends on:** nothing in the trust model. This ADR touches no receipt, no
  commitment, no anchor, and promises nothing about audit
- **Advances:** roadmap phase 1b
- **Amends:** nothing. Ships as an optional extra, absent unless installed

## Context

### The measurement, not the intuition

We have evidence that prose does not carry this protocol. Commit `24adb30` is
titled *"two examples for the part of the protocol a first integration gets
wrong"* — the gap was already known, and the answer was two more examples. An
automated reviewer then read those examples and the Terraveler brief and
**inverted the effect classification twice in one pull request** (#77): it
called a database write `recorded_effect`, where the protocol says
`external_effect`, and read-only calls the opposite.

That is not a reader failing. `recorded_effect` and `external_effect` are two
plausible names for the same intuition — *"this node talks to the outside"* —
and the protocol's meaning is the asymmetry between them: a read declared
external only blocks safe resumes, while **a write declared recorded never
meets the resume guard at all.** A classification with a consequence that
strong does not survive being paraphrased. It survives being answered, in the
concrete case somebody is holding.

### The failure this ADR exists to prevent

An MCP server is a comfortable place to put a summary. Summaries drift, and
they drift silently, because nothing recompiles when a document and a server
disagree. **In this project of all projects, our own server misstating our own
protocol demonstrates the opposite of the thesis.** Motus exists to make a
system's account of itself checkable. An MCP that answers from memory is an
account of Motus that nobody can check.

## Decision

### 1. The derivation rule, and its one extension

> **Every answer is derived, at call time, either from a source in the
> repository or from an artefact the caller supplied. Never from memory. Every
> answer names where it came from, and an answer combining both says which part
> came from where.**

The repository half is the roadmap's rule unchanged: a tool that cannot cite
`contract/node-protocol.md`, a schema, or an ADR must not exist. If the source
changes the answer changes; if the source is deleted the tool **fails instead
of inventing**.

The extension is what makes a debug capability possible at all. A diagnosis is
about the caller's graph, the caller's trace, the caller's error — none of
which is in our repository. Answering those from repository sources alone is
impossible, and the tempting alternative is to let the model reason about the
artefact from memory, which is exactly the drift this rule exists to stop.

So the artefact is admitted as a **second derivation source**, under a
constraint that keeps the rule's force: an artefact is read by **running
shipped code over it** — the validator, `Trace.root`, `GraphSpec.from_dict` —
never by describing it. The MCP's job is to put the caller's artefact and our
code in the same room and report what happened.

### 2. No prose lives in the server, and a test says so

Every advertised tool returns content that is either present in the repository
or produced by executing repository code. A test walks the advertised surface
and asserts it, and it is cheap enough to run on every release. Without that
test this ADR is an intention.

### 3. The describing surface

Three of these execute rather than describe, which is the advantage only Motus
has here — the validator already exists and is already the thing that will
refuse the caller's graph at runtime.

- **`motus_classify(description)`** — *"I need to INSERT a row"* →
  `external_effect`, the receipt requirements, and the citation. **This is the
  tool that pays for the whole thing**, because it is the error we measured;
- **`motus_review_graph(spec)`** — the real validator on their `GraphSpec`,
  with the rule that fired. Not advice: the verdict of the code that will
  refuse them;
- **`motus_review_node(source)`** — a `pure` node reading a module-level
  global; an effect node with no idempotency key;
- **`motus_explain(error)`** — `DeclarationViolation`, `ReplayMismatch`,
  `UnsafeResume`: what the runtime means, and what usually causes it;
- **`motus_start_here()`** — the shape of a Motus program in thirty lines;
- **`motus_where(intent)`** — where this code goes.

### 4. The debug capability

**`motus_diagnose(artefact, symptom)`** — the caller hands over what they
actually have (a trace, a graph spec, a node's source, a traceback) and what
they observed. The server runs the shipped code over it and reports what that
code said.

It is a distinct capability rather than a seventh describing tool because the
three decisions below apply to it and to nothing else.

**4a. Every diagnosis ships the command that reproduces it.**

```
verdict:  this trace has no derived root
because:  record seq=14 has prev_hash 3f9c…, and seq=13 digests to a1b7…
reproduce: python -m vitruvyan_motus.contract.validate trace ./run-4711.json
```

The caller can run that line and get the same verdict without us. This is the
load-bearing decision of the whole capability. A diagnostic tool is an
authority, and **an authority whose answers cannot be independently reproduced
is the precise thing this product exists to make unnecessary.** A Motus MCP
that asks to be believed is a contradiction in its own terms.

**4b. A diagnosis never proposes a change that removes evidence.**

If a trace does not verify, the answer is *which record broke the chain* —
never *regenerate the trace*. If a declaration blocks a resume, the answer is
*which declaration and what the protocol requires of it* — never *declare it
`pure`*.

This is not caution, it is the predictable failure mode. An agent is under
pressure to make an error stop appearing, and it will take the shortest path
offered. Here the shortest path destroys the property. A suggestion that
happens to be correct Python and happens to delete the audit is the single
worst output this server could produce, and it is also the most natural one.
It is refused in writing, and a test asserts that no diagnosis for a
verification failure suggests regenerating, deleting, or bypassing.

**4c. A diagnosis reports structure, never payload contents, unless asked.**

Traces carry the caller's data — states, facts, results, whatever their nodes
recorded. A diagnosis names records by `seq` and `kind`, and quotes a payload
only when the caller asks for it explicitly, **and says what it disclosed when
it does**. An MCP that quietly widens what leaves a customer's machine to make
an error message friendlier has made a decision that was not its to make.

**4d. "I cannot tell" is a permitted answer, and the tool must be able to give
it.** A symptom the shipped code cannot reproduce gets that verdict and the
commands that were tried. Guessing here costs more than silence, because a
confident wrong diagnosis sends somebody to rewrite working code.

### 5. What the MCP never does

It **never enters the merits of an audit.** It does not say whether a decision
was right, whether recorded facts are true, whether a run should have happened,
or whether a trace is trustworthy in any sense beyond what the shipped code
computes. It says what Motus refuses and why, and how the protocol wants a node
designed. Everything past that line is the auditor's, and the customer's, and
selling a tool that blurs it would sell the one thing we have committed to
never automate.

### 6. Off until installed

`pip install vitruvyan-motus[mcp]`. No impact on the runtime, no import on any
default path — the same house rule as ADR-021 decision 1. A library that grows
a server by default has become a service, and nobody decided that.

## Consequences

**The documentation splits, and the README stops doing two jobs.** It currently
has to convince a human to care *and* teach an implementer, which is why it is
eight hundred lines and serves neither well. The README becomes what a human
reads to decide; the MCP is what an agent uses to build.

**Tool usage becomes a defect report against the documentation.** A question
the MCP answers well is evidence that the *document* should answer it too. This
runs one way only: the MCP is never a reason to leave a document wrong. The
failure mode to watch for is the MCP quietly becoming the place documentation
gaps hide, and the signal that it is happening is a question asked often
through the server and answered nowhere in prose.

**`motus_diagnose` will be asked to fix things.** Decision 4b holds the line on
the dangerous half; the rest is ordinary and useful — an agent that learns the
protocol from concrete verdicts on its own code is the outcome this exists for.

**Terraveler is the first test.** They are integrating now, and the alternative
we would otherwise hand them is a three-hundred-line brief that a review
already found wrong in several places. Whatever they find outranks whatever we
think.

## Alternatives rejected

**A server of hand-written summaries.** Faster to build and it would work on
the day it shipped. It becomes a second source of truth immediately, and it is
the exact artefact this product exists to argue against. Rejected on the
thesis, not on the effort.

**No debug capability, only description.** The founder's addition, and it is
right for a reason worth recording: an implementer does not arrive with a
question, they arrive with a **failure**. A server that can only answer
questions meets them one step after the moment they needed it, and the step
between is where they invent a wrong model of the protocol.

**A diagnosis that also applies the fix.** Refused. The gap between diagnosing
and editing is where a human decides whether the fix is acceptable, and 4b
exists precisely because some fixes are not. Removing that gap would automate
the one class of change we have written down as never automatic.

**Letting the model read the artefact and reason about it.** This is the
comfortable version of decision 1's extension and it voids the whole ADR: the
answer would then come from memory with an artefact attached, which is
indistinguishable, to the caller, from an answer derived by running our code —
and wrong at a rate nobody can measure.
