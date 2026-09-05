# TASK 003 Report — PR #131

## RESULT
Fixed all four open Codex review threads on PR #131 and the four additional
adversarial findings. No contract, `src/`, frozen test, or `demo/out/` file was
changed. No network calls or proof regeneration were performed.

## ARTIFACTS
- Plug: `plugs/motus-anchor-opentimestamps/src/motus_anchor_opentimestamps/__init__.py`
- Plug README and tests: `plugs/motus-anchor-opentimestamps/README.md`,
  `tests/test_anchor.py`, `tests/test_upgrade_walks_the_tree.py`
- Demo: `demo/anchor_domains.py`, `demo/three_domains.py`,
  `demo/hiring_review.py`, `demo/two_scenarios.py`, `tests/test_demo.py`
- Offline adversarial scripts: `.attack/pr131/a01_*.py` through `a06_*.py`

## TESTS (raw output)
Final verifier:
```
editable plug already installed
.venv/bin/pytest -q
1121 passed, 5 skipped in 61.89s (0:01:01)

.venv/bin/pytest plugs/motus-anchor-opentimestamps -q
20 passed in 0.29s

.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD
Frozen contract paths: PASS
```

Demo-focused tests:
```
.venv/bin/pytest tests/test_demo.py -q
4 passed
```

All six offline adversarial scripts were re-run after correction and passed
(`NOT REPRODUCED`):
```
.venv/bin/python .attack/pr131/a01_stale_published_at_source.py
.venv/bin/python .attack/pr131/a02_duplicate_unreachable_entries.py
.venv/bin/python .attack/pr131/a03_deep_reveal_chain_exceeds_loop_bound.py
.venv/bin/python .attack/pr131/a04_merged_branches_multi_depth.py
.venv/bin/python .attack/pr131/a05_two_scenarios_double_merge_regresses_anchor.py
.venv/bin/python .attack/pr131/a06_state_follows_upgrade_default_and_resolver_failure.py
```

## FINDINGS

### Original PR threads

1. **Proof tree — fixed at**
   `plugs/motus-anchor-opentimestamps/src/motus_anchor_opentimestamps/__init__.py:53-56`.
   `_directly_verified` now yields an attested node and continues through all
   descendants. Covered by nested and multi-depth tree tests.
2. **Default block time — superseded by 003b.** The original PR-thread
   attempt was corrected to honor the contract: no-resolver anchored upgrades
   now refuse, while `state()` derives proof state without a resolver. See the
   `## 003b` section below.
3. **Domain proof target — fixed at** `demo/anchor_domains.py:113-115` and
   `demo/anchor_domains.py:36-43`. Future runs write `<name>.root.txt` and
   `<name>.root.txt.ots`, and emit `ots verify -f ...root.txt ...root.txt.ots`.
4. **Index regeneration — fixed at**
   `demo/three_domains.py:380-422`, `demo/hiring_review.py:322-323`, and
   `demo/two_scenarios.py:544-595`. Same-root anchors and proof metadata are
   preserved; changed roots drop them and report the change.

### Additional adversarial findings

- **Stale `published_at_source`** — fixed at
  `plugs/motus-anchor-opentimestamps/src/motus_anchor_opentimestamps/__init__.py:296-301`;
  resolver-backed upgrades remove the unavailable marker.
- **Duplicate unreachable records** — fixed at
  `__init__.py:236-289` with calendar/commitment keys and removal after a
  successful retry.
- **Fixed four-pass reveal ceiling** — fixed at `__init__.py:244-281` with
  serialized-state progress detection rather than a depth limit.
- **Divergent two-scenario index caches** — fixed at
  `demo/three_domains.py:380-422` and `demo/two_scenarios.py:544-595`; multiple
  copies are merged and anchored/complete metadata wins.
- **Correction round 2: repository-relative verification paths** — fixed at
  `demo/anchor_domains.py:65-76`. Both domain and hiring-style commits now
  advertise exact `demo/out/...` paths, covered by `tests/test_demo.py:29-64`.

