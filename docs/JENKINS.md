# Jenkins operations

## Weekly ADR-012 relative characterization

The dedicated Jenkins Pipeline job is **Motus / weekly-relative-characterization** on
`https://build.vitruvyan.com/`. Its repository-owned definition is
`Jenkinsfile.relative-characterization`; it is deliberately separate from the PR
multibranch Pipeline.

Jenkins schedules it every Monday at 04:17 UTC. The job checks out `main`, runs in
Python 3.10.12, and interleaves the current candidate with the fixed cumulative
anchor `v0.6.1` through `collect_relative_baseline.py`. It then runs
`check_relative_baseline.py --advisory`: one scheduled observation cannot satisfy
ADR-012's three-independent-job release verdict, but it still reports cumulative
drift and fails on malformed evidence. The raw `motus-relative-latest.json` is
archived even when the measurement or report fails. Jenkins retains artifacts and
build records for 90 days (at most 20 runs).

Moving the anchor requires an ADR. Changing the weekly schedule or retention policy
requires changing the Pipeline definition and the corresponding CI-foundation test.

## Pull-request frozen-contract guard

The authoritative frozen-path decision is the controller-owned Pipeline job
**Motus / frozen-contract-guard**, not a stage loaded from a pull request. It runs
on the controller every two minutes and can also be triggered by the GitHub hook.
Its Pipeline definition is stored in Jenkins configuration, outside the Motus SCM
checkout. The job fetches the current `origin/main` tip and the requested PR head,
computes their merge base for the diff boundary, then materializes
`tools/check_frozen_paths.py` from the current `origin/main` tip. The PR checkout is
therefore data only: changing `Jenkinsfile` or the checker in a PR cannot remove or
weaken the judge.

The job publishes `continuous-integration/jenkins/frozen-contract` against the exact
PR-head SHA. A missing/ambiguous PR ref, a moving head, a missing trusted checker,
or a status-publication error fails closed. The ordinary multibranch status does
not substitute for this status. After a merge, controller configuration is compared
with this documented trust boundary before the next pull request is accepted.
