# TASK 020-R — Motus 0.20.0 release

Parent: Point 7 / ADR-041.

Candidate source: `cb47451de41f60b1d7d8774f567774006ecbe4bb` on
`release/0.20.0`, based on the verified Point 7 merge
`228857c3d5c164e44014f3f39f3b30f167187f3c`.

Release evidence is complete:

- Jenkins relative builds `#25`, `#26`, and `#27` use v0.19.0 as baseline,
  v0.6.1 as cumulative anchor, Python 3.10.12, and five interleaved pairs;
- the +10% per-release gate passes at -3.6%, -2.3%, and -2.4%;
- the unchanged +20% cumulative arm fails at +106.9%, +131.3%, and +24.6%;
- five fresh live Orbis queries produce valid integrity-chained traces and a
  conservative Motus share of 0.114%, below ADR-018's 1% ceiling;
- Jenkins absolute build `#28` uses the same candidate SHA and passes the SLO
  baseline gate with runtime identity `vitruvyan-motus/0.20.0`.

Jenkins real-workload build `#11` is excluded: the checked-in job still named
the obsolete `release/0.15.0` checkout, so it stopped before installing or
measuring the candidate. Replay build `#12` applied the already validated
Orbis-agent/proxy correction used by build #10, substituted only the 0.20.0
branch, SHA, version check, and artifact names, and completed all five fixed
queries. No failed or successful measurement was retried to select a favourable
result.

Do not publish the draft GitHub Release or PyPI without separate founder
approval. PR review, merge, post-merge verification, annotated source tag and
post-tag roadmap closure remain.
