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
