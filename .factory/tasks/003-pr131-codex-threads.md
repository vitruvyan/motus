You are the lead of the Motus factory session. Checkout: /home/vitruvyan/motus-factory (this cwd), branch
`fix/pr131-codex-threads` from `demo/three-domains-anchored` (the PR #131 branch). Read first: `AGENTS.md`,
the four open review threads on PR #131 (`gh api graphql` on reviewThreads, or `gh pr view 131 --comments`),
`plugs/motus-anchor-opentimestamps/src/motus_anchor_opentimestamps/__init__.py`, its `README.md`,
`demo/anchor_domains.py`, `demo/three_domains.py`, `demo/bundle_scenarios.py` (how `root.txt` / `root.txt.ots`
are laid out there). Python: `.venv/bin/python`, `.venv/bin/pytest`; the plug's tests run with
`.venv/bin/pytest plugs/motus-anchor-opentimestamps -q` (install it editable into `.venv` first if needed:
`.venv/bin/pip install -e plugs/motus-anchor-opentimestamps`). Never commit, push, tag or release. No network
in tests: calendars and explorers are never called; use recorded proofs (`plugs/.../tests/pending.ots`) and fakes.
Flow: ONE implementer (agent "pi", agentArgs ["--model","openai-codex/gpt-5.6-luna"]) → verifier (pi, same
agentArgs: full suite + plug tests + `tools/check_frozen_paths.py demo/three-domains-anchored HEAD`) → ONE
adversary (agent "claude", lens: the proof tree — build timestamp trees where attestations sit at several
depths and on nodes that also have ops, and try to make `upgrade()`/`state()` miss one or raise) → REPORT.md
with MUTATION TARGETS. Two correction rounds maximum. No other roles.

# TASK 003 — the four review threads on PR #131 (Codex review, 2026-09-04), all confirmed by the CTO

## Plug (shipped code — the fix Limen waits for)
1. **`_directly_verified` stops at a node that has attestations** (`__init__.py:44-57`). A timestamp node
   can carry attestations *and* descendant ops after independent calendar branches are merged; the early
   `return` hides every attestation below. Yield the node **and** keep descending. Test: a hand-built
   `opentimestamps` timestamp with an attestation at depth 1 on a node that also has a sub-branch whose leaf
   holds a `BitcoinBlockHeaderAttestation`; `upgrade()` must find the block.
2. **The documented default path must not raise** (`__init__.py:74-95`, README lines 15 and 25).
   `OpenTimestampsAnchor()` with no `block_time` must, when a Bitcoin attestation is found, return
   `state="anchored"` with `reference` = the block height and `published_at = None` plus
   `published_at_source = "unavailable: no block_time resolver supplied"`. It still never uses the local
   clock, and it still never reports `anchored` for a pending proof (ADR-020 d7). `state()` follows the same
   rule. README: say exactly this, with the two-line example of supplying a resolver.
   Keep `raise` only for a resolver that was supplied and fails.

## Demo (not shipped, but the review is right)
3. **`ots verify` needs the committed bytes beside the proof** (`demo/anchor_domains.py:117`). `commit()`
   submits the root *string*; write it to `demo/out/domains/<name>.root.txt` and set
   `verify_with = "ots verify -f demo/out/domains/<name>.root.txt demo/out/domains/<name>.ots"`, naming the proof
   `<name>.root.txt.ots` if that is what makes the default client find its target (check
   `demo/bundle_scenarios.py`, which already does this for bundles, and follow it). Do not regenerate or resubmit
   any existing proof under `demo/out/`; the change applies to future runs. No network.
4. **Regenerating the index must keep the anchors** (`demo/three_domains.py:406`, and the same pattern in
   `hiring_review.py` / `two_scenarios.py` if present — check). When `index.json` exists, merge by `name`: if the
   entry's `root` is unchanged, carry its `anchor` (and `proof_file`, `verify_with`) forward; if the root changed,
   drop the anchor and say so on stdout. A unit test with a temp `OUT` that runs the merge function twice.

## Constraints
- Files: the plug `__init__.py` + README + its tests; `demo/anchor_domains.py`, `demo/three_domains.py`
  (+ the two siblings only for the same index-merge pattern), one demo test file. Nothing under `src/`,
  `contract/`, `tests/compat/`, `tests/contract/`.
- Nothing under `demo/out/` is modified or deleted: those are anchored artefacts.

## Acceptance
Full suite + plug tests green; frozen-path guard green; adversary's trees under `.attack/pr131/` re-run green;
REPORT.md lists, per thread, the commit-relative fix (file:line) so the CTO can reply on each thread and
resolve it, plus MUTATION TARGETS for the two plug changes.
