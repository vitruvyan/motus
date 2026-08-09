# ADR-018 — H1 measured: the executor's share of a real request

- **Status:** ACCEPTED
- **Date:** 2026-08-09
- **Accepted:** 2026-08-09 by the founder
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

**Release and host, which `guarantees.md` §3 requires and condition 2 below
repeats.** Motus at `64bea3f` — `main` after PR #68, the tree that becomes
0.8.1. Host: AMD EPYC Processor (with IBPB), Linux 6.8.0-124, CPython 3.12.3.
This measurement is 0.8.1 evidence and may not be inherited by any later
release, per decision 4.

```
request, end to end                        :  6492.8 ms
of which the awaited service               :  6489.1 ms
of which everything else                   :     3.7 ms   =  0.057 %
```

**The third line is an upper bound on Motus, not a measurement of it.** It
contains the consumer's own node code — `json.loads` over the service's
response body, the construction of fifteen Facts — because the harness times
the network call and the whole run, and attributes the difference to "not the
service". Motus's true share is smaller than 0.057 %, by an amount not
separated here. The bound is stated in the direction that can only weaken the
argument, which is the only direction it may be stated in.

Six runs. The service's own latency varied between 4 447 ms and 57 958 ms for
the identical query; the bound never approached the threshold. The trace
validated clean on every run, schema 2.0.0, integrity chain present.

**H1 survives, by a factor of 17 against its own pre-registered criterion, on
an upper bound.**

### What the measurement does not say

ADR-012 attached a second clause to the falsification — *"or if it grows with
graph size, which the no-op profile cannot tell us"* — and this measurement
does not test it. The graph had five nodes.

An earlier draft of this ADR answered it with an inequality, `nodes × 116 µs <
0.01 × wall-clock`, and gave boundaries of 560 and 43 nodes. **That was wrong
and it is withdrawn.** Measuring it on the host above rather than asserting it:

| graph | cost of `run()` |
|---|---:|
| 5 pure nodes, nothing written | 0.53 ms |
| 5 pure nodes, 15 facts written | 0.78 ms |
| 5 pure nodes, 40 facts written | 1.04 ms |
| 200 pure nodes, nothing written | 19.40 ms |

Two things follow, and the second is the one that kills the formula. Node count
alone is roughly linear at 97 µs per node **for nodes that write nothing** —
and the moment they write, the cost follows the payload instead: the same
five-node graph doubles between 0 and 40 facts without gaining a node. A trace
is a record of what happened, so a graph that records more costs more, and no
function of node count can bound that.

**So the envelope is not stated, because it is not measured.** What is measured
is one point: this graph, this payload, this host, 0.057 %. A consumer must
take its own number, and decision 4 requires exactly that of every release.

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

1. the **per-release** arm passes, with any exception declared the way ADR-012
   §"0.7.0 does not fit the budget it is setting" requires — a row in
   `DECLARED_EXCEPTIONS` in `check_relative_baseline.py`, keyed on the release
   and carrying its reason (ADR-012 §3 is about absolute figures and does not
   govern exceptions);
2. a **real-workload measurement** is published for that release — one graph, a
   real consumer, executor share of run wall-clock, with the method and the
   host;
3. that share is **under 1 %**, the criterion ADR-012 pre-registered;
4. the README carries **every failing cumulative ratio** — all of them, not the
   mildest — alongside the measured share, in the same paragraph, so neither
   can be read without the other. For 0.8.x that is three numbers, and a README
   that printed only `+20.3 %` while omitting `+121.6 %` would satisfy a
   singular reading of this rule and defeat it.

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
