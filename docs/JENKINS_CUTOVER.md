# Jenkins CI ownership

Jenkins is the authoritative automatic CI runner for Motus. The multibranch
job `motus` discovers branches and pull-request heads and loads `Jenkinsfile`
from each exact head. Pull requests run the complete contract suite, both plug
suites, the SLO baseline gate, and the frozen-contract guard.

## Frozen-contract trust boundary

The pull-request checkout supplies only the compared Git object. The guard
resolves the real `origin/$CHANGE_TARGET` and exact checked-out `GIT_COMMIT`,
then materializes `tools/check_frozen_paths.py` with `git show` from that base
SHA into a temporary read-only file. A PR's copy of the checker is never
executed. `tests/test_ci_foundation.py` constructs a malicious commit changing
both frozen evidence and its checker and proves that the base checker rejects
it.

## Weekly ADR-012 characterization

The dedicated Jenkins Pipeline job `motus-relative-characterization` loads
`jenkins/relative-characterization.Jenkinsfile` from `main`. It runs every
Monday at 04:17 UTC (`17 4 * * 1`) in `python:3.10.12-slim` and performs five
interleaved rounds using `collect_relative_baseline.py`. Both the cumulative
anchor and baseline are the ADR-controlled fixed tag `v0.6.1`; the candidate
is current `main`. `check_relative_baseline.py --advisory` validates and
reports the single weekly observation without pretending that one job is the
three-job canonical release verdict.

The raw `benchmarks/relative-latest.json` is archived with a fingerprint.
Build logs and artifacts are retained for 90 days, capped at 16 weekly builds.
The job is deliberately separate from the multibranch PR pipeline because
ADR-012 asks for periodic cumulative-drift evidence, not a measurement on
