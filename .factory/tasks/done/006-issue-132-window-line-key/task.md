You are motus-lead. After TASK 005 (#120) is reported, start this one: `git fetch origin && git checkout -b
factory/132-window-line-key origin/main` (main carries ADR-028 ACCEPTED). Read: adr/ADR-028, GitHub issue #132,
src/vitruvyan_motus/commitlog.py (`_append` :590, `_replay` :1249, `receipt_for`), contract/commitment.v1.schema.json,
contract/README.md. Flow: ONE implementer (pi luna) → verifier (pi luna) → ONE adversary (claude, lens: an old
log and a new log side by side — roots, checkpoints, receipts, and a log with both spellings in one window).
Never commit/push. Frozen dirs untouched. REPORT.md with MUTATION TARGETS.

# TASK 006 — #132 under ADR-028: the window line is the commitment envelope

1. FIRST, the hypothesis H1 as a test that must pass before anything else changes: take a window written under
   `c`, re-emit the same commitments under `commitment`, reopen both logs, assert equal leaves, equal window
   root and a byte-identical checkpoint. If it fails, STOP and report: the ADR is wrong.
2. `_append` writes `{"commitment": …, "witness": …}`. `_replay` and every reader of window lines read
   `commitment` and fall back to `c`; a log that contains a `c` line reports it once in its diagnostics as
   "written by a Motus before 0.14.0" (find the existing diagnostics channel; do not invent a print). No rewrite
   tool. `motus-validate commitment` unchanged (strict).
3. Tests: new logs validate line by line with `motus-validate commitment` (the Limen finding, 18/18); an old
   log opens, verifies its chain, produces receipts, and reports the diagnostic once; a window mixing both
   spellings (a log upgraded mid-window) opens and its checkpoint verifies; the fallback is marked for removal
   at 1.0.0 with a test that fails when TRACE_SCHEMA_VERSION or __version__ reaches 1.0.0 while the fallback
   exists. contract/README.md: one sentence naming the key on disk. README: which releases wrote `c`.
4. demo/out window files stay as they are (frozen evidence of the prior form); the demo README says so.
