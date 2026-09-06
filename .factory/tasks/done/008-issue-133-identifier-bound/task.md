You are motus-lead. `git fetch origin && git checkout -b factory/133-identifier-bound origin/main` (main at d36967c
carries #118, #120, #132, #125). Read: GitHub issue #133, adr/ADR-021, contract/commitment.v1.schema.json (`$defs`:
Identifier, Digest, Timestamp), contract/receipt.v1.schema.json, src/vitruvyan_motus/commitments.py (`_require_text`
and every dataclass that ends up in a receipt: AnchorReceipt, Commitment, Checkpoint, WitnessAck, Continuation),
src/vitruvyan_motus/commitlog.py (`receipt_for` — the ad-hoc 200-character checks 004 added, which this task
replaces with the class fix), .attack/pr131/a08_*.py if present. Flow: architect (claude) → ONE implementer (pi
luna) → verifier (pi luna) → ONE adversary (claude, lens: a value the runtime accepts and the validator refuses —
find any remaining one). Two correction rounds max. Never commit/push. Frozen dirs untouched. REPORT.md with
MUTATION TARGETS.

# TASK 008 — #133: what the runtime builds, the validator accepts

The class: a runtime dataclass field that maps to a schema `Identifier` (≤ 200 chars, non-blank), `Digest`
(`sha256:` + 64 hex) or `Timestamp` (RFC 3339 UTC) is checked today only for "non-blank" (`_require_text`). The
adversary on #131 built an `AnchorReceipt.reference` of 205 characters that the runtime accepted and the schema
refused; 004 patched `receipt_for` with ad-hoc length checks. Repair the class:
1. One module-level table in commitments.py, `_SCHEMA_FIELDS`, mapping each runtime type and field to its schema
   type (Identifier / Digest / Timestamp), and one validator per schema type that implements the schema's bound
   exactly (read the bounds from the schema's $defs at import time if that is cheap and stdlib-only — it is one
   json.load of a shipped file; otherwise mirror the constants with a test that reads the schema and asserts
   they are equal, so a schema change forces the runtime to follow).
2. Every `__post_init__` of those dataclasses validates through the table; `_require_text` stays only where the
   schema says free text. The ad-hoc checks in `receipt_for` and in the anchor-list branch are removed.
3. THE test that enumerates the class: a parametrised test walks every dataclass field of the runtime types that
   appears in receipt.v1 / commitment.v1 / checkpoint.v1 and asserts it is either in `_SCHEMA_FIELDS` or in an
   explicit allowlist of free-text fields with a reason — a new field forces a decision.
4. Tests: a 201-character reference, a digest with a wrong prefix or length, a timestamp with an offset instead
   of Z, each refused at construction with a message naming the field and the schema type; the plug's a08
   scenario ends at the runtime, not at the validator; existing frozen fixtures unaffected; full suite; frozen
   paths PASS; SLO gate untouched (construction cost: measure `Commitment(...)` before/after with timeit, report
   the ratio — if above +10 %, say so and stop, do not micro-optimise around it).
