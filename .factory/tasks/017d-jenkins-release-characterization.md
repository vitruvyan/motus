# TASK 017D-J — Jenkins-only 0.17.0 release characterization

Work directly on the Jenkins controller/VPS for repository `vitruvyan/motus`.

## Known good state

- Release branch: `release/0.17.0`
- Branch starts from merged Point 5 / PR #183.
- Runtime version on the branch: `0.17.0`.
- `benchmarks/check_slo_baseline.py` DEFAULT_CANDIDATE already points to:
  `benchmarks/candidate-v0.17.0-epyc-py310.json`.
- Canonical `Jenkinsfile` has been restored and is byte-identical to main.
- Do not change Motus product code.
- Do not use GitHub Actions.
- Do not create or move tags.
- Do not publish GitHub Release or PyPI.

## Why this task exists

The ChatGPT-side GitHub connector can modify the repository and read Jenkins commit statuses,
but cannot reach `build.vitruvyan.com` console logs or Jenkins artifacts directly.
Two attempts to make the release-branch job push generated evidence back to GitHub failed at
the Jenkins/push layer. No release evidence was committed and no product code was changed.

Diagnose controller-side details directly instead of guessing.

## Required release evidence

### A. Relative characterization

Run **three independent Jenkins dispatches** against the same release candidate.

For each dispatch:

- candidate tree: exact current `release/0.17.0` source tree;
- baseline: tag `v0.15.0`;
- cumulative anchor: tag `v0.6.1`;
- Python: 3.10.12;
- interleaved pairs: 5;
- command shape:

```sh
python benchmarks/collect_relative_baseline.py \
  --baseline-src <v0.15.0-worktree>/src \
  --baseline-ref v0.15.0 \
  --candidate-src src \
  --candidate-ref <exact release candidate SHA> \
  --anchor-src <v0.6.1-worktree>/src \
  --anchor-ref v0.6.1 \
  --pairs 5 \
  > benchmarks/relative-0.17.0/job-N.json
```

Each job must be a genuinely separate Jenkins build. Do not run the same process three times
inside one Jenkins build and call that three dispatches.

After all three:

```sh
python benchmarks/check_relative_baseline.py benchmarks/relative-0.17.0/*.json
```

This must produce the canonical 3-job release verdict.

If any per-release metric exceeds the current +10% budget, STOP. Do not widen the budget,
retry until a faster host appears, or add an exception without a new accepted ADR.

The cumulative arm may remain red only under ADR-018, which additionally requires the
real-workload measurement below.

### B. Absolute candidate profile

On the release branch, generate:

```sh
python benchmarks/collect_motus_baseline.py 5 \
  > benchmarks/candidate-v0.17.0-epyc-py310.json
python benchmarks/check_slo_baseline.py
```

The generated document must identify runtime `vitruvyan-motus/0.17.0`.

### C. Real-workload evidence — ADR-018

Because the cumulative arm is historically above budget, re-take the real-consumer
measurement for 0.17.0.

Use `e2e/pipeline_query.py` against the live Orbis graph service from a host where
`http://localhost:9004/run` is the actual service, using the same five fixed Italian
queries used for 0.15.0 unless there is a documented reason not to.

Commit complete raw output as:

`benchmarks/real-workload-0.17.0.txt`

Record at the top:

- exact candidate SHA;
- candidate label `release/0.17.0`;
- Python version;
- kernel/platform;
- CPU;
- load average.

ADR-018 requires the conservative executor share to remain under 1%.

Do not treat Orbis service-health metadata as Motus evidence. If the endpoint responds but
health fields are absent, classify that honestly exactly as 0.15.0 did.

## Commit evidence

Commit only measured evidence:

- `benchmarks/relative-0.17.0/job-1.json`
- `benchmarks/relative-0.17.0/job-2.json`
- `benchmarks/relative-0.17.0/job-3.json`
- `benchmarks/candidate-v0.17.0-epyc-py310.json`
- `benchmarks/real-workload-0.17.0.txt`

Commit message:

`release: publish 0.17.0 characterization evidence`

Push to `release/0.17.0`.

## Final report back

Return:

1. exact release branch SHA before measurement;
2. Jenkins build numbers/URLs for the three independent relative jobs;
3. the three per-release ratios and their canonical medians;
4. the three cumulative ratios and canonical medians;
5. whether `check_relative_baseline.py` PASSed;
6. whether `check_slo_baseline.py` PASSed;
7. real-workload five Motus costs and five request durations;
8. conservative real-workload share;
9. evidence commit SHA;
10. any anomaly or residual blocker.

Do not prepare tag/release publication. Stop after the evidence commit.
