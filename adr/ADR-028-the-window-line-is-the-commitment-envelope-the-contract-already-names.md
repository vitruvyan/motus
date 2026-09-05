# ADR-028 — the window line is the commitment envelope the contract already names

- **Status:** PROPOSED
- **Date:** 2026-09-05
- **Accepted:** —
- **Authority:** CTO proposes; the founder accepts. Closes #132 (P1, pre-freeze, contract).
- **Depends on:** ADR-021 (the commitment log, §5: a verifier compares the log against a witness's tally).
- **Amends:** nothing in `contract/`. The schema is right; the implementation is wrong, and this ADR records the migration rather than the rule. `contract/README.md` gains one sentence naming the key on disk.

## Context

`contract/commitment.v1.schema.json` is titled *Motus commitment envelope v1*: `required: ["commitment"]`, properties `commitment` and `witness`, `additionalProperties: false`. `CommitmentLog._append` (`commitlog.py:590`) writes each window line as `{"c": <commitment>, "witness": …}`, and `_replay` reads `body["c"]` (`commitlog.py:1249`).

Measured on Limen's first end-to-end run (2026-09-04, Motus 0.12.0): 18 window lines, 9/9 traces valid, the checkpoint valid, and **18/18 window lines refused** by `motus-validate commitment` — *Additional properties are not allowed ('at', 'kind', 'nonce', 'run_id', 'sequence', 'tenant', 'writer_id')*. Renamed `c` → `commitment`, 18/18 pass and nothing else differs. An earlier adversarial round had noticed the shape and it was not filed.

The window file is the artifact ADR-021 §5 hands to a verifier beside the checkpoint. Today the checkpoint validates and every line it commits to does not, so a third party running the published validator on the published log sees a contract violation per line. That is the authority order backwards: implementation over contract.

**What the key does not touch.** The leaf digest is taken over the commitment's canonical bytes (`commitment.leaf`, forced before the write since #112), the window root is the Merkle root over leaves, and the checkpoint commits to the window root. The envelope key is outside every digest. This is the hypothesis H1 below, and the implementation must prove it rather than assume it.

## Decision

1. **The key on disk is `commitment`.** From the release that carries this ADR, `_append` writes `{"commitment": …, "witness": …}`, exactly the envelope the schema names. No short form, no alias in the schema.
2. **Readers accept `c` for logs written before that release, and say so.** `_replay` and `receipt_for` read `commitment` and fall back to `c`; a log opened with a `c` line is reported once, in the log's own diagnostics, as *written by a Motus before 0.14.0*. The fallback is removed at 1.0.0 (contract freeze), which is the deprecation window; the ADR that freezes 1.0.0 must list it.
3. **No rewrite of evidence.** Window files already on disk — `demo/out`, the Limen trial, any pilot — keep their bytes. Motus does not ship a tool that rewrites an evidence file, even one that provably moves no root: an artifact that was edited after the fact is a worse thing to explain than a key that was wrong. Those files stay refused by `motus-validate commitment` and accepted by the log reader, and the README says which releases wrote them.
4. **The validator stays strict.** `motus-validate commitment` validates the envelope the schema names and nothing else. A version-scoped acceptance of `c` would put two spellings of one thing into the contract for ever, which is option 2 of #132, rejected below.

## Consequences

- Limen's trial log and Motus's own demo logs remain non-validating line by line. The cost is a sentence in two READMEs; the alternative was to make the contract describe the mistake.
- Every reader of window files outside this repository (Perpetuum, a witness tally) learns the key from the schema, which is what a contract is for; the ones written against `c` were written against a bug.
- The deprecation window is bounded by an event (1.0.0), not a date, so it cannot be forgotten: the freeze ADR has to enumerate what it removes.

## Alternatives rejected

- **Option 2 of #132 — the schema documents `c` as the on-disk short key.** Cheaper today, two spellings of one thing for ever, and the schema would carry an explanation of an accident.
- **A rewrite tool for old logs.** Provably root-preserving under H1, still an edit of evidence after the fact. Not shipped.
- **Reading only the new key from day one.** Breaks every existing log for a one-word gain; the fallback costs one branch and a diagnostic.

## Hypothesis, and how it fails

**H1 — no digest anywhere covers the envelope key.** Falsification plan, which is the implementation's first test: take a window written under `c`, re-emit it under `commitment`, reopen both, and assert equal leaves, equal window root and a byte-identical checkpoint. If any of the three differs, this ADR is wrong about the migration being free and must be amended before the code changes.
