# ADR-012 — Gating performance on the ratio, not on a ceiling

- **Status:** ACCEPTED
- **Date:** 2026-08-06
- **Accepted:** 2026-08-06 by the founder, after the canonical values, the
  sampling rule, the cumulative anchor, machine enforcement of the 0.7.0
  exception and hypothesis H1 were made explicit.
- **Authority:** founder direction during the 0.7.0 release
- **Depends on:** ADR-006 (v0.6 performance profile and the version/evidence
  coupling), ADR-002 (CI enforcement)
- **Amends:** `contract/guarantees.md` §3, which publishes absolute ceilings and
  the methodology behind them. ADR-001…011 stand unedited.

## Context

The 0.7.0 release characterization failed its own gate, and finding out why
took a differential nobody had run before.

| subject | CPU | per-node |
|---|---|---|
| v0.6.1 code, the runner it was characterized on | EPYC 9V74 | 51.23 µs |
| v0.6.1 code, a runner allocated today | EPYC 7763 | 66.11 µs |
| 0.7.0 code, that same runner | EPYC 7763 | 69.74 µs |
| published ceiling | | **56.25 µs** |

**v0.6.1's own released code fails v0.6.1's own ceiling**, unchanged, on
hardware allocated eight months later. The ceiling was derived from an EPYC
9V74; the gate guards it by requiring `"EPYC" in cpu_model.upper()`, and an
EPYC 7763 measured 29 % slower. `EPYC` is a brand, not a performance class.

A gate that answers differently on identical code is not measuring the code. On
shared runners the absolute gate had become a coin toss, and the two ways to
make it pass were both dishonest: re-dispatch until a fast host is allocated,
which is selecting evidence by outcome, or re-baseline the ceiling to whatever
was measured, which is a threshold that agrees with every measurement and
therefore constrains none.

It also nearly hid something real. The same differential showed 0.7 was **24 %**
slower than 0.6.1 on identical hardware, 72 % of which was one line —
`_is_async_node` asked per node on every `_start`, for a property of the
callable that cannot change between runs. Fixed in `786c534`; the residue is
about 6 %. That regression had passed seven adversarial rounds, a full test
suite and a code review. **The only thing that saw it was ADR-006's insistence
that a version bump re-characterize.**

## Decision

### 1. A release is gated on the ratio to the previous release

Both halves are measured **in the same job, on the same host, interleaved in a
rotating order**, so machine speed cancels and drift during the job is spread
across the subjects rather than concentrated in one.

**One harness, two subjects.** `bench_motus.py` always comes from the candidate
checkout; only `PYTHONPATH` differs. A difference in the numbers therefore
cannot come from a difference in how they were taken — which is the flaw that
made the historical comparison between two separately collected documents
uninterpretable.

Gated metrics: per-node overhead, 100-node no-op overhead, trace
materialization. These are costs; a ratio means something for a cost.

**Sampling and the decision rule**, normative, because a gate whose statistic
is implicit can be argued with after the fact:

| level | rule |
|---|---|
| one measurement | min-of-samples under the ADR-006 method — 2 warmups, ≥ 7 samples, `gc.collect()` before each — unchanged |
| one subject, one job | the **median** across ≥ 5 interleaved rounds |
| one job | the ratio of those two medians, per metric |
| one release | the **canonical value is the median of the job ratios** across ≥ 3 independent dispatches |
| the decision | the canonical value is compared to the budget |

**A single job is an observation, never a verdict.** The absolute measurement
moves 55 % between identical jobs on this runner class, so a release gated on
one of them is gated on which host GitHub allocated. The checker refuses fewer
than three jobs rather than answering from too little.

The per-job range is published beside the canonical value, so a release whose
jobs disagree widely is visible as such rather than averaged into confidence.

### 2. The budget is derived from measured variation, not chosen

Three identical characterizations were run on the reference runner class —
same code, same configuration, same inputs — to establish how much the ratio
moves when nothing changes. The budget is set comfortably above that floor and
well below the effect it must catch.

*(Observations and the derived figures are recorded in §Measurements below.)*

A budget picked to accommodate the release in front of it would be
re-baselining under a different name.

### 3. Absolute figures are recorded, with the CPU, and never gated

