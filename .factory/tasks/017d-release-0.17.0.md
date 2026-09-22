# TASK 017D — Motus 0.17.0 release

Parent: TASK 017 / ADR-038.

Starts only after 017C is merged and main is Jenkins-green.

Goal: prepare the 0.17.0 release using the repository release discipline.

Status: IN PROGRESS. Release branch exists, runtime version is 0.17.0, and DEFAULT_CANDIDATE points to candidate-v0.17.0-epyc-py310.json. Product work is closed. Remaining blocker is release evidence generation on Jenkins.

Do not:
- move or recreate existing release tags;
- publish GitHub Release or PyPI without the required founder approval;
- mix release fixes with new 0.17 product scope.

Release evidence must describe Regulatory Evidence Profile v1 as a neutral
evidence-mapping mechanism, not a compliance engine.
