# Architectural Plan — #118: `receipt_for` + `execution` (ADR-027)

## 1. Schema (`contract/receipt.v1.schema.json`)

Add a `$defs.Execution` and reference it as an optional top-level property:

```json
"execution": { "$ref": "#/$defs/Execution" }
```
```json
"Execution": {
  "type": "object",
  "description": "ADR-027 decision 4: the execution's locating key and its canonical fingerprint. `ref` is the key a reader uses to ask for this receipt again; `fingerprint` is what the reader verifies (Trace.root, null while the execution has no terminal record — never substituted by run_id, a partial-trace digest, or the bundle fingerprint); `run_id` is carried for the embedder's own correlation and is NOT a key. Optional until schema_version 1.0.0 is superseded; required from 1.0.0 (ADR-027).",
  "required": ["ref", "fingerprint", "run_id"],
  "additionalProperties": false,
  "properties": {
    "ref": { "type": "string", "minLength": 1 },
    "fingerprint": {
      "oneOf": [
        { "$ref": "commitment.v1.schema.json#/$defs/Digest" },
        { "type": "null" }
      ]
    },
    "run_id": { "$ref": "commitment.v1.schema.json#/$defs/Identifier" }
  }
}
```

`ref`'s shape (three `/`-joined non-empty segments, third a canonical decimal integer) is deliberately **not** a JSON Schema `pattern` — verifying it actually names the receipt's own BEGIN requires comparing against the segment's real `sequence`, which schema can't express. That check belongs in `validate.py`, reimplemented independently (never imported from `commitlog.py`), per this repo's "the contract recomputes, never imports" rule (see §3 below).

`additionalProperties: false` at the receipt's top level already exists; adding `execution` as a newly-allowed key doesn't touch any frozen fixture, since it's optional and absent from all of them today.

## 2. `contract/README.md`

- One paragraph under the `receipt.v1.schema.json` bullet in "What lives where," citing ADR-027: what `execution` carries and why `run_id` is explicitly documented as not a key.
- Two new rows in the P-rule table (§"The commitment rules, and why they recompute"):

| Rule | What it refuses |
|---|---|
| `P7` | An `execution.ref` that is malformed, or names a `(tenant, writer_id, sequence)` other than the receipt's own first segment's BEGIN |
| `P8` | An `execution.fingerprint` that disagrees with the root the paired trace derives (including: non-null where the trace derives none, or null where it derives one) |

Drive-by note, not required for this task: the existing `V1` rule (resume-fingerprint check at the bottom of `verify()`) is likewise undocumented in that table today.

## 3. `contract/validate.py` — two checks, two different homes

**P7 — structural, lives in `validate_receipt()`.** No trace needed, so it's fixture-testable via `tests/contract/`. Checked once per receipt, against **`segments[0]["begin"]["commitment"]`** — the *first* segment, not the last. ADR-027 names the execution by its *original* BEGIN's position; each resume's own BEGIN has its own coordinate, used only by `continues`, never by `execution.ref`. (This is the one point the ADR leaves implicit rather than states outright — see Risk 2.)

Logic, when `execution` is present:
- reject if `ref` doesn't split into exactly 3 non-empty `/`-separated segments, or the third isn't a canonical decimal integer (`str(int(x)) == x`: no sign, no leading zero);
- reject if the three parsed fields don't equal `(begin.tenant, begin.writer_id, begin.sequence)` of `segments[0]`.

**P8 — needs the trace, lives in `verify()`**, not `validate_receipt()`, and therefore is **not** exercisable through `tests/contract/` fixtures (that harness only calls `validate_receipt(document)` — see `tests/test_contract_fixtures.py:105-106`). It's covered only by `tests/test_verifier.py`'s tamper test.

