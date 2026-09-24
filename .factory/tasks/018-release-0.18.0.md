# TASK 018 — Motus 0.18.0 release

Parent: Point 5 / ADR-039.

Starts only after the Incident / CAPA Ledger v1 pull request is merged and
`main` is Jenkins-green.

Goal: prepare the 0.18.0 release using the repository release discipline.

Status: RELEASE EVIDENCE COMPLETE. The release branch starts at the Point 5
merge commit. Runtime identity and characterization targets are 0.18.0. Three
independent relative Jenkins builds pass the per-release budget, the cumulative
FAIL is disclosed under ADR-018 with a fresh sub-1% Orbis measurement, and the
absolute Jenkins candidate passes the SLO gate. PR review, merge, post-merge
verification, and the annotated source tag remain.

Do not:
- move or recreate existing release tags;
- publish the GitHub Release or PyPI without separate founder approval;
- mix release preparation with new product scope;
- retry characterization to select a favourable runner outcome.

Release evidence must describe Incident / CAPA Ledger v1 as immutable,
jurisdiction-neutral producer claims and links. Motus records and verifies
evidence; it does not decide incident severity, legal status, root cause,
corrective-action adequacy, or regulatory compliance.
