# TASK 012 — #123, ADR-031: an anchor is a chain, an attestation is a party

Written by the CTO session from the finishing implementer's report (subagents cannot write under `.factory/`).

## RESULT

Delivered. `receipt.v1` gains the optional `attestations[]` container with `$defs.Attestation` (seven required
fields, `proof` typed per type by `if/then`); `contract/validate.py` gains `KNOWN_ATTESTATION_TYPES`
(`rfc3161_timestamp` only), rules P9 and P10 (unknown type, algorithm the type does not admit, missing proof
keys — violation AND refusal, the P5 pattern), and the EXISTENCE verdict scoped by the subject (ADR-031
decision 7, review correction 1: CLAIMED only when the subject is the run root or a checkpoint whose range
includes the END; a BEGIN-only subject reports existence of the BEGIN and EXISTENCE NOT ESTABLISHED);
`commitments.py` gains the `Attestation` dataclass (validated through `_SCHEMA_FIELDS`, strict plain JSON
proof) and the `Attester` protocol; `commitlog.receipt_for(..., attestations=)` carries them; the evidence
package walks them like anchors. Six fixtures (311–316). The plug `plugs/motus-attest-rfc3161` builds the DER
`TimeStampReq`, reads `genTime` and the TSA name from the `TimeStampResp` with a bounded stdlib DER walker,
and produces the `Attestation` — **ADR-031 hypothesis H1 holds**: no dependency, and a real public TSA
(freetsa.org) parsed live. H1's line-count guess (200) was wrong (438 lines); not a falsification condition.

## ARTIFACTS

- `contract/receipt.v1.schema.json` (+51), `contract/validate.py` (+221/−34: `KNOWN_ATTESTATION_TYPES`,
  `_ATTESTATION_TYPE_RULES`, P9, P10, refusal echo, `_covers_execution` shared by anchors and attestations),
  `contract/README.md` (+38/−6: rows P9/P10, one refusal, «what an attestation is not»).
- `src/vitruvyan_motus/commitments.py` (+78: `Attestation`, `Attester`), `commitlog.py` (+8/−1),
  `evidence.py` (+5/−1).
- `tests/test_attestations.py` (431 lines, 22 tests incl. the four-row table that fails on a fifth member),
  `tests/test_commitments_schema_fields.py`, `tests/test_stand_ins.py`, `tests/test_contract_fixtures.py`.
- `contract/fixtures/311-receipt-attestation-rfc3161-claimed.json`, `312-…-p9-subject-is-not-a-checkpoint-or-root`,
  `313-…-p10-unknown-type`, `314-…-p10-algorithm-not-admitted`, `315-…-proof-missing-token-der`,
  `316-…-attestation-over-the-begins-checkpoint`.
- `plugs/motus-attest-rfc3161/` — `src/motus_attest_rfc3161/__init__.py` (438 lines: `_tlv`, `_children`,
  `_parse_timestamp_resp`, `_tsa_name`, `build_timestamp_request`, `Rfc3161Attester`), `tests/test_attester.py`
  (456 lines, 36 tests incl. the opt-in live test), `tests/fixtures/response.tsr` (a genuine `TimeStampResp`
  signed by a throwaway TSA with `openssl ts -reply` over a query built by the plug itself), `pyproject.toml`
  (zero runtime deps; `jsonschema` in test extras), `README.md`. `.github/workflows/ci.yml` (+6: the
  `anchor-plugs` job installs and tests this plug — PLAN-123 item 11 had been skipped).

## TESTS (raw)

```
$ .venv/bin/pytest tests/ plugs/motus-attest-rfc3161/tests -q
1396 passed, 6 skipped, 1 warning in 70.91s
$ python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
$ MOTUS_LIVE_TSA=1 .venv/bin/pytest plugs/motus-attest-rfc3161/tests/test_attester.py -k test_live_public_tsa -q
1 passed, 35 deselected in 0.78s
   → freetsa.org: {"type": "rfc3161_timestamp", "issuer": "www.freetsa.org", "issued_at": "2026-09-06T22:55:47Z",
     "algorithm": "sha256", "proof": {"tsa_url": "https://freetsa.org/tsr", "token_der_len": 6192}}
```
Fixture verdicts (`motus-validate receipt`): 311 exit 0, EXISTENCE CLAIMED naming the issuer and the
`openssl ts -verify` line; 312 exit 1 P9; 313 exit 1 P10 + all seven levels REFUSED («cannot evaluate the
attestation type 'qualified_timestamp'»); 314 exit 1 P10 («imprinted with 'sha1' … admits: sha256»);
315 exit 1 SCHEMA (`token_der` required); 316 exit 0, EXISTENCE NOT ESTABLISHED («existence of the BEGIN … does
not seal this receipt's END»). `tests/test_contract_fixtures.py` → 180 passed.

Hand-run neutering (not the tool: uncommitted tree): `type="rfc3161"` → `test_attest_builds_the_attestation_from_a_recorded_reply`
fails; nonce bound removed → `test_the_request_rejects_a_nonce_outside_its_bound` fails. Both restored.

## FINDINGS

- The plug's production code was right except ONE field: it emitted `type="rfc3161"` while the contract's
  closed set is `{"rfc3161_timestamp"}` — every attestation it produced would have been refused by P10. Fixed.
- The cheap model's test file had six distinct defects, not «one off-by-one»: a syntax error (`include-token-token`),
  calls to misnamed helpers, `_children` fed the outer tag, a `Rfc531Attester` typo in the live test, a recorded
  fixture signed over the wrong imprint (single vs double hash — unfixable by patching bytes, regenerated
  end-to-end), and hand-typed DER lengths (replaced by a `tlv`/`enc_len` encoder so the class cannot recur).
- **Recorded, not decided**: `_MAX_DEPTH` in `_tlv` is correct in isolation but no call site recurses on the
  attacker's nesting (each passes a literal depth matching the fixed TimeStampResp shape); deeply nested
  garbage is refused for another reason at depth 3. The docstring's claim is split into two tests; whether to
  correct the docstring or the architecture is for the adversarial round.
- CI never ran the plug's suite (PLAN item 11 skipped); fixed.

## MUTATION TARGETS

- `contract/validate.py:3595` P9 subject check; `:3604-3625` and the echo near `:3874` P10 (type / algorithm /
  proof keys); `:3996-4050` `_covers_execution` and the claim partitioning (fixtures 311/316 pin both branches).
- `plugs/motus-attest-rfc3161/src/motus_attest_rfc3161/__init__.py:299` OID check; `:297` imprint binding;
  `:331` nonce bound; `:106` `_MAX_DEPTH` (only killable directly); `:430` `type=ATTESTATION_TYPE`.

## OUT OF SCOPE

The three refused rows (`qualified_timestamp`, `electronic_seal`, `identity`) — each its own ADR; ADR-020
levels 6–7; the `_MAX_DEPTH` reachability gap (recorded).

## AGENTS

DeepSeek V4 Flash typed the contract, runtime, fixtures, `tests/test_attestations.py` and the plug's
production code (right except the `type` field); it stopped mid-way through the plug's tests. Sonnet finished:
plug tests rewritten against ADR-031's properties, fixture regenerated from a real signed request, live TSA run
once, CI step and test dependency added, delivery verified item by item.
