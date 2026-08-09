# ADR-018 — H1 measured: the executor's share of a real request

- **Status:** PROPOSED
- **Date:** 2026-08-09
- **Authority:** founder decision to publish Motus on PyPI, which the cumulative gate currently blocks
- **Depends on:** ADR-012, ADR-017
- **Amends:** ADR-012 §"The reason for deferring is a hypothesis" (H1 is now measured, and the ADR said it would be amended to say so) and ADR-012 §4 (what a cumulative FAIL entails — **not** its budget, its anchor or its arithmetic, none of which change)

## Context

### What the gate says about 0.8.0

Three independent dispatches, paired on one machine each, `benchmarks/relative-0.8.0/`:

```
Release budget (+10% over v0.7.0) — canonical is the median across jobs
  PASS  Per-node overhead         +70.3%   ADR-017 exception, ceiling +78%
  PASS  100-node no-op            +98.8%   ADR-017 exception, ceiling +105%
  PASS  Trace materialization     +24.5%   ADR-017 exception, ceiling +35%

Cumulative budget (+20% over anchor v0.6.1)
  FAIL  Per-node overhead         +82.6%
  FAIL  100-node no-op           +121.6%
  FAIL  Trace materialization     +20.3%
```

The cumulative arm is the one ADR-012 §4 built to be unwaivable. It has done
exactly what it was built to do, on its first outing, and the README says so
rather than hiding it. Nothing below suggests it was wrong.

### The hypothesis it was waiting on

ADR-012 recorded H1 as a hypothesis and pre-registered how to kill it:

> **H1.** Against a real Vitruvyan workload, executor overhead is a negligible
> share of wall-clock time […]
>
> **Falsification, during the Vitruvyan integration.** Instrument one real
> graph and record executor overhead as a fraction of run wall-clock. H1
> survives if that fraction is **under 1 %**.

That threshold was fixed before any measurement existed. It is used here
unchanged, which is the only reason the result below is worth anything.

### The measurement

2026-08-09. A five-node Motus graph wrapping a real call to the live
`api_graph` service (`frontier_graph`, `POST /run`), driven by a real Italian
user query. The harness is `e2e/pipeline_query.py`; the service was the
deployed one, not a stub.

```
request, end to end   :  6492.8 ms
of which the pipeline :  6489.1 ms
of which Motus        :     3.7 ms   =  0.057 %
```

Six runs. The pipeline's own latency varied between 4 447 ms and 57 958 ms for
the identical query; Motus's share never approached the threshold. The trace
validated clean on every run, schema 2.0.0, integrity chain present.

**H1 survives, by a factor of 17 against its own pre-registered criterion.**

### What the measurement does not say

ADR-012 attached a second clause to the falsification — *"or if it grows with
graph size, which the no-op profile cannot tell us"* — and this measurement
does not test it. The graph had five nodes.

The growth is not mysterious, though, and it can be stated as an inequality
rather than a hope. At 0.8.0's measured 116 µs per realistic node, the
executor's share stays under 1 % while

    nodes × 116 µs  <  0.01 × wall-clock

which is **560 nodes** for a 6.5-second request, and **43 nodes** for a
500-millisecond one. A consumer outside that envelope is outside H1, and the
number it must check is its own, not this one.

And the workload measured is I/O-bound: the node awaited a service that took
seconds. A consumer whose nodes are pure computation gets the full doubling
ADR-017 declared, in the words ADR-017 already uses — *"invisible against a
node that calls a model, and a doubling of the engine on pure computation"*.
This ADR does not soften that sentence and does not license anyone to.

## Decision

### 1. H1 is confirmed for the measured envelope, and only there

0.057 % against a pre-registered 1 % ceiling. The claim is bounded by the
inequality above and by the workload class; it is not a general statement that
executor cost does not matter.

### 2. The cumulative gate is not re-anchored, not widened, not waived

The anchor stays `v0.6.1`. The budget stays +20 %. The arithmetic stays. The
gate keeps failing on 0.8.x and keeps printing that it failed. Every route that
would have made the red go away is rejected below, and the reason is the same
one in each case: a gate that moves when it becomes inconvenient has never
measured anything.

