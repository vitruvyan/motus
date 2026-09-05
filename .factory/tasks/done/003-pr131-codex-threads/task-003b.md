You are motus-lead. TASK 003b — corrections to task 003, decided by the CTO after reading the diff against the
contract. ONE implementer (pi, luna), then verifier (pi, luna), then ONE adversary (claude) on the two changes
below only. Never commit/push. Update REPORT.md with a "## 003b" section and stop.

A. THE CONTRACT WINS. `contract/receipt.v1.schema.json:86-96` requires `published_at` on an `anchored` receipt,
   and `AnchorReceipt.__post_init__` (src/vitruvyan_motus/commitments.py:205-214) enforces exactly that, saying
   why. Task 003 bypassed it with `object.__new__(AnchorReceipt)` + `object.__setattr__` in `_receipt()`: a plug
   forging a runtime object the validator refuses. My brief's wording ("published_at=None on anchored") was wrong;
   the Codex thread (P2, plug:76) offered "require and demonstrate a resolver in the public usage example, or
   provide a contract-compliant default". We take the first:
   1. Delete the bypass in `_receipt()`. Restore `_resolve_block_time` to raise when no resolver is supplied,
      with its original message (git show HEAD for the text), and `-> str`.
   2. `upgrade()` without a resolver, on a proof that has reached a block, raises that ValueError — before
      mutating anything the caller holds. `publish()` never needs a resolver. `state()` must NOT need one: it
      re-derives "anchored"/"pending" from the proof alone (fix it if it currently calls the resolver; Codex
      says it raises today).
   3. README: the public usage example constructs `OpenTimestampsAnchor(block_time=...)` with a small
      documented resolver (stdlib `urllib` against a block explorer, `published_at_source` = the explorer URL,
      not our clock), and one sentence saying why the plug refuses to guess. No new runtime dependency.
   4. Drop `published_at_source` handling that assumes an anchored receipt without a time. When a resolver IS
      supplied, `proof["published_at_source"]` records what the embedder said it asked (a string the
      constructor may take: `block_time_source: str | None`), else absent.
   5. THE CLASS, not the instance: add ONE plug test that validates every receipt the plug returns (publish,
      upgrade pending, upgrade anchored, unreachable calendar) against `contract/receipt.v1.schema.json`'s
      anchor entry via the repo's own validator (see how `contract/validate.py` or tests/ validate receipts), so
      a forged object can never pass again. Replace `test_an_anchor_without_a_block_time_reports_unavailable_time`
      with: no resolver + block reached → ValueError naming `block_time=`; the receipt passed in is unchanged.

B. BOUND THE LOOP. `upgrade()` is now `while True` over network answers with serialized-state progress as its only
   exit. A calendar that reveals one new pending attestation per answer forever makes it spin (the adversary's
   own a03 shows the shape; the old `range(4)` was the guard against exactly this, only too small). Keep progress
   detection AND a hard ceiling of 32 passes (a constant with a comment saying it is a guard, not an algorithm);
   on hitting it, record `{"calendar": "<last>", "commitment": "<msg>", "reason": "upgrade stopped after 32
   passes: the proof kept growing"}` in `calendars_unreachable` and return the receipt pending. Test with a
   calendar double that grows the proof on every call; the test must finish in under a second.

Verifier: full suite, plug suite, frozen-path guard, plus `.attack/pr131/*.py` re-run. Adversary lens: can any
path still return an `AnchorReceipt` the schema refuses (grep for `object.__new__`, `__setattr__`, `replace(` on
receipts); can a calendar answer keep `upgrade()` busy past the ceiling. Add mutation targets for A.2 and B to
the report's MUTATION TARGETS list.