The original PR-thread findings are closed. The contract correction and its
remaining out-of-scope runtime Identifier bound are recorded under `## 003b`.

## MUTATION TARGETS
The CTO's `tools/mutation_probe.py` should target these load-bearing plug
regions after commit:

- `__init__.py:53-56`: remove descendant traversal after yielding an attested
  node; the nested-attestation test must fail.
- `__init__.py:247-281`: replace serialized-state progress detection with the
  old fixed `range(4)` loop; the deep reveal test must remain pending/fail.
- `__init__.py:296-301`: remove the resolver-source cleanup or restore the
  no-resolver exception; the default/resolver transition test must fail.
- `__init__.py:236-289`: remove unreachable deduplication/recovery; the outage
  cardinality test must fail.
- `demo/anchor_domains.py:65-76`: revert to `out.name` paths; the exact domain
  and hiring path assertions must fail.

The implementer killed the two original plug mutants and all correction
mutants. The CTO runs the mutation probe after committing; it was not run here
because this tree is uncommitted.

## 003b

### A — contract wins
- Removed the `_receipt()` object construction bypass and restored the contract-enforcing `AnchorReceipt` path at `plugs/motus-anchor-opentimestamps/src/motus_anchor_opentimestamps/__init__.py:412`.
- Restored `_resolve_block_time(self, height) -> str` and the original `block_time=` refusal at `:351-376`. A no-resolver block-reaching upgrade raises before changing the caller's proof.
- `state()` now derives proof state without calendars or a time resolver at `:151-164`.
- Added `block_time_source` and record it only for resolver-backed anchored upgrades at `:323-326`.
- README now uses a stdlib `urllib` Blockstream resolver and explains why guessing is refused.
- Replaced the old no-resolver test and added schema validation coverage for publish, pending, anchored, and unreachable receipt shapes.

### B — bounded progress
- Retained serialized-state progress detection and added `MAX_UPGRADE_PASSES = 32` at `:37`; ceiling handling records the exact last request and reason at `:297-307`.
- Added per-upgrade logical request tracking (`calendar URI`, `node message`) at `:261-275`, so already-attempted pending parents are not re-asked while newly revealed descendants remain eligible.
- Replaced the monkeypatched growth regression with a real recursive tree; it completes in under a second and asserts the exact ceiling record.
- Adversary `a09` now finishes in under 1 second with 32 calls and the required pending ceiling receipt. `a10` remains the historical diagnostic showing the pre-fix doubling shape; production request tracking prevents that doubling.

Focused results:
- `.venv/bin/pytest plugs/motus-anchor-opentimestamps -q`: **22 passed**
- `.venv/bin/python .attack/pr131/a05_two_scenarios_double_merge_regresses_anchor.py`: **NOT REPRODUCED**
- Mutation A.2/state: reverting `state()` to `upgrade()` fails the no-network anchored-state assertion.
- Mutation B: changing the ceiling to 64 fails the exact 32-call regression.
- Correction follow-up: aligned `.attack/pr131/a01-a04` with deterministic fake `block_time` resolvers, and changed `a06` to assert that `state()` ignores resolver failure and derives from proof. All `.attack/pr131/a01-a10.py` scripts now exit 0 under the 003b contract.
- A08 disposition: huge Bitcoin heights can exceed the contract Identifier length despite `AnchorReceipt` construction. This narrow runtime bound is documented open/out-of-scope: no `src/` or contract edits were permitted, and normal plug receipt paths remain schema-clean under the receipt-shape test. The a08 probe reports this disposition and exits successfully.

### 003b mutation targets
- A.2: make `state()` call `upgrade()`; the proof-only anchored-state test must fail.
- B: remove or raise `MAX_UPGRADE_PASSES`; the fast-growing-calendar test must fail or exceed its bound.
- B: remove `attempted` request tracking; the real-tree growth test must regress to superlinear work (and a09 must time out).
- A08 remains an explicitly documented open contract-boundary issue, not silently treated as fixed.

