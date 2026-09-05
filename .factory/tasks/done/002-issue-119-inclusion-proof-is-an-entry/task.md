You are the lead of the Motus factory session. Checkout: /home/vitruvyan/motus-factory (this cwd), branch
`fix/119-inclusion-proof-is-an-entry`. Read first: `AGENTS.md`, GitHub issue #119 (`gh issue view 119`),
`contract/receipt.v1.schema.json` (`$defs.Entry`), `src/vitruvyan_motus/commitlog.py` (`class InclusionProof`,
`to_dict`, `inclusion_proof`), `tests/test_verifier.py` lines 55-80 (the hand-built entry).
Python: `.venv/bin/python`, `.venv/bin/pytest`. Never commit, push, tag or release. `tests/compat/` and
`tests/contract/` are frozen: never add to or edit them. No contract change: the schema stays as it is; the
library moves.

Flow: ONE implementer (agent "pi", agentArgs ["--model","openai-codex/gpt-5.6-luna"]) → verifier (pi, same
agentArgs): full suite + `.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD` →
ONE adversary (agent "claude", lens: the public protocol a user would implement against — can a consumer
still get what `format`, `mode` and `checkpoint_digest` gave, and does `motus-validate receipt` accept an entry
built from `to_dict()`?) → REPORT.md with a MUTATION TARGETS section (the CTO runs `tools/mutation_probe.py`
after committing). No architect: the decision is in the issue and below.

# TASK 002 — #119: `InclusionProof.to_dict()` emits a receipt.v1 `Entry`, nothing more, nothing less

## Decision (CTO)
The library speaks the contract's vocabulary. `to_dict()` returns exactly an `Entry`:
`{"commitment": …, "witness": … | null, "proof": [{"side": "left"|"right", "digest": "sha256:…"}, …],
"checkpoint": …}` — `proof` where today it says `path`, and each step an object with `side` and `digest`
(today a two-element list). `format`, `mode` and `checkpoint_digest` leave the dict: they stay as
attributes/properties on `InclusionProof` (`.format`, `.mode`, `.checkpoint_digest`) for any caller that
wants them, and `mode` is documented as *achieved*, read from the commitment, never configured.

## Required
1. `commitlog.py`: the change above; docstring of `to_dict` says "a receipt.v1 Entry" and points at the schema.
2. `tests/test_verifier.py`: the hand-built entry at lines ~63-73 is replaced by `proof.to_dict()` from a real
   `CommitmentLog` in a `tmp_path`, so the test proves the library's output is what the verifier reads. If
   the fixture cannot be replaced without changing what the test asserts, keep the fixture and add a new test
   beside it; say which in REPORT.md.
3. A test that validates `to_dict()` output against `$defs.Entry` with the schema validator the repo already
   uses (`jsonschema` is a dependency), for a window with one leaf (empty proof) and with several.
4. A test that `motus-validate receipt <r> --trace <t>` accepts a receipt whose segment entries come from
   `to_dict()` — build the receipt dict by hand around them (the producer is #118, not this task).
5. `grep -rn '"path"' src/ demo/ tests/` must show no consumer of the old key left; report the grep.

## Constraints
- Only `src/vitruvyan_motus/commitlog.py` and files under `tests/` that are not frozen. Nothing under
  `contract/`.
- If anything under `tests/contract/` or `tests/compat/` fails after the change, **stop**: that means the
  frozen corpora carry the old shape and the change needs the founder — report it, do not touch the corpora.

## Acceptance
Full suite green; frozen-path guard green; adversary's report; REPORT.md with the grep and MUTATION TARGETS.