"How fast is it" is a real question and the answer stays published — but with
the machine that produced it named, because without that the number means
nothing. `guarantees.md` §3's Motus rows become **recorded measurements on a
stated host**, not thresholds.

The Axis reference rows and the `baseline-v0.4.0` document are untouched: they
are historical measurements the gate re-derives for internal consistency, not
ceilings anything must clear.

### 4. Cumulative regression is checked periodically, against an anchor

A weekly scheduled run compares the current default branch against a declared
**anchor** release, not merely the previous one. Its budget is deliberately
**less than the sum of successive release budgets**: a sequence of regressions
that each fit comfortably inside the per-release budget must not be able to
walk the runtime somewhere nobody agreed to go.

**The anchor is `v0.6.1`.** It is the last release characterized under the
absolute regime, which makes it the point from which this project's measured
history is continuous. It is recorded here, defaulted in
`motus-characterize-relative.yml`, and **moving it forward is a deliberate act
requiring an ADR** — never a consequence of releasing, because an anchor that
advances with every release measures nothing cumulative at all.

The cumulative arm therefore constrains nothing for 0.7.0, whose baseline and
anchor are the same release. It begins constraining at 0.8.0. That is stated
rather than glossed: a check that cannot fail on its first outing has not yet
demonstrated anything.

### 5. Measurement quality is reported alongside every figure

The checker prints the per-pair spread and flags it when it is **wider than the
effect being measured**. This is not decoration. During the 0.7 async work an
A/B on one host read 81.07/81.23/92.40 before and 81.89/77.69/94.91 after —
15 % variation between repetitions of the same configuration — and that was
written up as *"Free."* The measurement could not resolve a 23 % difference,
and "within noise" was reported as "no difference". A tool that cannot answer
must say so where the reader will see it.

## Measurements

Three dispatches of `motus-characterize-relative.yml`, same commit, same
inputs, same configuration, on the reference runner class. Nothing differed
between them except the host GitHub allocated.

| | run 1 | run 2 | run 3 | band |
|---|---|---|---|---|
| host | EPYC 7763 | EPYC 9V74 | EPYC 7763 | |
| **absolute**, v0.6.1 per-node | 64.81 µs | 41.79 µs | 60.91 µs | **55 %** |
| ratio, per-node | +6.5 % | +4.5 % | +7.6 % | 3.1 pts |
| ratio, 100-node no-op | +12.3 % | +9.2 % | +12.5 % | 3.3 pts |
| ratio, trace materialization | +0.6 % | −0.8 % | +1.1 % | 1.9 pts |

### The canonical value of each regression

Under §1's rule — the median of the job ratios — 0.7.0's figures against
v0.6.1 are:

| metric | **canonical** | observed across jobs |
|---|---|---|
| per-node overhead | **+6.6 %** | +4.5 % … +7.6 % |
| 100-node no-op overhead | **+12.3 %** | +9.2 % … +12.5 % |
| trace materialization | **+0.6 %** | −0.8 % … +1.1 % |

These three numbers are what 0.7.0 costs relative to 0.6.1. They are the
figures the gate compares, the figures the release notes publish, and the
figures 0.8.0 will be measured against. Ranges are published beside them and
are never the quoted value — a range invites picking an end.

**The absolute figure moves 55 % between identical runs. The ratio moves about
3 points.** That is the whole case for this ADR, measured rather than argued.
Note also that run 2's v0.6.1 measurement, 41.79 µs, is *faster* than the
51.23 µs the published ceiling was derived from — the allocation varies in both
directions, so the historical ceiling was not even the fast case.

### The derived budgets

Ratio measurement noise is about **±2 percentage points**. A budget below
roughly 5 % would be indistinguishable from the measurement.

- **`RELEASE_BUDGET` = +10 %**, five times the noise floor. Tight enough to be
  a constraint, wide enough that a passing release passed on its merits.
- **`CUMULATIVE_BUDGET` = +20 %** against the anchor. Less than two successive
  full-budget releases, so the budget cannot be spent twice over without
  somebody noticing.

Neither number was chosen by trying it against 0.7.0. The consequence of that
is the next section.