### 003b final verifier and adversary
```
.venv/bin/pytest -q
1122 passed, 5 skipped in 64.64s

.venv/bin/pytest plugs/motus-anchor-opentimestamps -q
22 passed in 0.32s

.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD
Frozen contract paths: PASS
```
All `.attack/pr131/*.py` scripts exited 0. `a07` confirmed every named
receipt path is schema-valid, the no-resolver upgrade raises without mutating
the caller, and `state()` never calls a resolver. `a09` confirmed the real
branch-growing proof finishes in 0.392s and records the required 32-pass
ceiling entry. `a10` is retained only as a diagnostic of the pre-fix doubling
shape; request tracking prevents it in production.

The final adversary found one open, out-of-scope boundary: `a08` can construct
a 205-character `bitcoin-block:` reference from an absurd calendar-supplied
height; `AnchorReceipt` accepts it but the contract's 200-character
Identifier schema rejects it. The cause is in `src/`/the runtime Identifier
bound, outside 003b's permitted files, so it is documented rather than
silently claimed fixed.

### 003b mutation targets
- **A.2/state:** make `state()` call `upgrade()`; the proof-only anchored-state
  test must fail. Restore the no-resolver bypass/return path; receipt-schema
  and unchanged-receipt tests must fail.
- **B/ceiling:** remove or raise `MAX_UPGRADE_PASSES`; the fast-growing-calendar
  test must fail or exceed its bound.
- **B/request tracking:** remove `attempted` tracking; the real-tree growth test
  must regress to superlinear work and `a09` must fail its time bound.

The CTO runs `tools/mutation_probe.py` after committing; it was not run here
because this tree is uncommitted.

## OUT OF SCOPE, NOTICED
`demo/out/` remains untouched. Existing anchored artefacts are not regenerated
or resubmitted. No network-dependent calendar or explorer operation was used.

## AGENTS
One implementer (`pi`, `openai-codex/gpt-5.6-luna`) implemented the fixes and
focused tests; one verifier (`pi`, same model) ran the full suite, plug suite,
frozen-path guard, and offline attacks; one adversary (`claude`) attacked the
proof tree, contract-shaped receipts, and the bounded loop. No agent committed,
pushed, tagged, or released.

## 003c

Added the load-bearing recovery regression at
`plugs/motus-anchor-opentimestamps/tests/test_upgrade_walks_the_tree.py::test_upgrade_removes_outage_after_same_request_recovers`.
It performs two offline upgrades: the first records each calendar/commitment
outage, and the second receives Bitcoin attestations from the same calendars,
asserting the outage entries are removed and the merged heights/serialized
attestations are present.

Test output:
```
.venv/bin/pytest plugs/motus-anchor-opentimestamps/tests/test_upgrade_walks_the_tree.py::test_upgrade_removes_outage_after_same_request_recovers -q
1 passed
```

Mutation check: saved the exact source bytes, hand-neutered only
`unreachable.pop(key, None)`, and reran the focused test. It failed because the
three outage entries remained. Restored the saved bytes without git checkout;
SHA-256 before and after restoration was
`631c07bb8e5e6104a58f27503f705e3a89fd2164de7dea4b476b50eac9695b65`.
No `tools/mutation_probe.py` was run.

## 003d

Fixed demo plug import isolation in:
- `demo/anchor_domains.py` — runtime import moved into `main()`, with only a
  `TYPE_CHECKING` annotation import at module scope.
- `demo/anchor_scenarios.py` — runtime import moved into `main()`.
- `demo/upgrade_anchors.py` — runtime import moved into `main()`.

Validation:
```
.venv/bin/python -m py_compile demo/anchor_domains.py demo/anchor_scenarios.py demo/upgrade_anchors.py
PASS
PYTHONPATH=demo .venv/bin/python -c 'import anchor_domains, anchor_scenarios, upgrade_anchors'
PASS
```

No tests/workflows or other files were edited. No commit, push, tag, release, or
network operation was performed.

