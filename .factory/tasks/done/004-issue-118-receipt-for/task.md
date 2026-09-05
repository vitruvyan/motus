You are motus-lead. Checkout /home/vitruvyan/motus-factory (this cwd), detached at origin/main (6b56ed0, v0.13.0):
create the branch `factory/118-receipt-for` from origin/main first (git checkout -b), never commit on it.
Read first: AGENTS.md, adr/ADR-027 (ACCEPTED by the founder on 2026-09-05 — PR #136 lands it on main; the text is
final), adr/ADR-021 §7, contract/receipt.v1.schema.json, contract/README.md rule 4, src/vitruvyan_motus/commitlog.py
(`InclusionProof.to_dict()` now emits a receipt.v1 Entry, #119), contract/validate.py `verify(receipt, trace)`,
tests/test_verifier.py (the hand-built fixture at :63-73 is what this task makes unnecessary), GitHub issue #118.
Flow: architect (claude) → ONE implementer (pi luna) → verifier (pi luna) → TWO adversaries (claude), lenses:
(1) the public protocol a consumer implements against; (2) positions and identity — can two executions collide
on a ref, can a ref name something that is not a BEGIN, resumed runs. Two correction rounds max. REPORT.md in
`.factory/tasks/` with MUTATION TARGETS. Never commit/push. `tests/contract/` and `tests/compat/` are frozen.

# TASK 004 — #118: Motus produces the receipt its own verifier verifies

## Decisions (CTO; the architect refines, does not reopen)
1. CONTRACT, under ADR-027 decision 4 (this is the ADR the change needs; cite it in the schema description):
   `contract/receipt.v1.schema.json` gains a top-level object `execution` — `ref` (string, `<tenant>/<writer_id>/<sequence>`,
   none of the three segments may contain `/`, sequence is the decimal integer of the BEGIN), `fingerprint`
   (`sha256:…` Identifier or null: the Trace.root, null while the execution has no terminal record — never
   substituted by anything else), `run_id` (string, documented in the schema as "carried for the embedder's
   correlation; NOT a key"). OPTIONAL now, the schema description says "required from 1.0.0". Every frozen receipt in
   tests/contract and tests/compat must still validate unchanged. `contract/README.md` receipt section: one
   paragraph. `contract/validate.py verify()`: when `execution` is present, check `ref` shape, that the segment's
   BEGIN carries that tenant/writer_id/sequence, and that `fingerprint` equals the root derived from the trace
   (a disagreement is a violation with its own rule id, added where the receipt rules live).
2. LIBRARY: `CommitmentLog.receipt_for(execution_ref: str, *, anchors: Iterable[AnchorReceipt] = ()) -> dict`
   returns a receipt.v1 document: `schema_version`, `mode` = the mode ACHIEVED read from the commitment (never
   the configured one), `segments` (BEGIN and its END if any; `end` absent for an unfinished execution — the
   verifier already says "an execution that left no completion"), each entry via `InclusionProof.to_dict()` with
   its checkpoint and link to the previous checkpoint, `anchors` only if supplied, `execution` as in 1.
   A ref that does not name a BEGIN in this log → `ValueError` naming the ref; a ref with a bad shape → ValueError
   at the boundary before any I/O. A resumed execution (ADR-023, Continuation) spans more than one segment: the
   receipt carries ALL segments of the execution it can find in this log, and if the predecessor lives in another
   log it says so in a top-level `continuation` note only if the schema already admits it — otherwise the receipt
   carries what this log has and the report says what is left out. Read-only: receipt_for never appends, never
   seals, never takes the exclusive lock longer than a read needs.
3. NO new identity, no digest of the receipt itself (#120 will say the same for the package).
4. TESTS (each fails without the code): the produced receipt validates against the schema AND passes
   `motus-validate receipt <r> --trace <t>` for: a finished execution; an unfinished one; a resumed one across
   two segments; with and without anchors. `tests/test_verifier.py`'s hand-built fixture is replaced by
   `receipt_for`. Refusals: bad shape, unknown ref, a ref naming an END. Tamper: a receipt whose `fingerprint` is
   edited is refused by `verify` with the new rule id. Frozen-path guard green.
5. Verifier runs the full suite, the frozen-path guard, and `motus-validate` on the artifacts under demo/out
   that carry receipts (they must still validate: the field is optional).
