# TASK 018-J — Jenkins-only 0.18.0 release characterization

Work through the reusable Jenkins `release-characterization` job for repository
`vitruvyan/motus`.

## Candidate

- Release branch: `release/0.18.0`
- Measured source SHA: `06b0d6cea96c90740815d366daee99c5896cd081`
- Baseline: tag `v0.17.0`
- Cumulative anchor: tag `v0.6.1`
- Python: 3.10.12
- Interleaved pairs: 5

Do not change Motus product code, move tags, publish a GitHub Release, or
publish to PyPI as part of this task.

## Required release evidence

Run three genuinely independent Jenkins builds in `relative` mode with the
candidate, baseline, anchor, and pair count above. Preserve their raw artifacts
as:

- `benchmarks/relative-0.18.0/job-1.json`
- `benchmarks/relative-0.18.0/job-2.json`
- `benchmarks/relative-0.18.0/job-3.json`

Then run:

```sh
python benchmarks/check_relative_baseline.py benchmarks/relative-0.18.0/*.json
```

If a per-release metric exceeds the unchanged +10% budget, stop. Do not widen
the budget, retry until a faster host appears, or add an exception without an
accepted ADR.

Run one Jenkins build in `absolute` mode against the same measured source SHA.
Preserve its artifact as:

`benchmarks/candidate-v0.18.0-epyc-py310.json`

The document must identify runtime `vitruvyan-motus/0.18.0`; afterwards
`python benchmarks/check_slo_baseline.py` must pass.

Because the cumulative arm is expected to retain the integrity-era debt, ADR-018
also requires a fresh real-consumer measurement for this release. Run
`e2e/pipeline_query.py` against the live Orbis graph service using the same five
fixed Italian queries as 0.17.0. Preserve complete raw output as:

`benchmarks/real-workload-0.18.0.txt`

Record the exact candidate SHA, candidate label, Python version, kernel, CPU,
and load average at the top. The conservative executor share must remain below
the pre-registered 1% ceiling. Service-health metadata is not Motus evidence;
classify absent health fields honestly.

## Evidence commit

Commit only the task record and measured evidence with message:

`release: publish 0.18.0 characterization evidence`

The final report must name the four Jenkins builds, all per-release and
cumulative ratios, both checker verdicts, the five real-workload costs and
durations, the conservative share, and any anomaly or blocker.
