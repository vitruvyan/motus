# TASK 022-R — Motus 0.22.0 release

Parent: Point 10 / ADR-043.

Candidate source: `77f326517919b68957d631588f8a79073eb91297` on
`release/0.22.0`, based on the verified Point 10 merge
`19e394abda7815758590da338336f1b6e6ace858`.

Release evidence is complete:

- Jenkins relative builds `#33`, `#34`, and `#35` use v0.21.0 as baseline,
  v0.6.1 as cumulative anchor, Python 3.10.12, and five interleaved pairs;
- the +10% per-release gate passes at +3.5%, -6.7%, and -2.1%;
- the unchanged +20% cumulative arm fails at +112.3%, +129.9%, and +16.4%;
- five fresh live Orbis queries produce valid integrity-chained traces and a
  conservative Motus share of 0.200%, below ADR-018's 1% ceiling;
- Jenkins absolute build `#36` uses the same candidate SHA and passes the SLO
  baseline gate with runtime identity `vitruvyan-motus/0.22.0`.

Jenkins real-workload build `#15` is excluded: the checked-in job still named
the obsolete `release/0.15.0` checkout, so it stopped before installing or
measuring the candidate. Replay build `#16` applied the already validated
Orbis-agent/proxy correction used by builds #12 and #14, substituted only the
0.22.0 branch, SHA, version check, and artifact names, and completed all five
fixed queries. No failed or successful measurement was retried to select a
favourable result.

Do not publish the draft GitHub Release or PyPI without separate founder
approval.