Placement: reuse `derived_root(trace)` — currently computed inline inside the `INTEGRITY` branch only when `trace is not None and "end" in last`. Restructure so the computed root is available regardless of branch (compute once, feed both the `INTEGRITY` block and this new check — don't call `derived_root` twice). Compare `receipt.get("execution", {}).get("fingerprint")` to it; on mismatch, append a `P8` `Violation` and downgrade `INTEGRITY` to `NOT_ESTABLISHED`, exactly the pattern the existing `V1` continuation check already uses at the bottom of `verify()`:

```python
violations = violations + [Violation("P8", "$.execution.fingerprint", "...")]
findings = [Finding(f.level, NOT_ESTABLISHED, f.reason) if f.level == "INTEGRITY" else f for f in findings]
```

Skip entirely (no violation) when `execution` is absent, or when `trace is None` — consistent with `INTEGRITY` itself reporting "no trace was supplied" rather than treating that as a violation.

## 4. `CommitmentLog.receipt_for`

```python
def receipt_for(self, execution_ref: str, *, anchors: Iterable[AnchorReceipt] = ()) -> dict:
```

- **Parse `execution_ref` before any I/O** — `ValueError` on bad shape (same rule as P7, reimplemented locally; `commitlog.py` and `validate.py` never call each other).
- If parsed `(tenant, writer_id) != (self.tenant, self.writer_id)` → `ValueError` naming the ref as not found. This store is already scoped to one tenant/writer directory, so a mismatch here is indistinguishable from "unknown ref."
- Search **sealed windows only** (`self._checkpoint_indices()` → `self._sealed_window(index)`) — an `Entry` structurally requires a `checkpoint`, so a BEGIN sitting in the still-open window can't be represented at all. Locate the BEGIN at the parsed sequence.
  - Not found in any sealed window → `ValueError` naming the ref.
  - Found, but it names an END, not a BEGIN → `ValueError` naming the ref (the explicit "names an END" refusal from decision 4).
- Segment 0: `proof_for(run_id, BEGIN, index, sequence=sequence).to_dict()`. Look for its paired END by scanning that **same sealed window**'s commitments for the matching `run_id` with `kind == END` (not "first END with that run_id" — see Risk 3), then `proof_for` it by its own sequence.
- Resumed segments: scan **all sealed windows, in order**, for a BEGIN whose `commitment.continues` names `(writer_id, sequence)` equal to the previous segment's BEGIN. Repeat until no successor is found in a sealed window — forward-only (see Risk 2).
- `mode`: the **first segment's** BEGIN's achieved mode (`InclusionProof.mode`) — not a free choice; it must match what `validate_receipt`'s own `P3` check keys off (`first = segments[0]["begin"]`), or `receipt_for`'s own output would fail the validator it has to pass.
- `anchors`: `[a.to_dict() for a in anchors]`, key omitted if empty.
- `execution`: `{"ref": execution_ref, "fingerprint": <last segment's END root, or None>, "run_id": segments[0].begin.commitment.run_id}`.
- **Read-only**: uses only `_checkpoint_indices`, `_sealed_window`, `proof_for` — never touches `self._lock`, `begin`, `end`, or `seal`.

## 5. Tests

- Replace the hand-built fixture at `tests/test_verifier.py:63-73` with a real `log.receipt_for(...)` call — this is the change that makes the rest of the suite exercise the real code path.
- Schema + `motus-validate receipt --trace`, via `receipt_for`: finished / unfinished (sealed BEGIN, no END) / resumed-across-two-segments / with and without anchors.
- Refusals: bad shape (`"acme"`, `"acme/w1"`, `"acme/w1/x"`, `"acme//1"`), unknown ref (well-formed, no such sequence), ref naming an END.
- Tamper: mutate `execution.fingerprint` on a `receipt_for` output; assert `verify()` reports `P8` and downgrades `INTEGRITY`.
- New negative fixture in `contract/fixtures/`: `receipt-p7-execution-ref-names-a-different-begin.json`, following the `receipt-p6-*` naming/shape (single document, no trace — this is the fixture-testable half; `P8` is not, since it needs a trace).
- Confirm `receipt-a-run-of-one-segment.json` / `receipt-a-run-resumed-once.json` still pass unchanged (they will — `execution` absent and optional).
- Verifier phase: also point `motus-validate` at `demo/out/anchor_receipt.json`; confirm it's actually a `receipt.v1` document and not an `AnchorReceipt` object before assuming decision 5 applies to it — note either way in the report rather than silently fixing or skipping.

## 6. Risks / edge cases flagged, not silently decided

1. **A BEGIN that exists but sits in the still-open (unsealed) window.** Decision 4 only names two refusal shapes ("unknown ref," "names an END"); it's silent on "correct ref, real BEGIN, not sealed yet." Read-only means it can't be fixed by sealing. I'm treating it as the same `ValueError` as "unknown ref," but with a distinguishable message ("not yet sealed" vs. "no such position") so a caller doesn't mistake a timing issue for a typo.
2. **Which segment does `execution.ref` name, for a resumed execution?** I've assumed the *first* segment's BEGIN — the stable, original locator, consistent with each resume's own coordinate already being used by `continues` rather than by `ref`. This is exactly the question adversarial lens 2 ("can a ref name something that is not a BEGIN, resumed runs") should stress: a receipt requested by a *later* segment's own (also valid, sealed) BEGIN position is a *different*, legitimate `execution_ref` naming a later execution-start — `receipt_for` on it would produce a receipt with just that segment forward, never walking backward to the predecessor. State this asymmetry in the docstring so it doesn't read as a bug later.
3. **Same-window BEGIN/END pairing when `run_id` repeats** (a retried job, two unpaired BEGINs in one window). `proof_for` already refuses an ambiguous `run_id` without a `sequence`; `receipt_for`'s internal END lookup must rely on the runtime's no-overlap invariant (a BEGIN's END, if any, is the next commitment for that `run_id` with no intervening BEGIN for it) — not "first END with matching run_id in file order," which would misattribute across a retried run_id.
4. **`mode` for a resumed execution** — decision 2 says "the mode ACHIEVED," singular, but N segments can each achieve a different mode (e.g. segment 0 witnessed, segment 1 local after a witness outage). Picking "first segment's" isn't a stylistic choice — it's forced by what `validate_receipt`'s `P3` check already reads.

None of the above reopens a CTO decision; all are implementation choices the ADR/task leave implicit, resolved for internal consistency with what `validate.py` already checks.