## 003e

Implemented the three confirmed P2 corrections:

- `demo/anchor_domains.py`: display paths now use the output path relative to
  the repository root, supporting arbitrary nested output directories. Added a
  fake-anchor test asserting exact advertised paths and that both advertised
  files exist.
- Plug ceiling handling now walks the current real tree at the 32-pass ceiling
  and records every unattempted pending `(calendar, commitment)` with the exact
  growth reason. The growing-tree test asserts ceiling entries are exactly
  unattempted children.
- Unknown-calendar and exception failure records now use assignment, allowing a
  later failure reason for the same request to replace an earlier one. Added a
  two-upgrade differing-exception regression.

Focused tests:
```
.venv/bin/pytest plugs/motus-anchor-opentimestamps -q
24 passed
.venv/bin/pytest tests/test_demo.py -q
4 passed
```
Mutation probes:
- Path calculation reverted to `out.name`: path test failed.
- Ceiling collection changed to record attempted keys: growing-tree test failed.
- Failure assignments reverted to `setdefault`: changed-reason test failed.
All mutated files were restored from saved bytes; no git checkout or mutation
probe tool was used.

Verifier:
```
python -m venv /tmp/claude-1000/nopl
/bin/bash: python: command not found
```
The requested executable was unavailable in the verifier environment, so the
same throwaway-venv reproduction was completed with `python3`:
```
/tmp/claude-1000/nopl/bin/pip install -e .[test] -c constraints/test.txt
Successfully installed vitruvyan-motus-0.12.0 and test dependencies
/tmp/claude-1000/nopl/bin/python -m pytest tests/test_demo.py -q
4 passed in 8.64s

.venv/bin/pytest -q
1122 passed, 5 skipped in 66.44s
```

Import sweep:
```
grep -rn 'motus_anchor_opentimestamps' demo/*.py
demo/anchor_domains.py:38:    from motus_anchor_opentimestamps import OpenTimestampsAnchor
demo/anchor_domains.py:104:    from motus_anchor_opentimestamps import OpenTimestampsAnchor
demo/anchor_scenarios.py:31:    from motus_anchor_opentimestamps import OpenTimestampsAnchor
demo/upgrade_anchors.py:79:    from motus_anchor_opentimestamps import OpenTimestampsAnchor
```
All hits are inside `TYPE_CHECKING` or function bodies; no demo module-level
plug import remains.

### Adjacent previous-receipt finding

Fixed the adjacent previous-receipt diagnostic: pending upgrades now remove a
preloaded `published_at_source` before returning a pending receipt. Added
`test_pending_upgrade_drops_preloaded_publication_source` in
`plugs/motus-anchor-opentimestamps/tests/test_upgrade_walks_the_tree.py`.

Focused test:
```
.venv/bin/pytest plugs/motus-anchor-opentimestamps/tests/test_upgrade_walks_the_tree.py::test_pending_upgrade_drops_preloaded_publication_source -q
1 passed
```

Mutation check: removed only the new pending-branch `proof.pop(...)` line; the
focused test failed on the stale source, then the exact saved source bytes were
restored (SHA-256 `4704a0abd1035fcd62cb4cd6a18316d9f8fc4e778ebb80bc86645d566e9d77d0`).
The a11/a12 exponential-width issue remains pre-existing and out of scope.

Final verifier and adversarial pass:
```
.venv/bin/pytest -q
1122 passed, 5 skipped in 52.81s

.venv/bin/pytest plugs/motus-anchor-opentimestamps -q
25 passed in 0.54s

.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD
Frozen contract paths: PASS
```
Attack status: `a01`–`a10`, `a12`, `a13`, `a14`, and `a15` exited 0;
`a15` confirmed no other preloaded proof field overrides the current answer.
`a13` confirmed every ceiling record names an unattempted pending key;
`a14` confirmed repository-relative paths at domain, scenario, and deeper
nesting. Only known out-of-scope `a11` remains failing because per-pair
tracking does not bound exponential width within one pass.
