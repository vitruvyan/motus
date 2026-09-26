# TASK 021-R — Motus 0.21.0 release

Parent: Point 9 / ADR-042.

Candidate source: `bf6be6acd9504c1e42639aafd52f53d8af82260e` on
`release/0.21.0`, based on the verified Point 9 merge
`4c5a5772ff1ae0bf4fb6c3e37e3705bf443657d1`.

Release evidence is complete:

- Jenkins relative builds `#29`, `#30`, and `#31` use v0.20.0 as baseline,
  v0.6.1 as cumulative anchor, Python 3.10.12, and five interleaved pairs;
- the +10% per-release gate passes at +1.6%, -0.8%, and -3.5%;
- the unchanged +20% cumulative arm fails at +110.9%, +128.3%, and +24.3%;
- five fresh live Orbis queries produce valid integrity-chained traces and a
  conservative Motus share of 0.957%, below ADR-018's 1% ceiling;
- Jenkins absolute build `#32` uses the same candidate SHA and passes the SLO
  baseline gate with runtime identity `vitruvyan-motus/0.21.0`.

Jenkins real-workload build `#13` is excluded: the checked-in job still named
the obsolete `release/0.15.0` checkout, so it stopped before installing or
measuring the candidate. Replay build `#14` applied the already validated
Orbis-agent/proxy correction used by build #12, substituted only the 0.21.0
branch, SHA, version check, and artifact names, and completed all five fixed
queries. No failed or successful measurement was retried to select a favourable
result.

Do not publish the draft GitHub Release or PyPI without separate founder
approval.

Source-release qualification reached on 2026-09-26: PR #199 merged as
`980683beeac14813be4b86edee569c6c4fb95e40`, Jenkins `main` build #22 passed,
and annotated tag `v0.21.0` points at that exact merge. Tag workflow
`36237246043` built and verified the distribution once and created only the
founder-reviewable draft Release; the PyPI publication job did not run. Under
ADR-032 the release and roadmap point remain open until the founder publishes
the draft and the coupled PyPI/index/hash verification succeeds. That step is
not currently authorised.