## 0.7.0 does not fit the budget it is setting

On the 100-node no-op metric — pure runtime overhead — 0.7.0 measures **+9.2 %
to +12.5 %** over v0.6.1, and would fail a +10 % budget on two of the three
observations.

**It is recorded as an exception rather than accommodated by a wider budget.**
Raising `RELEASE_BUDGET` to 15 % would make it pass, and would also make it
invisible for every release afterwards. A named exception on a named metric in
a named release is auditable and expires; a loosened threshold is neither.

The overage is understood, not mysterious. The no-op benchmark measures the
executor and nothing else, and 0.7's node invocation inversion adds a generator
round trip and one allocation per node — on nodes that do no work, that cost is
the entire measurement. The same change on the realistic profile is +6.6 %.

### The reason for deferring is a hypothesis, and it is stated as one

> **H1.** Against a real Vitruvyan workload, executor overhead is a negligible
> share of wall-clock time, because a node awaiting a model or a network call
> costs orders of magnitude more than the ~65 µs the executor spends around it.
> Under H1, 0.7.0's +12.3 % no-op regression is invisible end to end.

H1 is plausible and it is **not measured**. Every number in this ADR comes from
synthetic graphs of nodes that do nothing or almost nothing, and no Motus
workload has ever been run by anyone outside this repository. Reasoning from
those benchmarks to "it does not matter in practice" is precisely the step that
turned an unresolvable A/B into the word *"Free."* earlier in this cycle.

**Falsification, during the Vitruvyan integration.** Instrument one real graph
and record executor overhead as a fraction of run wall-clock. H1 survives if
that fraction is under 1 %. If it is materially higher — or if it grows with
graph size, which the no-op profile cannot tell us — the deferral is wrong and
the invocation path is reworked, with this ADR amended to say so.

**Not deferred because it is small — deferred because there is nothing yet to
judge it against.** Reducing it now would mean optimising an allocation against
a synthetic benchmark with no user behind it, which is the same misallocation
this project has already made more than once.

**The exception is machine-enforced, not a note somebody has to remember.** It
is a row in `DECLARED_EXCEPTIONS` in `check_relative_baseline.py`, keyed on
`(baseline ref, candidate's own declared version, metric)`:

```
("v0.6.1", "0.7.0", "noop_100_overhead_ms") -> ceiling +13 %, ADR-012
```

The ceiling is the canonical +12.3 % plus a small margin, not a round number
chosen upward. The key includes the candidate's version as the runtime itself
declares it, so the exception **expires by construction**: the identical
measurements, relabelled 0.8.0, fail — verified, not asserted.

The checker prints every exception it applied, under its own heading with the
authority and the reason, so a passing gate cannot conceal that it passed
conditionally.

## Consequences

- A release can be gated on shared, heterogeneous runners without the gate
  measuring the runner. This is what makes the check meaningful at all on
  GitHub-hosted infrastructure.
- The historical absolute ceilings stop being enforceable claims. That is a
  reduction in what the contract asserts, and it is deliberate: the previous
  assertion was not reproducible, and an unreproducible guarantee is worse than
  an honest measurement.
- A release must now state its cost relative to the last one. 0.7.0's is
  **+5.5 %**, measured, published, and explained rather than absorbed.
- The characterization job gets longer: it measures two or three trees instead
  of one. Acceptable — it runs on dispatch and on a weekly schedule, never in
  the pull-request path.
- `check_slo_baseline.py` keeps validating the Axis reference evidence and the
  published table's internal consistency. Only the Motus candidate ceilings
  stop being pass/fail.

## Alternatives rejected

**Pin the exact CPU model.** Correct in principle and unusable in practice:
GitHub allocates hosts we do not choose, so characterization would succeed only
by luck, and the temptation would be to re-dispatch until it did — selecting
evidence by outcome.

**Re-baseline the ceilings to the current runner class.** Fastest, and it is
what the failing gate invites. Rejected because a threshold that adjusts to
whatever was measured constrains nothing, and because it would have buried the
24 % regression rather than surfacing it.

**Dedicated hardware.** The honest fix for absolute numbers, and out of
proportion to a project with no users. Revisit if absolute latency ever becomes
a commitment to someone.
