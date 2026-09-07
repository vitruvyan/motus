---
name: release
description: Cut a Motus release — version bump, relative characterization, tag, GitHub Release. Use when the founder authorises a release. Encodes ADR-006's version/evidence coupling and ADR-012's relative gate, both of which are easy to get wrong and expensive when you do.
---

# Releasing Motus

A release is **one act, not four**. ADR-006 couples the version string to
committed performance evidence: `bench_motus.py` stamps
`f"vitruvyan-motus/{__version__}"` and the gate refuses a candidate whose
runtime identity does not match. Bumping without re-characterizing makes CI red
for a reason unrelated to the code.

**Never do any of this without explicit founder authorisation.** It creates
public objects. Since ADR-032, a release also publishes to PyPI as its
last step — the same authorisation covers both; that is not a second
thing to ask for.

## Order, and why it is this order

1. **Branch** `release/X.Y.Z` from `main`.
2. **Bump** `__version__`, and retarget every reference to the candidate
   evidence filename: `check_slo_baseline.py` `DEFAULT_CANDIDATE`, `ci.yml`,
   and `motus-characterize.yml` (both the output path and the artifact name).
   The tree is deliberately red between here and step 4 — say so.
3. **Characterize** by dispatching `motus-characterize-relative.yml` against
   **this branch**, so the runs are stamped with the new version.
   **Three times.** ADR-012 requires ≥3 independent dispatches; one job is an
   observation, never a verdict.
4. **Commit the evidence** under `benchmarks/relative-X.Y.Z/` and run
   `check_relative_baseline.py` over all three.
5. **PR, green, merge** with a merge commit. Verify the reviewed SHA is an
   ancestor of `main` before tagging.
6. **Annotated tag** on the verified merge SHA. The tag fires a build job that
   builds the wheel and sdist **once**, runs every gate against those exact
   files, attaches them as assets to a **draft** GitHub Release, and *also*
   uploads them as a workflow-run artifact with their SHA-256 written into both
   the draft body and the run summary. The Release is where the bytes are
   shown; the run is where they are authenticated — a draft is editable by
   anyone with `contents: write`, a completed run's artifact is not.
7. **Read the draft and publish it.** Check the hash against the run summary,
   not the draft body — the summary is what cannot be rewritten. This is the
   founder's act and it is the approval, made with the artifact and its hash on
   screen rather than a click on an automated gate: required reviewers on a
   deployment environment are public-repository-only on this plan, so for the
   first publication there is no button to press. Publishing the draft fires
   `release: [published]`, which runs the publish job.
8. The publish job **downloads both the run artifact and the Release assets,
   and refuses unless they are byte-identical** — a swapped asset is a named
   failure, not a publication. It does not build. Confirm three equalities
   before calling the release finished: the workflow ran and succeeded; the
   version on the index equals the tag; the SHA-256 on the index equals the
   SHA-256 in the release note. A publication that did not happen is a release
   that is not finished. PyPI does not allow a version to be re-uploaded — a
   wrong wheel is yanked and a new version released — the same discipline *What
   will go wrong* already imposes when it says never to make the gate pass by
   retrying, now with an external witness.

## What will go wrong, because it did

**The gate will refuse a legitimate release, and you must not make it pass by
retrying.** Re-dispatching until a fast runner is allocated is selecting
evidence by outcome. If a metric exceeds its budget, either fix it, or record a
**scoped, machine-enforced exception** in `DECLARED_EXCEPTIONS` keyed on
`(baseline ref, candidate version, metric)` with an ADR behind it. Widening the
budget instead makes the overage invisible forever.

**A regression will hide behind a slower runner.** When a figure looks bad, run
the differential before concluding anything: characterize the *previous
release's code* on *today's runner*. The 0.7.0 release found +24 %, of which
29 % was the machine and 23 % was ours — and 72 % of ours was a single line.

**"Within noise" is not "no difference".** If the measurement spread is wider
than the effect, the honest report is that the measurement cannot answer. The
checker prints that; read it.

## Before you tag

Dispatch `motus-verifier`. Then confirm by hand: the tag points at the merge
commit you verified, the README names the new release, and no ADR is left
`PROPOSED` that this release depends on.

## Release notes

State the cost. ADR-012's canonical figures — median of job ratios across ≥3
dispatches — belong in the notes, with the range beside them and any exception
named. A release that publishes its own regression is worth more than one that
implies there wasn't one.
