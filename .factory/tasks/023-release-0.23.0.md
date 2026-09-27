# TASK 023-R — Motus 0.23.0 release

Parent: integration point / ADR-044.

Candidate source: `85b775f512b289222da10c500cc6461a7dc28399` on
`release/0.23.0`, based on the verified Point 23 merge
`a744167153e9b5f70bcc46ae4c4f836679627fbe`.

The release PR must use a two-parent merge commit that preserves the complete
`release/0.23.0` ancestry. Squash and rebase merge are prohibited: either would
sever the tag's ancestry from the characterised candidate and the Jenkins-green
release head.

Release evidence is complete:

- Jenkins relative builds `#37`, `#38`, and `#39` use v0.22.0 as baseline,
  v0.6.1 as cumulative anchor, Python 3.10.12, and five interleaved pairs;
- the +10% per-release gate passes at -3.2%, +4.7%, and -2.4%;
- the unchanged +20% cumulative arm fails at +103.8%, +167.6%, and +18.8%;
- five fresh live Orbis queries produce valid integrity-chained traces and a
  conservative Motus share of 0.185%, below ADR-018's 1% ceiling;
- Jenkins absolute build `#40` uses the same candidate SHA and passes the SLO
  baseline gate with runtime identity `vitruvyan-motus/0.23.0`.

Jenkins real-workload build `#17` is excluded: the saved job still named the
obsolete `release/0.15.0` checkout, so it stopped before checkout, installation,
or measurement. Replay build `#18` checked out the exact 0.23.0 candidate but
inherited build #16's old runtime parameter values and stopped at the immutable
SHA assertion before installation or measurement. Replay build `#19` fixed the
0.23.0 SHA and label in the pipeline environment, preserved the previously
qualified Orbis-agent/proxy correction and five fixed queries, and completed
successfully. No failed or successful measurement was retried to select a
favourable result.

Do not publish the draft GitHub Release or PyPI without separate founder
approval. The founder has explicitly withheld PyPI publication authorisation.