### 3. What a cumulative FAIL entails changes, and only that

A release MAY ship with the cumulative arm failing **if and only if all four
hold**:

1. the **per-release** arm passes, exceptions declared per ADR-012 §3;
2. a **real-workload measurement** is published for that release — one graph, a
   real consumer, executor share of run wall-clock, with the method and the
   host;
3. that share is **under 1 %**, the criterion ADR-012 pre-registered;
4. the README carries **both** figures — the failing ratio and the measured
   share — in the same paragraph, so neither can be read without the other.

Absent a real consumer, condition 2 cannot be met and the exemption does not
exist. This is deliberately stricter than what 0.8.0 shipped under: 0.8.0 went
to `main` untagged with no such measurement, and could not have satisfied this
rule on the day it was written.

### 4. The measurement is per release, not once

Condition 2 is re-taken for every release that uses this route. A share
measured against 0.8.1 says nothing about 0.9.0, and an inherited number is
the same error as an inherited baseline.

## Consequences

**What is given up.** "The gate is green" stops being the same statement as
"the release may ship". A reader of CI alone can no longer tell; they must read
the release note. That is a real loss of legibility and it is the price of not
falsifying the gate instead.

**What is gained, beyond shipping.** A new obligation that did not exist: no
release may claim performance acceptability from synthetic benchmarks alone
once a consumer exists. The project's own history is the argument — the phrase
*"Free."* entered this record earlier in the cycle by reasoning from a synthetic
A/B, and ADR-012 was written to stop it happening again.

**What stays broken.** The engine is genuinely +82.6 % per node against v0.6.1
and this ADR does not repair one microsecond of it. Issue #38 stays open. If a
consumer appears whose share exceeds 1 %, condition 3 fails, the exemption
closes, and the invocation path is reworked — which is what ADR-012 said would
happen and remains true.

## A wrong turn, recorded because it is inviting

While preparing this ADR the cumulative regression was first computed by
comparing the stored candidate files — `candidate-v0.6.1-epyc-py310.json`
against `candidate-v0.8.0-epyc-py310.json` — which gives **+127 %**.

That number is meaningless. The two files were collected on different hosts: an
EPYC 9V74 and an EPYC 7763, which ADR-012 opens by recording as 29 % apart on
identical code. Comparing them measures the runner, which is the exact failure
the relative harness exists to remove. The correct figure, +82.6 %, comes only
from the paired same-machine runs.

The mistake is inviting because the candidate files sit in one directory, are
named alike, and carry the same schema; nothing about them announces that they
are not comparable. A future reader reaching for them should reach for
`benchmarks/relative-*/` instead.

## Alternatives rejected

**Move the anchor to v0.7.0 or v0.8.0.** Rejected on ADR-012 §4's own words:
*"an anchor that advances with every release measures nothing cumulative at
all."* The integrity chain's cost would vanish from the record on the day it
was paid.

**Widen the cumulative budget to fit.** Rejected: a budget chosen to
accommodate the change in front of it is not a budget. +20 % was derived from
job-to-job variation before 0.8.0 existed; +90 % would be derived from 0.8.0.

**Make the integrity chain optional.** Rejected on contract grounds, not taste.
Schema 2.0.0 **requires** the chain, and T11 refuses non-null hashes in 1.x
precisely so *"unverified hashes cannot masquerade as tamper evidence"*. A
switchable chain reintroduces the ambiguity T11 was written to end, and the
chain is the product's claim, not an ornament on it.

**Block until the engine recovers the 34 %.** Rejected with its cost stated.
ADR-017 already records four cheaper canonical forms attempted, one of which
measured *slower*; the remaining work is real and unbounded. Against it stands
the measurement: the work would improve 0.057 % of a request nobody has
complained about, while the consumer that would have paid for it waits. If the
envelope in §"What the measurement does not say" is ever exceeded, this
rejection is void and the work is due.

**Publish and say nothing.** Rejected. The README currently explains why 0.8.0
is untagged. Removing that paragraph without replacing it with this one is the
single move this ADR exists to prevent.
