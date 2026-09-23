# TASK 017D — Motus 0.17.0 release

Parent: TASK 017 / ADR-038.

Starts only after 017C is merged and main is Jenkins-green.

Goal: prepare the 0.17.0 release using the repository release discipline.

Status: COMPLETE. PR #188 was merged as `bb50ebc8d89fd59f6550eb4d39255c6eb76738e4`; the reviewed release head and measured candidate are ancestors of that merge; Jenkins passed on the PR and on `main`; and annotated tag `v0.17.0` points to the merge commit. The release workflow built and verified the wheel and sdist and created a draft GitHub Release. Publishing that draft and publishing to PyPI remain separate founder actions and are not authorised.

Do not:
- move or recreate existing release tags;
- publish GitHub Release or PyPI without the required founder approval;
- mix release fixes with new 0.17 product scope.

Release evidence must describe Regulatory Evidence Profile v1 as a neutral
evidence-mapping mechanism, not a compliance engine.
