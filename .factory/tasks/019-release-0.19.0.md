# TASK 019-R — Motus 0.19.0 release

Parent: Point 6 / ADR-040.

Candidate source: `90787d4bfae1af7df17eea99ff31fcad461e2c72` on
`release/0.19.0`, based on the verified Point 6 merge
`d09409e6d8924a4dbf541720c5832a51ab956482`.

Release evidence is complete:

- Jenkins relative builds `#21`, `#22`, and `#23` use v0.18.0 as baseline,
  v0.6.1 as cumulative anchor, Python 3.10.12, and five interleaved pairs;
- the +10% per-release gate passes at +2.7%, -2.0%, and +0.1%;
- the unchanged +20% cumulative arm fails at +106.8%, +131.4%, and +23.6%;
- five fresh live Orbis queries produce valid integrity-chained traces and a
  conservative Motus share of 0.056%, below ADR-018's 1% ceiling;
- Jenkins absolute build `#24` uses the same candidate SHA and passes the SLO
  baseline gate with runtime identity `vitruvyan-motus/0.19.0`.

Jenkins builds `#17` through `#20` are excluded failed setup attempts: their
candidate parameter named a non-existent SHA produced by an operator
transcription error, so no measurement from them is release evidence. The first
complete, correctly parameterised sequence is `#21` through `#24`; no build was
retried to select a favourable measurement.

Do not publish the draft GitHub Release or PyPI without separate founder
approval.

Release closure completed on 2026-09-25: PR #193 merged as
`080f39cf9641de440bfa6dda72e1fa67383e317b`, Jenkins `main` build #16 passed,
and annotated tag `v0.19.0` points at that exact merge. The draft release assets
are byte-identical to the authenticated workflow artifact.
